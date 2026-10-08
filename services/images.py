import concurrent.futures as cf
import json
import os
import time
import urllib.parse
import urllib.request

from config import log

_TIMEOUT = 15
_WORKERS = 4
_BUDGET = 90
_POLLINATION_GAP = 16
_TOKEN_GAP = 5
_MAX_BYTES = 8_000_000
_UA = "TalabaBot/1.0 (+https://github.com/asilbek006/talaba_bot)"
_SAFE_MIMES = ("image/jpeg", "image/png", "image/webp", "image/gif")


def slide_prompt(title: str, bullets: list[str], fallback: str = "") -> str:
    points = "; ".join(str(b) for b in bullets)[:240]
    theme = points or fallback or title
    return (f"Minimal flat vector illustration for a presentation slide about '{title}'. "
            f"Key ideas: {theme}. Clean professional editorial style, soft modern colors, "
            f"light background, no text, no letters, no numbers, no watermark, wide 16:9 composition")


def _get(url: str, headers: dict | None = None) -> bytes:
    hdr = {"User-Agent": _UA}
    if headers:
        hdr.update(headers)
    req = urllib.request.Request(url, headers=hdr)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return r.read(_MAX_BYTES)


def _jpeg_or_png(data: bytes) -> bool:
    return data.startswith(b"\xff\xd8") or data.startswith(b"\x89PNG")


def _commons_queries(title: str, hint: str) -> list[str]:
    out: list[str] = []
    for base in (hint, title):
        words = " ".join(str(base or "").split())
        if not words:
            continue
        for q in dict.fromkeys((words, " ".join(words.split()[:2]))):
            q = q[:120]
            full = f"{q} filetype:bitmap"
            if full not in out:
                out.append(full)
    return out


def _commons_api(query: str) -> dict:
    api = ("https://commons.wikimedia.org/w/api.php?action=query&generator=search"
           f"&gsrsearch={urllib.parse.quote(query)}&gsrnamespace=6&gsrlimit=6"
           "&prop=imageinfo&iiprop=url%7Cmime&iiurlwidth=1024&format=json")
    raw = _get(api, {"User-Agent": _UA})
    return json.loads(raw).get("query", {}).get("pages") or {}


def _commons_pick(pages: dict):
    for page in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        ii = (page.get("imageinfo") or [{}])[0]
        if ii.get("mime") not in _SAFE_MIMES:
            continue
        url = ii.get("thumburl") or ii.get("url")
        if not url:
            continue
        img = _get(url)
        if len(img) > 3000 and _jpeg_or_png(img):
            return img
    return None


def _commons_one(i: int, title: str, hint: str):
    queries = _commons_queries(title, hint)
    if not queries:
        return i, None
    for attempt in range(2):
        try:
            for query in queries:
                pages = _commons_api(query)
                img = _commons_pick(pages)
                if img:
                    return i, img
            return i, None
        except Exception as e:
            if attempt == 0:
                time.sleep(2)
            else:
                log.debug("Commons rasm olinmadi (slayd %s): %s", i, e)
    return i, None


def _fetch(url: str, headers: dict | None = None) -> bytes:
    data = _get(url, headers)
    if len(data) < 2000:
        raise ValueError("empty response")
    if not _jpeg_or_png(data):
        raise ValueError("not a jpeg/png")
    return data


def _pollinations_one(i: int, title: str, bullets: list[str], seed: int):
    try:
        q = urllib.parse.quote(slide_prompt(title, bullets))
        token = os.getenv("POLLINATIONS_TOKEN", "").strip()
        if token:
            url = (f"https://gen.pollinations.ai/image/{q}"
                   f"?width=1024&height=576&seed={seed + i}&nologo=true")
            img = _fetch(url, {"Authorization": f"Bearer {token}"})
        else:
            url = (f"https://image.pollinations.ai/prompt/{q}"
                   f"?width=1024&height=576&seed={seed + i}&nologo=true")
            img = _fetch(url)
        if len(img) <= 3000:
            return i, None
        return i, img
    except Exception as e:
        log.debug("Pollinations rasm olinmadi (slayd %s): %s", i, e)
        return i, None


def fetch_for_slides(slides: list, seed: int) -> list:
    n = len(slides)
    if n == 0:
        return []
    out: list = [None] * n
    t0 = time.time()
    deadline = t0 + _BUDGET
    titles = [str(s.get("title") or "") for s in slides]
    hints = [str(s.get("image_hint") or s.get("image_prompt") or "") for s in slides]
    bullets = [[str(b) for b in s.get("bullets") or []] for s in slides]

    try:
        head = min(max(_BUDGET // 3, 20), _BUDGET - 25)
        ex = cf.ThreadPoolExecutor(max_workers=min(_WORKERS, n))
        try:
            futs = [ex.submit(_commons_one, i, titles[i], hints[i]) for i in range(n)]
            for f in cf.as_completed(futs, timeout=head):
                i, img = f.result()
                if img:
                    out[i] = img
        except cf.TimeoutError:
            log.debug("Commons bosqich vaqti tugadi (%ss)", head)
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        log.debug("Commons bosqichi: %s/%s", sum(1 for x in out if x), n)

        token = os.getenv("POLLINATIONS_TOKEN", "").strip()
        gap = _TOKEN_GAP if token else _POLLINATION_GAP
        last = 0.0
        for i in range(n):
            if out[i]:
                continue
            if time.time() + gap + 5 >= deadline:
                break
            wait = gap - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            last = time.time()
            _, img = _pollinations_one(i, titles[i], bullets[i], seed)
            if img:
                out[i] = img
    except Exception as e:
        log.debug("Rasm yuklash umumiy xatolik: %s", e)

    got = sum(1 for x in out if x)
    log.info("Slaydlar uchun rasm: %s/%s ta (%.1fs)", got, n, time.time() - t0)
    return out


def add_ppt_images(data: dict) -> dict:
    slides = data.get("slides", [])
    imgs = fetch_for_slides(slides, int(time.time()))
    failures = 0
    for slide, img in zip(slides, imgs):
        if img:
            slide["image_bytes"] = img
        else:
            failures += 1
    data["image_failures"] = failures
    return data
