import os
import pathlib
import sys
import tempfile
from urllib.parse import urlparse

# Tests never inherit production credentials or write to the application's files.
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
if TEST_DATABASE_URL and not any(
    word in urlparse(TEST_DATABASE_URL).path.lower() for word in ("test", "audit", "repro")
):
    raise RuntimeError("TEST_DATABASE_URL must name a dedicated test database")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["GEMINI_API_KEY"] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["ADMIN_IDS"] = ""
os.environ["ADMIN_SECRET"] = ""
_artifacts = pathlib.Path(tempfile.mkdtemp(prefix="talaba-tests-"))
os.environ["FILES_DIR"] = str(_artifacts / "files")
os.environ["LOG_DIR"] = str(_artifacts / "logs")
ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
