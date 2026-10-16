import os
import sys
import importlib.abc
import importlib.util
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

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

LOGS_DIR = os.path.join(_root, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

log_file_path = os.path.join(LOGS_DIR, "bot.log")
nohup_file_path = os.path.join(_root, "nohup.out")

# Create main logger
logger = logging.getLogger("attendance_bot")
logger.setLevel(logging.DEBUG)
logger.propagate = False

# Formatter
log_format = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# 1. Console Handler (stdout)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(log_format)

# 2. Rotating File Handler (logs/bot.log) - Max 10MB per file, keep 5 backups
file_handler = RotatingFileHandler(
    log_file_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(log_format)

# 3. Rotating File Handler (nohup.out in root directory for backward compatibility)
nohup_handler = RotatingFileHandler(
    nohup_file_path, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
nohup_handler.setLevel(logging.INFO)
nohup_handler.setFormatter(log_format)

if not logger.handlers:
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.addHandler(nohup_handler)

# Also configure root logger so logging.error / logging.warning from other modules go to the same files
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
if not root_logger.handlers:
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(nohup_handler)

def log_callback(user_id, callback_data, action_desc=""):
    desc_str = f" -> {action_desc}" if action_desc else ""
    logger.info(f"🔘 [CALLBACK] User {user_id} clicked: '{callback_data}'{desc_str}")

def log_message(user_id, text, step_name=""):
    step_str = f" in step [{step_name}]" if step_name else ""
    clean_text = str(text or "").replace('\n', ' ')[:60]
    logger.info(f"💬 [MESSAGE] User {user_id}{step_str}: \"{clean_text}\"")

def log_action(user_id, project_id, action_name, details=""):
    p_str = f" [Project {project_id}]" if project_id else ""
    d_str = f" | {details}" if details else ""
    logger.info(f"⚙️ [ACTION] User {user_id}{p_str} performed: {action_name}{d_str}")

def log_error(user_id, error_msg, exc=None):
    logger.error(f"❌ [ERROR] User {user_id}: {error_msg}")
    if exc:
        logger.exception(exc)