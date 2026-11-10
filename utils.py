import re
from datetime import datetime, timezone, timedelta
from config import LATE_THRESHOLD_SECONDS

try:
    from zoneinfo import ZoneInfo
    IRAN_TZ = ZoneInfo("Asia/Tehran")
except Exception:
    IRAN_TZ = timezone(timedelta(hours=3, minutes=30))


def get_iran_now():
    """دریافت زمان دقیق فعلی به وقت ایران"""
    return datetime.now(IRAN_TZ).replace(tzinfo=None)


def normalize_persian(text):
    """نرمال‌سازی پیشرفته برای جستجو: یکسان‌سازی ی، ئ، ي، ى، ک، ك، آ، ا و اعداد"""
    if text is None:
        return ""
    text = str(text)
    replacements = {
        'ئ': 'ی',
        'ي': 'ی',
        'ى': 'ی',
        'ك': 'ک',
        'ة': 'ه',
        'ۀ': 'ه',
        'ؤ': 'و',
        'إ': 'ا',
        'أ': 'ا',
        'آ': 'ا',
        '\u200c': '',
        '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
        '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9',
        '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
        '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
    }
    for old_char, new_char in replacements.items():
        text = text.replace(old_char, new_char)
    return text.strip()


def to_persian_digits(text):
    if text is None:
        return ""
    persian_digits = "۰۱۲۳۴۵۶۷۸۹"
    english_digits = "0123456789"
    translation_table = str.maketrans(english_digits, persian_digits)
    return str(text).translate(translation_table)


def safe_markdown(text):
    if text is None:
        return ""
    s = str(text)
    for ch in ['_', '*', '`', '[']:
        s = s.replace(ch, f'\\{ch}')
    return s


def gregorian_to_jalali(gy, gm, gd):
    """الگوریتم استاندارد و بدون خطای تبدیل تاریخ میلادی به شمسی (جلالی)"""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy -= 1600
    gm -= 1
    gd -= 1

    g_day_no = 365 * gy + (gy + 3) // 4 - (gy + 99) // 100 + (gy + 399) // 400
    for i in range(gm):
        g_day_no += g_d_m[i + 1] - g_d_m[i]
    if gm > 1 and ((gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)):
        g_day_no += 1
    g_day_no += gd

    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053

    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461

    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365

    for i in range(11):
        days_in_month = 31 if i < 6 else 30
        if j_day_no < days_in_month:
            jm = i + 1
            jd = j_day_no + 1
            break
        j_day_no -= days_in_month
    else:
        jm = 12
        jd = j_day_no + 1

    return jy, jm, jd


def jalali_to_gregorian(jy, jm, jd):
    """الگوریتم استاندارد تبدیل تاریخ شمسی (جلالی) به میلادی"""
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd + (31 * (jm - 1) if jm < 7 else ((jm - 7) * 30) + 186)
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    sal_a = [0, 31, (29 if ((gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)) else 28), 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 13 and gd > sal_a[gm]:
        gd -= sal_a[gm]
        gm += 1
    return gy, gm, gd


def get_today_jalali_str(now=None):
    """دریافت تاریخ امروز ایران به فرمت استاندارد شمسی YYYY/MM/DD (با ارقام انگلیسی برای دیتابیس)"""
    if now is None:
        now = get_iran_now()
    jy, jm, jd = gregorian_to_jalali(now.year, now.month, now.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def get_today_gregorian_str(now=None):
    if now is None:
        now = get_iran_now()
    return now.strftime("%Y-%m-%d")


def normalize_date_to_jalali(date_str):
    """
    هر نوع رشته تاریخ (شمسی مثل 1405/7/1 یا 1405-07-01 یا میلادی مثل 2026-10-16)
    را تشخیص داده و همیشه به فرمت استاندارد شمسی YYYY/MM/DD برمی‌گرداند.
    """
    if not date_str:
        return ""
    clean = normalize_persian(str(date_str)).replace('-', '/').replace('.', '/').strip()
    match = re.match(r'^(\d{4})/(\d{1,2})/(\d{1,2})$', clean)
    if not match:
        # پشتیبانی از فرمت DD/MM/YYYY در صورتی که سال در انتها باشد
        match_rev = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', clean)
        if match_rev:
            d, m, y = int(match_rev.group(1)), int(match_rev.group(2)), int(match_rev.group(3))
        else:
            return clean
    else:
        y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))

    if y >= 1900:
        jy, jm, jd = gregorian_to_jalali(y, m, d)
        return f"{jy:04d}/{jm:02d}/{jd:02d}"
    return f"{y:04d}/{m:02d}/{d:02d}"


def parse_any_date_to_datetime(date_str):
    """تبدیل رشته تاریخ (شمسی یا میلادی) به شیء datetime میلادی جهت محاسبات فاصله روزها"""
    if not date_str:
        return None
    clean = normalize_persian(str(date_str)).replace('-', '/').replace('.', '/').strip()
    match = re.match(r'^(\d{4})/(\d{1,2})/(\d{1,2})$', clean)
    if not match:
        return None
    y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
    try:
        if 1300 <= y <= 1600:
            gy, gm, gd = jalali_to_gregorian(y, m, d)
            return datetime(gy, gm, gd)
        return datetime(y, m, d)
    except Exception:
        return None


def datetime_to_jalali_str(dt):
    if not dt:
        return ""
    jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def is_same_calendar_day(date_str_1, date_str_2):
    """بررسی برابری دو تاریخ (چه شمسی باشند چه میلادی)"""
    if not date_str_1 or not date_str_2:
        return False
    j1 = normalize_date_to_jalali(date_str_1)
    j2 = normalize_date_to_jalali(date_str_2)
    return bool(j1 and j2 and j1 == j2)


def is_time_in_window(start_time_str, end_time_str, now=None):
    """بررسی اینکه آیا ساعت فعلی ایران در بازه [start_time, end_time] قرار دارد یا خیر"""
    if now is None:
        now = get_iran_now()
    now_mins = now.hour * 60 + now.minute

    start_mins = None
    end_mins = None

    if start_time_str and ":" in str(start_time_str):
        m1 = re.search(r'(\d{1,2}):(\d{1,2})', normalize_persian(str(start_time_str)))
        if m1:
            start_mins = int(m1.group(1)) * 60 + int(m1.group(2))

    if end_time_str and ":" in str(end_time_str):
        m2 = re.search(r'(\d{1,2}):(\d{1,2})', normalize_persian(str(end_time_str)))
        if m2:
            end_mins = int(m2.group(1)) * 60 + int(m2.group(2))

    if start_mins is not None and end_mins is not None:
        if start_mins <= end_mins:
            return start_mins <= now_mins <= end_mins
        else:
            return now_mins >= start_mins or now_mins <= end_mins
    elif start_mins is not None:
        return now_mins >= start_mins
    elif end_mins is not None:
        return now_mins <= end_mins
    return True


def is_date_in_jalali_range(target_jalali_str, start_date_str, end_date_str):
    """بررسی قرار گرفتن تاریخ شمسی در بازه شروع و پایان پروژه"""
    t_norm = normalize_date_to_jalali(target_jalali_str)
    if not t_norm:
        return True
    s_norm = normalize_date_to_jalali(start_date_str) if start_date_str else ""
    e_norm = normalize_date_to_jalali(end_date_str) if end_date_str else ""

    if s_norm and re.match(r'^\d{4}/\d{2}/\d{2}$', s_norm):
        if t_norm < s_norm:
            return False
    if e_norm and re.match(r'^\d{4}/\d{2}/\d{2}$', e_norm):
        if t_norm > e_norm:
            return False
    return True


def get_user_emojis(status, card_status):
    st = str(status or "").strip()
    cd = str(card_status or "").strip()

    if "حاضر" in st:
        att_icon = "🟢"
    elif "غایب" in st:
        att_icon = "❌"
    elif "تاخیر" in st:
        att_icon = "⚠️"
    elif "بدون شیفت" in st:
        att_icon = "⚪️"
    else:
        att_icon = "❔"

    if "تحویل داده شد" in cd:
        card_icon = "💳"
    elif "پس گرفته شد" in cd or "تحویل گرفته شد" in cd:
        card_icon = "🔄"
    elif "بدون کارت" in cd:
        card_icon = "⚪️"
    else:
        card_icon = "▫️"

    return f"{att_icon}{card_icon}"


def format_delay_minutes(shift_time_str, now=None):
    if not shift_time_str or ":" not in str(shift_time_str):
        return 0
    if now is None:
        now = get_iran_now()
    clean_time = normalize_persian(str(shift_time_str))
    match = re.search(r'(\d{1,2}):(\d{1,2})', clean_time)
    if not match:
        return 0
    try:
        sh_hour = int(match.group(1))
        sh_min = int(match.group(2))
        shift_dt = now.replace(hour=sh_hour, minute=sh_min, second=0, microsecond=0)
        diff_seconds = (now - shift_dt).total_seconds()
        if diff_seconds <= 0:
            return 0
        return int(diff_seconds // 60)
    except Exception:
        return 0


def calculate_status_with_delay(shift_time_str, now=None, threshold_seconds=None):
    if not shift_time_str or ":" not in str(shift_time_str):
        return "حاضر"
    if now is None:
        now = get_iran_now()
    if threshold_seconds is None:
        threshold_seconds = LATE_THRESHOLD_SECONDS

    clean_time = normalize_persian(str(shift_time_str))
    match = re.search(r'(\d{1,2}):(\d{1,2})', clean_time)
    if not match:
        return "حاضر"

    try:
        sh_hour = int(match.group(1))
        sh_min = int(match.group(2))
        shift_dt = now.replace(hour=sh_hour, minute=sh_min, second=0, microsecond=0)
        diff_seconds = (now - shift_dt).total_seconds()
        if diff_seconds > threshold_seconds:
            delay_mins = int(diff_seconds // 60)
            return f"حاضر (با {delay_mins} دقیقه تاخیر)"
        return "حاضر"
    except Exception:
        return "حاضر"


def get_persian_weekday(dt=None):
    if dt is None:
        dt = get_iran_now()
    weekdays = {
        5: "شنبه",
        6: "یکشنبه",
        0: "دوشنبه",
        1: "سه‌شنبه",
        2: "چهارشنبه",
        3: "پنج‌شنبه",
        4: "جمعه"
    }
    return weekdays.get(dt.weekday(), "")