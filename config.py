import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

DAILY_LIMIT = max(1, int(os.getenv("DAILY_LIMIT", "2").strip() or "2"))
GEMINI_MAX_CONCURRENT = max(1, int(os.getenv("GEMINI_MAX_CONCURRENT", "2").strip() or "2"))

# Pro paket (bir martalik): PREMIUM_DAYS kun ichida PRO_WORD ta Word + PRO_SLIDE ta slayd + quiz tahlili
PREMIUM_DAYS = max(1, int(os.getenv("PREMIUM_DAYS", "30").strip() or "30"))
PRO_WORD = max(0, int(os.getenv("PRO_WORD", "5").strip() or "5"))
PRO_SLIDE = max(0, int(os.getenv("PRO_SLIDE", "3").strip() or "3"))

# Qo'lda to'lov uchun karta (faqat .env da — GitHub/gitga tushmaydi!)
PAYMENT_CARD = os.getenv("PAYMENT_CARD", "").strip()
PAYMENT_HOLDER = os.getenv("PAYMENT_HOLDER", "").strip()
PAYMENT_AMOUNT = os.getenv("PAYMENT_AMOUNT", "50000").strip()

FILES_DIR = BASE_DIR / "files"
FILES_DIR.mkdir(exist_ok=True)

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

MAX_DOWNLOAD = 20 * 1024 * 1024

_formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
logging.basicConfig(level=logging.INFO)
_handler = RotatingFileHandler(LOG_DIR / "bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
_handler.setFormatter(_formatter)
logging.getLogger().addHandler(_handler)

log = logging.getLogger("talababot")