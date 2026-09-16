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

from datetime import datetime
try:
    from config import LATE_THRESHOLD_SECONDS
except ImportError:
    LATE_THRESHOLD_SECONDS = 600

def normalize_persian(text):
    if not text:
        return ""
    text = str(text)
    
    # Standardize Persian / Arabic letters
    text = text.replace("ي", "ی")
    text = text.replace("ك", "ک")
    text = text.replace("ة", "ه")
    text = text.replace("ئ", "ی")  # همگام‌سازی ئ و ی (مانند طباطبائی و طباطبایی)
    text = text.replace("آ", "ا").replace("أ", "ا").replace("إ", "ا")
    text = text.replace("ؤ", "و")

    # Standardize Persian & Arabic digits to English digits
    fa_digits = "۰۱۲۳۴۵۶۷۸۹"
    ar_digits = "٠١٢٣٤٥٦٧٨٩"
    for i in range(10):
        text = text.replace(fa_digits[i], str(i))
        text = text.replace(ar_digits[i], str(i))

    # Remove zero-width non-joiner and whitespaces
    text = text.replace("\u200c", "").replace(" ", "").replace("-", "").replace("_", "").replace(".", "").replace(":", "").strip().lower()
    return text

def safe_markdown(text):
    if not text:
        return "نامشخص"
    text = str(text)
    return text.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "")

def get_user_emojis(status, card_status=""):
    status = str(status or "").strip()
    card = str(card_status or "").strip()
    emojis = []
    
    if "حاضر" in status or "تاخیر" in status:
        emojis.append("✅")
    elif "غایب" in status:
        emojis.append("❌")
    elif "بدون شیفت" in status:
        emojis.append("⚪️")
        
    if "تحویل داده شد" in card:
        emojis.append("💳")
    elif "پس گرفته شد" in card or "تحویل گرفته شد" in card:
        emojis.append("🔄")
    elif "بدون کارت" in card:
        emojis.append("⛔️")
        
    return " ".join(emojis)

def calculate_status_with_delay(shift_time_str, now=None, threshold_seconds=LATE_THRESHOLD_SECONDS):
    if not shift_time_str or str(shift_time_str).strip() in ("None", "-", "", "nan"):
        return "حاضر"
    shift_str = str(shift_time_str).strip()
    if ":" not in shift_str:
        return "حاضر"
    try:
        if now is None:
            now = datetime.now()
        parts = shift_str.split(":")
        shift_h = int(parts[0])
        shift_m = int(parts[1])
        shift_time = now.replace(hour=shift_h, minute=shift_m, second=0, microsecond=0)
        if now > shift_time:
            diff_seconds = (now - shift_time).total_seconds()
            if diff_seconds >= threshold_seconds:
                hours = int(diff_seconds // 3600)
                minutes = int((diff_seconds % 3600) // 60)
                delay_str = f"{hours} ساعت و " if hours > 0 else ""
                return f"تاخیر ({delay_str}{minutes} دقیقه)"
        return "حاضر"
    except Exception:
        return "حاضر"

def format_delay_minutes(shift_time_str, now=None):
    if not shift_time_str or ":" not in str(shift_time_str):
        return 0
    try:
        if now is None:
            now = datetime.now()
        parts = str(shift_time_str).strip().split(":")
        shift_time = now.replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0)
        if now > shift_time:
            return int((now - shift_time).total_seconds() // 60)
        return 0
    except Exception:
        return 0


def get_persian_weekday(dt=None):
    if dt is None:
        dt = datetime.now()
    mapping = {
        5: "شنبه",
        6: "یکشنبه",
        0: "دوشنبه",
        1: "سه‌شنبه",
        2: "چهارشنبه",
        3: "پنج‌شنبه",
        4: "جمعه"
    }
    return mapping.get(dt.weekday(), "")
