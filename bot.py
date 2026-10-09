import asyncio
import datetime
import re
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BotCommand, CallbackQuery, ErrorEvent, FSInputFile,
                           InlineKeyboardButton, InlineKeyboardMarkup, Message)

import config
import db
import services.convert as conv
import services.documents as docs
import services.generator as gen
import services.images as images_mod
import services.pdf as pdf_tool
import services.payments as payments
import services.quiz as quiz_tool
from config import log
from services.fsm_pg import PostgresStorage
from i18n import DEFAULT_LANG, LANGS, t

router = Router()


@router.error()
async def global_error_handler(event: ErrorEvent):
    ex = event.exception
    if isinstance(ex, TelegramForbiddenError):
        log.info("Foydalanuvchi botni bloklagan (xabar yuborilmadi): %s", ex)
        return True
    if isinstance(ex, TelegramBadRequest):
        msg = str(ex).lower()
        if "query is too old" in msg or "message is not modified" in msg or "message to edit not found" in msg:
            log.info("TelegramBadRequest e'tiborga olinmadi: %s", ex)
            return True
    log.exception("Update qayta ishlashda xatolik: %s", ex)
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
    return bool(getattr(doc, "file_size", 0) > config.MAX_DOWNLOAD)


_gemini_lock = asyncio.Semaphore(config.GEMINI_MAX_CONCURRENT)
_gemini_queue = 0
_notified_soon: set[tuple[int, int]] = set()


async def premium_notifier(bot: Bot) -> None:
    await asyncio.sleep(30)
    while True:
        now = time.time()
        try:
            day = int(now // 86400)
            for u in await db.expiring_premiums(86400):
                key = (u["id"], day)
                if key in _notified_soon:
                    continue
                _notified_soon.add(key)
                hours = max(1, int((u["premium_until"] - now) // 3600))
                await bot.send_message(
                    u["id"], t(u.get("lang") or DEFAULT_LANG, "premium_expire_soon", hours=hours))
            for u in await db.expired_premiums():
                await bot.send_message(
                    u["id"], t(u.get("lang") or DEFAULT_LANG, "premium_expired"))
        except Exception as e:
            log.warning("Premium bildirishnoma xatosi: %s", e)
        await asyncio.sleep(6 * 3600)


async def call_ai(target: Message, state: FSMContext, fn, *args):
    """Gemini ishini global navbat bilan ishga tushiradi (maks. N ta bir vaqtda)."""
    global _gemini_queue
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    notified = None
    if _gemini_lock.locked():
        _gemini_queue += 1
        notified = await target.answer(t(lang, "queue", pos=_gemini_queue))
    await _gemini_lock.acquire()
    if _gemini_queue > 0:
        _gemini_queue -= 1
    try:
        if notified:
            await delete_quietly(notified)
        return await asyncio.to_thread(fn, *args)
    finally:
        _gemini_lock.release()


_pay_kb = lambda lang: InlineKeyboardMarkup(inline_keyboard=[
    [btn(t(lang, "btn_premium"), "m:premium")],
    [btn(t(lang, "back"), "back")],
])


async def quota_ok(uid: int, kind: str, target: Message, state: FSMContext,
                   bot: Bot | None = None) -> bool:
    """kind: 'word' | 'slide'. Pro paket kvotasi yoki bepul davolimni tekshiradi."""
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    pro, word_left, slide_left = await db.package_status(uid)
    if pro:
        left = word_left if kind == "word" else slide_left
        if left > 0:
            return True
        if bot:
            await notify_admin(bot, f"⛔ Pro paket limiti tugadi\n👤 {await user_name(uid)} ({uid})\n{kind}: 0")
        what = t(lang, "unit_word" if kind == "word" else "unit_slide")
        await target.answer(t(lang, "pro_limit_msg", what=what), reply_markup=_pay_kb(lang))
        return False
    _, used = await db.daily_status(uid)
    if used < config.DAILY_LIMIT:
        return True
    if bot:
        await notify_admin(
            bot, f"⛔ Kunlik limit tugadi\n👤 {await user_name(uid)} ({uid})\n📄 Bugun: {used}/{config.DAILY_LIMIT}")
    await target.answer(t(lang, "limit_msg", n=config.DAILY_LIMIT), reply_markup=_pay_kb(lang))
    return False


async def consume_quota(uid: int, kind: str) -> None:
    if (await db.package_status(uid))[0]:
        await db.consume_package(uid, kind)
    else:
        await db.track_used(uid)

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
    tmerge = State()
    tsplit_file = State()
    tsplit_range = State()
    tdocx2ppt = State()
    tslides = State()
    trewrite = State()
    ttranslate = State()
    murojaat = State()
    pay_photo = State()


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
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn(f"{v['flag']} {v['name']}", f"l:{k}")] for k, v in LANGS.items()])


def kb_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn(t(lang, "btn_referat"), "m:ref"), btn(t(lang, "btn_pptx"), "m:ppt")],
        [btn(t(lang, "btn_xlsx"), "m:xls"), btn(t(lang, "btn_test"), "m:test")],
        [btn(t(lang, "btn_quiz"), "m:quiz")],
        [btn(t(lang, "btn_doc2pdf"), "c:docx"), btn(t(lang, "btn_pdf2doc"), "c:pdf")],
        [btn(t(lang, "btn_img2pdf"), "c:img")],
        [btn(t(lang, "btn_myfiles"), "myfiles"), btn(t(lang, "btn_premium"), "m:premium")],
        [btn(t(lang, "btn_support"), "m:support")],
    ])


def kb_after(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn(t(lang, "to_pdf"), "topdf"), btn(t(lang, "again"), "again")],
        [btn(t(lang, "back"), "back")],
    ])


def kb_back(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn(t(lang, "back"), "back")]])


def kb_premium(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("💳 " + t(lang, "premium_buy_btn"), "premium_buy")],
        [btn(t(lang, "back"), "back")],
    ])


def kb_conv_pick(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn(t(lang, "conv_docx"), "c:docx"), btn(t(lang, "conv_pdf"), "c:pdf")],
        [btn(t(lang, "conv_img"), "c:img")],
        [btn(t(lang, "back"), "back")],
    ])


def kb_tools(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn(t(lang, "t_conv"), "t:conv"),
         btn(t(lang, "t_merge"), "t:merge"),
         btn(t(lang, "t_split"), "t:split")],
        [btn(t(lang, "t_docx2ppt"), "t:docx2ppt"),
         btn(t(lang, "t_rewrite"), "t:rewrite"),
         btn(t(lang, "t_translate"), "t:translate")],
        [btn(t(lang, "back"), "back")],
    ])


def kb_target(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("🇺🇿 Oʻzbekcha", "x:uz"), btn("🇷🇺 Русский", "x:ru"), btn("🇬🇧 English", "x:en")],
        [btn(t(lang, "back"), "back")],
    ])


def kb_quiz(lang: str, q: dict, qidx: int) -> InlineKeyboardMarkup:
    rows = []
    letters = "ABCDEFGH"
    for oi, opt in enumerate(q.get("options", [])):
        rows.append([btn(f"{letters[oi]}) {str(opt)[:40]}", f"q:{qidx}:{oi}")])
    rows.append([btn("⛔ " + t(lang, "quiz_stop").replace("⛔ ", ""), "q:stop")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_about(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn(t(lang, "btn_aboutwrite"), "m:aboutwrite")],
        [btn(t(lang, "back"), "back")],
    ])


def user_dir(uid: int) -> Path:
    d = config.FILES_DIR / str(uid)
    d.mkdir(parents=True, exist_ok=True)
    for old in d.iterdir():
        if time.time() - old.stat().st_mtime > 30 * 86400:
            old.unlink(missing_ok=True)
    return d


FILE_EMOJI = {
    "ref": "📄", "ppt": "🎤", "xls": "📊", "test": "📋",
    "pdf": "📄", "word": "📝", "merge": "🗂️", "split": "✂️", "docx2ppt": "🎤",
    "rewrite": "✍️", "translate": "🌐",
}
FILE_LABEL = {
    "ref": "Referat", "ppt": "Prezentatsiya", "xls": "Jadval", "test": "Test",
    "pdf": "PDF", "word": "Word", "merge": "PDF birlashma", "split": "PDF boʻlish",
    "docx2ppt": "PPT (Word→PPT)", "rewrite": "Qayta yozildi", "translate": "Tarjima",
}


def fmt_file_row(f: dict, idx: int, lang: str) -> str:
    kind = str(f.get("kind", ""))
    emoji = FILE_EMOJI.get(kind, "📁")
    label = FILE_LABEL.get(kind, kind)
    title = str(f.get("title") or "")[:30]
    size = f.get("size") or 0
    s = f"{size // 1024} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"
    date = datetime.datetime.fromtimestamp(f.get("created_at") or 0).strftime("%d.%m")
    return f"{idx}. {emoji} {label} — {title}\n    🗓 {date} · {s}"


async def safe_edit(msg, text: str, kb=None):
    try:
        await msg.edit_text(text, reply_markup=kb)
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
    await state.clear()
    uid = message.from_user.id
    existing = await db.get_user(uid)
    if existing and existing.get("name"):
        lang = existing.get("lang") or DEFAULT_LANG
        await state.update_data(lang=lang, name=existing["name"])
        await message.answer(t(lang, "greet", name=existing["name"]), reply_markup=kb_menu(lang))
        uname = f"@{message.from_user.username}" if message.from_user.username else "username yoʻq"
        await notify_admin(bot, f"🔁 Qayta kirdi\n👤 {existing['name']} ({uname})\n🆔 ID: {uid}")
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


@router.message(Command("admin"))
async def cmd_admin(message: Message, bot: Bot):
    if message.from_user.id in await db.admin_ids():
        await message.answer(t(DEFAULT_LANG, "admin_info"))
        return
    text = message.text or ""
    secret = config.ADMIN_SECRET
    if secret and text.strip() == secret:
        await db.add_admin(message.from_user.id)
        await message.answer(t(DEFAULT_LANG, "admin_ok"))
        uname = await user_name(message.from_user.id)
        await notify_admin(bot, f"🆕 Yangi admin kod bilan qoʻshildi: {uname} ({message.from_user.id})")
        return
    if secret and text.startswith("/admin "):
        code = text.split(" ", 1)[1].strip()
        if code == secret:
            await db.add_admin(message.from_user.id)
            await message.answer(t(DEFAULT_LANG, "admin_ok"))
            uname = await user_name(message.from_user.id)
            await notify_admin(bot, f"🆕 Yangi admin kod bilan qoʻshildi: {uname} ({message.from_user.id})")
            return
    await message.answer(t(DEFAULT_LANG, "admin_denied"))
    uname = await user_name(message.from_user.id)
    await notify_admin(bot, f"⛔ /admin ishlatishga urindi (rad etildi): {uname} ({message.from_user.id})")


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
    if len(parts) != 3 or not parts[1].strip("-").isdigit() or not parts[2].isdigit():
        await message.answer(t(DEFAULT_LANG, "grant_usage"))
        return
    uid, days = int(parts[1]), int(parts[2])
    if uid <= 0 or days <= 0:
        await message.answer(t(DEFAULT_LANG, "grant_usage"))
        return
    u = await db.get_user(uid)
    if not u:
        await message.answer(t(DEFAULT_LANG, "grant_nouser", uid=uid))
        return
    await db.grant_package(uid, days, config.PRO_WORD, config.PRO_SLIDE)
    await message.answer(t(DEFAULT_LANG, "granted", uid=uid, days=days))
    try:
        ulang = u.get("lang", DEFAULT_LANG)
        await bot.send_message(uid, t(ulang, "premium_granted", days=days))
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


@router.callback_query(F.data.startswith("l:"))
async def cb_lang(call: CallbackQuery, state: FSMContext):
    lang = call.data.split(":")[1]
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
    if not (2 <= len(name) <= 50):
        await message.answer(t(lang, "bad_name"))
        return
    is_new = await db.save_user(message.from_user.id, name, message.from_user.username or "", lang)
    await state.update_data(name=name)
    await state.set_state(None)
    await message.answer(t(lang, "greet", name=name), reply_markup=kb_menu(lang))
    uname = f"@{message.from_user.username}" if message.from_user.username else "username yoʻq"
    tag = "🆕 Yangi foydalanuvchi" if is_new else "🔁 Qayta kirdi"
    await notify_admin(bot, f"{tag}\n👤 {name} ({uname})\n🆔 ID: {message.from_user.id}\n🌐 Til: {lang}")


@router.callback_query(F.data == "lang")
async def cb_lang_change(call: CallbackQuery, state: FSMContext):
    await state.set_state(St.lang)
    await safe_edit(call.message, t(DEFAULT_LANG, "lang_choose"), kb_lang())
    await call.answer()


@router.callback_query(F.data == "back")
async def cb_back(call: CallbackQuery, state: FSMContext):
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
    lang = (await ctx(state, call.from_user.id)).get("lang", DEFAULT_LANG)
    if mode == "quiz":
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
        await safe_edit(call.message, t(lang, "premium_msg",
                                        n=config.DAILY_LIMIT, w=config.PRO_WORD, s=config.PRO_SLIDE),
                        kb_premium(lang))
        await call.answer()
        return
    if mode in ("support", "aboutwrite"):
        await state.set_state(St.murojaat)
        await safe_edit(call.message, t(lang, "ask_murojaat"), kb_back(lang))
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
        text = t(lang, "pay_not_configured_kb") if call.from_user.id in await db.admin_ids() \
            else t(lang, "pay_not_configured")
        await safe_edit(call.message, text, kb_premium(lang))
        await call.answer()
        return
    info = payments.card_info()
    await state.set_state(St.pay_photo)
    await safe_edit(call.message, t(
        lang, "pay_manual", card=info["card"], holder=info["holder"],
        amount=info["amount"], days=config.PREMIUM_DAYS,
        w=config.PRO_WORD, s=config.PRO_SLIDE), kb_back(lang))
    await call.answer()


@router.message(St.pay_photo, F.photo | F.document)
async def on_pay_proof(message: Message, state: FSMContext, bot: Bot):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    await state.set_state(None)
    await message.answer(t(lang, "pay_proof_sent"), reply_markup=kb_menu(lang))
    admins = await db.admin_ids()
    if not admins:
        return
    info = payments.card_info()
    uname = await user_name(message.from_user.id)
    caption = (
        "💰 Toʻlov cheki\n"
        f"👤 {esc(uname)} (ID: {message.from_user.id})\n"
        f"💳 {info['card']}\n"
        f"💵 {info['amount']} soʻm\n"
        f"📦 {config.PREMIUM_DAYS} kun Pro")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("✅ Tasdiqlash", f"pay:ok:{message.from_user.id}")],
        [btn("❌ Rad etish", f"pay:no:{message.from_user.id}")],
    ])
    if message.photo:
        media = message.photo[-1].file_id
        for a in admins:
            try:
                await bot.send_photo(a, media, caption=caption, reply_markup=kb)
            except Exception as e:
                log.warning("Toʻlov cheki adminga (photo) yuborilmadi %s: %s", a, e)
    elif message.document:
        media = message.document.file_id
        for a in admins:
            try:
                await bot.send_document(a, media, caption=caption, reply_markup=kb)
            except Exception as e:
                log.warning("Toʻlov cheki adminga (document) yuborilmadi %s: %s", a, e)


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
    uid = int(call.data.split(":")[2])
    u = await db.get_user(uid)
    ulang = (u or {}).get("lang", DEFAULT_LANG)
    if ok:
        await db.grant_package(uid, config.PREMIUM_DAYS, config.PRO_WORD, config.PRO_SLIDE)
        await bot.send_message(uid, t(ulang, "pro_activated", days=config.PREMIUM_DAYS,
                                      w=config.PRO_WORD, s=config.PRO_SLIDE))
        await call.answer(t(DEFAULT_LANG, "pay_ok_admin"), show_alert=True)
        await notify_admin(bot, f"✅ Pro tasdiqlandi: {await user_name(uid)} ({uid})")
    else:
        await bot.send_message(uid, t(ulang, "pay_rejected"))
        await call.answer(t(DEFAULT_LANG, "pay_no_admin"), show_alert=True)
    try:
        cap = (call.message.caption or "") + ("\n\n✅ TASDIQLANDI" if ok else "\n\n❌ RAD ETILDI")
        await call.message.edit_caption(caption=cap, reply_markup=None)
    except Exception:
        pass


@router.message(St.topic, F.text)
async def on_topic(message: Message, state: FSMContext):
    topic = message.text.strip()
    lang = (await ctx(state, message.from_user.id)).get("lang", DEFAULT_LANG)
    if len(topic) < 4:
        await message.answer(t(lang, "bad_topic"))
        return
    data = await state.get_data()
    kind = MODES[data["mode"]]["kind"]
    await state.update_data(topic=topic)
    await state.set_state(St.count)
    await message.answer(t(lang, f"ask_{kind}"))


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
    lang, mode, topic, n = data["lang"], data["mode"], data["topic"], data["count"]
    kind = "slide" if mode == "ppt" else "word"
    if mode == "xls":
        kind = "word"
    if not await quota_ok(uid, kind, target, state, bot):
        return
    uname = await user_name(uid)
    wait = await target.answer(t(lang, "working"))
    await notify_admin(
        bot,
        f"📄 Soʻrov\n👤 {uname} (ID: {uid})\n"
        f"📌 {MODE_NAMES[mode]} — {topic}\n🔢 Hajm: {n}",
    )
    try:
        path = user_dir(uid) / f"{mode}_{int(time.time())}.{MODES[mode]['ext']}"
        if mode == "ref":
            content = await call_ai(target, state, gen.gen_referat, lang, topic, n)
            await asyncio.to_thread(docs.build_referat, content, lang, path)
        elif mode == "ppt":
            content = await call_ai(target, state, gen.gen_pptx, lang, topic, n)
            content = await asyncio.to_thread(images_mod.add_ppt_images, content)
            await asyncio.to_thread(docs.build_pptx, content, lang, path)
        elif mode == "xls":
            content = await call_ai(target, state, gen.gen_xlsx, lang, topic, n)
            await asyncio.to_thread(docs.build_xlsx, content, lang, path)
        else:
            content = await call_ai(target, state, gen.gen_test, lang, topic, n)
            await asyncio.to_thread(docs.build_test_docx, content, lang, path)

        await state.update_data(last_file=str(path))
        await db.bump_docs(uid)
        await consume_quota(uid, kind)
        await keep(uid, mode, topic, path)
        await delete_quietly(wait)
        caption = t(lang, MODES[mode]["done"])
        if mode == "ppt" and content.get("image_failures", 0):
            caption += "\n" + t(lang, "ppt_images_missing", count=content["image_failures"])
        await send_file(target, path, caption, kb_after(lang))
        await notify_admin(
            bot,
            f"✅ Tayyor — {MODE_NAMES[mode]}\n👤 {uname} (ID: {uid})\n"
            f"📌 {topic}\n📁 {path.name} ({path.stat().st_size // 1024} KB)",
        )
    except Exception as e:
        await delete_quietly(wait)
        await target.answer(t(lang, "error", err=str(e)[:250]), reply_markup=kb_back(lang))
        await notify_admin(bot, f"❌ Xatolik — {MODE_NAMES[mode]}\n👤 {uname} (ID: {uid})\n{str(e)[:200]}")


@router.callback_query(F.data == "again")
async def cb_again(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await generate(call.message, state, call.from_user.id, bot)


@router.callback_query(F.data == "topdf")
async def cb_topdf(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", DEFAULT_LANG)
    last = data.get("last_file")
    if not last or not Path(last).exists():
        await call.answer(t(lang, "unknown"), show_alert=True)
        return
    await call.answer()
    wait = await call.message.answer(t(lang, "conv_busy"))
    try:
        out = await conv.docx_to_pdf(Path(last))
        await keep(call.from_user.id, "pdf", Path(last).stem, out)
        await delete_quietly(wait)
        await call.message.answer_document(
            FSInputFile(out), caption=t(lang, "conv_done"), reply_markup=kb_back(lang))
    except Exception as e:
        await delete_quietly(wait)
        await call.message.answer(t(lang, "conv_error") + f"\n{str(e)[:150]}",
                                  reply_markup=kb_back(lang))


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
        await msg.edit_text(text, reply_markup=kb_quiz(lang, q, i))
    except Exception as e:
        log.debug("Quiz savolini ko'rsatishda xatolik: %s", e)


async def finish_quiz(msg, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", DEFAULT_LANG)
    quiz = data.get("quiz")
    if not quiz:
        return
    quiz["done"] = True
    await state.update_data(quiz=quiz)
    c, n = quiz["score"], quiz["n"]
    pct = int(c / n * 100) if n else 0
    praise = t(lang, "praise_hi") if pct >= 70 else (t(lang, "praise_mid") if pct >= 50 else t(lang, "praise_lo"))
    text = t(lang, "quiz_result", c=c, n=n, pct=pct, praise=praise)
    try:
        await msg.edit_text(text, reply_markup=kb_back(lang))
    except Exception as e:
        log.debug("Quiz natijasini ko'rsatishda xatolik: %s", e)
    uid = getattr(msg.from_user, "id", None)
    if uid:
        pro, _, _ = await db.package_status(uid)
        if pro:
            try:
                await msg.answer(quiz_analysis_text(quiz, lang), reply_markup=kb_back(lang))
            except Exception as e:
                log.warning("Quiz tahlilini ko'rsatishda xatolik (%s): %s", uid, e)


def quiz_analysis_text(quiz: dict, lang: str) -> str:
    detail = quiz.get("detail") or []
    letters = "ABCDEFGH"
    lines = [t(lang, "quiz_analysis_title"), ""]
    for i, d in enumerate(detail, 1):
        given = d.get("given")
        given_txt = f"{letters[given]}) {d['opts'][given]}" \
            if isinstance(given, int) and 0 <= given < len(d["opts"]) else "-"
        ans = d.get("ans")
        ans_txt = f"{letters[ans]}) {d['opts'][ans]}" \
            if isinstance(ans, int) and 0 <= ans < len(d["opts"]) else "-"
        mark = "✅" if d.get("ok") else "❌"
        q = str(d.get("q") or "")[:70]
        lines.append(f"{mark} {i}. {esc(q)}")
        lines.append(f"   Siz: {esc(given_txt)}")
        lines.append(f"   To'g'ri: {esc(ans_txt)}")
    wrong = [d for d in detail if not d.get("ok")]
    lines.append("")
    if wrong:
        lines.append(t(lang, "quiz_weak", n=len(wrong)))
    else:
        lines.append(t(lang, "quiz_perfect"))
    return "\n".join(lines)


async def quiz_timeout_task(seconds, msg, state: FSMContext):
    await asyncio.sleep(seconds)
    data = await state.get_data()
    quiz = data.get("quiz")
    if not quiz or quiz.get("done"):
        return
    lang = data.get("lang", DEFAULT_LANG)
    try:
        await msg.edit_text(t(lang, "quiz_timeout") + "\n", reply_markup=None)
    except Exception as e:
        log.debug("Quiz vaqt tugashi xabari: %s", e)
    await finish_quiz(msg, state)


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
    src = user_dir(message.from_user.id) / f"bank_{int(time.time())}.docx"
    try:
        await bot.download(doc, destination=src)
        lines = await asyncio.to_thread(quiz_tool.extract_docx, src)
        qs = await asyncio.to_thread(quiz_tool.parse_questions, lines)
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
    await message.answer(t(lang, "quiz_count", n=len(qs), lo=lo))


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
    if not (lo <= n <= len(bank)):
        await message.answer(t(lang, "bad_range", lo=lo, hi=len(bank)))
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
    quiz = {
        "qs": data["quiz_bank"][:data["quiz_n"]],
        "i": 0,
        "score": 0,
        "n": data["quiz_n"],
        "deadline": time.time() + mins * 60,
        "done": False,
    }
    await state.update_data(quiz=quiz)
    wait = await message.answer(t(lang, "working"))
    i = 0
    q = quiz["qs"][i]
    remaining = mins * 60
    mm, ss = divmod(remaining, 60)
    text = t(lang, "quiz_q", i=i + 1, n=quiz["n"], mm=mm, ss=ss, q=q["q"])
    await delete_quietly(wait)
    sent = await message.answer(text, reply_markup=kb_quiz(lang, q, i))
    quiz["msg_id"] = sent.message_id
    quiz["chat_id"] = sent.chat.id
    await state.update_data(quiz=quiz)
    asyncio.create_task(quiz_timeout_task(mins * 60, sent, state))


@router.callback_query(F.data.startswith("q:"))
async def on_quiz_answer(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    data = await state.get_data()
    quiz = data.get("quiz")
    if not quiz or quiz.get("done"):
        await call.answer()
        return
    if call.data == "q:stop":
        quiz["done"] = True
        await state.update_data(quiz=quiz)
        await safe_edit(call.message, t(lang, "quiz_stop"), kb_back(lang))
        await call.answer()
        return
    qidx, oidx = map(int, call.data.split(":")[1:])
    if qidx != quiz["i"]:
        await call.answer()
        return
    q = quiz["qs"][qidx]
    letters = "ABCDEFGH"
    if oidx == q.get("answer"):
        quiz["score"] += 1
        feedback = t(lang, "quiz_correct")
    else:
        ans_letter = q["answer"]
        ans_text = q["options"][ans_letter] if 0 <= ans_letter < len(q["options"]) else "?"
        feedback = t(lang, "quiz_wrong", ans=f"{letters[ans_letter]}) {ans_text}")
    quiz["i"] += 1
    quiz.setdefault("detail", []).append({
        "q": q["q"],
        "given": oidx,
        "ans": q.get("answer"),
        "opts": q["options"],
        "ok": oidx == q.get("answer"),
    })
    await state.update_data(quiz=quiz)
    if quiz["i"] >= quiz["n"] or time.time() > quiz["deadline"]:
        await finish_quiz(call.message, state)
        await call.answer()
        return
    await call.answer(feedback, show_alert=False)
    try:
        await call.message.edit_text(feedback)
    except Exception as e:
        log.debug("Quiz javob xabarini yangilashda xatolik: %s", e)
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
    src = user_dir(message.from_user.id) / f"up_{int(time.time())}{Path(fname).suffix}"
    await bot.download(doc, destination=src)
    try:
        if kind == "docx":
            out = await conv.docx_to_pdf(src)
            out_kind, out_label = "pdf", "Word→PDF"
        elif kind == "pdf":
            out = await conv.pdf_to_docx(src)
            out_kind, out_label = "word", "PDF→Word"
        else:
            out = await conv.image_to_pdf(src)
            out_kind, out_label = "pdf", "Rasm→PDF"
        await keep(message.from_user.id, out_kind, out_label + " " + Path(fname).stem, out)
        await delete_quietly(wait)
        await message.answer_document(
            FSInputFile(out), caption=t(lang, "conv_done"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [btn(t(lang, "back"), "back")]]))
    except Exception as e:
        await delete_quietly(wait)
        await message.answer(t(lang, "conv_error") + f"\n{str(e)[:150]}",
                             reply_markup=kb_back(lang))


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
    src = user_dir(message.from_user.id) / f"m_{int(time.time())}.pdf"
    await bot.download(doc, destination=src)
    data = await state.get_data()
    files = data.get("merge_files", []) + [src]
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
    if message.text.strip().lower() in ("ok", "tayyor", "done", "✅", "готово"):
        await do_merge(message, state)
    else:
        await message.answer(t(lang, "t_merge_more",
                                n=len((await state.get_data()).get("merge_files", []))))


async def do_merge(target: Message, state: FSMContext):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    data = await state.get_data()
    files = [Path(f) for f in data.get("merge_files", []) if Path(f).exists()]
    if len(files) < 2:
        await state.set_state(St.tmerge)
        await target.answer(t(lang, "t_merge_ask"))
        return
    wait = await target.answer(t(lang, "conv_busy"))
    out = config.FILES_DIR / f"merge_{int(time.time())}.pdf"
    try:
        await asyncio.to_thread(pdf_tool.merge_pdfs, files, out)
        await keep(target.from_user.id, "merge", f"{len(files)} ta PDF", out)
        await delete_quietly(wait)
        await target.answer_document(FSInputFile(out),
                                     caption=t(lang, "t_merge_done", n=len(files)),
                                     reply_markup=kb_back(lang))
        await state.update_data(merge_files=[])
        await state.set_state(None)
    except Exception as e:
        await delete_quietly(wait)
        await target.answer(t(lang, "conv_error") + f"\n{str(e)[:150]}",
                            reply_markup=kb_back(lang))


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
    src = user_dir(message.from_user.id) / f"s_{int(time.time())}.pdf"
    try:
        await bot.download(doc, destination=src)
        total = len(pdf_tool.PdfReader(str(src)).pages)
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
    ranges = await asyncio.to_thread(pdf_tool.parse_ranges, message.text, total)
    if not ranges:
        await message.answer(t(lang, "t_split_bad"))
        return
    wait = await message.answer(t(lang, "conv_busy"))
    try:
        outs = await asyncio.to_thread(pdf_tool.split_pdf, src, ranges)
        for o in outs:
            await keep(message.from_user.id, "split", Path(o).stem, Path(o))
        await delete_quietly(wait)
        for i, o in enumerate(outs):
            cap = t(lang, "t_split_done", n=len(outs))
            await message.answer_document(
                FSInputFile(o),
                caption=(cap if i == len(outs) - 1 else None),
                reply_markup=(kb_back(lang) if i == len(outs) - 1 else None))
        await state.set_state(None)
    except Exception as e:
        await delete_quietly(wait)
        await message.answer(t(lang, "conv_error") + f"\n{str(e)[:150]}",
                             reply_markup=kb_back(lang))


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
    src = user_dir(message.from_user.id) / f"pp_{int(time.time())}.docx"
    try:
        await bot.download(doc, destination=src)
        lines = await asyncio.to_thread(quiz_tool.extract_docx, src)
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
    if not await quota_ok(message.from_user.id, "slide", message, state, bot):
        return
    wait = await message.answer(t(lang, "working"))
    try:
        content = await call_ai(message, state, gen.gen_ppt_from_text, lang, data["ppt_text"], n)
        content = await asyncio.to_thread(images_mod.add_ppt_images, content)
        path = user_dir(message.from_user.id) / f"conv_{int(time.time())}.pptx"
        await asyncio.to_thread(docs.build_pptx, content, lang, path)
        await keep(message.from_user.id, "docx2ppt", Path(data["ppt_src"]).stem, path)
        await consume_quota(message.from_user.id, "slide")
        await delete_quietly(wait)
        caption = t(lang, "done_pptx")
        if content.get("image_failures", 0):
            caption += "\n" + t(lang, "ppt_images_missing", count=content["image_failures"])
        await send_file(message, path, caption, kb_back(lang))
        await state.set_state(None)
    except Exception as e:
        await delete_quietly(wait)
        await message.answer(t(lang, "error", err=str(e)[:200]), reply_markup=kb_back(lang))


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
    if await try_busy(target.from_user.id, target, state):
        return
    try:
        await _run_rewrite(target, state, lang, text, paragraphs)
    finally:
        _busy.discard(target.from_user.id)


async def _run_rewrite(target: Message, state: FSMContext, lang, text, paragraphs):
    if not await quota_ok(target.from_user.id, "word", target, state):
        return
    wait = await target.answer(t(lang, "working"))
    try:
        out_p = await call_ai(target, state, gen.gen_rewrite, paragraphs)
        path = user_dir(target.from_user.id) / f"rew_{int(time.time())}.docx"
        await asyncio.to_thread(docs.build_text_docx, out_p, "", path)
        await keep(target.from_user.id, "rewrite", text[:30], path)
        await consume_quota(target.from_user.id, "word")
        await delete_quietly(wait)
        await send_file(target, path, t(lang, "t_rewrite_done"), kb_back(lang))
        await state.set_state(None)
    except Exception as e:
        await delete_quietly(wait)
        await target.answer(t(lang, "error", err=str(e)[:200]), reply_markup=kb_back(lang))


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
    src = user_dir(message.from_user.id) / f"rw_{int(time.time())}.docx"
    try:
        await bot.download(doc, destination=src)
        lines = await asyncio.to_thread(quiz_tool.extract_docx, src)
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
    src = user_dir(message.from_user.id) / f"tr_{int(time.time())}.docx"
    try:
        await bot.download(doc, destination=src)
        lines = await asyncio.to_thread(quiz_tool.extract_docx, src)
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
    if not await quota_ok(call.from_user.id, "word", call.message, state):
        return
    wait = await call.message.answer(t(lang, "working"))
    try:
        out_p = await call_ai(call.message, state, gen.gen_translate, LANG_TARGET.get(target, target), lines)
        path = config.FILES_DIR / f"tr_{int(time.time())}.docx"
        await asyncio.to_thread(docs.build_text_docx, out_p, "", path)
        await keep(call.from_user.id, "translate", Path(data.get("trans_src") or "").stem, path)
        await consume_quota(call.from_user.id, "word")
        await delete_quietly(wait)
        await send_file(call.message, path, t(lang, "t_translate_done"), kb_back(lang))
        await state.set_state(None)
        await state.update_data(trans_lines=[])
    except Exception as e:
        await delete_quietly(wait)
        await call.message.answer(t(lang, "error", err=str(e)[:200]), reply_markup=kb_back(lang))


# ---------- Mening ishlarim ----------

async def keep(uid: int, kind: str, title: str, path: Path) -> None:
    try:
        await db.add_file(uid, kind, title, str(path), int(path.stat().st_size))
    except Exception as e:
        log.exception("Fayl jadvalga yozilmadi (uid=%s, kind=%s, path=%s): %s", uid, kind, path, e)


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
    p = Path(f["path"])
    if not p.exists():
        await call.answer(t(lang, "file_gone"), show_alert=True)
        return
    await call.answer()
    try:
        await call.message.answer_document(
            FSInputFile(p), caption=f"{FILE_EMOJI.get(f['kind'], '📁')} {f.get('title') or f['kind']}",
            reply_markup=kb_back(lang))
    except Exception:
        await call.answer(t(lang, "file_gone"), show_alert=True)


# ---------- Murojaat ----------

_album_lock = asyncio.Lock()
_album_messages: dict[str, list[Message]] = {}
_album_processing: set[str] = set()


@router.message(St.murojaat, F.text)
async def on_murojaat(message: Message, state: FSMContext, bot: Bot):
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    text = message.text.strip()
    if len(text) < 3:
        await message.answer(t(lang, "bad_topic"))
        return
    uname = await user_name(message.from_user.id)
    u = f"@{message.from_user.username}" if message.from_user.username else "username yoʻq"
    await notify_admin(
        bot,
        f"💬 <b>YANGI MUROJAAT / SUPPORT</b>\n👤 <b>{uname}</b> ({u})\n🆔 ID: <code>{message.from_user.id}</code>\n🌐 Til: {lang}"
        f"\n\n❓ <b>Savol:</b>\n{text}\n\n<i>— Javob berish uchun ushbu xabarga Reply qiling —</i>")
    await state.set_state(None)
    await message.answer(t(lang, "murojaat_sent"), reply_markup=kb_menu(lang))


# ---------- Generic ----------

@router.message(F.document)
async def any_document(message: Message, state: FSMContext, bot: Bot):
    st = await state.get_state()
    text_states = {St.name.state, St.topic.state, St.count.state,
                   St.quiz_count.state, St.quiz_time.state, St.tsplit_range.state,
                   St.tslides.state, St.murojaat.state}
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


@router.message(F.photo)
async def any_photo(message: Message, state: FSMContext, bot: Bot):
    st = await state.get_state()
    if st == St.pay_photo.state:
        return
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)

    if message.media_group_id:
        gid = message.media_group_id
        async with _album_lock:
            if gid not in _album_messages:
                _album_messages[gid] = []
            _album_messages[gid].append(message)
            if gid in _album_processing:
                return
            _album_processing.add(gid)

        await asyncio.sleep(1.2)

        async with _album_lock:
            msgs = _album_messages.pop(gid, [])
            _album_processing.discard(gid)

        if not msgs:
            return

        wait = await message.answer(t(lang, "conv_busy"))
        msgs.sort(key=lambda m: m.message_id)
        uid = message.from_user.id
        ts = int(time.time())
        img_paths = []
        for idx, m in enumerate(msgs):
            p = user_dir(uid) / f"album_{ts}_{idx}.jpg"
            photo = m.photo[-1]
            await bot.download(photo, destination=p)
            img_paths.append(p)

        try:
            out_pdf = user_dir(uid) / f"album_{ts}.pdf"
            conv.images_to_pdf_sync(img_paths, out_pdf)
            await keep(uid, "pdf", f"Rasmlar ({len(img_paths)} ta)→PDF", out_pdf)
            await delete_quietly(wait)
            caption = f"✅ {len(img_paths)} ta rasm bitta PDF hujjatga birlashtirildi!"
            await message.answer_document(
                FSInputFile(out_pdf), caption=caption,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [btn(t(lang, "back"), "back")]]))
        except Exception as e:
            await delete_quietly(wait)
            await message.answer(t(lang, "conv_error") + f"\n{str(e)[:150]}",
                                 reply_markup=kb_back(lang))
        return

    wait = await message.answer(t(lang, "conv_busy"))
    photo = message.photo[-1]
    src = user_dir(message.from_user.id) / f"img_{int(time.time())}.jpg"
    await bot.download(photo, destination=src)
    try:
        out = await conv.image_to_pdf(src)
        await keep(message.from_user.id, "pdf", "Rasm→PDF", out)
        await delete_quietly(wait)
        await message.answer_document(
            FSInputFile(out), caption=t(lang, "conv_img_done"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [btn(t(lang, "back"), "back")]]))
    except Exception as e:
        await delete_quietly(wait)
        await message.answer(t(lang, "conv_error") + f"\n{str(e)[:150]}",
                             reply_markup=kb_back(lang))


@router.message(F.text)
async def any_text(message: Message, state: FSMContext, bot: Bot):
    if message.reply_to_message is not None and message.from_user.id in await db.admin_ids():
        rtext = message.reply_to_message.text or message.reply_to_message.caption or ""
        m = re.search(r"ID[: ]+(\d+)", rtext)
        if m:
            uid = int(m.group(1))
            u = await db.get_user(uid)
            ulang = (u or {}).get("lang", DEFAULT_LANG)
            await bot.send_message(uid, t(ulang, "admin_reply", text=message.text))
            await message.reply(t(DEFAULT_LANG, "reply_sent"))
            return
    secret = config.ADMIN_SECRET
    if secret and message.from_user.id not in await db.admin_ids() and \
            message.text and message.text.strip() == secret:
        await db.add_admin(message.from_user.id)
        await message.answer(t(DEFAULT_LANG, "admin_ok"))
        await notify_admin(bot, f"🆕 Yangi admin kod bilan qoʻshildi: {await user_name(message.from_user.id)} ({message.from_user.id})")
        return
    lang = (await state.get_data()).get("lang", DEFAULT_LANG)
    await message.answer(t(lang, "unknown"), reply_markup=kb_menu(lang))


async def main():
    if not config.BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN topilmadi — .env faylini toʻldiring")
    await db.init()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=PostgresStorage())
    dp.include_router(router)
    await bot.set_my_commands([
        BotCommand(command="start", description="🏠 Bosh sahifa"),
        BotCommand(command="help", description="❓ Yordam"),
        BotCommand(command="lang", description="🌐 Tilni tanlash"),
        BotCommand(command="stats", description="📊 Statistika"),
        BotCommand(command="admin", description="⚙️ Admin boʻlish"),
    ])
    print("TalabaBot ishga tushdi...")
    asyncio.get_running_loop().create_task(premium_notifier(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
