import asyncio
import datetime
import hashlib
import hmac
import re
import time
import uuid
from contextlib import suppress
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    Chat,
    ErrorEvent,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

import config
import db
import services.convert as conv
import services.documents as docs
import services.generator as gen
import services.images as images_mod
import services.payments as payments
import services.pdf as pdf_tool
import services.quiz as quiz_tool
import services.storage as file_store
import services.badwords as badwords
import services.moderation as mod
from config import log
from i18n import DEFAULT_LANG, LANGS, t
from services.fsm_pg import EventIsolation, PostgresStorage
from services.runtime import run_blocking, run_sync

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


@router.error()
async def global_error_handler(event: ErrorEvent):
    ex = event.exception
    if isinstance(ex, TelegramForbiddenError):
        log.info("Foydalanuvchi botni bloklagan (xabar yuborilmadi): %s", ex)
        return True
    if isinstance(ex, TelegramBadRequest):
        msg = str(ex).lower()
        if (
            "query is too old" in msg
            or "message is not modified" in msg
            or "message to edit not found" in msg
        ):
            log.info("TelegramBadRequest e'tiborga olinmadi: %s", ex)
            return True
    log.error("Update failed", exc_info=(type(ex), ex, ex.__traceback__))
    update = event.update
    target = update.message or (update.callback_query.message if update.callback_query else None)
    if target:
        try:
            await target.answer(
                t(DEFAULT_LANG, "error_generic"), reply_markup=kb_menu(DEFAULT_LANG)
            )
        except Exception:
            log.warning("Could not deliver error feedback")
    return True


_busy: set[int] = set()


async def try_busy(uid: int, target: Message, state: FSMContext) -> bool:
    if uid in _busy:
        lang = (await state.get_data()).get("lang", DEFAULT_LANG)
        await target.answer(t(lang, "busy"))
        return True
    _busy.add(uid)
    return False


def doc_too_big(doc) -> bool:
    return bool((getattr(doc, "file_size", 0) or 0) > config.MAX_DOWNLOAD)


_gemini_lock = asyncio.Semaphore(config.GEMINI_MAX_CONCURRENT)
_gemini_queue = 0
_running_jobs: set[asyncio.Task] = set()


async def notify_premiums_once(bot: Bot):
    now = time.time()
    for soon, users in (
        (True, await db.expiring_premiums(86400)),
        (False, await db.expired_premiums()),
    ):
        for user in users:
            try:
                lang = user.get("lang") or DEFAULT_LANG
                text = (
                    t(
                        lang,
                        "premium_expire_soon",
                        hours=max(1, int((user["premium_until"] - now) // 3600)),
                    )
                    if soon
                    else t(lang, "premium_expired")
                )
                await bot.send_message(user["id"], text)
            except TelegramForbiddenError:
                pass  # Do not retry permanently blocked users every cycle.
            except Exception:
                log.exception("Premium notice failed uid=%s", user["id"])
                continue
            await db.mark_premium_notified(user["id"], user["premium_until"], soon)


async def premium_notifier(bot: Bot) -> None:
    while True:
        try:
            await notify_premiums_once(bot)
        except Exception:
            log.exception("Premium notification scan failed")
        await asyncio.sleep(6 * 3600)


async def maintenance():
    while True:
        try:
            await asyncio.to_thread(file_store.cleanup_expired)
            await asyncio.to_thread(conv.cleanup_temp)
            await db.prune_expired_records()
        except Exception:
            log.exception("Storage maintenance failed")
        await asyncio.sleep(3600)


async def call_ai(target: Message, state: FSMContext, fn, *args):
    global _gemini_queue
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    if _gemini_queue >= config.GEMINI_MAX_CONCURRENT + config.GEMINI_QUEUE_LIMIT:
        raise RuntimeError(t(lang, "queue_full"))
    _gemini_queue += 1
    notified = None
    acquired = False
    try:
        if _gemini_lock.locked():
            notified = await target.answer(
                t(lang, "queue", pos=max(1, _gemini_queue - config.GEMINI_MAX_CONCURRENT))
            )
        await asyncio.wait_for(_gemini_lock.acquire(), timeout=300)
        acquired = True
        if notified:
            await delete_quietly(notified)
            notified = None
        return await run_sync(fn, *args)
    finally:
        if acquired:
            _gemini_lock.release()
        _gemini_queue -= 1
        if notified:
            await delete_quietly(notified)


def _pay_kb(lang):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(t(lang, "btn_premium"), "m:premium")],
            [btn(t(lang, "back"), "back")],
        ]
    )


async def begin_job(uid: int, kind: str, target: Message, state: FSMContext, payload: dict):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    job = await db.reserve_job(uid, kind, payload)
    if job and job.get("busy"):
        await target.answer(t(lang, "busy"))
        return None
    if not job:
        await target.answer(t(lang, "limit_msg", n=config.DAILY_LIMIT), reply_markup=_pay_kb(lang))
    return job


_document_lock = asyncio.Semaphore(2)
_images_lock = asyncio.Semaphore(2)


async def run_document_job(
    target, state, uid, kind, file_kind, title, produce, caption_key, payload=None, after=False
):
    lang = (await ctx(state, uid))["lang"]
    job = await begin_job(uid, kind, target, state, payload or {})
    if not job:
        return
    _running_jobs.add(asyncio.current_task())
    wait = None
    started = time.monotonic()
    try:
        wait = await target.answer(t(lang, "working"))
        path, missing_images = await produce()
        path, fid = await keep(uid, file_kind, title, path, job_id=job["id"])
        await state.update_data(
            last_file=str(path), trans_lines=[], ppt_text="", trans_src="", ppt_src=""
        )
        await state.set_state(None)
        caption = t(lang, caption_key)
        if missing_images:
            caption += "\n" + t(lang, "ppt_images_missing", count=missing_images)
        await delete_quietly(wait)
        wait = None
        await send_file(
            target, path, caption, kb_after(lang, fid, job["id"]) if after else kb_back(lang)
        )
        log.info(
            "AI job completed id=%s kind=%s elapsed=%.2fs",
            job["id"],
            file_kind,
            time.monotonic() - started,
        )
    except Exception:
        log.exception("AI job failed id=%s kind=%s", job["id"], file_kind)
        # A completed file remains in My files if Telegram delivery failed.
        completed = await db.get_job(job["id"], uid)
        await target.answer(
            t(lang, "delivery_retry" if completed else "error_generic"), reply_markup=kb_back(lang)
        )
    finally:
        if wait:
            await delete_quietly(wait)
        try:
            await db.refund_job(job["id"])  # A completed job is never refunded.
        finally:
            _running_jobs.discard(asyncio.current_task())


LANG_TARGET = {"uz": "oʻzbek tiliga", "ru": "русский язык", "en": "English"}


class St(StatesGroup):
    lang = State()
    name = State()
    topic = State()
    count = State()
    conv = State()
    quiz_file = State()
    quiz_count = State()
    quiz_time = State()
    quiz_active = State()
    tmerge = State()
    tsplit_file = State()
    tsplit_range = State()
    tdocx2ppt = State()
    tslides = State()
    trewrite = State()
    ttranslate = State()
    murojaat = State()
    pay_photo = State()
    admin_reply = State()


MODE_NAMES = {"ref": "Referat", "ppt": "Prezentatsiya", "xls": "Jadval", "test": "Test"}


async def user_name(uid: int) -> str:
    u = await db.get_user(uid)
    if not u:
        return str(uid)
    return u.get("name") or u.get("username") or str(uid)


def esc(v) -> str:
    from html import escape

    return escape(str(v))


async def ctx(state: FSMContext, uid: int) -> dict:
    data = await state.get_data()
    u = await db.get_user(uid)
    u = u or {}
    data.setdefault("lang", u.get("lang") or DEFAULT_LANG)
    data.setdefault("name", u.get("name") or "")
    await db.touch_user(uid)
    return data


async def notify_admin(bot: Bot, text: str):
    for aid in await db.admin_ids():
        try:
            await bot.send_message(aid, text)
        except Exception as e:
            log.debug("Admin (ID %s)ga xabar bormadi: %s", aid, e)


MODES = {
    "ref": {"kind": "pages", "ask": "ask_topic_ref", "done": "done_ref", "ext": "docx"},
    "ppt": {"kind": "slides", "ask": "ask_topic_ppt", "done": "done_pptx", "ext": "pptx"},
    "xls": {"kind": "rows", "ask": "ask_topic_xlsx", "done": "done_xlsx", "ext": "xlsx"},
    "test": {"kind": "questions", "ask": "ask_topic_test", "done": "done_test", "ext": "docx"},
}
COUNT_RANGE = {"pages": (1, 30), "slides": (3, 30), "rows": (5, 200), "questions": (5, 50)}


def btn(text, cb):
    return InlineKeyboardButton(text=text, callback_data=cb)


def kb_lang() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[btn(f"{v['flag']} {v['name']}", f"l:{k}")] for k, v in LANGS.items()]
    )


def kb_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(t(lang, "btn_referat"), "m:ref"), btn(t(lang, "btn_pptx"), "m:ppt")],
            [btn(t(lang, "btn_xlsx"), "m:xls"), btn(t(lang, "btn_test"), "m:test")],
            [btn(t(lang, "btn_quiz"), "m:quiz")],
            [btn(t(lang, "btn_doc2pdf"), "c:docx"), btn(t(lang, "btn_pdf2doc"), "c:pdf")],
            [btn(t(lang, "btn_img2pdf"), "c:img")],
            [btn(t(lang, "btn_myfiles"), "myfiles"), btn(t(lang, "btn_premium"), "m:premium")],
            [btn(t(lang, "btn_tools"), "m:tools")],
            [btn(t(lang, "btn_lang"), "lang"), btn(t(lang, "btn_support"), "m:support")],
        ]
    )


def kb_after(lang: str, file_id: int, job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(t(lang, "to_pdf"), f"topdf:{file_id}"), btn(t(lang, "again"), f"again:{job_id}")],
            [btn(t(lang, "back"), "back")],
        ]
    )


def kb_back(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[btn(t(lang, "back"), "back")]])


def kb_premium(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("💳 " + t(lang, "premium_buy_btn"), "premium_buy")],
            [btn(t(lang, "back"), "back")],
        ]
    )


def kb_conv_pick(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(t(lang, "conv_docx"), "c:docx"), btn(t(lang, "conv_pdf"), "c:pdf")],
            [btn(t(lang, "conv_img"), "c:img")],
            [btn(t(lang, "back"), "back")],
        ]
    )


def kb_tools(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn(t(lang, "t_conv"), "t:conv"),
                btn(t(lang, "t_merge"), "t:merge"),
                btn(t(lang, "t_split"), "t:split"),
            ],
            [
                btn(t(lang, "t_docx2ppt"), "t:docx2ppt"),
                btn(t(lang, "t_rewrite"), "t:rewrite"),
                btn(t(lang, "t_translate"), "t:translate"),
            ],
            [btn(t(lang, "back"), "back")],
        ]
    )


def kb_target(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("🇺🇿 Oʻzbekcha", "x:uz"), btn("🇷🇺 Русский", "x:ru"), btn("🇬🇧 English", "x:en")],
            [btn(t(lang, "back"), "back")],
        ]
    )


def kb_quiz(lang: str, q: dict, qidx: int, session_id: str) -> InlineKeyboardMarkup:
    rows = []
    letters = "ABCDEFGH"
    for oi, opt in enumerate(q.get("options", [])):
        rows.append([btn(f"{letters[oi]}) {str(opt)[:40]}", f"q:{session_id}:{qidx}:{oi}")])
    rows.append([btn("⛔ " + t(lang, "quiz_stop").replace("⛔ ", ""), f"q:{session_id}:stop")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_about(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(t(lang, "btn_aboutwrite"), "m:aboutwrite")],
            [btn(t(lang, "back"), "back")],
        ]
    )


def user_dir(uid: int) -> Path:
    return file_store.user_dir(uid)


def new_file(uid: int, prefix: str, suffix: str) -> Path:
    return file_store.new_file(uid, prefix, suffix)


FILE_EMOJI = {
    "ref": "📄",
    "ppt": "🎤",
    "xls": "📊",
    "test": "📋",
    "pdf": "📄",
    "word": "📝",
    "merge": "🗂️",
    "split": "✂️",
    "docx2ppt": "🎤",
    "rewrite": "✍️",
    "translate": "🌐",
}
FILE_LABEL = {
    "ref": "Referat",
    "ppt": "Prezentatsiya",
    "xls": "Jadval",
    "test": "Test",
    "pdf": "PDF",
    "word": "Word",
    "merge": "PDF birlashma",
    "split": "PDF boʻlish",
    "docx2ppt": "PPT (Word→PPT)",
    "rewrite": "Qayta yozildi",
    "translate": "Tarjima",
}


def fmt_file_row(f: dict, idx: int, lang: str) -> str:
    kind = str(f.get("kind", ""))
    emoji = FILE_EMOJI.get(kind, "📁")
    label = FILE_LABEL.get(kind, kind)
    title = esc(str(f.get("title") or "")[:30])
    size = f.get("size") or 0
    s = f"{size // 1024} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"
    date = datetime.datetime.fromtimestamp(f.get("created_at") or 0, config.APP_TIMEZONE).strftime(
        "%d.%m"
    )
    return f"{idx}. {emoji} {label} — {title}\n    🗓 {date} · {s}"


async def safe_edit(msg, text: str, kb=None):
    if getattr(msg, "document", None) or getattr(msg, "photo", None) or getattr(msg, "text", None) is None:
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        if hasattr(msg, "answer"):
            await msg.answer(text, reply_markup=kb)
        elif hasattr(msg, "bot") and hasattr(msg, "chat"):
            await msg.bot.send_message(chat_id=msg.chat.id, text=text, reply_markup=kb)
        return
    try:
        await msg.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            if hasattr(msg, "answer"):
                await msg.answer(text, reply_markup=kb)
            elif hasattr(msg, "bot") and hasattr(msg, "chat"):
                await msg.bot.send_message(chat_id=msg.chat.id, text=text, reply_markup=kb)
    except Exception as e:
        log.debug("safe_edit bajarilmadi: %s", e)


async def send_file(target: Message, path: Path, caption: str, kb=None):
    await target.answer_document(FSInputFile(path), caption=caption, reply_markup=kb)


async def delete_quietly(msg):
    try:
        await msg.delete()
    except Exception as e:
        log.debug("Xabarni o'chirish imkonsiz: %s", e)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    await cancel_quiz(state)
    await state.clear()
    uid = message.from_user.id
    await db.touch_user(uid)
    existing = await db.get_user(uid)
    if existing and existing.get("name"):
        lang = existing.get("lang") or DEFAULT_LANG
        await state.update_data(lang=lang, name=existing["name"])
        await message.answer(t(lang, "greet", name=existing["name"]), reply_markup=kb_menu(lang))
        uname = f"@{message.from_user.username}" if message.from_user.username else "username yoʻq"
        await notify_admin(
            bot, f"🔁 Qayta kirdi\n👤 {esc(existing['name'])} ({uname})\n🆔 ID: {uid}"
        )
    else:
        await state.set_state(St.lang)
        await message.answer(t(DEFAULT_LANG, "lang_choose"), reply_markup=kb_lang())


@router.message(F.text.regexp(r"^/+$"))
async def slash_menu(message: Message, state: FSMContext, bot: Bot):
    await cmd_start(message, state, bot)


@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    await message.answer(t(lang, "help"), reply_markup=kb_menu(lang))


@router.message(Command("lang"))
async def cmd_lang(message: Message, state: FSMContext):
    await state.set_state(St.lang)
    await message.answer(t(DEFAULT_LANG, "lang_choose"), reply_markup=kb_lang())


_admin_attempts: dict[int, tuple[int, float]] = {}


@router.message(Command("admin"))
async def cmd_admin(message: Message, bot: Bot):
    uid = message.from_user.id
    if uid in await db.admin_ids():
        await message.answer(t(DEFAULT_LANG, "admin_info"))
        return
    now = time.monotonic()
    for key, (_, until) in list(_admin_attempts.items()):
        if until <= now:
            del _admin_attempts[key]
    attempts, until = _admin_attempts.get(uid, (0, now + 900))
    parts = (message.text or "").split(maxsplit=1)
    secret = config.ADMIN_SECRET
    accepted = False
    if (
        attempts < 5
        and secret
        and len(parts) == 2
        and hmac.compare_digest(parts[1].strip().encode(), secret.encode())
    ):
        accepted = await db.claim_admin_secret(uid, hashlib.sha256(secret.encode()).hexdigest())
    _admin_attempts[uid] = (attempts + 1, until)
    await message.answer(t(DEFAULT_LANG, "admin_ok" if accepted else "admin_denied"))
    if accepted:
        with suppress(TelegramBadRequest):
            await message.delete()
        await notify_admin(bot, f"🆕 Yangi admin: {uid}")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in await db.admin_ids():
        return
    s = await db.stats()
    kinds = await db.stats_kinds()
    lines = [
        "📊 Statistika\n",
        f"👥 Foydalanuvchilar: {s['total']}",
        f"🟢 Oxirgi 24 soatda: {s['day']}",
        f"📄 Yaratilgan hujjatlar: {s['docs']}",
        f"⭐ Premium: {s['premium']}",
    ]
    if kinds:
        lines.append("")
        lines.append("📁 Hujjat turlari:")
        for k in kinds:
            lines.append(f"   • {FILE_LABEL.get(k['kind'], k['kind'])}: {k['n']}")
    await message.answer("\n".join(lines))


@router.message(Command("grant"))
async def cmd_grant(message: Message, bot: Bot):
    if message.from_user.id not in await db.admin_ids():
        return
    parts = (message.text or "").split()
    if (
        len(parts) not in (2, 3)
        or not parts[1].isdigit()
        or (len(parts) == 3 and not parts[2].isdigit())
    ):
        await message.answer(t(DEFAULT_LANG, "grant_usage"))
        return
    uid, days = int(parts[1]), int(parts[2]) if len(parts) == 3 else 0
    if uid <= 0:
        await message.answer(t(DEFAULT_LANG, "grant_usage"))
        return
    u = await db.get_user(uid)
    if not u:
        await message.answer(t(DEFAULT_LANG, "grant_nouser", uid=uid))
        return
    await db.grant_package(uid, days, config.PRO_WORD, config.PRO_SLIDE, config.PRO_QUIZ)
    await message.answer(t(DEFAULT_LANG, "granted", uid=uid))
    try:
        ulang = u.get("lang", DEFAULT_LANG)
        await bot.send_message(
            uid, t(ulang, "pro_activated", w=config.PRO_WORD, s=config.PRO_SLIDE, q=config.PRO_QUIZ)
        )
    except Exception as e:
        log.debug("Premium xabari yuborilmadi (%s): %s", uid, e)


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, bot: Bot):
    if message.from_user.id not in await db.admin_ids():
        return
    text = (message.text or "").removeprefix("/broadcast").strip()
    if not text:
        await message.answer(t(DEFAULT_LANG, "broadcast_usage"))
        return
    users = await db.all_users()
    ok = fail = 0
    for u in users:
        try:
            await bot.send_message(u["id"], text)
            ok += 1
        except Exception as e:
            fail += 1
            log.debug("Tarqatish (id=%s) xato: %s", u.get("id"), e)
        await asyncio.sleep(0.05)
    await message.answer(t(DEFAULT_LANG, "broadcast_done", ok=ok, fail=fail))


async def send_admin_reply(message, state, bot, uid, text):
    user = await db.get_user(uid)
    if not user:
        await message.reply("Foydalanuvchi topilmadi.")
        return
    try:
        await bot.send_message(uid, t(user.get("lang", DEFAULT_LANG), "admin_reply", text=text))
    except Exception:
        log.exception("Support reply failed uid=%s", uid)
        await message.reply(t(DEFAULT_LANG, "support_unavailable"))
        return
    await state.set_state(None)
    await state.update_data(reply_to_uid=None)
    await message.reply(t(DEFAULT_LANG, "reply_sent"))


@router.callback_query(F.data.startswith("adm_rep:"))
async def cb_admin_reply_click(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in await db.admin_ids():
        await call.answer()
        return
    uid = await db.support_recipient(call.message.chat.id, call.message.message_id)
    if not uid or call.data != f"adm_rep:{uid}":
        await call.answer(t(DEFAULT_LANG, "unknown"), show_alert=True)
        return
    await state.set_state(St.admin_reply)
    await state.update_data(reply_to_uid=uid)
    await call.message.reply(
        f"✍️ ID: <code>{uid}</code> — javobingizni yozing. Bekor qilish: /cancel"
    )
    await call.answer()


@router.message(Command("reply", "javob"))
async def cmd_reply(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id not in await db.admin_ids():
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3 or not parts[1].isdigit():
        await message.reply("Foydalanish: <code>/reply &lt;user_id&gt; &lt;javob&gt;</code>")
        return
    await send_admin_reply(message, state, bot, int(parts[1]), parts[2])


@router.message(St.admin_reply, F.text)
async def on_admin_reply_text(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id not in await db.admin_ids():
        return
    if message.text.strip() == "/cancel":
        await state.set_state(None)
        await state.update_data(reply_to_uid=None)
        await message.reply("Javob bekor qilindi.")
        return
    uid = (await state.get_data()).get("reply_to_uid")
    if uid:
        await send_admin_reply(message, state, bot, uid, message.text)


async def is_support_reply(message: Message):
    if not message.reply_to_message or message.from_user.id not in await db.admin_ids():
        return False
    uid = await db.support_recipient(message.chat.id, message.reply_to_message.message_id)
    return {"support_uid": uid} if uid else False


@router.message(F.text, is_support_reply)
async def on_reply_to_message(message: Message, state: FSMContext, bot: Bot, support_uid: int):
    await send_admin_reply(message, state, bot, support_uid, message.text)


@router.callback_query(F.data.startswith("l:"))
async def cb_lang(call: CallbackQuery, state: FSMContext):
    lang = call.data.split(":")[1]
    if lang not in LANGS:
        await call.answer()
        return
    existing = await db.get_user(call.from_user.id)
    if existing and existing.get("name"):
        await db.save_user(call.from_user.id, existing["name"], call.from_user.username or "", lang)
        await state.update_data(lang=lang, name=existing["name"])
        await state.set_state(None)
        await safe_edit(call.message, t(lang, "menu"), kb_menu(lang))
    else:
        await state.update_data(lang=lang)
        await state.set_state(St.name)
        await safe_edit(call.message, t(lang, "ask_name"))
    await call.answer()


@router.message(St.name, F.text)
async def on_name(message: Message, state: FSMContext, bot: Bot):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    name = message.text.strip()
    ok, reason = mod.check_content(name)
    if badwords.has_badword(name):
        await message.answer(t(lang, "warn_haqorat"))
        return
    if not (2 <= len(name) <= 50):
        await message.answer(t(lang, "bad_name"))
        return
    is_new = await db.save_user(message.from_user.id, name, message.from_user.username or "", lang)
    await state.update_data(name=name)
    await state.set_state(None)
    await message.answer(t(lang, "greet", name=name), reply_markup=kb_menu(lang))
    uname = f"@{message.from_user.username}" if message.from_user.username else "username yoʻq"
    tag = "🆕 Yangi foydalanuvchi" if is_new else "🔁 Qayta kirdi"
    await notify_admin(
        bot, f"{tag}\n👤 {esc(name)} ({uname})\n🆔 ID: {message.from_user.id}\n🌐 Til: {lang}"
    )


@router.callback_query(F.data == "lang")
async def cb_lang_change(call: CallbackQuery, state: FSMContext):
    await state.set_state(St.lang)
    await safe_edit(call.message, t(DEFAULT_LANG, "lang_choose"), kb_lang())
    await call.answer()


@router.callback_query(F.data == "back")
async def cb_back(call: CallbackQuery, state: FSMContext):
    await cancel_quiz(state)
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    await state.set_state(None)
    await safe_edit(call.message, t(lang, "menu"), kb_menu(lang))
    await call.answer()


@router.callback_query(F.data == "help")
async def cb_help(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    await safe_edit(call.message, t(lang, "help"), kb_menu(lang))
    await call.answer()


@router.callback_query(F.data.startswith("m:"))
async def cb_mode(call: CallbackQuery, state: FSMContext):
    mode = call.data.split(":")[1]
    await cancel_quiz(state)
    lang = (await ctx(state, call.from_user.id)).get("lang", DEFAULT_LANG)
    if mode == "quiz":
        await cancel_quiz(state)
        await state.update_data(mode="quiz")
        await state.set_state(St.quiz_file)
        await safe_edit(call.message, t(lang, "quiz_format"), kb_back(lang))
        await call.answer()
        return
    if mode == "tools":
        await safe_edit(call.message, t(lang, "tools_menu"), kb_tools(lang))
        await call.answer()
        return
    if mode == "premium":
        pro, words, slides, quizzes = await db.package_status(call.from_user.id)
        _, used = await db.daily_status(call.from_user.id)
        balance = t(
            lang,
            "quota_balance",
            w=words if pro else 0,
            s=slides if pro else 0,
            q=quizzes if pro else 0,
            free=max(0, config.DAILY_LIMIT - used),
        )
        await safe_edit(
            call.message,
            t(
                lang,
                "premium_msg",
                n=config.DAILY_LIMIT,
                w=config.PRO_WORD,
                s=config.PRO_SLIDE,
                q=config.PRO_QUIZ,
            )
            + "\n\n"
            + balance,
            kb_premium(lang),
        )
        await call.answer()
        return
    if mode in ("support", "aboutwrite"):
        await state.set_state(St.murojaat)
        await safe_edit(call.message, t(lang, "ask_murojaat"), kb_back(lang))
        await call.answer()
        return
    if mode not in MODES:
        await call.answer()
        return
    await state.update_data(mode=mode)
    await state.set_state(St.topic)
    await safe_edit(call.message, t(lang, MODES[mode]["ask"]), kb_back(lang))
    await call.answer()


@router.callback_query(F.data == "premium_buy")
async def cb_premium_buy(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    if not payments.is_ready():
        text = (
            t(lang, "pay_not_configured_kb")
            if call.from_user.id in await db.admin_ids()
            else t(lang, "pay_not_configured")
        )
        await safe_edit(call.message, text, kb_premium(lang))
        await call.answer()
        return
    info = payments.card_info()
    await state.set_state(St.pay_photo)
    await safe_edit(
        call.message,
        t(
            lang,
            "pay_manual",
            card=info["card"],
            holder=info["holder"],
            amount=info["amount"],
            days=config.PREMIUM_DAYS,
            w=config.PRO_WORD,
            s=config.PRO_SLIDE,
            q=config.PRO_QUIZ,
        ),
        kb_back(lang),
    )
    await call.answer()


def payment_keyboard(pid: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("✅ Tasdiqlash", f"pay:ok:v2:{pid}"), btn("❌ Rad etish", f"pay:no:v2:{pid}")]
        ]
    )


async def forward_payment(bot: Bot, admin_id: int, payment: dict):
    caption = (
        f"💰 Toʻlov #{payment['id']}\n🆔 ID: {payment['user_id']}\n"
        f"💵 {esc(payment['amount'])} soʻm\n📦 Muddatsiz Pro: "
        f"{payment['word']} Word, {payment['slide']} prezentatsiya, {payment['quiz']} quiz"
    )
    send = bot.send_photo if payment["proof_kind"] == "photo" else bot.send_document
    await send(
        admin_id, payment["proof_id"], caption=caption, reply_markup=payment_keyboard(payment["id"])
    )


@router.message(St.pay_photo, F.photo | F.document)
async def on_pay_proof(message: Message, state: FSMContext, bot: Bot):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    admins = await db.admin_ids()
    if not admins:
        await message.answer(t(lang, "support_unavailable"), reply_markup=kb_back(lang))
        return
    proof = message.photo[-1] if message.photo else message.document
    payment = await db.create_payment(
        message.from_user.id,
        proof.file_id,
        proof.file_unique_id,
        "photo" if message.photo else "document",
    )
    if payment["status"] != "pending":
        await message.answer(t(lang, "pay_already_processed"), reply_markup=kb_menu(lang))
        await state.set_state(None)
        return
    for aid in admins:
        try:
            await forward_payment(bot, aid, payment)
        except Exception:
            log.exception("Receipt delivery failed payment=%s admin=%s", payment["id"], aid)
    await state.set_state(None)
    await message.answer(t(lang, "pay_proof_sent"), reply_markup=kb_menu(lang))


@router.message(Command("payments"))
async def cmd_payments(message: Message, bot: Bot):
    if message.from_user.id not in await db.admin_ids():
        return
    pending = await db.pending_payments()
    if not pending:
        await message.answer(t(DEFAULT_LANG, "payments_empty"))
    for payment in pending:
        await forward_payment(bot, message.chat.id, payment)


@router.callback_query(F.data.startswith("pay:ok:"))
async def cb_pay_ok(call: CallbackQuery, bot: Bot):
    await _pay_decision(call, bot, ok=True)


@router.callback_query(F.data.startswith("pay:no:"))
async def cb_pay_no(call: CallbackQuery, bot: Bot):
    await _pay_decision(call, bot, ok=False)


async def _pay_decision(call: CallbackQuery, bot: Bot, ok: bool):
    if call.from_user.id not in await db.admin_ids():
        await call.answer()
        return
    parts = call.data.split(":")
    # Old user-ID-only buttons cannot identify a receipt safely.
    if len(parts) != 4 or parts[2] != "v2" or not parts[3].isdigit():
        await call.answer(t(DEFAULT_LANG, "pay_legacy"), show_alert=True)
        return
    payment = await db.decide_payment(int(parts[3]), call.from_user.id, ok)
    if payment is None:
        await call.answer(t(DEFAULT_LANG, "pay_already_processed"), show_alert=True)
        return
    try:
        await call.answer(
            t(DEFAULT_LANG, "pay_ok_admin" if ok else "pay_no_admin"), show_alert=True
        )
        await call.message.edit_caption(
            caption=esc(call.message.caption or "")
            + ("\n\n✅ TASDIQLANDI" if ok else "\n\n❌ RAD ETILDI"),
            reply_markup=None,
        )
    except Exception:
        # The committed decision must still reach the customer if the admin's
        # old message can no longer be edited or its callback has expired.
        log.warning("Payment decision UI update failed payment=%s", payment["id"])
    uid = payment["user_id"]
    u = await db.get_user(uid)
    lang = (u or {}).get("lang", DEFAULT_LANG)
    try:
        text = (
            t(lang, "pro_activated", w=payment["word"], s=payment["slide"], q=payment["quiz"])
            if ok
            else t(lang, "pay_rejected")
        )
        await bot.send_message(uid, text)
    except Exception:
        log.exception("Payment result notification failed payment=%s", payment["id"])
    await notify_admin(bot, f"{'✅' if ok else '❌'} Toʻlov #{payment['id']} — ID: {uid}")


@router.message(St.topic, F.text)
async def on_topic(message: Message, state: FSMContext):
    topic = message.text.strip()
    lang = (await ctx(state, message.from_user.id)).get("lang", DEFAULT_LANG)
    if badwords.has_badword(topic):
        await message.answer(t(lang, "warn_haqorat"))
        return
    if len(topic) < 4:
        await message.answer(t(lang, "bad_topic"))
        return
    data = await state.get_data()
    kind = MODES[data["mode"]]["kind"]
    await state.update_data(topic=topic)
    await state.set_state(St.count)
    await message.answer(t(lang, "ask_count_qu" if kind == "questions" else f"ask_{kind}"))


@router.message(St.count, F.text)
async def on_count(message: Message, state: FSMContext, bot: Bot):
    data = await ctx(state, message.from_user.id)
    lang = data.get("lang", DEFAULT_LANG)
    kind = MODES[data["mode"]]["kind"]
    lo, hi = COUNT_RANGE[kind]
    try:
        n = int(message.text.strip().replace(" ", ""))
    except Exception:
        await message.answer(t(lang, "bad_number"))
        return
    if not (lo <= n <= hi):
        await message.answer(t(lang, "bad_range", lo=lo, hi=hi))
        return
    await state.update_data(count=n)
    await generate(message, state, message.from_user.id, bot)


async def generate(target: Message, state: FSMContext, uid: int, bot: Bot):
    if await try_busy(uid, target, state):
        return
    try:
        await _generate(target, state, uid, bot)
    finally:
        _busy.discard(uid)


async def _generate(target: Message, state: FSMContext, uid: int, bot: Bot):
    data = await ctx(state, uid)
    lang, mode = data["lang"], data.get("mode")
    if mode not in MODES or not data.get("topic") or not isinstance(data.get("count"), int):
        await target.answer(t(lang, "unknown"), reply_markup=kb_menu(lang))
        return
    topic, n = data["topic"], data["count"]
    kind = "slide" if mode == "ppt" else ("quiz" if mode == "test" else "word")

    async def produce():
        path = new_file(uid, mode, "." + MODES[mode]["ext"])
        generator, builder = {
            "ref": (gen.gen_referat, docs.build_referat),
            "ppt": (gen.gen_pptx, docs.build_pptx),
            "xls": (gen.gen_xlsx, docs.build_xlsx),
            "test": (gen.gen_test, docs.build_test_docx),
        }[mode]
        content = await call_ai(target, state, generator, lang, topic, n)
        if mode == "ppt":
            content = await run_blocking(_images_lock, images_mod.add_ppt_images, content)
        await run_blocking(_document_lock, builder, content, lang, path)
        return path, content.get("image_failures", 0) if mode == "ppt" else 0

    await run_document_job(
        target,
        state,
        uid,
        kind,
        mode,
        topic,
        produce,
        MODES[mode]["done"],
        {"mode": mode, "topic": topic, "count": n, "lang": lang},
        after=True,
    )


@router.callback_query(F.data.startswith("again:"))
async def cb_again(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    try:
        job = await db.get_job(int(call.data.split(":")[1]), call.from_user.id)
    except (ValueError, IndexError):
        job = None
    if not job or job["payload"].get("mode") not in MODES:
        await call.message.answer(t(DEFAULT_LANG, "unknown"), reply_markup=kb_menu(DEFAULT_LANG))
        return
    await state.update_data(**job["payload"])
    await generate(call.message, state, call.from_user.id, bot)


@router.callback_query(F.data.startswith("topdf:"))
async def cb_topdf(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    try:
        record = await db.get_file(int(call.data.split(":")[1]), call.from_user.id)
    except (ValueError, IndexError):
        record = None
    if not record or not Path(record["path"]).is_file():
        await call.answer(t(lang, "file_gone"), show_alert=True)
        return
    await call.answer()
    wait = await call.message.answer(t(lang, "conv_busy"))
    try:
        source = await archive_path(call.from_user.id, record)
        out = await conv.docx_to_pdf(source)
        out, _ = await keep(call.from_user.id, "pdf", record["title"], out)
        await call.message.answer_document(
            FSInputFile(out), caption=t(lang, "conv_done"), reply_markup=kb_back(lang)
        )
    except Exception:
        log.exception("PDF conversion failed")
        await call.message.answer(t(lang, "conv_error"), reply_markup=kb_back(lang))
    finally:
        await delete_quietly(wait)


@router.callback_query(F.data.in_({"again", "topdf"}))
async def cb_legacy_result(call: CallbackQuery):
    await call.answer(t(DEFAULT_LANG, "unknown"), show_alert=True)


# ---------- Quiz ----------


async def render_quiz(msg, state: FSMContext, i: int):
    data = await state.get_data()
    lang = data.get("lang", DEFAULT_LANG)
    quiz = data["quiz"]
    q = quiz["qs"][i]
    remaining = max(0, int(quiz["deadline"] - time.time()))
    mm, ss = divmod(remaining, 60)
    text = t(lang, "quiz_q", i=i + 1, n=quiz["n"], mm=mm, ss=ss, q=q["q"])
    try:
        await msg.edit_text(text, reply_markup=kb_quiz(lang, q, i, quiz["id"]))
    except Exception as e:
        log.debug("Quiz savolini ko'rsatishda xatolik: %s", e)


async def finish_quiz(msg, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", DEFAULT_LANG)
    quiz = data.get("quiz")
    if not quiz:
        return
    if quiz.get("done"):
        return
    if quiz.get("job_id"):
        await db.complete_quiz(quiz["job_id"], quiz["uid"])
    quiz["done"] = True
    await state.update_data(quiz=quiz)
    if await state.get_state() == St.quiz_active.state:
        await state.set_state(None)
    cancel_quiz_timer(quiz.get("id"))
    c, n = quiz["score"], quiz["n"]
    pct = int(c / n * 100) if n else 0
    praise = (
        t(lang, "praise_hi")
        if pct >= 70
        else (t(lang, "praise_mid") if pct >= 50 else t(lang, "praise_lo"))
    )
    text = t(lang, "quiz_result", c=c, n=n, pct=pct, praise=praise)
    try:
        await msg.edit_text(text, reply_markup=kb_back(lang))
    except Exception as e:
        log.debug("Quiz natijasini ko'rsatishda xatolik: %s", e)
    uid = quiz.get("uid")
    if uid:
        pro = (await db.package_status(uid))[0]
        if pro or quiz.get("pro"):
            try:
                for chunk in split_text(quiz_analysis_text(quiz, lang)):
                    await msg.answer(chunk, reply_markup=kb_back(lang))
            except Exception as e:
                log.warning("Quiz tahlilini ko'rsatishda xatolik (%s): %s", uid, e)


def quiz_analysis_text(quiz: dict, lang: str) -> str:
    detail = list(quiz.get("detail") or [])
    for question in quiz.get("qs", [])[len(detail) :]:
        detail.append(
            {
                "q": question["q"],
                "given": None,
                "ans": question["answer"],
                "opts": question["options"],
                "ok": False,
            }
        )
    letters = "ABCDEFGH"
    lines = [t(lang, "quiz_analysis_title"), ""]
    for i, d in enumerate(detail, 1):
        given = d.get("given")
        given_txt = (
            f"{letters[given]}) {str(d['opts'][given])[:250]}"
            if isinstance(given, int) and 0 <= given < len(d["opts"])
            else "-"
        )
        ans = d.get("ans")
        ans_txt = (
            f"{letters[ans]}) {str(d['opts'][ans])[:250]}"
            if isinstance(ans, int) and 0 <= ans < len(d["opts"])
            else "-"
        )
        mark = "✅" if d.get("ok") else "❌"
        q = str(d.get("q") or "")[:70]
        lines.append(f"{mark} {i}. {esc(q)}")
        lines.append(t(lang, "quiz_your_answer", answer=given_txt))
        lines.append(t(lang, "quiz_right_answer", answer=ans_txt))
    wrong = [d for d in detail if not d.get("ok")]
    lines.append("")
    if wrong:
        lines.append(t(lang, "quiz_weak", n=len(wrong)))
    else:
        lines.append(t(lang, "quiz_perfect"))
    return "\n".join(lines)


_event_isolation = EventIsolation()
_quiz_tasks: dict[str, asyncio.Task] = {}


def split_text(text: str, limit: int = 3500):
    chunk = ""
    for line in text.splitlines():
        if len(chunk) + len(line) + 1 > limit and chunk:
            yield chunk.rstrip()
            chunk = ""
        chunk += line + "\n"
    if chunk:
        yield chunk.rstrip()


def cancel_quiz_timer(session_id):
    task = _quiz_tasks.pop(session_id, None)
    if task and task is not asyncio.current_task():
        task.cancel()


async def cancel_quiz(state):
    quiz = (await state.get_data()).get("quiz")
    if quiz and not quiz.get("done"):
        if quiz.get("job_id"):
            await db.refund_job(quiz["job_id"])
        quiz["done"] = True
        await state.update_data(quiz=quiz)
    if quiz:
        cancel_quiz_timer(quiz.get("id"))


async def quiz_timeout_task(seconds, msg, state: FSMContext, session_id: str):
    try:
        await asyncio.sleep(max(0, seconds))
        async with _event_isolation.lock(key=state.key):
            quiz = (await state.get_data()).get("quiz")
            if (
                not quiz
                or quiz.get("done")
                or quiz.get("id") != session_id
                or quiz["deadline"] > time.time()
            ):
                return
            await finish_quiz(msg, state)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Quiz timeout failed session=%s", session_id)
    finally:
        if _quiz_tasks.get(session_id) is asyncio.current_task():
            _quiz_tasks.pop(session_id, None)


def schedule_quiz(msg, state, quiz):
    cancel_quiz_timer(quiz["id"])
    _quiz_tasks[quiz["id"]] = asyncio.create_task(
        quiz_timeout_task(quiz["deadline"] - time.time(), msg, state, quiz["id"])
    )


@router.message(St.quiz_file, F.document)
async def on_quiz_file(message: Message, state: FSMContext, bot: Bot):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    doc = message.document
    if not (doc.file_name or "").lower().endswith(".docx"):
        await message.answer(t(lang, "quiz_bad"))
        return
    if doc_too_big(doc):
        await message.answer(t(lang, "file_too_big"))
        return
    wait = await message.answer(t(lang, "conv_busy"))
    src = new_file(message.from_user.id, "bank", ".docx")
    try:
        await bot.download(doc, destination=src)
        lines = await run_blocking(_document_lock, quiz_tool.extract_docx, src)
        qs = await run_blocking(_document_lock, quiz_tool.parse_questions, lines)
    except Exception as e:
        log.warning("Savol banki o'qilmadi (%s): %s", src, e)
        await delete_quietly(wait)
        await message.answer(t(lang, "quiz_bad"), reply_markup=kb_back(lang))
        return
    await delete_quietly(wait)
    if len(qs) < 2:
        await message.answer(t(lang, "quiz_bad"), reply_markup=kb_back(lang))
        return
    await state.update_data(quiz_bank=qs)
    await state.set_state(St.quiz_count)
    lo = min(5, len(qs))
    await message.answer(t(lang, "quiz_count", n=min(100, len(qs)), lo=lo))


@router.message(St.quiz_count, F.text)
async def on_quiz_count(message: Message, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    data = await state.get_data()
    bank = data["quiz_bank"]
    try:
        n = int(message.text.strip())
    except Exception:
        await message.answer(t(lang, "bad_number"))
        return
    lo = min(5, len(bank))
    if not (lo <= n <= min(100, len(bank))):
        await message.answer(t(lang, "bad_range", lo=lo, hi=min(100, len(bank))))
        return
    await state.update_data(quiz_n=n)
    await state.set_state(St.quiz_time)
    await message.answer(t(lang, "quiz_time"))


@router.message(St.quiz_time, F.text)
async def on_quiz_time(message: Message, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    data = await state.get_data()
    try:
        mins = int(message.text.strip())
    except Exception:
        await message.answer(t(lang, "bad_number"))
        return
    if not (1 <= mins <= 120):
        await message.answer(t(lang, "bad_range", lo=1, hi=120))
        return
    await cancel_quiz(state)
    job = await begin_job(message.from_user.id, "quiz", message, state, {"interactive": True})
    if not job:
        return
    try:
        quiz = {
            "id": uuid.uuid4().hex[:16],
            "uid": message.from_user.id,
            "job_id": job["id"],
            "pro": job["source"] == "quiz",
            "qs": data["quiz_bank"][: data["quiz_n"]],
            "i": 0,
            "score": 0,
            "n": data["quiz_n"],
            "deadline": time.time() + mins * 60,
            "done": False,
        }
        await state.update_data(quiz=quiz, quiz_bank=[])
        wait = await message.answer(t(lang, "working"))
        i = 0
        q = quiz["qs"][i]
        remaining = mins * 60
        mm, ss = divmod(remaining, 60)
        text = t(lang, "quiz_q", i=i + 1, n=quiz["n"], mm=mm, ss=ss, q=q["q"])
        await delete_quietly(wait)
        sent = await message.answer(text, reply_markup=kb_quiz(lang, q, i, quiz["id"]))
        quiz["msg_id"] = sent.message_id
        quiz["chat_id"] = sent.chat.id
        await state.update_data(quiz=quiz)
        await state.set_state(St.quiz_active)
        schedule_quiz(sent, state, quiz)
    except BaseException:
        await db.refund_job(job["id"])
        await cancel_quiz(state)
        await state.update_data(quiz_bank=data["quiz_bank"])
        raise


@router.callback_query(F.data.startswith("q:"))
async def on_quiz_answer(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang, quiz = data.get("lang", DEFAULT_LANG), data.get("quiz")
    parts = call.data.split(":")
    if (
        not quiz
        or quiz.get("done")
        or quiz.get("uid") != call.from_user.id
        or len(parts) < 3
        or parts[1] != quiz.get("id")
        or call.message.message_id != quiz.get("msg_id")
    ):
        await call.answer()
        return
    if time.time() >= quiz["deadline"]:
        await call.answer()
        await finish_quiz(call.message, state)
        return
    if len(parts) == 3 and parts[2] == "stop":
        await cancel_quiz(state)
        await state.set_state(None)
        await safe_edit(call.message, t(lang, "quiz_stop"), kb_back(lang))
        await call.answer()
        return
    try:
        if len(parts) != 4:
            raise ValueError
        qidx, oidx = int(parts[2]), int(parts[3])
        if qidx != quiz["i"] or not 0 <= qidx < len(quiz["qs"]):
            raise ValueError
        q = quiz["qs"][qidx]
        if not 0 <= oidx < len(q["options"]):
            raise ValueError
    except (ValueError, IndexError):
        await call.answer()
        return
    correct = oidx == q["answer"]
    if correct:
        quiz["score"] += 1
    answer_text = f"{'ABCDEFGH'[q['answer']]}) {q['options'][q['answer']]}"
    feedback = t(lang, "quiz_correct") if correct else t(lang, "quiz_wrong", ans=answer_text)
    quiz["i"] += 1
    quiz.setdefault("detail", []).append(
        {"q": q["q"], "given": oidx, "ans": q["answer"], "opts": q["options"], "ok": correct}
    )
    await state.update_data(quiz=quiz)
    await call.answer(feedback[:190])
    if quiz["i"] >= quiz["n"]:
        await finish_quiz(call.message, state)
    else:
        await render_quiz(call.message, state, quiz["i"])


# ---------- Tools ----------


@router.callback_query(F.data == "t:conv")
async def cb_tool_conv(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    await state.set_state(St.conv)
    await safe_edit(call.message, t(lang, "conv_pick"), kb_conv_pick(lang))
    await call.answer()


@router.callback_query(F.data == "c:pick")
async def cb_conv_pick(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    await state.set_state(St.conv)
    await safe_edit(call.message, t(lang, "conv_pick"), kb_conv_pick(lang))
    await call.answer()


@router.callback_query(F.data.startswith("c:"))
async def cb_conv_kind(call: CallbackQuery, state: FSMContext):
    kind = call.data.split(":")[1]
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    if kind not in ("docx", "pdf", "img"):
        await call.answer()
        return
    await state.set_state(St.conv)
    await state.update_data(conv_kind=kind)
    prompt_key = "send_docx" if kind == "docx" else ("send_pdf" if kind == "pdf" else "send_img")
    await safe_edit(call.message, t(lang, prompt_key), kb_back(lang))
    await call.answer()


@router.message(St.conv, F.document)
async def on_convert_file(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    lang = data.get("lang", DEFAULT_LANG)
    kind = data.get("conv_kind", "docx")
    doc = message.document
    fname = (doc.file_name or "").lower()
    if kind == "docx" and not (fname.endswith(".docx") or fname.endswith(".doc")):
        await message.answer(t(lang, "send_docx"))
        return
    if kind == "pdf" and not fname.endswith(".pdf"):
        await message.answer(t(lang, "send_pdf"))
        return
    if kind == "img" and not fname.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
        await message.answer(t(lang, "send_img"))
        return
    if doc_too_big(doc):
        await message.answer(t(lang, "file_too_big"))
        return
    wait = await message.answer(t(lang, "conv_busy"))
    src = new_file(message.from_user.id, "up", Path(fname).suffix)
    try:
        await bot.download(doc, destination=src)
        if kind == "docx":
            out = await conv.docx_to_pdf(src)
            out_kind, out_label = "pdf", "Word→PDF"
        elif kind == "pdf":
            out = await conv.pdf_to_docx(src)
            out_kind, out_label = "word", "PDF→Word"
        else:
            out = await conv.image_to_pdf(src)
            out_kind, out_label = "pdf", "Rasm→PDF"
        out, _ = await keep(message.from_user.id, out_kind, out_label + " " + Path(fname).stem, out)
        await delete_quietly(wait)
        await message.answer_document(
            FSInputFile(out),
            caption=t(lang, "conv_done"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn(t(lang, "back"), "back")]]),
        )
    except Exception:
        log.exception("Document conversion failed")
        await delete_quietly(wait)
        await message.answer(t(lang, "conv_error"), reply_markup=kb_back(lang))


@router.callback_query(F.data == "t:merge")
async def cb_tool_merge(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    await state.update_data(merge_files=[])
    await state.set_state(St.tmerge)
    await safe_edit(call.message, t(lang, "t_merge_ask"), kb_back(lang))
    await call.answer()


@router.message(St.tmerge, F.document)
async def on_merge_file(message: Message, state: FSMContext, bot: Bot):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    doc = message.document
    if not (doc.file_name or "").lower().endswith(".pdf"):
        await message.answer(t(lang, "t_merge_bad"))
        return
    if doc_too_big(doc):
        await message.answer(t(lang, "file_too_big"))
        return
    src = new_file(message.from_user.id, "m", ".pdf")
    await bot.download(doc, destination=src)
    data = await state.get_data()
    files = data.get("merge_files", []) + [str(src)]
    await state.update_data(merge_files=files)
    if len(files) >= 10:
        await do_merge(message, state)
    else:
        await message.answer(t(lang, "t_merge_more", n=len(files)))
        if len(files) == 1:
            await message.answer(t(lang, "t_merge_ask"))


@router.message(St.tmerge, F.text)
async def on_merge_done(message: Message, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    if message.text.strip().lower().removeprefix("✅").strip() in (
        "ok",
        "tayyor",
        "done",
        "готово",
        "",
    ):
        await do_merge(message, state)
    else:
        await message.answer(
            t(lang, "t_merge_more", n=len((await state.get_data()).get("merge_files", [])))
        )


async def do_merge(target: Message, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    data = await state.get_data()
    files = [Path(f) for f in data.get("merge_files", []) if Path(f).exists()]
    if len(files) < 2:
        await state.set_state(St.tmerge)
        await target.answer(t(lang, "t_merge_ask"))
        return
    wait = await target.answer(t(lang, "conv_busy"))
    out = new_file(target.from_user.id, "merge", ".pdf")
    try:
        await run_blocking(_document_lock, pdf_tool.merge_pdfs, files, out)
        out, _ = await keep(target.from_user.id, "merge", f"{len(files)} ta PDF", out)
        await delete_quietly(wait)
        await target.answer_document(
            FSInputFile(out),
            caption=t(lang, "t_merge_done", n=len(files)),
            reply_markup=kb_back(lang),
        )
        await state.update_data(merge_files=[])
        await state.set_state(None)
    except Exception:
        log.exception("PDF merge failed")
        await delete_quietly(wait)
        await target.answer(t(lang, "conv_error"), reply_markup=kb_back(lang))


@router.callback_query(F.data == "t:split")
async def cb_tool_split(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    await state.set_state(St.tsplit_file)
    await safe_edit(call.message, t(lang, "t_split_askfile"), kb_back(lang))
    await call.answer()


@router.message(St.tsplit_file, F.document)
async def on_split_file(message: Message, state: FSMContext, bot: Bot):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    doc = message.document
    if not (doc.file_name or "").lower().endswith(".pdf"):
        await message.answer(t(lang, "t_merge_bad"))
        return
    if doc_too_big(doc):
        await message.answer(t(lang, "file_too_big"))
        return
    src = new_file(message.from_user.id, "s", ".pdf")
    try:
        await bot.download(doc, destination=src)
        total = await run_blocking(_document_lock, pdf_tool.page_count, src)
    except Exception as e:
        log.warning("Split PDF o'qilmadi (%s): %s", src, e)
        await message.answer(t(lang, "t_merge_bad"), reply_markup=kb_back(lang))
        return
    await state.update_data(split_file=str(src), split_total=total)
    await state.set_state(St.tsplit_range)
    await message.answer(t(lang, "t_split_askrng", n=total))


@router.message(St.tsplit_range, F.text)
async def on_split_range(message: Message, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    data = await state.get_data()
    src = Path(data["split_file"])
    total = data["split_total"]
    ranges = pdf_tool.parse_ranges(message.text, total)
    if not ranges:
        await message.answer(t(lang, "t_split_bad"))
        return
    wait = await message.answer(t(lang, "conv_busy"))
    try:
        outs = await run_blocking(_document_lock, pdf_tool.split_pdf, src, ranges)
        outs = [(await keep(message.from_user.id, "split", Path(o).stem, Path(o)))[0] for o in outs]
        await delete_quietly(wait)
        for i, o in enumerate(outs):
            cap = t(lang, "t_split_done", n=len(outs))
            await message.answer_document(
                FSInputFile(o),
                caption=(cap if i == len(outs) - 1 else None),
                reply_markup=(kb_back(lang) if i == len(outs) - 1 else None),
            )
        await state.set_state(None)
    except Exception:
        log.exception("Document conversion failed")
        await delete_quietly(wait)
        await message.answer(t(lang, "conv_error"), reply_markup=kb_back(lang))


@router.callback_query(F.data == "t:docx2ppt")
async def cb_tool_docx2ppt(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    await state.set_state(St.tdocx2ppt)
    await safe_edit(call.message, t(lang, "t_docx2ppt_askfile"), kb_back(lang))
    await call.answer()


@router.message(St.tdocx2ppt, F.document)
async def on_docx2ppt_file(message: Message, state: FSMContext, bot: Bot):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    doc = message.document
    if not (doc.file_name or "").lower().endswith(".docx"):
        await message.answer(t(lang, "t_docx2ppt_askfile"))
        return
    if doc_too_big(doc):
        await message.answer(t(lang, "file_too_big"))
        return
    src = new_file(message.from_user.id, "pp", ".docx")
    try:
        await bot.download(doc, destination=src)
        lines = await run_blocking(_document_lock, quiz_tool.extract_docx, src)
    except Exception as e:
        log.warning("Word->PPT manba o'qilmadi (%s): %s", src, e)
        await message.answer(t(lang, "quiz_bad"), reply_markup=kb_back(lang))
        return
    if not lines:
        await message.answer(t(lang, "quiz_bad"), reply_markup=kb_back(lang))
        return
    await state.update_data(ppt_src=str(src), ppt_text="\n".join(lines))
    await state.set_state(St.tslides)
    await message.answer(t(lang, "t_docx2ppt_askcount"))


@router.message(St.tslides, F.text)
async def on_slides_count(message: Message, state: FSMContext, bot: Bot):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    data = await state.get_data()
    try:
        n = int(message.text.strip())
    except Exception:
        await message.answer(t(lang, "bad_number"))
        return
    if not (3 <= n <= 20):
        await message.answer(t(lang, "bad_range", lo=3, hi=20))
        return
    if await try_busy(message.from_user.id, message, state):
        return
    try:
        await _run_docx2ppt(message, state, bot, lang, n, data)
    finally:
        _busy.discard(message.from_user.id)


async def _run_docx2ppt(message: Message, state: FSMContext, bot: Bot, lang, n, data):
    async def produce():
        content = await call_ai(message, state, gen.gen_ppt_from_text, lang, data["ppt_text"], n)
        content = await run_blocking(_images_lock, images_mod.add_ppt_images, content)
        path = new_file(message.from_user.id, "conv", ".pptx")
        await run_blocking(_document_lock, docs.build_pptx, content, lang, path)
        return path, content.get("image_failures", 0)

    await run_document_job(
        message,
        state,
        message.from_user.id,
        "slide",
        "docx2ppt",
        Path(data["ppt_src"]).stem,
        produce,
        "done_pptx",
    )


@router.callback_query(F.data == "t:rewrite")
async def cb_tool_rewrite(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    await state.set_state(St.trewrite)
    await safe_edit(call.message, t(lang, "t_rewrite_ask"), kb_back(lang))
    await call.answer()


async def do_rewrite(target: Message, state: FSMContext, paragraphs: list[str]):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    text = "\n\n".join(p for p in paragraphs if p.strip())
    if not text.strip():
        await target.answer(t(lang, "bad_topic"))
        return
    ok, reason = mod.check_content(text)
    if not ok:
        msg_key = "warn_haqorat" if reason == "haqorat" else "no_personal_info"
        await target.answer(t(lang, msg_key), reply_markup=kb_back(lang))
        return
    if await try_busy(target.from_user.id, target, state):
        return
    try:
        await _run_rewrite(target, state, lang, text, paragraphs)
    finally:
        _busy.discard(target.from_user.id)


async def _run_rewrite(target: Message, state: FSMContext, lang, text, paragraphs):
    async def produce():
        out_p = await call_ai(target, state, gen.gen_rewrite, paragraphs)
        path = new_file(target.from_user.id, "rew", ".docx")
        await run_blocking(_document_lock, docs.build_text_docx, out_p, "", path)
        return path, 0

    await run_document_job(
        target, state, target.from_user.id, "word", "rewrite", text[:80], produce, "t_rewrite_done"
    )


@router.message(St.trewrite, F.document)
async def on_rewrite_file(message: Message, state: FSMContext, bot: Bot):
    doc = message.document
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    if not (doc.file_name or "").lower().endswith(".docx"):
        await message.answer(t(lang, "t_rewrite_ask"))
        return
    if doc_too_big(doc):
        await message.answer(t(lang, "file_too_big"))
        return
    src = new_file(message.from_user.id, "rw", ".docx")
    try:
        await bot.download(doc, destination=src)
        lines = await run_blocking(_document_lock, quiz_tool.extract_docx, src)
    except Exception as e:
        log.warning("Qayta yozish manbasi o'qilmadi (%s): %s", src, e)
        await message.answer(t(lang, "t_rewrite_ask"), reply_markup=kb_back(lang))
        return
    await do_rewrite(message, state, lines)


@router.message(St.trewrite, F.text)
async def on_rewrite_text(message: Message, state: FSMContext):
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", message.text) if p.strip()]
    await do_rewrite(message, state, paragraphs)


@router.callback_query(F.data == "t:translate")
async def cb_tool_translate(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    await state.set_state(St.ttranslate)
    await safe_edit(call.message, t(lang, "t_translate_askfile"), kb_back(lang))
    await call.answer()


@router.message(St.ttranslate, F.document)
async def on_translate_file(message: Message, state: FSMContext, bot: Bot):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    doc = message.document
    if not (doc.file_name or "").lower().endswith(".docx"):
        await message.answer(t(lang, "t_translate_askfile"))
        return
    if doc_too_big(doc):
        await message.answer(t(lang, "file_too_big"))
        return
    src = new_file(message.from_user.id, "tr", ".docx")
    try:
        await bot.download(doc, destination=src)
        lines = await run_blocking(_document_lock, quiz_tool.extract_docx, src)
    except Exception as e:
        log.warning("Tarjima manbasi o'qilmadi (%s): %s", src, e)
        await message.answer(t(lang, "t_translate_askfile"), reply_markup=kb_back(lang))
        return
    if not lines:
        await message.answer(t(lang, "t_translate_askfile"))
        return
    await state.update_data(trans_src=str(src), trans_lines=lines)
    await message.answer(t(lang, "t_translate_target"), reply_markup=kb_target(lang))


@router.callback_query(F.data.startswith("x:"))
async def on_translate_target(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    target = call.data.split(":")[1]
    data = await state.get_data()
    lines = data.get("trans_lines")
    if not lines:
        await call.answer(t(lang, "unknown"))
        return
    await call.answer()
    if await try_busy(call.from_user.id, call.message, state):
        return
    try:
        await _run_translate(call, state, lang, target, lines, data)
    finally:
        _busy.discard(call.from_user.id)


async def _run_translate(call: CallbackQuery, state: FSMContext, lang, target, lines, data):
    async def produce():
        out_p = await call_ai(
            call.message, state, gen.gen_translate, LANG_TARGET.get(target, target), lines
        )
        path = new_file(call.from_user.id, "tr", ".docx")
        await run_blocking(_document_lock, docs.build_text_docx, out_p, "", path)
        return path, 0

    await run_document_job(
        call.message,
        state,
        call.from_user.id,
        "word",
        "translate",
        Path(data.get("trans_src") or "translation").stem,
        produce,
        "t_translate_done",
    )


# ---------- Mening ishlarim ----------


async def keep(uid: int, kind: str, title: str, path: Path, job_id=None) -> tuple[Path, int]:
    if path.stat().st_size > config.MAX_UPLOAD:
        raise ValueError("Natija fayli juda katta")
    original = path
    path = await run_blocking(_document_lock, file_store.persist, uid, path)
    if job_id is not None:
        fid = await db.complete_job(job_id, uid, kind, title, str(path), path.stat().st_size)
    else:
        fid = await db.add_file(uid, kind, title, str(path), path.stat().st_size)
    if path != original:
        await asyncio.to_thread(conv.cleanup_result, original)
    return path, fid


async def archive_path(uid: int, record: dict) -> Path:
    path = Path(record["path"])
    if file_store.owned_path(uid, path):
        return path
    # Migrate unambiguous legacy files lazily. A shared path may already have
    # overwritten another user's data; never expose that ambiguous old archive.
    roots = (
        config.FILES_DIR.resolve(),
        (config.BASE_DIR / "files").resolve(),
        Path("/tmp/talaba_bot_convert").resolve(),
    )
    if (
        path.is_symlink()
        or not any(path.resolve().is_relative_to(root) for root in roots)
        or await db.file_owner_count(str(path)) != 1
    ):
        raise FileNotFoundError("Legacy archive ownership is ambiguous")
    relocated = await run_blocking(_document_lock, file_store.persist, uid, path)
    await db.relocate_file(record["id"], uid, str(relocated))
    return relocated


async def render_myfiles(target, state: FSMContext, lang: str, uid: int, page: int):
    files = await db.list_files(uid, 8, page * 8)
    total = await db.count_files(uid)
    if not files:
        await safe_edit(target, t(lang, "myfiles_empty"), kb_back(lang))
        return
    lines = [t(lang, "myfiles_title"), ""]
    buttons = []
    start = page * 8
    for i, f in enumerate(files, 1):
        lines.append(fmt_file_row(f, start + i, lang))
        buttons.append([btn(f"⬇️ {start + i}", f"dl:{f['id']}")])
    nav = []
    max_p = max(0, (total - 1) // 8)
    if page > 0:
        nav.append(btn("⬅️", f"mf:{page - 1}"))
    nav.append(btn(t(lang, "back"), "back"))
    if page < max_p:
        nav.append(btn("➡️", f"mf:{page + 1}"))
    buttons.append(nav)
    await safe_edit(target, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data == "myfiles")
async def cb_myfiles(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    await state.set_state(None)
    await render_myfiles(call.message, state, lang, call.from_user.id, 0)
    await call.answer()


@router.callback_query(F.data.startswith("mf:"))
async def cb_myfiles_page(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    try:
        page = max(0, int(call.data.split(":")[1]))
    except Exception:
        page = 0
    await render_myfiles(call.message, state, lang, call.from_user.id, page)
    await call.answer()


@router.callback_query(F.data.startswith("dl:"))
async def cb_dl(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    try:
        fid = int(call.data.split(":")[1])
    except Exception:
        await call.answer()
        return
    f = await db.get_file(fid, call.from_user.id)
    if not f:
        await call.answer(t(lang, "unknown"), show_alert=True)
        return
    try:
        p = await archive_path(call.from_user.id, f)
    except (FileNotFoundError, ValueError):
        await call.answer(t(lang, "file_gone"), show_alert=True)
        return
    if not p.exists():
        await call.answer(t(lang, "file_gone"), show_alert=True)
        return
    await call.answer()
    try:
        await call.message.answer_document(
            FSInputFile(p),
            caption=f"{FILE_EMOJI.get(f['kind'], '📁')} {esc(f.get('title') or f['kind'])}",
            reply_markup=kb_back(lang),
        )
    except Exception:
        await call.answer(t(lang, "file_gone"), show_alert=True)


# ---------- Murojaat ----------

_album_messages: dict[tuple[int, str], list[Message]] = {}
_album_tasks: dict[tuple[int, str], asyncio.Task] = {}


@router.message(St.murojaat, F.text)
async def on_murojaat(message: Message, state: FSMContext, bot: Bot):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    text = message.text.strip()
    if badwords.has_badword(text):
        await message.answer(t(lang, "warn_haqorat"))
        return
    if len(text) < 3:
        await message.answer(t(lang, "bad_topic"))
        return
        await message.answer(t(lang, "bad_topic"))
        return
    uname = await user_name(message.from_user.id)
    u = f"@{message.from_user.username}" if message.from_user.username else "username yoʻq"
    delivered = 0
    for aid in await db.admin_ids():
        try:
            sent = await bot.send_message(
                aid,
                f"💬 <b>YANGI MUROJAAT / SUPPORT</b>\n👤 {esc(uname)} ({esc(u)})"
                f"\n🆔 ID: <code>{message.from_user.id}</code>\n\n{esc(text)}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[btn("✍️ Javob yozish", f"adm_rep:{message.from_user.id}")]]
                ),
            )
            await db.remember_support_reply(aid, sent.message_id, message.from_user.id)
            delivered += 1
        except Exception:
            log.exception("Support delivery failed admin=%s", aid)
    if delivered:
        await state.set_state(None)
    await message.answer(
        t(lang, "murojaat_sent" if delivered else "support_unavailable"), reply_markup=kb_menu(lang)
    )


# ---------- Generic ----------


@router.message(F.document)
async def any_document(message: Message, state: FSMContext, bot: Bot):
    st = await state.get_state()
    text_states = {
        St.name.state,
        St.topic.state,
        St.count.state,
        St.quiz_count.state,
        St.quiz_time.state,
        St.tsplit_range.state,
        St.tslides.state,
        St.murojaat.state,
    }
    if st in text_states:
        lang = (await state.get_data()).get("lang", DEFAULT_LANG)
        await message.answer(t(lang, "expect_text"), reply_markup=kb_back(lang))
        return
    doc = message.document
    fname = (doc.file_name or "").lower()
    if fname.endswith((".docx", ".doc")):
        await state.set_state(St.conv)
        await state.update_data(conv_kind="docx")
        await on_convert_file(message, state, bot)
        return
    if fname.endswith(".pdf"):
        await state.set_state(St.conv)
        await state.update_data(conv_kind="pdf")
        await on_convert_file(message, state, bot)
        return
    if fname.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
        await state.set_state(St.conv)
        await state.update_data(conv_kind="img")
        await on_convert_file(message, state, bot)
        return
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    await message.answer(t(lang, "unknown"), reply_markup=kb_menu(lang))


async def process_album(key, state, bot):
    wait = None
    message = None
    try:
        await asyncio.sleep(1.2)
        messages = sorted(_album_messages.pop(key, []), key=lambda m: m.message_id)
        if not messages:
            return
        message = messages[0]
        lang = (await state.get_data()).get("lang", DEFAULT_LANG)
        wait = await message.answer(t(lang, "conv_busy"))
        paths = []
        for item in messages:
            path = new_file(item.from_user.id, "album", ".jpg")
            await bot.download(item.photo[-1], destination=path)
            paths.append(path)
        out = await conv.images_to_pdf(paths)
        out, _ = await keep(message.from_user.id, "pdf", f"Album ({len(paths)})", out)
        await message.answer_document(
            FSInputFile(out),
            caption=t(lang, "album_done", n=len(paths)),
            reply_markup=kb_back(lang),
        )
    except Exception:
        log.exception("Album conversion failed")
        if message:
            await message.answer(t(DEFAULT_LANG, "conv_error"), reply_markup=kb_back(DEFAULT_LANG))
    finally:
        _album_tasks.pop(key, None)
        if _album_messages.get(key) and not asyncio.current_task().cancelling():
            _album_tasks[key] = asyncio.create_task(process_album(key, state, bot))
        else:
            _album_messages.pop(key, None)
        if wait:
            await delete_quietly(wait)


@router.message(F.photo)
async def any_photo(message: Message, state: FSMContext, bot: Bot):
    st = await state.get_state()
    if st == St.pay_photo.state:
        return
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)

    if st not in (None, St.conv.state):
        await message.answer(t(lang, "expect_text"), reply_markup=kb_back(lang))
        return
    if message.media_group_id:
        key = (message.chat.id, message.media_group_id)
        messages = _album_messages.setdefault(key, [])
        if len(messages) < 10:
            messages.append(message)
        if key not in _album_tasks:
            _album_tasks[key] = asyncio.create_task(process_album(key, state, bot))
        return

    wait = await message.answer(t(lang, "conv_busy"))
    photo = message.photo[-1]
    src = new_file(message.from_user.id, "img", ".jpg")
    try:
        await bot.download(photo, destination=src)
        out = await conv.image_to_pdf(src)
        out, _ = await keep(message.from_user.id, "pdf", "Rasm→PDF", out)
        await delete_quietly(wait)
        await message.answer_document(
            FSInputFile(out),
            caption=t(lang, "conv_img_done"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn(t(lang, "back"), "back")]]),
        )
    except Exception:
        log.exception("Document conversion failed")
        await delete_quietly(wait)
        await message.answer(t(lang, "conv_error"), reply_markup=kb_back(lang))


@router.message(F.text)
async def any_text(message: Message, state: FSMContext, bot: Bot):
    if message.reply_to_message is not None and message.from_user.id in await db.admin_ids():
        uid = await db.support_recipient(message.chat.id, message.reply_to_message.message_id)
        if uid:
            u = await db.get_user(uid)
            ulang = (u or {}).get("lang", DEFAULT_LANG)
            await bot.send_message(uid, t(ulang, "admin_reply", text=message.text))
            await message.reply(t(DEFAULT_LANG, "reply_sent"))
            return
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    ok, reason = mod.check_content(message.text)
    if not ok:
        msg_key = "warn_haqorat" if reason == "haqorat" else "no_personal_info"
        await message.answer(t(lang, msg_key), reply_markup=kb_menu(lang))
        return
    await message.answer(t(lang, "unknown"), reply_markup=kb_menu(lang))


async def restore_quizzes(bot, storage):
    rows = await db.POOL.fetch(
        "SELECT * FROM fsm_record WHERE bot_id=$1 AND data->'quiz'->>'done'='false'", bot.id
    )
    import json

    for row in rows:
        data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
        quiz = data["quiz"]
        state = FSMContext(
            storage=storage,
            key=StorageKey(
                bot_id=bot.id,
                chat_id=row["chat_id"],
                user_id=row["user_id"],
                thread_id=row["thread_id"] or None,
            ),
        )
        if not all(key in quiz for key in ("id", "uid", "msg_id", "deadline")):
            await cancel_quiz(state)
            continue
        message = Message(
            message_id=quiz["msg_id"],
            date=datetime.datetime.now(datetime.timezone.utc),
            chat=Chat(id=row["chat_id"], type="private"),
            from_user=User(id=bot.id, is_bot=True, first_name="TalabaBot"),
        ).as_(bot)
        schedule_quiz(message, state, quiz)


def create_dispatcher(storage):
    # Public registration APIs put rate/busy checks before the per-user FSM lock.
    from services.middleware import ActivityMiddleware, RateLimitMiddleware, TaskTrackingMiddleware

    dp = Dispatcher(storage=storage, events_isolation=_event_isolation, disable_fsm=True)
    dp.update.outer_middleware(TaskTrackingMiddleware())
    dp.update.outer_middleware(RateLimitMiddleware(busy=lambda uid: uid in _busy))
    dp.update.outer_middleware(dp.fsm)
    router.message.outer_middleware(ActivityMiddleware())
    router.callback_query.outer_middleware(ActivityMiddleware())
    dp.include_router(router)
    return dp


async def wait_for_telegram(bot):
    while True:
        try:
            await bot.get_me()
            return
        except (TelegramNetworkError, TelegramRetryAfter, asyncio.TimeoutError) as exc:
            log.warning("Internet / Telegram ulanishi kutilmoqda: %s", type(exc).__name__)
            await asyncio.sleep(getattr(exc, "retry_after", 5))


async def main():
    if not config.BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN topilmadi — .env faylini toʻldiring")
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await db.init()
    storage = PostgresStorage()
    dp = create_dispatcher(storage)
    background = []
    lock_connection = await db.POOL.acquire()
    try:
        locked = await lock_connection.fetchval(
            "SELECT pg_try_advisory_lock(hashtextextended($1,0))", f"talababot:{bot.id}"
        )
        if not locked:
            raise RuntimeError("Bu botning boshqa nusxasi allaqachon ishlayapti")
        await wait_for_telegram(bot)
        recovered = await db.recover_interrupted_jobs()
        for uid in recovered:
            try:
                user = await db.get_user(uid)
                await bot.send_message(
                    uid, t((user or {}).get("lang", DEFAULT_LANG), "job_interrupted")
                )
            except Exception:
                log.warning("Interrupted-job notice failed uid=%s", uid)
        await restore_quizzes(bot, storage)
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="🏠 Bosh sahifa"),
                BotCommand(command="help", description="❓ Yordam"),
                BotCommand(command="lang", description="🌐 Tilni tanlash"),
            ]
        )
        background = [
            asyncio.create_task(premium_notifier(bot)),
            asyncio.create_task(maintenance()),
        ]
        log.info("TalabaBot started")
        await dp.start_polling(bot, close_bot_session=False, tasks_concurrency_limit=64)
    finally:
        from services.middleware import active_handlers

        tasks = (
            set(active_handlers)
            | set(background)
            | set(_quiz_tasks.values())
            | set(_album_tasks.values())
            | _running_jobs
        )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await lock_connection.execute("SELECT pg_advisory_unlock_all()")
        await db.POOL.release(lock_connection)
        await storage.close()
        await db.POOL.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
