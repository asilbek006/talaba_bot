import re
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "bad_words.txt"

DEFAULT_BAD_WORDS = {
    "jalab",
    "jallab",
    "dalbayob",
    "dalbayop",
    "dolboyob",
    "dolboyop",
    "ahmoq",
    "axmoq",
    "haromi",
    "xaromi",
    "itvachcha",
    "siktir",
    "sikay",
    "sikey",
    "sikish",
    "sikaman",
    "qotoq",
    "qo'toq",
    "qotoqbosh",
    "qo'toqbosh",
    "gandon",
    "padariga",
    "onangni",
    "onangdi",
    "suka",
    "blyad",
    "blyat",
    "pizda",
    "pizdec",
    "xuy",
    "huy",
    "ebat",
    "yobaniy",
    "pidor",
    "pidoras",
    "mudak",
    "fuck",
    "bitch",
    "cunt",
    "asshole",
    "dick",
    "pussy",
}

_BAD_WORDS: set[str] = set()
_last_mtime: float = 0.0

CYR_MAP = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "ж": "j",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "x",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sh",
    "ъ": "'",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
    "ў": "o'",
    "қ": "q",
    "ғ": "g'",
    "ҳ": "h",
}


def normalize_text(text: str) -> str:
    """Apostroflar va kirill harflarini lotinlashtirib normallashtiradi."""
    s = text.lower()
    for ap in ("ʻ", "’", "‘", "`", "´"):
        s = s.replace(ap, "'")
    res = [CYR_MAP.get(ch, ch) for ch in s]
    return "".join(res)


def load_bad_words() -> set[str]:
    """bad_words.txt faylidan so'zlarni qayta o'qiydi."""
    global _BAD_WORDS, _last_mtime
    words = set(DEFAULT_BAD_WORDS)
    if DATA_FILE.exists():
        try:
            _last_mtime = DATA_FILE.stat().st_mtime
            for line in DATA_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    words.add(line)
        except Exception:
            pass
    _BAD_WORDS = words
    return _BAD_WORDS


def get_bad_words() -> set[str]:
    """Keshdagi yoki fayl yangilanganda yangi so'zlar to'plamini beradi."""
    global _BAD_WORDS, _last_mtime
    if DATA_FILE.exists():
        try:
            mtime = DATA_FILE.stat().st_mtime
            if mtime != _last_mtime:
                return load_bad_words()
        except Exception:
            pass
    if not _BAD_WORDS:
        return load_bad_words()
    return _BAD_WORDS


load_bad_words()


def is_personal_query(text: str) -> bool:
    """Asilbek Davlatov haqida so'ralganini aniqlaydi."""
    norm = normalize_text(text)
    if "asilbek davlatov" in norm or "davlatov asilbek" in norm:
        return True
    if "davlatov" in norm:
        return True
    if "asilbek" in norm:
        triggers = [
            "haqida",
            "kim",
            "tarjimai hol",
            "biografiya",
            "kimdir",
            "ma'lumot",
            "malumot",
            "tarixi",
            "yoshi",
            "ish",
            "qayerda",
            "telefon",
            "karta",
            "admin",
            "muallif",
            "egasi",
            "referat",
            "slayd",
        ]
        if any(tr in norm for tr in triggers):
            return True
    return False


def is_insult(text: str) -> bool:
    """Haqoratli so'zlar bor-yo'qligini tekshiradi."""
    raw_lower = text.lower()
    norm = normalize_text(text)
    words = get_bad_words()

    raw_tokens = set(re.findall(r"[a-zа-яёўқғҳ0-9']+", raw_lower))
    norm_tokens = set(re.findall(r"[a-z0-9']+", norm))
    tokens = raw_tokens | norm_tokens

    for bw in words:
        bw_norm = normalize_text(bw)
        if " " in bw or " " in bw_norm:
            if bw in raw_lower or bw_norm in norm:
                return True
        else:
            if bw in tokens or bw_norm in tokens:
                return True
            if len(bw_norm) >= 4 and (bw in raw_lower or bw_norm in norm):
                return True
    return False


def check_content(text: str | None) -> tuple[bool, str | None]:
    """
    Matnni tekshiradi.
    Qaytaradi: (is_allowed: bool, reason: "haqorat" | "personal" | None)
    """
    if not text:
        return True, None
    s = text.strip()
    if not s:
        return True, None

    if is_personal_query(s):
        return False, "personal"

    if is_insult(s):
        return False, "haqorat"

    return True, None
