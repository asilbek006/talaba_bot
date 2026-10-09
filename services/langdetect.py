import re
from typing import Literal

Lang = Literal["uz", "ru", "en"]


def detect_lang(text: str | None) -> Lang:
    if not text:
        return "uz"
    s = text.strip()
    if not s:
        return "uz"
    lower = s.lower()
    cyr = len(re.findall(r"[а-яё]", lower))
    lat = len(re.findall(r"[a-z]", lower))
    uz_words = [
        "o'zbekiston",
        "o‘zbekiston",
        "toshkent",
        "mavzu",
        "talaba",
        "referat",
        "prezentatsiya",
        "hujjat",
        "bo‘lim",
        "bo'lim",
        "slayd",
        "qator",
        "jadval",
        "test",
        "savol",
        "xulosa",
        "kirish",
        "ish",
        "kurs",
        "bitiruv",
        "loyiha",
        "malumot",
        "ma'lumot",
        "axborot",
        "tuzilma",
        "xavfsizlik",
        "xavf",
        "kripto",
        "kriptografiya",
        "algebra",
        "geometriya",
        "fanidan",
        "haqida",
        "tili",
        "adabiyoti",
        "ona",
        "o'zbek",
    ]
    ru_words = [
        "предмет",
        "курсовая",
        "доклад",
        "презентация",
        "введение",
        "заключение",
        "тема",
        "работа",
        "информация",
        "система",
        "алгоритм",
        "безопасность",
        "криптография",
        "математика",
        "алгебра",
        "геометрия",
        "русский",
        "литература",
        "язык",
    ]
    en_words = [
        "subject",
        "coursework",
        "report",
        "presentation",
        "introduction",
        "conclusion",
        "topic",
        "work",
        "thesis",
        "algorithm",
        "programming",
        "computer",
        "network",
        "database",
        "analysis",
        "system",
        "information",
        "data",
        "security",
        "cryptography",
        "mathematics",
        "algebra",
        "geometry",
        "english",
        "literature",
        "language",
    ]
    has_ru = any(w in lower for w in ru_words)
    has_en = any(w in lower for w in en_words)
    if cyr > 0 and lat == 0:
        return "ru"
    if has_ru:
        return "ru"
    uz_score = sum(1 for w in uz_words if w in lower)
    if uz_score >= 1:
        return "uz"
    if has_en:
        return "en"
    if cyr > lat:
        return "ru"
    if lat > cyr:
        return "en"
    return "uz"
