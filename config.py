import os
import sys
import importlib.abc
import importlib.util

_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

class _DirectFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        name = fullname.split('.')[-1]
        for p in (path or sys.path):
            fpath = os.path.join(p or os.getcwd(), name + '.py')
            if os.path.isfile(fpath):
                return importlib.util.spec_from_file_location(fullname, fpath)
        return None

if not any(isinstance(f, _DirectFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _DirectFinder())

# Load environment variables from .env file if available
try:
    from dotenv import load_dotenv
    env_path = os.path.join(_root, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
    else:
        load_dotenv()
except ImportError:
    pass

BASE_PATH = os.path.abspath(os.path.dirname(__file__))

DATA_DIR = os.path.join(BASE_PATH, "data")
PROJECT_FILES_DIR = os.path.join(BASE_PATH, "project_files")
BACKUPS_DIR = os.path.join(BASE_PATH, "backups")
DB_BACKUPS_DIR = os.path.join(BACKUPS_DIR, "db_backups")
EXCEL_BACKUPS_DIR = os.path.join(BACKUPS_DIR, "excel_backups")
ARCHIVES_DIR = os.path.join(BACKUPS_DIR, "archives")
LOGS_DIR = os.path.join(BASE_PATH, "logs")

for d in [DATA_DIR, PROJECT_FILES_DIR, DB_BACKUPS_DIR, EXCEL_BACKUPS_DIR, ARCHIVES_DIR, LOGS_DIR]:
    os.makedirs(d, exist_ok=True)

DEFAULT_SQLITE_PATH = os.getenv("SQLITE_PATH", "/tmp/bot_cache.db")

BALE_API_URL = os.getenv("BALE_API_URL", "https://tapi.bale.ai/bot{0}/{1}")
BALE_FILE_URL = os.getenv("BALE_FILE_URL", "https://tapi.bale.ai/file/bot{0}/{1}")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

MYSQL_ENABLED = os.getenv("MYSQL_ENABLED", "0") == "1"
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_USER = os.getenv("MYSQL_USER", "")
MYSQL_PASS = os.getenv("MYSQL_PASS", "")
MYSQL_DB = os.getenv("MYSQL_DB", "")
MYSQL_TIMEOUT = int(os.getenv("MYSQL_TIMEOUT", "5"))

DEFAULT_SUPER_ADMINS = [1129742448, 541843838]
super_admins_env = os.getenv("SUPER_ADMINS")
if super_admins_env:
    SUPER_ADMINS = [int(x.strip()) for x in super_admins_env.split(",") if x.strip().isdigit()]
else:
    SUPER_ADMINS = DEFAULT_SUPER_ADMINS

DB_BACKUP_INTERVAL = int(os.getenv("DB_BACKUP_INTERVAL", "3600"))
LATE_REMINDER_INTERVAL = int(os.getenv("LATE_REMINDER_INTERVAL", "1800"))
SHORTAGE_CHECK_INTERVAL = int(os.getenv("SHORTAGE_CHECK_INTERVAL", "10"))
LATE_THRESHOLD_SECONDS = int(os.getenv("LATE_THRESHOLD_SECONDS", "600"))
DAILY_REPORT_HOUR = int(os.getenv("DAILY_REPORT_HOUR", "23"))
DAILY_REPORT_MINUTE = int(os.getenv("DAILY_REPORT_MINUTE", "30"))
