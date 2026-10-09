import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

DAILY_LIMIT = max(1, int(os.getenv("DAILY_LIMIT", "3").strip() or "3"))
GEMINI_MAX_CONCURRENT = max(1, int(os.getenv("GEMINI_MAX_CONCURRENT", "2").strip() or "2"))
GEMINI_QUEUE_LIMIT = max(1, int(os.getenv("GEMINI_QUEUE_LIMIT", "20")))
GEMINI_TIMEOUT = max(5, int(os.getenv("GEMINI_TIMEOUT", "60")))
CONVERT_MAX_CONCURRENT = max(1, int(os.getenv("CONVERT_MAX_CONCURRENT", "2")))
APP_TIMEZONE = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Tashkent"))
FILE_RETENTION_DAYS = max(1, int(os.getenv("FILE_RETENTION_DAYS", "30")))
MAX_PDF_PAGES = max(1, int(os.getenv("MAX_PDF_PAGES", "300")))
MAX_TEXT_CHARS = max(1000, int(os.getenv("MAX_TEXT_CHARS", "120000")))

# Pro credits do not expire. PREMIUM_DAYS remains for legacy configuration compatibility.
PREMIUM_DAYS = max(1, int(os.getenv("PREMIUM_DAYS", "30").strip() or "30"))
PRO_WORD = max(0, int(os.getenv("PRO_WORD", "5").strip() or "5"))
PRO_SLIDE = max(0, int(os.getenv("PRO_SLIDE", "5").strip() or "5"))
PRO_QUIZ = max(0, int(os.getenv("PRO_QUIZ", "5").strip() or "5"))

# Qo'lda to'lov uchun karta (faqat .env da — GitHub/gitga tushmaydi!)
PAYMENT_CARD = os.getenv("PAYMENT_CARD", "").strip()
PAYMENT_HOLDER = os.getenv("PAYMENT_HOLDER", "").strip()
PAYMENT_AMOUNT = os.getenv("PAYMENT_AMOUNT", "15000").strip()

FILES_DIR = Path(os.getenv("FILES_DIR", str(BASE_DIR / "files"))).resolve()
FILES_DIR.mkdir(parents=True, exist_ok=True)

LOG_DIR = Path(os.getenv("LOG_DIR", str(BASE_DIR / "logs"))).resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)

MAX_DOWNLOAD = 20 * 1024 * 1024
MAX_UPLOAD = 49 * 1024 * 1024

_formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
logging.basicConfig(level=logging.INFO)
_handler = RotatingFileHandler(
    LOG_DIR / "bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_handler.setFormatter(_formatter)
logging.getLogger().addHandler(_handler)

log = logging.getLogger("talababot")
