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

import requests
import shutil
from datetime import datetime
from config import (
    BALE_API_URL, BALE_FILE_URL, BOT_TOKEN, LOGS_DIR
)
from logger import logger, log_callback, log_message, log_action, log_error
from db_manager import db_instance, DatabaseUnavailableError
from utils import normalize_persian, safe_markdown, get_user_emojis, calculate_status_with_delay
from permission_manager import permission_manager
from project_manager import project_manager
from staff_manager import staff_manager
from attendance_manager import attendance_manager
from shortage_manager import shortage_manager
from excel_manager import excel_manager
from report_manager import report_manager
from worker_manager import worker_manager

try:
    import telebot
    from telebot import types
    telebot.apihelper.API_URL = BALE_API_URL
    telebot.apihelper.FILE_URL = BALE_FILE_URL
    bot = telebot.TeleBot(BOT_TOKEN)
    TELEBOT_AVAILABLE = True

    # --- Safe Network & API Wrappers to prevent 400 timeout & entity parsing crashes ---
    _orig_answer_callback_query = bot.answer_callback_query
    def _safe_answer_callback_query(callback_query_id, text=None, show_alert=None, url=None, cache_time=None):
        try:
            return _orig_answer_callback_query(callback_query_id, text=text, show_alert=show_alert, url=url, cache_time=cache_time)
        except Exception as err:
            logger.debug(f"Suppressed answer_callback_query error for {callback_query_id}: {err}")
            return False
    bot.answer_callback_query = _safe_answer_callback_query

    _orig_edit_message_text = bot.edit_message_text
    def _safe_edit_message_text(text, chat_id=None, message_id=None, **kwargs):
        if isinstance(text, str):
            text = text.replace('\\n', '\n')
        try:
            return _orig_edit_message_text(text, chat_id=chat_id, message_id=message_id, **kwargs)
        except Exception as err:
            err_str = str(err).lower()
            if "message is not modified" in err_str:
                return False
            if "can't parse entities" in err_str or "entity" in err_str:
                try:
                    kwargs.pop('parse_mode', None)
                    return _orig_edit_message_text(text, chat_id=chat_id, message_id=message_id, **kwargs)
                except Exception:
                    pass
            logger.warning(f"Suppressed edit_message_text error for msg {message_id}: {err}")
            return False
    bot.edit_message_text = _safe_edit_message_text

    _orig_edit_reply_markup = bot.edit_message_reply_markup
    def _safe_edit_reply_markup(chat_id=None, message_id=None, **kwargs):
        try:
            return _orig_edit_reply_markup(chat_id=chat_id, message_id=message_id, **kwargs)
        except Exception as err:
            if "message is not modified" in str(err).lower():
                return False
            logger.debug(f"Suppressed edit_message_reply_markup error: {err}")
            return False
    bot.edit_message_reply_markup = _safe_edit_reply_markup

    _orig_send_message = bot.send_message
    def _safe_send_message(chat_id, text, **kwargs):
        if isinstance(text, str):
            text = text.replace('\\n', '\n')
        try:
            return _orig_send_message(chat_id, text, **kwargs)
        except Exception as err:
            err_str = str(err).lower()
            if "can't parse entities" in err_str or "entity" in err_str:
                try:
                    kwargs.pop('parse_mode', None)
                    return _orig_send_message(chat_id, text, **kwargs)
                except Exception:
                    pass
            logger.warning(f"Error in send_message to {chat_id}: {err}")
            return type('FallbackMsg', (), {'message_id': 0, 'chat': type('C', (), {'id': chat_id})(), 'text': text})()
    bot.send_message = _safe_send_message

    _orig_register_next_step_handler = getattr(bot, 'register_next_step_handler', None)
    def _safe_register_next_step_handler(message, callback, *args, **kwargs):
        try:
            target_chat_id = None
            if hasattr(message, 'chat') and hasattr(message.chat, 'id'):
                target_chat_id = message.chat.id
            elif isinstance(message, int):
                target_chat_id = message

            if target_chat_id and hasattr(bot, 'register_next_step_handler_by_chat_id'):
                bot.register_next_step_handler_by_chat_id(target_chat_id, callback, *args, **kwargs)
            elif _orig_register_next_step_handler and message:
                _orig_register_next_step_handler(message, callback, *args, **kwargs)
        except Exception as err:
            logger.warning(f"Suppressed register_next_step_handler error: {err}")
    bot.register_next_step_handler = _safe_register_next_step_handler
except ImportError:
    TELEBOT_AVAILABLE = False
    class DummyTypes:
        class InlineKeyboardMarkup:
            def __init__(self, row_width=1): self.keyboard = []
            def add(self, *buttons): self.keyboard.append(list(buttons))
        class InlineKeyboardButton:
            def __init__(self, text, callback_data=None):
                self.text = text; self.callback_data = callback_data
    types = DummyTypes()
    class DummyBot:
        def send_message(self, *args, **kwargs): return type('DummyMsg', (), {'message_id': 999, 'chat': type('C', (), {'id': args[0] if args else 0})()})()
        def edit_message_text(self, *args, **kwargs): pass
        def edit_message_reply_markup(self, *args, **kwargs): pass
        def answer_callback_query(self, *args, **kwargs): pass
        def register_next_step_handler(self, *args, **kwargs): pass
        def clear_step_handler_by_chat_id(self, *args, **kwargs): pass
    bot = DummyBot()

bot_state = {}

def get_user_active_project_and_session(user_id):
    pid, sid = permission_manager.get_user_context(user_id)
    if not pid:
        projects = permission_manager.list_accessible_projects(user_id)
        if len(projects) == 1:
            pid = projects[0]['id']
            permission_manager.set_user_context(user_id, project_id=pid)
        else:
            return None, None
            
    if pid:
        sessions = attendance_manager.list_sessions(pid, include_cancelled=False)
        if not sessions:
            sid = attendance_manager.create_session(pid, "روز 1")
            permission_manager.set_user_context(user_id, project_id=pid, session_id=sid)
        else:
            session_ids = [s['id'] for s in sessions]
            if sid not in session_ids:
                sid = sessions[0]['id']
                permission_manager.set_user_context(user_id, project_id=pid, session_id=sid)
    return pid, sid

def send_welcome(message):
    chat_id = message.chat.id
    log_message(chat_id, message.text or "/start", "send_welcome")
    if TELEBOT_AVAILABLE and bot: bot.clear_step_handler_by_chat_id(chat_id)

    user = permission_manager.get_user(chat_id)
    if not user:
        permission_manager.upsert_user(chat_id, f"کاربر {chat_id}", "خانم")
        user = permission_manager.get_user(chat_id)
        logger.info(f"Registered new guest user: {chat_id}")

    if not user.get('is_active', 1):
        logger.warning(f"Inactive user attempted access: {chat_id}")
        if TELEBOT_AVAILABLE and bot:
            bot.send_message(chat_id, f"⛔️ حساب کاربری شما غیرفعال شده است.\nآیدی شما جهت پیگیری: `{chat_id}`", parse_mode="Markdown")
        return

    projects = permission_manager.list_accessible_projects(chat_id)
    is_global_admin = user.get('is_global_super_admin', 0)

    if not projects and not is_global_admin:
        logger.info(f"User {chat_id} has no accessible projects.")
        if TELEBOT_AVAILABLE and bot:
            bot.send_message(chat_id, f"⛔️ شما در حال حاضر عضو هیچ پروژه فعالی نیستید.\nشناسه کاربری شما: `{chat_id}`", parse_mode="Markdown")
        return

    pid, sid = get_user_active_project_and_session(chat_id)
    if pid and len(projects) == 1 and not is_global_admin:
        show_project_menu(chat_id, pid, sid)
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for p in projects:
        icon = "🌱" if p.get('type') == 'کلاس' else "🏛" if p.get('type') == 'همایش' else "🏕" if p.get('type') == 'اردو' else "🏴" if p.get('type') == 'مراسم' else "🏢"
        markup.add(types.InlineKeyboardButton(f"{icon} {p['name']} ({p.get('type', 'عمومی')})", callback_data=f"selproj_{p['id']}"))

    if is_global_admin:
        markup.add(
            types.InlineKeyboardButton("➕ ایجاد پروژه جدید (هوشمند)", callback_data="menu_new_project_prompt"),
            types.InlineKeyboardButton("👥 اعضای ثابت امور کادر (سرمایه انسانی)", callback_data="menu_hr_members"),
            types.InlineKeyboardButton("📁 پروژه‌های آرشیو شده", callback_data="menu_archived_projects")
        )

    admin_badge = "👑 مدیر ارشد کل سامانه" if is_global_admin else "همکار گرامی"
    text = (
        f"سلام **{user.get('staff_name', 'کاربر')}** ({admin_badge})\n\n"
        f"لطفاً یکی از پروژه‌ها را برای ورود انتخاب نمایید:"
    )
    if TELEBOT_AVAILABLE and bot:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

def show_project_menu(chat_id, project_id, session_id, message_id=None):
    proj = project_manager.get_project(project_id)
    if not proj:
        send_welcome(type('Msg', (), {'chat': type('Chat', (), {'id': chat_id}), 'text': '/start'})())
        return

    role, gender, staff_name, is_active, assigned_unit = permission_manager.get_user_project_role(project_id, chat_id)
    user = permission_manager.get_user(chat_id)
    is_global = user.get('is_global_super_admin', 0) if user else 0

    if not is_active and not is_global:
        logger.warning(f"User {chat_id} access revoked for project {project_id}")
        if TELEBOT_AVAILABLE and bot: bot.send_message(chat_id, "⛔️ دسترسی شما به این پروژه غیرفعال شده است.")
        return

    sess = attendance_manager.get_session(session_id)
    sess_name = sess['name'] if sess else "نامشخص"

    # Dedicated Limited Menu for Unit Head (مسئول واحد)
    if role == "unit_head":
        unit_label = assigned_unit or "نامشخص"
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(f"📢 اعلام کمبود نیرو ({unit_label})", callback_data="menu_unit_head_shortage"),
            types.InlineKeyboardButton(f"📋 کادر واحد {unit_label}", callback_data="menu_unit_head_staff"),
            types.InlineKeyboardButton(f"📊 وضعیت حضور کادر {unit_label}", callback_data="menu_unit_head_attendance"),
            types.InlineKeyboardButton("🔙 تغییر پروژه / خروج", callback_data="menu_switch_project")
        )
        role_fa = f"مسئول واحد {assigned_unit}" if assigned_unit else "مسئول واحد"
        text = (
            f"🏢 **سامانه مدیریت کادر - {proj['name']}**\n\n"
            f"👤 کاربر: **{staff_name}** ({role_fa})\n"
            f"📅 جلسه فعال: **{sess_name}**\n"
            f"عملیات مورد نظر را انتخاب فرمایید:"
        )
        if TELEBOT_AVAILABLE and bot:
            if message_id:
                try:
                    bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode="Markdown")
                    return
                except Exception: pass
            bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
        return
    sess_name = sess['name'] if sess else "نامشخص"

    latecomers = attendance_manager.get_latecomers(project_id, session_id)
    late_filtered = permission_manager.apply_gender_filter(latecomers, chat_id, project_id)
    late_count = len(late_filtered)

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🔍 جستجوی نیروی کادر", callback_data="menu_search"),
        types.InlineKeyboardButton("🏢 لیست واحدها", callback_data="menu_units"),
        types.InlineKeyboardButton(f"🚨 لیست متأخرین ({late_count} نفر)", callback_data="menu_late_page_0"),
        types.InlineKeyboardButton("📢 مشاهده کمبود نیرو", callback_data="menu_view_shortages"),
        types.InlineKeyboardButton("➕ افزودن نیروی جدید", callback_data="menu_add_user")
    )

    if role in ("operator", "user"):
        is_abs = permission_manager.is_operator_absent_today(project_id, chat_id)
        abs_btn_text = "✅ اعلام حضور امروز" if is_abs else "🏖 ثبت عدم حضور امروز (مرخصی)"
        markup.add(types.InlineKeyboardButton(abs_btn_text, callback_data="toggle_my_daily_absence"))

    if role in ("admin", "super_admin"):
        markup.add(
            types.InlineKeyboardButton("📊 داشبورد آماری (زنده)", callback_data="menu_dashboard"),
            types.InlineKeyboardButton("📋 گزارش عملکرد کادر", callback_data="menu_staff_performance"),
            types.InlineKeyboardButton(f"📅 تغییر جلسه (فعلی: {sess_name})", callback_data="menu_change_session"),
            types.InlineKeyboardButton("🔄 آپدیت دیتابیس (آپلود اکسل)", callback_data="menu_update_excel"),
            types.InlineKeyboardButton("📥 دریافت خروجی اکسل", callback_data="download_excel")
        )

    if proj.get('type') == 'کلاس' and role in ("admin", "super_admin"):
        markup.add(types.InlineKeyboardButton("📋 گزارش متنی کادر و حضور", callback_data="menu_class_text_report"))

    if role == "super_admin" or is_global:
        markup.add(
            types.InlineKeyboardButton("📢 اعلام کمبود نیروی قطعی", callback_data="menu_shortage"),
            types.InlineKeyboardButton("⏰ ویرایش گروهی ساعت حضور", callback_data="menu_group_shift"),
            types.InlineKeyboardButton("🔑 کادر اجرایی این پروژه", callback_data="menu_permissions"),
            types.InlineKeyboardButton("🗓 مدیریت جلسات و لغو جلسه", callback_data="menu_manage_sessions"),
            types.InlineKeyboardButton("⚙️ تنظیمات و مدیریت پروژه", callback_data="menu_manage_project")
        )

    markup.add(types.InlineKeyboardButton("🔙 تغییر پروژه / خروج", callback_data="menu_switch_project"))

    role_fa = "مدیر کل سامانه" if is_global else "مدیر ارشد پروژه" if role == "super_admin" else "مدیر پروژه" if role == "admin" else "اپراتور کادر"
    text = (
        f"🏢 **سامانه مدیریت کادر - {proj['name']}**\n\n"
        f"👤 کاربر: **{staff_name}** ({role_fa})\n"
        f"📅 جلسه فعال: **{sess_name}**\n"
        f"عملیات مورد نظر را انتخاب فرمایید:"
    )

    if TELEBOT_AVAILABLE and bot:
        if message_id:
            try:
                bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode="Markdown")
                return
            except Exception: pass
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

def handle_callbacks(call):
    chat_id = call.message.chat.id
    data = call.data
    log_callback(chat_id, data)

    # Initialize all context & role variables globally at the start of handle_callbacks
    # to guarantee UnboundLocalError is physically impossible anywhere in callbacks
    user = permission_manager.get_user(chat_id)
    is_global = user.get('is_global_super_admin', 0) if user else 0
    pid, sid = get_user_active_project_and_session(chat_id)
    role, gender, staff_name, is_active, assigned_unit = permission_manager.get_user_project_role(pid, chat_id)

    try:
        if TELEBOT_AVAILABLE and bot:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

        # ==========================================
        # 1. Global / Project-Independent Callbacks
        # ==========================================
        if data == "toggle_my_daily_absence":
            is_abs = permission_manager.is_operator_absent_today(pid, chat_id)
            if is_abs:
                permission_manager.clear_operator_daily_absence(pid, chat_id)
                bot.answer_callback_query(call.id, "✅ وضعیت شما به «حاضر امروز» تغییر یافت و یادآوری‌ها فعال شد.", show_alert=True)
            else:
                permission_manager.record_operator_daily_absence(pid, chat_id)
                bot.answer_callback_query(call.id, "🏖 عدم حضور شما برای امروز ثبت شد. پیام‌های یادآوری امروز برای شما ارسال نخواهد شد.", show_alert=True)
            show_project_menu(chat_id, pid, sid, call.message.message_id)
            return

        if data == "menu_switch_project":
            permission_manager.set_user_context(chat_id, project_id=None, session_id=None)
            send_welcome(call.message)
            return

        if data.startswith("selproj_"):
            pid = int(data.split("_")[1])
            sessions = attendance_manager.list_sessions(pid, include_cancelled=False)
            sid = sessions[0]['id'] if sessions else None
            permission_manager.set_user_context(chat_id, project_id=pid, session_id=sid)
            log_action(chat_id, pid, "Select Project", f"Session: {sid}")
            show_project_menu(chat_id, pid, sid, call.message.message_id)
            return

        # ----------------------------------------------------
        # Global: HR Fixed Staff Pool (معاونت سرمایه انسانی / امور کادر)
        # ----------------------------------------------------
        if data in ("menu_global_operators", "menu_hr_members"):
            user = permission_manager.get_user(chat_id)
            if not user or not user.get('is_global_super_admin', 0):
                bot.answer_callback_query(call.id, "دسترسی غیرمجاز", show_alert=True)
                return
            users_list = permission_manager.list_all_users()
            markup = types.InlineKeyboardMarkup(row_width=1)
            for u in users_list:
                crown = "👑 " if u['is_global_super_admin'] else "👤 "
                act_icon = "🟢" if u['is_active'] else "🔴"
                rank = "مدیر ارشد امور کادر" if u['is_global_super_admin'] else "نیروی امور کادر"
                p_cnt = u.get('active_projects_count', 0)
                btn_txt = f"{act_icon} {crown}{u['staff_name']} ({rank}) [{p_cnt} پروژه]"
                markup.add(types.InlineKeyboardButton(btn_txt, callback_data=f"hr_view_{u['user_id']}"))
            markup.add(
                types.InlineKeyboardButton("➕ ثبت نیروی جدید در امور کادر", callback_data="guser_add_new"),
                types.InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu_switch_project")
            )
            bot.edit_message_text("👥 **اعضای معاونت سرمایه انسانی (امور کادر):**\nجهت مشاهده سوابق، تغییر رتبه یا انتساب به پروژه‌ها، روی نام نیرو کلیک فرمایید:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("hr_view_") or data.startswith("guser_view_"):
            user = permission_manager.get_user(chat_id)
            if not user or not user.get('is_global_super_admin', 0): return
            prefix = "hr_view_" if data.startswith("hr_view_") else "guser_view_"
            t_uid = int(data.replace(prefix, ""))
            t_user = permission_manager.get_user(t_uid)
            if not t_user: return

            is_g = t_user.get('is_global_super_admin', 0)
            is_act = t_user.get('is_active', 1)
            rank_title = "👑 مدیر ارشد امور کادر" if is_g else "👤 نیروی ثابت امور کادر"

            user_projs = permission_manager.list_user_assigned_projects(t_uid)
            proj_lines = ""
            if not user_projs:
                proj_lines = "  _(در حال حاضر به پروژه‌ای منتسب نیست)_"
            else:
                for up in user_projs:
                    if up['role'] == 'admin':
                        r_fa = "مدیر پروژه"
                    elif up['role'] == 'unit_head':
                        r_fa = f"مسئول واحد {up['assigned_unit']}" if up.get('assigned_unit') else "مسئول واحد"
                    else:
                        r_fa = "اپراتور کادر"
                    proj_lines += f"  🔹 **{up['project_name']}** (نقش: {r_fa})\n"

            text = (
                f"👤 **پروفایل نیروی امور کادر:**\n\n"
                f"نام و نام خانوادگی: **{t_user['staff_name']}**\n"
                f"شناسه بله (Chat ID): `{t_uid}`\n"
                f"جنسیت: {t_user['gender']}\n"
                f"رتبه در امور کادر: **{rank_title}**\n"
                f"وضعیت حساب: {'🟢 فعال' if is_act else '🔴 غیرفعال'}\n\n"
                f"🏢 **پروژه‌های منتسب‌شده:**\n{proj_lines}\n"
                f"عملیات مورد نظر را انتخاب فرمایید:"
            )
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton("➕ انتساب این نیرو به یک پروژه", callback_data=f"hr_assignproj_{t_uid}"))
            if user_projs:
                markup.add(types.InlineKeyboardButton("❌ لغو عضویت از یک پروژه", callback_data=f"hr_unassign_menu_{t_uid}"))

            if is_g:
                markup.add(types.InlineKeyboardButton("👤 تبدیل به نیروی ثابت (عادی)", callback_data=f"guser_setg_{t_uid}_0"))
            else:
                markup.add(types.InlineKeyboardButton("👑 ارتقا به مدیر ارشد امور کادر", callback_data=f"guser_setg_{t_uid}_1"))

            act_txt = "🔴 غیرفعال کردن موقت در سیستم" if is_act else "🟢 فعال کردن حساب"
            act_val = "0" if is_act else "1"
            markup.add(
                types.InlineKeyboardButton(act_txt, callback_data=f"guser_setact_{t_uid}_{act_val}"),
                types.InlineKeyboardButton("🔙 بازگشت به لیست اعضا", callback_data="menu_hr_members")
            )
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("hr_assignproj_") or data.startswith("guser_assignproj_"):
            prefix = "hr_assignproj_" if data.startswith("hr_assignproj_") else "guser_assignproj_"
            t_uid = int(data.replace(prefix, ""))
            all_projs = project_manager.list_projects(status='ACTIVE')
            markup = types.InlineKeyboardMarkup(row_width=1)
            for p in all_projs:
                markup.add(types.InlineKeyboardButton(f"🏢 {p['name']}", callback_data=f"hr_doassign_{t_uid}_{p['id']}"))
            markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"hr_view_{t_uid}"))
            bot.edit_message_text("نیرو را به کدام پروژه منتسب می‌فرمایید؟", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("hr_doassign_") or data.startswith("guser_doassign_"):
            parts = data.split("_")
            t_uid = int(parts[2])
            target_pid = int(parts[3])
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton("🛡 مدیر این پروژه (Admin)", callback_data=f"hr_setrole_{t_uid}_{target_pid}_admin"),
                types.InlineKeyboardButton("🎖 مسئول واحد (Unit Head)", callback_data=f"hr_pickunit_{t_uid}_{target_pid}"),
                types.InlineKeyboardButton("👤 اپراتور کادر (Staff / Operator)", callback_data=f"hr_setrole_{t_uid}_{target_pid}_user"),
                types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"hr_assignproj_{t_uid}")
            )
            proj = project_manager.get_project(target_pid)
            pname = proj['name'] if proj else f"پروژه {target_pid}"
            bot.edit_message_text(f"نقش نیرو در پروژه «**{pname}**» را مشخص فرمایید:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("hr_pickunit_"):
            parts = data.split("_")
            t_uid = int(parts[2])
            target_pid = int(parts[3])
            chart = project_manager.get_project_org_chart(target_pid)
            units = sorted(list(chart.keys()))
            if not units:
                all_s = staff_manager.list_staff(target_pid)
                units = sorted(list(set(s['unit'] for s in all_s if s.get('unit'))))
            markup = types.InlineKeyboardMarkup(row_width=2)
            for u in units:
                markup.add(types.InlineKeyboardButton(f"🏢 {u}", callback_data=f"hr_setrole_{t_uid}_{target_pid}_unithead_{u}"))
            markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"hr_doassign_{t_uid}_{target_pid}"))
            bot.edit_message_text("مسئول کدام واحد این پروژه تعیین شود؟", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("hr_setrole_") or data.startswith("guser_confassign_"):
            parts = data.split("_")
            t_uid = int(parts[2])
            target_pid = int(parts[3])
            target_role = parts[4]
            if target_role == 'unithead': target_role = 'unit_head'
            assigned_u = parts[5] if len(parts) > 5 else None
            permission_manager.set_project_user(target_pid, t_uid, role=target_role, assigned_unit=assigned_u, is_active=1)
            bot.answer_callback_query(call.id, "نیرو با موفقیت به پروژه منتسب گردید ✅", show_alert=True)
            call.data = f"hr_view_{t_uid}"
            handle_callbacks(call)
            return

        if data.startswith("hr_unassign_menu_"):
            t_uid = int(data.replace("hr_unassign_menu_", ""))
            user_projs = permission_manager.list_user_assigned_projects(t_uid)
            markup = types.InlineKeyboardMarkup(row_width=1)
            for up in user_projs:
                markup.add(types.InlineKeyboardButton(f"❌ لغو عضویت از {up['project_name']}", callback_data=f"hr_dounassign_{t_uid}_{up['project_id']}"))
            markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"hr_view_{t_uid}"))
            bot.edit_message_text("عضویت نیرو از کدام پروژه لغو گردد؟", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("hr_dounassign_"):
            parts = data.split("_")
            t_uid = int(parts[2])
            target_pid = int(parts[3])
            permission_manager.remove_project_user(target_pid, t_uid)
            bot.answer_callback_query(call.id, "عضویت در پروژه لغو گردید ✅", show_alert=True)
            call.data = f"hr_view_{t_uid}"
            handle_callbacks(call)
            return

        if data.startswith("guser_setg_"):
            parts = data.split("_")
            t_uid = int(parts[2])
            is_g = int(parts[3])
            permission_manager.set_global_super_admin(t_uid, is_g)
            bot.answer_callback_query(call.id, "رتبه در امور کادر به‌روزرسانی شد ✅", show_alert=True)
            call.data = f"hr_view_{t_uid}"
            handle_callbacks(call)
            return

        if data.startswith("guser_setact_"):
            parts = data.split("_")
            t_uid = int(parts[2])
            act = int(parts[3])
            permission_manager.set_user_active(t_uid, act)
            bot.answer_callback_query(call.id, "وضعیت کاربر تغییر یافت ✅", show_alert=True)
            call.data = f"hr_view_{t_uid}"
            handle_callbacks(call)
            return

        if data == "guser_add_new":
            bot_state[chat_id] = {'type': 'global_add_user'}
            msg = bot.send_message(chat_id, "➕ **ثبت نیروی جدید در امور کادر**\n\nلطفاً **شناسه عددی (Chat ID)** نیرو در بله را ارسال فرمایید:")
            bot.register_next_step_handler(msg, process_global_add_user_id)
            return

        # Smart Project Creation Wizard
        # ----------------------------------------------------
        if data == "menu_new_project_prompt":
            user = permission_manager.get_user(chat_id)
            if not user or not user.get('is_global_super_admin', 0):
                bot.answer_callback_query(call.id, "دسترسی غیرمجاز", show_alert=True)
                return
            bot_state[chat_id] = {'type': 'new_project_wiz'}
            msg = bot.send_message(chat_id, "🚀 **تعریف هوشمند پروژه جدید**\n\n**مرحله ۱:** لطفاً **نام پروژه** را ارسال فرمایید:\n(مثلاً: `کلاس حکمت سطح ۱` یا `همایش بهشت بانو` یا `اردوی مشهد`)",
                                   reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 انصراف", callback_data="menu_switch_project")), parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_wiz_project_name)
            return

        # Wizard: Type Selection
        if data.startswith("wiz_type_"):
            ptype = data.replace("wiz_type_", "")
            st = bot_state.get(chat_id, {})
            st['project_type'] = ptype
            bot_state[chat_id] = st
            pname = st.get('name', 'پروژه')

            if ptype == 'کلاس':
                # Class flow: Ask for recurring days
                markup = types.InlineKeyboardMarkup(row_width=3)
                days = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه"]
                for d in days:
                    markup.add(types.InlineKeyboardButton(d, callback_data=f"wiz_day_{d}"))
                markup.add(types.InlineKeyboardButton("🔙 انصراف", callback_data="menu_switch_project"))
                bot.edit_message_text(f"🌱 پروژه کلاسی: **{pname}**\n\n**مرحله ۳:** روز برگزاری کلاس را انتخاب فرمایید:\n_(یا می‌توانید روزها را به صورت متن ارسال کنید، مثلاً: «یکشنبه‌ها»)_",
                                      chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            else:
                # Event / Ceremony / Camp flow: Ask for Start Date
                markup = types.InlineKeyboardMarkup(row_width=2)
                today_str = datetime.now().strftime("%Y-%m-%d")
                markup.add(types.InlineKeyboardButton(f"📅 امروز ({today_str})", callback_data="wiz_start_today"))
                markup.add(types.InlineKeyboardButton("🔙 انصراف", callback_data="menu_switch_project"))
                msg = bot.edit_message_text(f"🏛 رویداد: **{pname}** ({ptype})\n\n**مرحله ۳:** لطفاً **تاریخ شروع رویداد** را انتخاب یا به صورت متنی ارسال فرمایید:\n(مثلاً: `1405/07/01` یا `2026-09-20`)",
                                            chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
                bot.register_next_step_handler(msg, process_wiz_start_date_text)
            return

        # Class Flow: Day Selected
        if data.startswith("wiz_day_"):
            day_val = data.replace("wiz_day_", "")
            bot_state[chat_id]['recurring_days'] = day_val
            msg = bot.send_message(chat_id, f"🗓 روز برگزاری: **{day_val}**\n\n**مرحله ۴:** لطفاً **ساعت شروع و فعال‌سازی کلاس** را با فرمت `HH:MM` وارد فرمایید:\n(مثلاً: `16:00` یا `08:30`)", parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_wiz_class_time)
            return

        # Class Flow: Session Count Quick Buttons
        if data.startswith("wiz_cnt_"):
            cnt_val = int(data.replace("wiz_cnt_", ""))
            bot_state[chat_id]['total_sessions'] = cnt_val
            finalize_smart_project_creation(chat_id)
            return

        # Event Flow: Start Today Button
        if data == "wiz_start_today":
            today_str = datetime.now().strftime("%Y-%m-%d")
            bot_state[chat_id]['start_date'] = today_str
            ask_wiz_end_date(chat_id, today_str)
            return

        # Event Flow: End Sameday Button
        if data == "wiz_end_sameday":
            sdate = bot_state.get(chat_id, {}).get('start_date', '')
            bot_state[chat_id]['end_date'] = sdate
            bot_state[chat_id]['total_sessions'] = 1
            ask_wiz_prep_day(chat_id)
            return

        # Event Flow: Prep Day Selection
        if data.startswith("wiz_prep_"):
            has_prep = data.replace("wiz_prep_", "") == "1"
            bot_state[chat_id]['has_prep_day'] = has_prep
            finalize_smart_project_creation(chat_id)
            return

        # Archived Projects
        if data == "menu_archived_projects":
            user = permission_manager.get_user(chat_id)
            if not user or not user.get('is_global_super_admin', 0):
                bot.answer_callback_query(call.id, "دسترسی غیرمجاز", show_alert=True)
                return
            archived = project_manager.list_projects(status='ARCHIVED')
            markup = types.InlineKeyboardMarkup(row_width=1)
            if not archived:
                text = "هیچ پروژه بایگانی‌شده‌ای وجود ندارد."
            else:
                text = "📁 **پروژه‌های آرشیو شده:**\nجهت فعال‌سازی مجدد یا حذف دائم روی گزینه مورد نظر کلیک فرمایید:"
                for p in archived:
                    markup.add(
                        types.InlineKeyboardButton(f"📦 {p['name']} (فعال‌سازی مجدد)", callback_data=f"unarchive_{p['id']}"),
                        types.InlineKeyboardButton(f"🗑 حذف {p['name']}", callback_data=f"deleteproj_{p['id']}")
                    )
            markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="menu_switch_project"))
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("unarchive_"):
            pid = int(data.split("_")[1])
            project_manager.update_project_status(pid, 'ACTIVE')
            log_action(chat_id, pid, "Unarchive Project")
            bot.answer_callback_query(call.id, "پروژه مجدداً فعال شد ✅", show_alert=True)
            send_welcome(call.message)
            return

        # ==========================================
        # 2. Project-Scoped Callbacks (Require pid)
        # ==========================================
        pid, sid = get_user_active_project_and_session(chat_id)
        if not pid:
            logger.warning(f"User {chat_id} had no active project context for '{data}'. Redirecting to welcome.")
            send_welcome(call.message)
            return

        role, gender, staff_name, is_active, assigned_unit = permission_manager.get_user_project_role(pid, chat_id)
        if not is_active:
            bot.answer_callback_query(call.id, "دسترسی غیرمجاز", show_alert=True)
            return

# --- Project Management & Settings ---
        if data == "menu_manage_project":
            if role != "super_admin" and not is_global: return
            proj = project_manager.get_project(pid)
            if not proj: return
            is_arch = proj.get('status') == 'ARCHIVED'
            status_fa = "📁 آرشیو شده" if is_arch else "🟢 فعال"
            text = (
                f"⚙️ **تنظیمات و مدیریت پروژه: {safe_markdown(proj['name'])}**\n\n"
                f"🏷 نوع پروژه: **{proj.get('type', 'عمومی')}**\n"
                f"📝 توضیحات: {safe_markdown(proj.get('description') or 'ندارد')}\n"
                f"📊 وضعیت پروژه: **{status_fa}**\n\n"
                f"عملیات مورد نظر را انتخاب فرمایید:"
            )
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✏️ ویرایش نام", callback_data=f"editproj_name_{pid}"),
                types.InlineKeyboardButton("📝 ویرایش توضیحات", callback_data=f"editproj_desc_{pid}")
            )
            if is_arch:
                markup.add(types.InlineKeyboardButton("♻️ فعال‌سازی مجدد (خروج از آرشیو)", callback_data=f"unarchiveproj_{pid}"))
            else:
                markup.add(types.InlineKeyboardButton("📁 آرشیو کردن پروژه", callback_data=f"archiveproj_{pid}"))
            markup.add(types.InlineKeyboardButton("🗑 حذف کامل پروژه", callback_data=f"deleteproj_{pid}"))
            markup.add(types.InlineKeyboardButton("🔙 بازگشت به منوی پروژه", callback_data="start_menu"))
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("deleteproj_"):
            if role != "super_admin" and not is_global: return
            target_pid = int(data.replace("deleteproj_", ""))
            proj = project_manager.get_project(target_pid)
            pname = proj['name'] if proj else f"پروژه {target_pid}"
            text = (
                f"⚠️ **هشدار بسیار مهم حذف پروژه**\n\n"
                f"آیا از حذف کامل پروژه «**{safe_markdown(pname)}**» اطمینان دارید؟\n\n"
                f"⚠️ **توجه:** با حذف پروژه، تمامی اطلاعات شامل اعضای کادر، جلسات، چارت سازمانی، وضعیت حضور و غیاب، و فایل‌های اکسل مربوط به این پروژه به طور دائمی و غیرقابل بازگشت پاک خواهند شد!"
            )
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton("❌ بله، پروژه کاملاً حذف شود", callback_data=f"confirm_delproj_{target_pid}"),
                types.InlineKeyboardButton("🔙 انصراف و بازگشت", callback_data="menu_manage_project")
            )
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("confirm_delproj_"):
            if role != "super_admin" and not is_global: return
            target_pid = int(data.replace("confirm_delproj_", ""))
            proj = project_manager.get_project(target_pid)
            pname = proj['name'] if proj else f"پروژه {target_pid}"
            project_manager.delete_project(target_pid)
            permission_manager.set_user_context(chat_id, project_id=None, session_id=None)
            log_action(chat_id, target_pid, "Delete Project", f"Project: {pname}")
            bot.answer_callback_query(call.id, f"پروژه «{pname}» به طور کامل حذف گردید ✅", show_alert=True)
            send_welcome(call.message)
            return

        if data.startswith("archiveproj_"):
            if role != "super_admin" and not is_global: return
            target_pid = int(data.replace("archiveproj_", ""))
            project_manager.archive_project(target_pid)
            log_action(chat_id, target_pid, "Archive Project")
            bot.answer_callback_query(call.id, "پروژه به آرشیو منتقل شد 📁", show_alert=True)
            call.data = "menu_manage_project"
            handle_callbacks(call)
            return

        if data.startswith("unarchiveproj_"):
            if role != "super_admin" and not is_global: return
            target_pid = int(data.replace("unarchiveproj_", ""))
            project_manager.unarchive_project(target_pid)
            log_action(chat_id, target_pid, "Unarchive Project")
            bot.answer_callback_query(call.id, "پروژه مجدداً فعال شد 🟢", show_alert=True)
            call.data = "menu_manage_project"
            handle_callbacks(call)
            return

        if data.startswith("editproj_name_"):
            if role != "super_admin" and not is_global: return
            target_pid = int(data.replace("editproj_name_", ""))
            msg = bot.send_message(chat_id, "✏️ لطفاً **نام جدید پروژه** را ارسال فرمایید:",
                                   reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="menu_manage_project")), parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_edit_project_name, target_pid)
            return

        if data.startswith("editproj_desc_"):
            if role != "super_admin" and not is_global: return
            target_pid = int(data.replace("editproj_desc_", ""))
            msg = bot.send_message(chat_id, "📝 لطفاً **توضیحات جدید پروژه** را ارسال فرمایید:",
                                   reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="menu_manage_project")), parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_edit_project_desc, target_pid)
            return

        # Class text report
        if data == "menu_class_text_report":
            if role not in ("admin", "super_admin"): return
            rep_text = report_manager.generate_class_text_report(pid, sid)
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="start_menu"))
            if len(rep_text) > 4000:
                for chunk in [rep_text[i:i+3800] for i in range(0, len(rep_text), 3800)]:
                    bot.send_message(chat_id, chunk)
                bot.send_message(chat_id, "✅ گزارش کادر و حضور جلسه فعال ارسال گردید.", reply_markup=markup)
            else:
                bot.send_message(chat_id, rep_text, reply_markup=markup)
            return

        # Approving / Rejecting candidate suggested for shortage
        if data.startswith("apprshrt_"):
            parts = data.split("_")
            sh_id = int(parts[1])
            suggester_id = int(parts[2]) if len(parts) > 2 else 0
            sh = shortage_manager.get_shortage(sh_id)
            if not sh:
                bot.answer_callback_query(call.id, "کمبود یافت نشد.", show_alert=True)
                return
            shortage_manager.approve_shortage(sh_id)
            c_name = sh.get('assigned_name') or "نیروی جدید"
            c_phone = sh.get('phone') or ""
            c_gender = sh.get('target_group') or "خانم"
            staff_id = staff_manager.add_staff_member(sh['project_id'], c_name, c_phone, sh['unit'], sh['section'], position="نیرو", gender=c_gender)
            sessions = attendance_manager.list_sessions(sh['project_id'])
            for s in sessions:
                attendance_manager.update_attendance_status(sh['project_id'], s['id'], staff_id, "")
            bot.answer_callback_query(call.id, "نیرو با موفقیت تایید و به کادر اضافه شد ✅", show_alert=True)
            try:
                bot.edit_message_text(f"✅ نیروی پیشنهادی (**{c_name}**) توسط شما تایید و به کادر واحد **{sh['unit']}** اضافه گردید.", chat_id, call.message.message_id, parse_mode="Markdown")
            except Exception: pass
            if suggester_id:
                try:
                    bot.send_message(suggester_id, f"🎉 نیروی پیشنهادی شما (**{c_name}**) توسط مدیر پروژه تایید شد و به کادر واحد **{sh['unit']}** افزوده گردید.", parse_mode="Markdown")
                except Exception: pass
            return

        if data.startswith("rejshrt_"):
            parts = data.split("_")
            sh_id = int(parts[1])
            suggester_id = int(parts[2]) if len(parts) > 2 else 0
            sh = shortage_manager.get_shortage(sh_id)
            c_name = (sh.get('assigned_name') or "نیروی پیشنهادی") if sh else ""
            unit_name = sh.get('unit', '') if sh else ''
            shortage_manager.reject_shortage(sh_id)
            bot.answer_callback_query(call.id, "پیشنهاد رد شد ❌", show_alert=True)
            try:
                bot.edit_message_text(f"❌ پیشنهاد جذب نیروی **{c_name}** رد شد و ردیف کمبود مجدداً فعال گردید.", chat_id, call.message.message_id, parse_mode="Markdown")
            except Exception: pass
            if suggester_id:
                try:
                    bot.send_message(suggester_id, f"❌ پیشنهاد شما برای نیروی **{c_name}** در واحد **{unit_name}** توسط مدیر پروژه مورد موافقت قرار نگرفت.", parse_mode="Markdown")
                except Exception: pass
            return


        if data == "start_menu":
            show_project_menu(chat_id, pid, sid, call.message.message_id)
            return

        # Change session
        if data == "menu_change_session":
            sessions = attendance_manager.list_sessions(pid, include_cancelled=False)
            markup = types.InlineKeyboardMarkup(row_width=2)
            for s in sessions:
                status_icon = "🟢" if s['status'] == 'SCHEDULED' else "🔵"
                markup.add(types.InlineKeyboardButton(f"{status_icon} {s['name']}", callback_data=f"setsess_{s['id']}"))
            markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="start_menu"))
            bot.edit_message_text("لطفاً جلسه مورد نظر را برای مدیریت انتخاب فرمایید:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("setsess_"):
            new_sid = int(data.split("_")[1])
            permission_manager.set_user_context(chat_id, project_id=pid, session_id=new_sid)
            log_action(chat_id, pid, "Switch Session", f"New Session: {new_sid}")
            bot.answer_callback_query(call.id, "جلسه فعال تغییر یافت ✅")
            show_project_menu(chat_id, pid, new_sid, call.message.message_id)
            return

        # Manage sessions: List all sessions
        if data == "menu_manage_sessions":
            if role != "super_admin": return
            sessions = attendance_manager.list_sessions(pid, include_cancelled=True)
            markup = types.InlineKeyboardMarkup(row_width=1)
            for s in sessions:
                is_canc = s.get('status') == 'CANCELLED'
                c_icon = "❌ لغوشده" if is_canc else "🟢 فعال"
                day_str = f" ({s['day_of_week']})" if s.get('day_of_week') else ""
                btn_txt = f"{s['name']}{day_str} | {c_icon}"
                markup.add(types.InlineKeyboardButton(btn_txt, callback_data=f"sess_detail_{s['id']}"))
            markup.add(
                types.InlineKeyboardButton("➕ ایجاد جلسه جدید (با کپی کادر روز قبل)", callback_data="add_new_session_prompt"),
                types.InlineKeyboardButton("🔙 بازگشت", callback_data="start_menu")
            )
            bot.edit_message_text("🗓 **مدیریت جلسات پروژه:**\nبرای مشاهده جزییات و ویرایش اطلاعات هر جلسه روی آن کلیک فرمایید:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        # Session Detail View
        if data.startswith("sess_detail_"):
            if role != "super_admin": return
            target_sid = int(data.replace("sess_detail_", ""))
            show_session_details(chat_id, pid, target_sid, call.message.message_id)
            return

        # Toggle Session Status (Cancel / Reactivate)
        if data.startswith("togglesess_"):
            if role != "super_admin": return
            target_sid = int(data.replace("togglesess_", ""))
            attendance_manager.toggle_session_status(target_sid)
            bot.answer_callback_query(call.id, "وضعیت جلسه تغییر یافت ✅", show_alert=True)
            show_session_details(chat_id, pid, target_sid, call.message.message_id)
            return

        # Edit Session Field Callbacks
        if data.startswith("editsess_"):
            if role != "super_admin": return
            parts = data.split("_")
            field_name = parts[1]
            target_sid = int(parts[2])
            field_fa = "نام جلسه" if field_name == "name" else "تاریخ جلسه" if field_name == "date" else "ساعت جلسه" if field_name == "time" else "روز هفته"
            db_col = "name" if field_name == "name" else "session_date" if field_name == "date" else "time_str" if field_name == "time" else "day_of_week"
            msg = bot.send_message(chat_id, f"✏️ لطفاً مقدار جدید برای **{field_fa}** را ارسال فرمایید:",
                                   reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"sess_detail_{target_sid}")))
            bot.register_next_step_handler(msg, process_edit_session_field, target_sid, db_col)
            return
        if data == "add_new_session_prompt":
            if role != "super_admin": return
            bot_state[chat_id] = {'type': 'add_session', 'pid': pid}
            msg = bot.send_message(chat_id, "🗓 نام جلسه جدید را ارسال فرمایید (مثلاً `جلسه ۵` یا `1405/08/04`):",
                                   reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="menu_manage_sessions")), parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_add_session_name)
            return

        # Update Excel
        if data == "menu_update_excel":
            if role not in ("admin", "super_admin"): return
            msg = bot.send_message(chat_id, "🔄 **آپدیت دیتابیس از روی اکسل:**\n\nلطفاً فایل اکسل تکمیل‌شده این پروژه را ارسال نمایید:",
                                   reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="start_menu")), parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_update_excel_file, pid)
            return

        # Search
        if data == "menu_search":
            msg = bot.send_message(chat_id, "🔍 بخشی از نام، فامیل، شماره تماس، بخش یا عنوان کارت نیرو را ارسال فرمایید:",
                                   reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="start_menu")))
            bot.register_next_step_handler(msg, process_search, pid, sid)
            return

        # Units
        if data == "menu_units":
            chart = project_manager.get_project_org_chart(pid)
            if not chart:
                all_staff = staff_manager.list_staff(pid)
                units = sorted(list(set(s['unit'] for s in all_staff if s.get('unit'))))
            else:
                units = sorted(list(chart.keys()))

            markup = types.InlineKeyboardMarkup(row_width=2)
            for u in units: markup.add(types.InlineKeyboardButton(u, callback_data=f"up_0:{u}"))
            markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="start_menu"))
            bot.edit_message_text("🏢 واحدهای موجود در پروژه:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("up_"):
            parts = data.split(":", 1)
            page = int(parts[0].replace("up_", ""))
            selected_unit = parts[1]
            render_unit_page(chat_id, pid, sid, selected_unit, page, call.message.message_id)
            return

        # Staff profile
        if data.startswith("user_"):
            staff_id = int(data.split("_")[1])
            show_user_profile(chat_id, pid, sid, staff_id, call.message.message_id)
            return

        # Actions: status / card
        if data.startswith("act_"):
            parts = data.split("_")
            action_type = parts[1]
            staff_id = int(parts[2])
            val = parts[3]
            if val == "clear": val = ""

            if action_type == "status":
                ok = attendance_manager.update_attendance_status(pid, sid, staff_id, val, actor_user_id=chat_id)
                if not ok:
                    bot.answer_callback_query(call.id, "⚠️ شما اجازه تغییر وضعیت این نیرو را ندارید.", show_alert=True)
                    return
                report_manager.log_staff_action(pid, chat_id, 'attendance')
                log_action(chat_id, pid, "Update Attendance", f"Staff: {staff_id}, Val: '{val}'")
            elif action_type == "card":
                ok = attendance_manager.update_card_status(pid, sid, staff_id, val, actor_user_id=chat_id)
                if not ok:
                    bot.answer_callback_query(call.id, "⚠️ شما اجازه تغییر وضعیت کارت این نیرو را ندارید.", show_alert=True)
                    return
                report_manager.log_staff_action(pid, chat_id, 'card')
                log_action(chat_id, pid, "Update Card", f"Staff: {staff_id}, Val: '{val}'")

            bot.answer_callback_query(call.id, "ثبت شد ✅")
            show_user_profile(chat_id, pid, sid, staff_id, call.message.message_id)
            return

        # Track late
        if data.startswith("tracklate_"):
            staff_id = int(data.split("_")[1])
            bot_state[chat_id] = {'type': 'track_late', 'staff_id': staff_id, 'pid': pid, 'sid': sid}
            msg = bot.send_message(chat_id, "📞 لطفاً نتیجه تماس یا پیگیری تأخیر را ارسال نمایید:",
                                   reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"user_{staff_id}")))
            bot.register_next_step_handler(msg, process_track_late)
            return

        # Edit desc
        if data.startswith("editdesc_"):
            staff_id = int(data.split("_")[1])
            bot_state[chat_id] = {'type': 'edit_desc', 'staff_id': staff_id, 'pid': pid, 'sid': sid}
            msg = bot.send_message(chat_id, "📝 متن توضیحات جدید را ارسال فرمایید:",
                                   reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"user_{staff_id}")))
            bot.register_next_step_handler(msg, process_edit_desc)
            return

        # Latecomers list
        if data.startswith("menu_late_page_"):
            page = int(data.split("_")[3])
            latecomers = attendance_manager.get_latecomers(pid, sid)
            late_filtered = permission_manager.apply_gender_filter(latecomers, chat_id, pid)
            items_per_page = 8
            total_pages = max(1, (len(late_filtered) - 1) // items_per_page + 1)
            current_items = late_filtered[page * items_per_page:(page + 1) * items_per_page]

            markup = types.InlineKeyboardMarkup(row_width=1)
            for u in current_items:
                tr_icon = "📞" if str(u.get('late_tracking', '')).strip() else ""
                delay_mins = u.get('delay_mins', 0)
                markup.add(types.InlineKeyboardButton(f"🚨 {u['name']} {tr_icon} ({delay_mins} دقیقه)", callback_data=f"user_{u['staff_id']}"))

            nav_btns = []
            if page > 0: nav_btns.append(types.InlineKeyboardButton("◀️ قبلی", callback_data=f"menu_late_page_{page-1}"))
            if page < total_pages - 1: nav_btns.append(types.InlineKeyboardButton("بعدی ▶️", callback_data=f"menu_late_page_{page+1}"))
            if nav_btns: markup.add(*nav_btns)
            markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="start_menu"))

            sess = attendance_manager.get_session(sid)
            text = f"🚨 **متأخرین جلسه: {sess['name'] if sess else ''}**\n📄 صفحه {page+1} از {total_pages}\n💡 علامت 📞 به معنی ثبت پیگیری است."
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        # Dashboard
        if data == "menu_dashboard":
            if role not in ("admin", "super_admin"): return
            stats = report_manager.get_dashboard_stats(pid, sid)
            sess = attendance_manager.get_session(sid)
            sess_name = sess['name'] if sess else ""

            text = f"📊 **داشبورد آماری زنده - جلسه {sess_name}**\n\n"
            for unit, d in sorted(stats['unit_stats'].items()):
                prs = d['prs_m'] + d['prs_f']
                pct = int((prs / d['exp']) * 100) if d['exp'] > 0 else 0
                text += f"🏢 **{unit}**\n"
                text += f"👥 باید می‌آمدند: {d['exp']} | ✅ حاضر: {prs} ({pct}%)\n"
                text += f"👨🏻 آقا: {d['prs_m']} | 👩🏻 خانم: {d['prs_f']} | ⚠️ غایب: {d['abs']}\n"
                text += f"💳 کارت: (تحویل: {d['cg']} / عودت: {d['cr']})\n〰️〰️〰️〰️\n"

            text += f"\n📈 **آمار کل:**\nکل مورد انتظار: {stats['total_expected']} | کل حاضرین: {stats['total_present']} | درصد مشارکت: **{stats['overall_percentage']}%**"
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔄 بروزرسانی", callback_data="menu_dashboard"), types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="start_menu"))
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        # Staff performance
        if data == "menu_staff_performance":
            if role not in ("admin", "super_admin"): return
            logs = report_manager.get_staff_performance_report(pid)
            text = "📋 **گزارش عملکرد کادر پروژه:**\n\n"
            if not logs:
                text += "امروز هیچ عملکردی ثبت نشده است."
            else:
                perf = {}
                for r in logs:
                    name = r.get('staff_name') or f"کاربر {r['user_id']}"
                    if name not in perf: perf[name] = {'attendance': 0, 'card': 0, 'call': 0}
                    act = r['action_type']
                    if act in perf[name]: perf[name][act] = r['cnt']
                for name, d in perf.items():
                    text += f"👤 **{name}**:\n  ✅ ثبت حضور: {d['attendance']} بار\n  💳 ثبت کارت: {d['card']} بار\n  📞 تماس پیگیری: {d['call']} بار\n〰️〰️〰️〰️\n"
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="start_menu"))
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        # Add staff member
        if data == "menu_add_user":
            bot_state[chat_id] = {'type': 'add_staff', 'pid': pid, 'sid': sid}
            msg = bot.send_message(chat_id, "➕ **افزودن نیروی جدید به پروژه**\n\n**مرحله ۱:** نام و نام خانوادگی نیرو را وارد فرمایید:",
                                   reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="start_menu")), parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_add_staff_name)
            return

        if data.startswith("addu_unit_"):
            unit = data.replace("addu_unit_", "")
            bot_state[chat_id]['unit'] = unit
            chart = project_manager.get_project_org_chart(pid)
            sections = chart.get(unit, [])
            if not sections or (len(sections) == 1 and sections[0] == "اصلی"):
                bot_state[chat_id]['section'] = "بدون بخش"
                msg = bot.send_message(chat_id, f"🏢 واحد: **{unit}**\n\n**مرحله ۳:** شماره تماس نیرو را وارد فرمایید:", parse_mode="Markdown")
                bot.register_next_step_handler(msg, process_add_staff_phone)
            else:
                markup = types.InlineKeyboardMarkup(row_width=2)
                for s in sections: markup.add(types.InlineKeyboardButton(s, callback_data=f"addu_sec_{s}"))
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="start_menu"))
                bot.edit_message_text(f"🏢 واحد: **{unit}**\n\n**مرحله ۳:** بخش را انتخاب فرمایید:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("addu_sec_"):
            sec = data.replace("addu_sec_", "")
            bot_state[chat_id]['section'] = sec
            msg = bot.send_message(chat_id, f"🗂 بخش: **{sec}**\n\n**مرحله ۴:** شماره تماس نیرو را وارد فرمایید:", parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_add_staff_phone)
            return

        if data.startswith("addu_gen_"):
            gen = data.replace("addu_gen_", "")
            finalize_add_staff(chat_id, gen)
            return

# --- Unit Head Operations ---
        if data == "menu_unit_head_shortage":
            role_chk, _, _, _, assigned_u = permission_manager.get_user_project_role(pid, chat_id)
            if role_chk != "unit_head" or not assigned_u:
                bot.answer_callback_query(call.id, "دسترسی فقط مخصوص مسئول واحد است", show_alert=True)
                return

            chart = project_manager.get_project_org_chart(pid)
            sections = chart.get(assigned_u, [])
            if not sections:
                all_s = staff_manager.list_staff(pid)
                sections = sorted(list(set(s['section'] for s in all_s if s.get('unit') == assigned_u and s.get('section'))))

            markup = types.InlineKeyboardMarkup(row_width=2)
            for sec in sections:
                markup.add(types.InlineKeyboardButton(sec, callback_data=f"uh_sec_{sec[:30]}"))
            markup.add(
                types.InlineKeyboardButton("✏️ تایپ بخش جدید", callback_data="uh_sec_manual_prompt"),
                types.InlineKeyboardButton("🔙 بازگشت به منو", callback_data="start_menu")
            )
            bot_state[chat_id] = {'type': 'uh_shortage', 'pid': pid, 'unit': assigned_u}
            bot.edit_message_text(
                f"📢 **اعلام کمبود نیرو - واحد {assigned_u}**\n\nلطفاً بخش نیازمند نیرو را انتخاب فرمایید:",
                chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown"
            )
            return

        if data == "uh_sec_manual_prompt":
            st = bot_state.get(chat_id)
            if not st or st.get('type') != 'uh_shortage': return
            msg = bot.send_message(chat_id, "✏️ لطفاً نام بخش مورد نظر را تایپ و ارسال فرمایید:")
            bot.register_next_step_handler(msg, process_uh_manual_section)
            return

        if data.startswith("uh_sec_"):
            sec = data.replace("uh_sec_", "")
            st = bot_state.get(chat_id)
            if not st or st.get('type') != 'uh_shortage': return
            st['section'] = sec
            msg = bot.send_message(chat_id, f"🗂 بخش: **{sec}**\n\nتعداد نیروی مورد نیاز را وارد فرمایید (مثلاً: `2`):", parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_uh_shortage_count)
            return

        if data.startswith("uh_grp_"):
            grp = data.replace("uh_grp_", "")
            st = bot_state.get(chat_id)
            if not st or st.get('type') != 'uh_shortage': return
            st['target_group'] = grp
            msg = bot.send_message(chat_id, "📝 لطفاً توضیحات کمبود را ارسال فرمایید (یا بنویسید 'ندارد'):")
            bot.register_next_step_handler(msg, process_uh_shortage_desc)
            return

        # View Unit Staff
        if data == "menu_unit_head_staff":
            role_chk, _, _, _, assigned_u = permission_manager.get_user_project_role(pid, chat_id)
            if role_chk != "unit_head" or not assigned_u: return
            all_s = staff_manager.list_staff(pid, unit=assigned_u)
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="start_menu"))
            if not all_s:
                text = f"📋 در حال حاضر هیچ نیرویی در واحد **{assigned_u}** ثبت نشده است."
            else:
                text = f"📋 **لیست اعضای کادر واحد {assigned_u} ({len(all_s)} نفر):**\n\n"
                for idx, s in enumerate(all_s, 1):
                    sec_str = f" | بخش: {s['section']}" if s.get('section') else ""
                    pos_str = f" ({s['position']})" if s.get('position') else ""
                    phone_str = f" - `{s['phone']}`" if s.get('phone') else ""
                    text += f"{idx}. **{s['name']}**{pos_str}{sec_str}{phone_str}\n"
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        # View Unit Attendance in Active Session
        if data == "menu_unit_head_attendance":
            role_chk, _, _, _, assigned_u = permission_manager.get_user_project_role(pid, chat_id)
            if role_chk != "unit_head" or not assigned_u: return
            all_att = attendance_manager.get_session_attendance(pid, sid)
            unit_att = [s for s in all_att if s.get('unit') == assigned_u]
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="start_menu"))
            sess = attendance_manager.get_session(sid)
            s_name = sess['name'] if sess else ""
            if not unit_att:
                text = f"📊 در جلسه «{s_name}» هیچ نیرویی برای واحد **{assigned_u}** ثبت نشده است."
            else:
                text = f"📊 **وضعیت حضور کادر واحد {assigned_u} در جلسه «{s_name}»:**\n\n"
                for idx, s in enumerate(unit_att, 1):
                    st = s.get('status', '').strip()
                    icon = "🟢" if "حاضر" in st else "❌" if "غایب" in st else "⏱" if "تاخیر" in st else "⚪️"
                    st_label = st if st else "تعیین‌نشده"
                    text += f"{idx}. {icon} **{s['name']}**: {st_label} (بخش {s.get('section', 'اصلی')})\n"
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        # --- Admin Approval for Unit Head Shortage ---
        if data.startswith("appr_uh_shrt_"):
            if role not in ("admin", "super_admin") and not is_global: return
            parts = data.split("_")
            sh_id = int(parts[3])
            uh_id = int(parts[4]) if len(parts) > 4 else 0
            sh = shortage_manager.get_shortage(sh_id)
            if not sh:
                bot.answer_callback_query(call.id, "درخواست کمبود یافت نشد.", show_alert=True)
                return

            shortage_manager.approve_unit_head_shortage(sh_id)
            bot.answer_callback_query(call.id, "درخواست کمبود تایید و برودکست شد ✅", show_alert=True)

            # Broadcast to eligible project members
            proj = project_manager.get_project(sh['project_id'])
            pname = proj['name'] if proj else ""
            grp = sh.get('target_group', 'عمومی')
            broadcast_msg = (
                f"🔔 **اعلام کمبود نیرو - {pname}**\n\n"
                f"🏢 واحد: **{sh['unit']}**\n"
                f"🗂 بخش: **{sh['section']}**\n"
                f"👥 تعداد: **{sh['count']} نفر** ({grp})\n"
                f"📝 توضیحات: {sh.get('description') or 'ندارد'}\n\n"
                f"لطفاً در صورت شناخت فرد مناسب، جهت تأمین کمبود از منوی ربات اقدام فرمایید."
            )
            members = permission_manager.get_project_members(sh['project_id'])
            for m in members:
                # Strictly exclude unit heads from shortage broadcast announcements
                if m['is_active'] and m['role'] != 'unit_head' and (m['role'] in ('admin', 'super_admin') or (grp == 'عمومی' or m['project_gender'] == grp)):
                    try:
                        bot.send_message(m['user_id'], broadcast_msg, parse_mode="Markdown")
                    except Exception: pass

            try:
                bot.edit_message_text(f"✅ درخواست کمبود نیرو در واحد **{sh['unit']}** (بخش {sh['section']}) توسط شما تایید و به کادر اعلام گردید.", chat_id, call.message.message_id, parse_mode="Markdown")
            except Exception: pass

            if uh_id:
                try:
                    bot.send_message(uh_id, f"🎉 درخواست کمبود نیروی شما برای واحد **{sh['unit']}** (بخش {sh['section']}) توسط مدیر پروژه تایید شد و فراخوان عمومی آن برای کادر ارسال گردید.", parse_mode="Markdown")
                except Exception: pass
            return

        if data.startswith("rej_uh_shrt_"):
            if role not in ("admin", "super_admin") and not is_global: return
            parts = data.split("_")
            sh_id = int(parts[3])
            uh_id = int(parts[4]) if len(parts) > 4 else 0
            sh = shortage_manager.get_shortage(sh_id)
            u_title = sh['unit'] if sh else ""
            s_title = sh['section'] if sh else ""
            shortage_manager.reject_shortage(sh_id)
            bot.answer_callback_query(call.id, "درخواست کمبود رد شد ❌", show_alert=True)
            try:
                bot.edit_message_text(f"❌ درخواست کمبود نیرو در واحد **{u_title}** رد گردید.", chat_id, call.message.message_id, parse_mode="Markdown")
            except Exception: pass

            if uh_id:
                try:
                    bot.send_message(uh_id, f"❌ درخواست کمبود نیروی شما برای واحد **{u_title}** (بخش {s_title}) توسط مدیر پروژه رد گردید.", parse_mode="Markdown")
                except Exception: pass
            return

        if data.startswith("edit_uh_count_"):
            if role not in ("admin", "super_admin") and not is_global: return
            parts = data.split("_")
            sh_id = int(parts[3])
            uh_id = int(parts[4]) if len(parts) > 4 else 0
            msg = bot.send_message(chat_id, "✏️ لطفاً **تعداد تاییدشده جدید** را با عدد وارد فرمایید:")
            bot.register_next_step_handler(msg, process_admin_edit_uh_count, sh_id, uh_id)
            return

        # Shortages
        if data == "menu_view_shortages":
            shortages = shortage_manager.get_all_unresolved_shortages(pid)
            markup = types.InlineKeyboardMarkup(row_width=1)
            if not shortages:
                text = "✅ هیچ کمبود تامین نشده‌ای در این پروژه وجود ندارد."
            else:
                text = "📢 **لیست کمبودهای نیروی پروژه:**\n\n"
                for sh in shortages:
                    sid_sh = sh['id']
                    st = sh['status']
                    if st == 'تامین نشده':
                        text += f"🏢 واحد: **{sh['unit']}** | 🗂 بخش: **{sh['section']}**\n👥 جنسیت: {sh['target_group']}\n📝 توضیحات: {sh['description']}\n〰️〰️〰️〰️\n"
                        markup.add(types.InlineKeyboardButton(f"🙋🏻‍♂️ معرفی نیرو: {sh['unit']} - {sh['section']}", callback_data=f"suggestshrt_{sid_sh}"))
                    elif st == 'در انتظار تایید ادمین':
                        text += f"📢 **درخواست کمبود مسئول واحد (در انتظار بررسی):**\n🏢 واحد: **{sh['unit']}** | 🗂 بخش: **{sh['section']}**\n👥 تعداد: **{sh['count']} نفر** ({sh.get('target_group', 'عمومی')})\n📝 توضیحات: {sh.get('description') or 'ندارد'}\n〰️〰️〰️〰️\n"
                        if role in ('admin', 'super_admin') or is_global:
                            req_by = sh.get('requested_by') or 0
                            markup.add(
                                types.InlineKeyboardButton(f"✅ تایید: {sh['unit']} - {sh['section']}", callback_data=f"appr_uh_shrt_{sid_sh}_{req_by}"),
                                types.InlineKeyboardButton(f"❌ رد: {sh['unit']}", callback_data=f"rej_uh_shrt_{sid_sh}_{req_by}")
                            )
                    elif st == 'در انتظار تایید':
                        text += f"⏳ **در انتظار تایید ادمین:**\n🏢 واحد: **{sh['unit']}** | 🗂 بخش: **{sh['section']}**\n👤 نیروی پیشنهادی: {sh.get('assigned_name', '')} ({sh.get('phone', '')})\n〰️〰️〰️〰️\n"
                        if role in ('admin', 'super_admin'):
                            markup.add(types.InlineKeyboardButton(f"✅ تایید پیشنهاد: {sh['unit']} - {sh['section']}", callback_data=f"approveshrt_{sid_sh}"))
                            markup.add(types.InlineKeyboardButton(f"❌ رد پیشنهاد: {sh['unit']} - {sh['section']}", callback_data=f"rejectshrt_{sid_sh}"))

            markup.add(types.InlineKeyboardButton("🔄 بروزرسانی", callback_data="menu_view_shortages"), types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="start_menu"))
            try: bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            except: pass
            return

        if data.startswith("suggestshrt_"):
            sh_id = int(data.split("_")[1])
            bot_state[chat_id] = {'type': 'suggest_shrt', 'shrt_id': sh_id}
            msg = bot.send_message(chat_id, "نام و نام خانوادگی نیروی پیشنهادی را ارسال فرمایید:")
            bot.register_next_step_handler(msg, process_suggest_shrt_name)
            return

        if data.startswith("approveshrt_"):
            if role not in ('admin', 'super_admin'): return
            sh_id = int(data.split("_")[1])
            shortage_manager.approve_shortage(sh_id)
            log_action(chat_id, pid, "Approve Shortage", f"ID: {sh_id}")
            bot.answer_callback_query(call.id, "کمبود تأمین و تأیید شد ✅", show_alert=True)
            call.data = "menu_view_shortages"
            handle_callbacks(call)
            return

        if data.startswith("rejectshrt_"):
            if role not in ('admin', 'super_admin'): return
            sh_id = int(data.split("_")[1])
            shortage_manager.reject_shortage(sh_id)
            log_action(chat_id, pid, "Reject Shortage", f"ID: {sh_id}")
            bot.answer_callback_query(call.id, "پیشنهاد رد شد ❌", show_alert=True)
            call.data = "menu_view_shortages"
            handle_callbacks(call)
            return

        # Super admin shortage creation
        if data == "menu_shortage":
            if role != "super_admin": return
            chart = project_manager.get_project_org_chart(pid)
            units = sorted(list(chart.keys()))
            if not units:
                all_s = staff_manager.list_staff(pid)
                units = sorted(list(set(s['unit'] for s in all_s if s.get('unit'))))
            markup = types.InlineKeyboardMarkup(row_width=2)
            for u in units: markup.add(types.InlineKeyboardButton(u, callback_data=f"shrt_unit_{u}"))
            markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="start_menu"))
            bot.edit_message_text("📢 **اعلام کمبود نیرو:**\nلطفاً واحد نیازمند نیرو را انتخاب فرمایید:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("shrt_unit_"):
            u = data.replace("shrt_unit_", "")
            bot_state[chat_id] = {'type': 'create_shortage', 'pid': pid, 'unit': u}
            msg = bot.send_message(chat_id, f"🏢 واحد: **{u}**\n\nلطفاً نام بخش را ارسال فرمایید (یا بنویسید 'اصلی'):", parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_shortage_section)
            return

        if data.startswith("shrt_grp_"):
            grp = data.replace("shrt_grp_", "")
            st = bot_state.get(chat_id)
            if st and st.get('type') == 'create_shortage':
                count_val = int(st.get('count', 1))
                shrt_ids = shortage_manager.add_shortage(st['pid'], st['unit'], st['section'], count_val, grp, st.get('desc', 'ندارد'))
                for sid_item in shrt_ids:
                    shortage_manager.mark_shortage_notified(sid_item)

                log_action(chat_id, pid, "Create Shortage", f"Unit: {st['unit']}, Sec: {st['section']}, Count: {count_val}")
                bot.send_message(chat_id, f"✅ کمبود {count_val} نفر در واحد **{st['unit']}** با موفقیت ثبت و برودکست شد.",
                                 reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="start_menu")), parse_mode="Markdown")

                # Broadcast exactly ONE message to eligible project members
                proj = project_manager.get_project(st['pid'])
                pname = proj['name'] if proj else ""
                broadcast_msg = (
                    f"🔔 **اعلام کمبود نیرو - {pname}**\n\n"
                    f"🏢 واحد: **{st['unit']}**\n"
                    f"🗂 بخش: **{st['section']}**\n"
                    f"👥 تعداد: **{count_val} نفر** ({grp})\n"
                    f"📝 توضیحات: {st.get('desc', 'ندارد')}\n\n"
                    f"لطفاً در صورت شناخت فرد مناسب، جهت تأمین کمبود از منوی ربات اقدام فرمایید."
                )
                members = permission_manager.get_project_members(st['pid'])
                for m in members:
                    # Strictly exclude unit heads from shortage broadcast announcements
                    if m['is_active'] and m['role'] != 'unit_head' and (m['role'] in ('admin', 'super_admin') or (grp == 'عمومی' or m['project_gender'] == grp)):
                        try:
                            bot.send_message(m['user_id'], broadcast_msg, parse_mode="Markdown")
                        except Exception:
                            pass

                del bot_state[chat_id]
            return

        # Group shift
        if data == "menu_group_shift":
            if role != "super_admin": return
            chart = project_manager.get_project_org_chart(pid)
            units = sorted(list(chart.keys()))
            markup = types.InlineKeyboardMarkup(row_width=2)
            for u in units: markup.add(types.InlineKeyboardButton(u, callback_data=f"grpshift_u_{u}"))
            markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="start_menu"))
            bot.edit_message_text("⏰ **ویرایش گروهی ساعت حضور:**\nواحد مورد نظر را انتخاب فرمایید:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("grpshift_u_"):
            u = data.replace("grpshift_u_", "")
            bot_state[chat_id] = {'type': 'group_shift', 'pid': pid, 'unit': u}
            chart = project_manager.get_project_org_chart(pid)
            sections = chart.get(u, [])
            markup = types.InlineKeyboardMarkup(row_width=2)
            for s in sections: markup.add(types.InlineKeyboardButton(s, callback_data=f"grpshift_s_{s}"))
            markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="menu_group_shift"))
            bot.edit_message_text(f"🏢 واحد: **{u}**\n\nبخش مورد نظر را انتخاب فرمایید:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("grpshift_s_"):
            s = data.replace("grpshift_s_", "")
            bot_state[chat_id]['section'] = s
            msg = bot.send_message(chat_id, f"بخش **{s}** انتخاب شد.\nساعت جدید حضور را با فرمت `HH:MM` ارسال فرمایید (مثلاً `08:30`):", parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_group_shift_time)
            return

        # Project Permissions / Members Management (کادر اجرایی پروژه)
        
        if data == "proj_add_uh_prompt":
            if role != "super_admin" and not is_global: return
            bot_state[chat_id] = {'type': 'add_project_uh', 'pid': pid}
            msg = bot.send_message(chat_id, "🎖 **انتصاب مسئول واحد برای پروژه:**\n\nلطفاً **شناسه عددی (Chat ID) بله** کاربر مورد نظر را ارسال فرمایید:",
                                   reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="menu_permissions")), parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_proj_add_uh_uid)
            return

        if data.startswith("proj_uh_gen_"):
            gen = data.replace("proj_uh_gen_", "")
            st = bot_state.get(chat_id)
            if not st or st.get('type') != 'add_project_uh': return
            st['gender'] = gen
            ask_proj_uh_unit(chat_id, st['pid'], st['staff_name'])
            return

        if data.startswith("proj_uh_setunit_"):
            unit = data.replace("proj_uh_setunit_", "")
            st = bot_state.get(chat_id)
            if not st or st.get('type') != 'add_project_uh': return
            pid_target = st['pid']
            t_uid = st['uid']
            s_name = st.get('staff_name') or f"مسئول واحد {unit}"
            gen_val = st.get('gender', 'خانم')
            permission_manager.set_project_unit_head(pid_target, t_uid, unit, staff_name=s_name, gender=gen_val)
            log_action(chat_id, pid_target, "Assign Unit Head", f"User: {t_uid}, Name: {s_name}, Unit: {unit}")
            bot.send_message(chat_id, f"✅ «{s_name}» با موفقیت به عنوان مسئول واحد **{unit}** در این پروژه منصوب شد.\n\nدسترسی ایشان محدود به همین پروژه و واحد **{unit}** است و در لیست عمومی امور کادر نمایش داده نمی‌شود.",
                             reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔑 بازگشت به کادر اجرایی", callback_data="menu_permissions")), parse_mode="Markdown")
            del bot_state[chat_id]
            return

        if data == "menu_permissions":
            if role != "super_admin" and not is_global: return
            members = permission_manager.get_project_members(pid)
            markup = types.InlineKeyboardMarkup(row_width=1)
            for m in members:
                r = m['role']
                if m.get('is_global_super_admin'):
                    r_fa = "👑 مدیر ارشد امور کادر"
                elif r == 'admin':
                    r_fa = "🛡 مدیر پروژه"
                elif r == 'unit_head':
                    r_fa = f"🎖 مسئول واحد {m.get('assigned_unit', '')}" if m.get('assigned_unit') else "🎖 مسئول واحد"
                else:
                    r_fa = "👤 اپراتور کادر"
                act_icon = "🟢" if m['is_active'] else "🔴"
                markup.add(types.InlineKeyboardButton(f"{act_icon} {m['staff_name']} ({r_fa})", callback_data=f"proj_editmember_{m['user_id']}"))
            markup.add(
                types.InlineKeyboardButton("🎖 انتصاب مسئول واحد برای این پروژه", callback_data="proj_add_uh_prompt"),
                types.InlineKeyboardButton("➕ انتصاب از اعضای امور کادر", callback_data="proj_add_from_hr"),
                types.InlineKeyboardButton("➕ ثبت مستقیم با شناسه عددی", callback_data="perm_add_user"),
                types.InlineKeyboardButton("🔙 بازگشت به منوی پروژه", callback_data="start_menu")
            )
            proj = project_manager.get_project(pid)
            pname = proj['name'] if proj else ""
            bot.edit_message_text(f"🔑 **کادر اجرایی پروژه «{pname}»:**\nجهت تغییر نقش یا حذف هر نیرو روی نام او کلیک فرمایید:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        # Add existing HR member to this project with 1 click
        if data == "proj_add_from_hr":
            if role != "super_admin" and not is_global: return
            avail_members = permission_manager.get_available_hr_members_for_project(pid)
            markup = types.InlineKeyboardMarkup(row_width=1)
            if not avail_members:
                text = "⚠️ تمامی اعضای ثبت‌شده امور کادر در حال حاضر در این پروژه عضو هستند."
            else:
                text = "👥 **انتخاب نیرو از اعضای امور کادر:**\nلطفاً نیروی مورد نظر را جهت انتساب به این پروژه انتخاب فرمایید:"
                for m in avail_members:
                    rank_icon = "👑 " if m.get('is_global_super_admin') else "👤 "
                    markup.add(types.InlineKeyboardButton(f"{rank_icon}{m['staff_name']} ({m['user_id']})", callback_data=f"proj_setmember_role_{m['user_id']}"))
            markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="menu_permissions"))
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("proj_setmember_role_"):
            if role != "super_admin" and not is_global: return
            t_uid = int(data.replace("proj_setmember_role_", ""))
            t_user = permission_manager.get_user(t_uid)
            uname = t_user['staff_name'] if t_user else f"کاربر {t_uid}"
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton("🛡 مدیر پروژه (Admin)", callback_data=f"proj_applyrole_{t_uid}_admin"),
                types.InlineKeyboardButton("🎖 مسئول واحد (Unit Head)", callback_data=f"proj_pickunit_{t_uid}"),
                types.InlineKeyboardButton("👤 اپراتور کادر (Staff)", callback_data=f"proj_applyrole_{t_uid}_user"),
                types.InlineKeyboardButton("🔙 بازگشت", callback_data="proj_add_from_hr")
            )
            bot.edit_message_text(f"نقش **{uname}** در این پروژه را انتخاب فرمایید:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("proj_pickunit_"):
            if role != "super_admin" and not is_global: return
            t_uid = int(data.replace("proj_pickunit_", ""))
            chart = project_manager.get_project_org_chart(pid)
            units = sorted(list(chart.keys()))
            if not units:
                all_s = staff_manager.list_staff(pid)
                units = sorted(list(set(s['unit'] for s in all_s if s.get('unit'))))
            markup = types.InlineKeyboardMarkup(row_width=2)
            for u in units:
                markup.add(types.InlineKeyboardButton(f"🏢 {u}", callback_data=f"proj_applyrole_{t_uid}_unithead_{u}"))
            markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data=f"proj_setmember_role_{t_uid}"))
            bot.edit_message_text("مسئولیت کدام واحد این پروژه به ایشان سپرده شود؟", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("proj_applyrole_"):
            if role != "super_admin" and not is_global: return
            parts = data.split("_")
            t_uid = int(parts[2])
            target_role = parts[3]
            if target_role == 'unithead': target_role = 'unit_head'
            assigned_u = parts[4] if len(parts) > 4 else None
            permission_manager.set_project_user(pid, t_uid, role=target_role, assigned_unit=assigned_u, is_active=1)
            t_user = permission_manager.get_user(t_uid)
            uname = t_user['staff_name'] if t_user else f"کاربر {t_uid}"
            log_action(chat_id, pid, "Assign Project Member", f"User: {t_uid}, Role: {target_role}, Unit: {assigned_u}")
            bot.answer_callback_query(call.id, f"«{uname}» با موفقیت به کادر پروژه افزوده شد ✅", show_alert=True)
            call.data = "menu_permissions"
            handle_callbacks(call)
            return

        # Edit existing member inside project
        if data.startswith("proj_editmember_") or data.startswith("perm_edit_"):
            if role != "super_admin" and not is_global: return
            prefix = "proj_editmember_" if data.startswith("proj_editmember_") else "perm_edit_"
            t_uid = int(data.replace(prefix, ""))
            t_user = permission_manager.get_user(t_uid)
            uname = t_user['staff_name'] if t_user else f"کاربر {t_uid}"
            t_role, _, _, t_active, t_unit = permission_manager.get_user_project_role(pid, t_uid)
            curr_role_fa = "مدیر پروژه" if t_role == 'admin' else f"مسئول واحد {t_unit}" if t_role == 'unit_head' else "اپراتور کادر"
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton("🛡 تنظیم به عنوان مدیر پروژه (Admin)", callback_data=f"proj_applyrole_{t_uid}_admin"),
                types.InlineKeyboardButton("🎖 تعیین به عنوان مسئول واحد", callback_data=f"proj_pickunit_{t_uid}"),
                types.InlineKeyboardButton("👤 تنظیم به عنوان اپراتور کادر", callback_data=f"proj_applyrole_{t_uid}_user"),
                types.InlineKeyboardButton("🔴 حذف از کادر این پروژه", callback_data=f"proj_removemember_{t_uid}"),
                types.InlineKeyboardButton("🔙 بازگشت به لیست کادر پروژه", callback_data="menu_permissions")
            )
            text = (
                f"👤 **مدیریت عضو در پروژه: {uname}**\n"
                f"شناسه: `{t_uid}`\n"
                f"نقش فعلی در پروژه: **{curr_role_fa}**\n\n"
                f"جهت تغییر نقش گزینه مورد نظر را انتخاب فرمایید:"
            )
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            return

        if data.startswith("proj_removemember_"):
            if role != "super_admin" and not is_global: return
            t_uid = int(data.replace("proj_removemember_", ""))
            permission_manager.remove_project_user(pid, t_uid)
            bot.answer_callback_query(call.id, "نیرو از کادر این پروژه حذف گردید ❌", show_alert=True)
            call.data = "menu_permissions"
            handle_callbacks(call)
            return

        
        if data.startswith("guser_gen_"):
            gen = data.replace("guser_gen_", "")
            st = bot_state.get(chat_id)
            if not st or st.get('type') != 'global_add_user': return
            t_uid = st['uid']
            name = st['name']
            permission_manager.upsert_user(t_uid, name, gen, is_global_super_admin=0, is_active=1, is_hr_member=1)
            log_action(chat_id, None, "Global Register User", f"UID: {t_uid}, Name: {name}, Gender: {gen}")
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("👥 بازگشت به امور کادر", callback_data="menu_hr_members"))
            bot.send_message(chat_id, f"✅ نیروی جدید «{name}» ({gen}) با شناسه `{t_uid}` با موفقیت در امور کادر ثبت شد.",
                             reply_markup=markup, parse_mode="Markdown")
            del bot_state[chat_id]
            return

        if data.startswith("perm_gen_"):
            gen = data.replace("perm_gen_", "")
            st = bot_state.get(chat_id)
            if not st or st.get('type') != 'add_perm_user': return
            st['gender'] = gen
            ask_perm_role(chat_id, st['pid'], st['name'], gen)
            return

        if data.startswith("perm_setrole_"):
            target_role = data.replace("perm_setrole_", "")
            st = bot_state.get(chat_id)
            if not st or st.get('type') != 'add_perm_user': return
            pid_target = st['pid']
            t_uid = st['uid']
            name = st['name']
            gen = st['gender']

            if target_role == "unithead":
                chart = project_manager.get_project_org_chart(pid_target)
                units = sorted(list(chart.keys()))
                if not units:
                    all_s = staff_manager.list_staff(pid_target)
                    units = sorted(list(set(s['unit'] for s in all_s if s.get('unit'))))
                markup = types.InlineKeyboardMarkup(row_width=2)
                for u in units: markup.add(types.InlineKeyboardButton(f"🏢 {u}", callback_data=f"perm_setunit_{u}"))
                markup.add(types.InlineKeyboardButton("🔙 انصراف", callback_data="menu_permissions"))
                bot.send_message(chat_id, f"مسئول واحد: **{name}** ({gen})\n\nمسئولیت کدام واحد این پروژه به ایشان سپرده شود؟", reply_markup=markup, parse_mode="Markdown")
                return

            role_db = "admin" if target_role == "admin" else "operator"
            permission_manager.upsert_user(t_uid, name, gen, is_global_super_admin=0, is_active=1, is_hr_member=1 if role_db == 'admin' else 0)
            permission_manager.set_project_user(pid_target, t_uid, role=role_db, gender=gen, is_active=1)
            role_fa = "مدیر پروژه (Admin)" if role_db == 'admin' else "اپراتور کادر (Operator)"
            log_action(chat_id, pid_target, "Add Project Member", f"User: {t_uid}, Name: {name}, Role: {role_db}, Gender: {gen}")
            bot.send_message(chat_id, f"✅ «{name}» ({gen}) با موفقیت به عنوان **{role_fa}** به این پروژه افزوده شد.\n\nتفکیک جنسیتی به درستی برای دسترسی ایشان لحاظ گردید.",
                             reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔑 بازگشت به کادر اجرایی", callback_data="menu_permissions")), parse_mode="Markdown")
            del bot_state[chat_id]
            return

        if data.startswith("perm_setunit_"):
            unit = data.replace("perm_setunit_", "")
            st = bot_state.get(chat_id)
            if not st or st.get('type') != 'add_perm_user': return
            pid_target = st['pid']
            t_uid = st['uid']
            name = st['name']
            gen = st['gender']
            permission_manager.set_project_unit_head(pid_target, t_uid, unit, staff_name=name, gender=gen)
            log_action(chat_id, pid_target, "Assign Unit Head", f"User: {t_uid}, Name: {name}, Unit: {unit}, Gender: {gen}")
            bot.send_message(chat_id, f"✅ «{name}» ({gen}) با موفقیت به عنوان مسئول واحد **{unit}** در این پروژه منصوب شد.\n\nدسترسی ایشان فقط به همین واحد محدود است و در حوضچه عمومی امور کادر نمایش داده نمی‌شود.",
                             reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔑 بازگشت به کادر اجرایی", callback_data="menu_permissions")), parse_mode="Markdown")
            del bot_state[chat_id]
            return

        if data == "perm_add_user":
            if role != "super_admin" and not is_global: return
            bot_state[chat_id] = {'type': 'add_perm_user', 'pid': pid}
            msg = bot.send_message(chat_id, "لطفاً **شناسه عددی (Chat ID)** کاربر را ارسال فرمایید:")
            bot.register_next_step_handler(msg, process_perm_new_user)
            return

        # Excel download
        if data == "download_excel":
            if role not in ("admin", "super_admin"): return
            bot.send_message(chat_id, "⏳ در حال استخراج آخرین اطلاعات در فایل اکسل...")
            out_path = excel_manager.export_project_excel(pid)
            if out_path and os.path.exists(out_path):
                proj = project_manager.get_project(pid)
                log_action(chat_id, pid, "Export Excel", f"File: {out_path}")
                with open(out_path, "rb") as f:
                    bot.send_document(chat_id, f, caption=f"📥 آخرین نسخه اکسل پروژه: {proj['name']}\nتاریخ: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            else:
                log_error(chat_id, f"Failed to export excel for project {pid}")
                bot.send_message(chat_id, "❌ خطا در تولید خروجی اکسل.")
            return

        # Unhandled callback fallback
        logger.warning(f"⚠️ Unhandled callback received: '{data}' from User {chat_id}")
        if TELEBOT_AVAILABLE and bot:
            bot.answer_callback_query(call.id, "دستور نامشخص یا منقضی‌شده", show_alert=False)

    except Exception as e:
        log_error(chat_id, f"Error processing callback '{data}': {e}", e)
        if TELEBOT_AVAILABLE and bot:
            try: bot.answer_callback_query(call.id, "خطا در پردازش عملیات", show_alert=True)
            except Exception: pass

# ==========================================
# Wizard Step Handlers
# ==========================================
def process_wiz_project_name(message):
    chat_id = message.chat.id
    name = message.text.strip()
    log_message(chat_id, name, "process_wiz_project_name")
    bot_state[chat_id] = {'name': name}
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🌱 دوره / کلاس آموزشی", callback_data="wiz_type_کلاس"),
        types.InlineKeyboardButton("🏛 همایش", callback_data="wiz_type_همایش"),
        types.InlineKeyboardButton("🏕 اردو", callback_data="wiz_type_اردو"),
        types.InlineKeyboardButton("🏴 مراسم / هیئت", callback_data="wiz_type_مراسم"),
        types.InlineKeyboardButton("🏢 عمومی", callback_data="wiz_type_عمومی"),
        types.InlineKeyboardButton("🔙 انصراف", callback_data="menu_switch_project")
    )
    bot.send_message(chat_id, f"نام پروژه: **{name}**\n\n**مرحله ۲:** لطفاً **نوع پروژه** را انتخاب فرمایید:", reply_markup=markup, parse_mode="Markdown")

def process_wiz_class_time(message):
    chat_id = message.chat.id
    if chat_id not in bot_state: bot_state[chat_id] = {}
    ctime = message.text.strip()
    log_message(chat_id, ctime, "process_wiz_class_time")
    if ":" not in ctime or len(ctime) > 5:
        msg = bot.send_message(chat_id, "❌ فرمت ساعت نامعتبر است. لطفاً دقیقاً مانند `16:00` یا `08:30` ارسال فرمایید:", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_wiz_class_time)
        return
    bot_state[chat_id]['activation_time'] = ctime
    
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        types.InlineKeyboardButton("۴ جلسه", callback_data="wiz_cnt_4"),
        types.InlineKeyboardButton("۸ جلسه", callback_data="wiz_cnt_8"),
        types.InlineKeyboardButton("۱۰ جلسه", callback_data="wiz_cnt_10"),
        types.InlineKeyboardButton("۱۲ جلسه", callback_data="wiz_cnt_12"),
        types.InlineKeyboardButton("۱۶ جلسه", callback_data="wiz_cnt_16"),
        types.InlineKeyboardButton("🔙 انصراف", callback_data="menu_switch_project")
    )
    msg = bot.send_message(chat_id, f"⏰ ساعت شروع: **{ctime}**\n\n**مرحله ۵:** لطفاً **تعداد جلسات کلاس** را انتخاب یا عدد آن را تایپ فرمایید:", reply_markup=markup, parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_wiz_class_count_text)

def process_wiz_class_count_text(message):
    chat_id = message.chat.id
    if chat_id not in bot_state: bot_state[chat_id] = {}
    txt = message.text.strip()
    log_message(chat_id, txt, "process_wiz_class_count_text")
    try:
        cnt = int(txt)
        bot_state[chat_id]['total_sessions'] = cnt
        finalize_smart_project_creation(chat_id)
    except ValueError:
        msg = bot.send_message(chat_id, "❌ لطفاً تعداد جلسات را فقط به صورت عدد (مثلاً `10`) ارسال فرمایید:")
        bot.register_next_step_handler(msg, process_wiz_class_count_text)

def process_wiz_start_date_text(message):
    chat_id = message.chat.id
    if chat_id not in bot_state: bot_state[chat_id] = {}
    txt = message.text.strip()
    log_message(chat_id, txt, "process_wiz_start_date_text")
    bot_state[chat_id]['start_date'] = txt
    ask_wiz_end_date(chat_id, txt)

def ask_wiz_end_date(chat_id, start_date):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("همان روز (۱ روزه)", callback_data="wiz_end_sameday"),
        types.InlineKeyboardButton("🔙 انصراف", callback_data="menu_switch_project")
    )
    msg = bot.send_message(chat_id, f"📅 تاریخ شروع: **{start_date}**\n\n**مرحله ۴:** لطفاً **تاریخ پایان رویداد** را وارد فرمایید:\n(یا اگر رویداد ۱ روزه است، دکمه «همان روز» را لمس فرمایید)", reply_markup=markup, parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_wiz_end_date_text)

def process_wiz_end_date_text(message):
    chat_id = message.chat.id
    if chat_id not in bot_state: bot_state[chat_id] = {}
    txt = message.text.strip()
    log_message(chat_id, txt, "process_wiz_end_date_text")
    bot_state[chat_id]['end_date'] = txt
    bot_state[chat_id]['total_sessions'] = 2  # default multi-day estimate
    ask_wiz_prep_day(chat_id)

def ask_wiz_prep_day(chat_id):
    st = bot_state.get(chat_id, {})
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ بله، دارد", callback_data="wiz_prep_1"),
        types.InlineKeyboardButton("❌ خیر، ندارد", callback_data="wiz_prep_0")
    )
    bot.send_message(chat_id, f"🛠 **آیا این پروژه نیاز به «روز آماده‌سازی» دارد؟**\n\nدر صورت انتخاب «بله»، شیت و جلسه اختصاصی `روز آماده سازی` به طور خودکار قبل از روزهای اصلی رویداد تعریف خواهد شد.", reply_markup=markup, parse_mode="Markdown")

def finalize_smart_project_creation(chat_id):
    st = bot_state.get(chat_id, {})
    name = st.get('name')
    ptype = st.get('project_type', 'عمومی')
    tot_sess = st.get('total_sessions', 1)
    rec_days = st.get('recurring_days', '')
    act_time = st.get('activation_time', '')
    sdate = st.get('start_date', '')
    edate = st.get('end_date', '')
    has_prep = st.get('has_prep_day', False)

    logger.info(f"Finalizing project creation for {chat_id}: name={name}, type={ptype}, sessions={tot_sess}, prep={has_prep}")
    try:
        new_pid, first_sid = project_manager.create_project_with_smart_sessions(
            name=name, project_type=ptype,
            total_sessions=tot_sess, recurring_days=rec_days, activation_time=act_time,
            start_date=sdate, end_date=edate, has_prep_day=has_prep
        )
        permission_manager.set_project_user(new_pid, chat_id, role="super_admin", is_active=1)
        permission_manager.set_user_context(chat_id, project_id=new_pid, session_id=first_sid)

        sessions = attendance_manager.list_sessions(new_pid)
        sess_names = "، ".join([s['name'] for s in sessions])

        summary_text = (
            f"✅ **پروژه جدید با موفقیت ایجاد گردید!**\n\n"
            f"🏢 نام پروژه: **{name}**\n"
            f"🏷 نوع: **{ptype}**\n"
        )
        if ptype == 'کلاس':
            summary_text += f"🗓 روز برگزاری: **{rec_days}** (ساعت: `{act_time}`)\n"
            summary_text += f"🔢 تعداد جلسات ایجاد شده: **{len(sessions)} جلسه**\n"
        else:
            summary_text += f"📅 بازه زمانی: از **{sdate or 'نامشخص'}** تا **{edate or 'نامشخص'}**\n"
            summary_text += f"🛠 روز آماده‌سازی: **{'دارد' if has_prep else 'ندارد'}**\n"
            summary_text += f"📋 روزها و جلسات ایجاد شده: **{sess_names}**\n"

        summary_text += "\nشما به عنوان مدیر ارشد این پروژه تنظیم شدید و جلسه اولیه فعال گردید."
        bot.send_message(chat_id, summary_text, parse_mode="Markdown")

        if chat_id in bot_state: del bot_state[chat_id]
        log_action(chat_id, new_pid, "Smart Project Created", f"Type: {ptype}, Sessions: {len(sessions)}")
        show_project_menu(chat_id, new_pid, first_sid)

    except Exception as e:
        log_error(chat_id, f"Error in finalize_smart_project_creation: {e}", e)
        bot.send_message(chat_id, f"❌ خطا در ایجاد هوشمند پروژه: {e}",
                         reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="menu_switch_project")))

def process_global_add_user_id(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'global_add_user': return
    txt = message.text.strip()
    log_message(chat_id, txt, "process_global_add_user_id")
    from utils import normalize_persian
    norm_txt = normalize_persian(txt)
    if not norm_txt.isdigit():
        msg = bot.send_message(chat_id, "❌ لطفاً شناسه عددی (Chat ID) را فقط به صورت عدد ارسال فرمایید:")
        bot.register_next_step_handler(msg, process_global_add_user_id)
        return
    t_uid = int(norm_txt)
    state['uid'] = t_uid
    msg = bot.send_message(chat_id, f"شناسه عددی: `{t_uid}`\n\n**مرحله ۲:** لطفاً **نام و نام خانوادگی** نیرو را ارسال فرمایید:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_global_add_user_name)

def process_global_add_user_name(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'global_add_user': return
    name = message.text.strip()
    log_message(chat_id, name, "process_global_add_user_name")
    state['name'] = name
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("👩🏻 خانم", callback_data="guser_gen_خانم"),
        types.InlineKeyboardButton("👨🏻 آقا", callback_data="guser_gen_آقا")
    )
    bot.send_message(chat_id, f"نیرو: **{name}**\n\n**مرحله ۳:** لطفاً **جنسیت** نیرو را مشخص فرمایید:\n_(تعیین‌کننده تفکیک دسترسی اپراتورها به لیست خواهران یا برادران)_", reply_markup=markup, parse_mode="Markdown")

# ==========================================
# Other Operational Step Handlers
# ==========================================
def show_user_profile(chat_id, project_id, session_id, staff_id, message_id=None):
    rec = attendance_manager.get_staff_session_attendance(project_id, session_id, staff_id)
    if not rec:
        if TELEBOT_AVAILABLE and bot: bot.send_message(chat_id, "❌ اطلاعات نیرو یافت نشد.")
        return

    role, _, _, _, _ = permission_manager.get_user_project_role(project_id, chat_id)
    text = (
        f"👤 **نام:** {rec.get('name', '-')}\n"
        f"🏢 **واحد:** {rec.get('unit', '-')}\n"
        f"🗂 **بخش:** {rec.get('section', '-')}\n"
        f"🎯 **سمت:** {rec.get('position', '-')}\n"
        f"📱 **شماره:** `{rec.get('phone', '-')}`\n"
        f"🏷 **عنوان کارت:** {rec.get('card_title', '-')}\n"
        f"⏰ **ساعت شیفت:** {rec.get('shift_time', '-')}\n"
        f"📊 **وضعیت حضور:** {rec.get('status', '-')}\n"
        f"💳 **وضعیت کارت:** {rec.get('card_status', '-')}\n"
        f"📝 **توضیحات:** {rec.get('description', '-')}\n"
        f"📞 **نتیجه پیگیری:** {rec.get('late_tracking', '-')}"
    )

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ حاضر", callback_data=f"act_status_{staff_id}_حاضر"),
        types.InlineKeyboardButton("❌ غایب", callback_data=f"act_status_{staff_id}_غایب"),
        types.InlineKeyboardButton("💳 تحویل کارت", callback_data=f"act_card_{staff_id}_تحویل داده شد"),
        types.InlineKeyboardButton("🔄 پس گرفتن کارت", callback_data=f"act_card_{staff_id}_پس گرفته شد")
    )
    markup.add(types.InlineKeyboardButton("📞 ثبت تماس / پیگیری", callback_data=f"tracklate_{staff_id}"))

    if role in ("admin", "super_admin"):
        markup.add(
            types.InlineKeyboardButton("⚪️ بدون شیفت", callback_data=f"act_status_{staff_id}_بدون شیفت"),
            types.InlineKeyboardButton("⚪️ بدون کارت", callback_data=f"act_card_{staff_id}_بدون کارت")
        )
        markup.add(
            types.InlineKeyboardButton("🧹 پاک کردن حضور", callback_data=f"act_status_{staff_id}_clear"),
            types.InlineKeyboardButton("🧹 پاک کردن کارت", callback_data=f"act_card_{staff_id}_clear")
        )

    markup.add(types.InlineKeyboardButton("📝 ویرایش توضیحات", callback_data=f"editdesc_{staff_id}"))
    markup.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="start_menu"))

    if TELEBOT_AVAILABLE and bot:
        if message_id:
            try:
                bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode="Markdown")
                return
            except Exception: pass
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

def render_unit_page(chat_id, project_id, session_id, unit, page, message_id=None):
    all_att = attendance_manager.get_session_attendance(project_id, session_id)
    unit_members = [m for m in all_att if m.get('unit') == unit and 'بدون شیفت' not in str(m.get('status', ''))]
    filtered_members = permission_manager.apply_gender_filter(unit_members, chat_id, project_id)

    items_per_page = 8
    total_pages = max(1, (len(filtered_members) - 1) // items_per_page + 1)
    current_items = filtered_members[page * items_per_page:(page + 1) * items_per_page]

    markup = types.InlineKeyboardMarkup(row_width=1)
    for m in current_items:
        emojis = get_user_emojis(m.get('status'), m.get('card_status'))
        btn_text = f"👤 {m['name']} ({m.get('section', '-')}) | {emojis}"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"user_{m['staff_id']}"))

    nav_btns = []
    if page > 0: nav_btns.append(types.InlineKeyboardButton("◀️ قبلی", callback_data=f"up_{page-1}:{unit}"))
    if page < total_pages - 1: nav_btns.append(types.InlineKeyboardButton("بعدی ▶️", callback_data=f"up_{page+1}:{unit}"))
    if nav_btns: markup.add(*nav_btns)
    markup.add(types.InlineKeyboardButton("🔙 بازگشت به واحدها", callback_data="menu_units"), types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="start_menu"))

    text = f"🏢 **نیروهای واحد: {unit}**\n📄 صفحه {page+1} از {total_pages}"
    if TELEBOT_AVAILABLE and bot:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode="Markdown")

def process_search(message, project_id, session_id):
    chat_id = message.chat.id
    q = message.text.strip()
    log_message(chat_id, q, "process_search")
    all_att = attendance_manager.get_session_attendance(project_id, session_id)
    filtered = permission_manager.apply_gender_filter(all_att, chat_id, project_id)
    clean_q = normalize_persian(q)
    results = []
    for s in filtered:
        if (clean_q in normalize_persian(s.get('name', '')) or
            clean_q in normalize_persian(str(s.get('phone', ''))) or
            clean_q in normalize_persian(s.get('unit', '')) or
            clean_q in normalize_persian(s.get('section', '')) or
            clean_q in normalize_persian(s.get('card_title', ''))):
            results.append(s)

    if not results:
        bot.send_message(chat_id, "❌ هیچ فردی با مشخصات وارد شده یافت نشد.",
                         reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔍 جستجوی مجدد", callback_data="menu_search"), types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="start_menu")))
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for s in results[:10]:
        emojis = get_user_emojis(s.get('status'), s.get('card_status'))
        markup.add(types.InlineKeyboardButton(f"👤 {s['name']} ({s.get('section', '-')}) | {emojis}", callback_data=f"user_{s['staff_id']}"))

    markup.add(types.InlineKeyboardButton("🔍 جستجوی مجدد", callback_data="menu_search"), types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="start_menu"))
    bot.send_message(chat_id, f"🔍 **{len(results)} نتیجه یافت شد:**", reply_markup=markup, parse_mode="Markdown")

def process_track_late(message):
    chat_id = message.chat.id
    log_message(chat_id, message.text, "process_track_late")
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'track_late': return
    pid = state['pid']; sid = state['sid']; staff_id = state['staff_id']
    ok = attendance_manager.add_late_tracking(pid, sid, staff_id, message.text, actor_user_id=chat_id)
    if not ok:
        bot.send_message(chat_id, "⚠️ شما اجازه ثبت پیگیری برای این نیرو را ندارید.")
        return
    report_manager.log_staff_action(pid, chat_id, 'call')
    log_action(chat_id, pid, "Add Late Tracking", f"Staff: {staff_id}, Note: {message.text}")
    bot.send_message(chat_id, "✅ نتیجه پیگیری ثبت گردید.")
    show_user_profile(chat_id, pid, sid, staff_id)
    del bot_state[chat_id]

def process_edit_desc(message):
    chat_id = message.chat.id
    log_message(chat_id, message.text, "process_edit_desc")
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'edit_desc': return
    pid = state['pid']; sid = state['sid']; staff_id = state['staff_id']
    ok = attendance_manager.add_description(pid, sid, staff_id, message.text, actor_user_id=chat_id)
    if not ok:
        bot.send_message(chat_id, "⚠️ شما اجازه ویرایش توضیحات این نیرو را ندارید.")
        return
    log_action(chat_id, pid, "Edit Description", f"Staff: {staff_id}, Desc: {message.text}")
    bot.send_message(chat_id, "✅ توضیحات با موفقیت ثبت شد.")
    show_user_profile(chat_id, pid, sid, staff_id)
    del bot_state[chat_id]

def show_session_details(chat_id, project_id, session_id, message_id=None):
    sess = attendance_manager.get_session(session_id)
    if not sess:
        if TELEBOT_AVAILABLE and bot: bot.send_message(chat_id, "❌ جلسه یافت نشد.")
        return

    is_canc = sess.get('status') == 'CANCELLED'
    status_fa = "❌ لغوشده (در حضور و غیاب محاسبه نمی‌شود)" if is_canc else "🟢 فعال در جریان کاری"
    date_fa = sess.get('session_date') or "تعیین نشده"
    time_fa = sess.get('time_str') or "تعیین نشده"
    day_fa = sess.get('day_of_week') or "تعیین نشده"

    sname_safe = safe_markdown(sess['name'])
    text = (
        f"🗓 **جزییات و ویرایش جلسه: {sname_safe}**\n\n"
        f"🏷 نام جلسه: **{sname_safe}**\n"
        f"📅 تاریخ: `{date_fa}`\n"
        f"⏰ ساعت شروع: `{time_fa}`\n"
        f"🗓 روز هفته: **{safe_markdown(day_fa)}**\n"
        f"📊 وضعیت: **{status_fa}**\n\n"
        f"جهت ویرایش هر بخش، گزینه مورد نظر را انتخاب فرمایید:"
    )
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✏️ ویرایش نام", callback_data=f"editsess_name_{session_id}"),
        types.InlineKeyboardButton("📅 ویرایش تاریخ", callback_data=f"editsess_date_{session_id}"),
        types.InlineKeyboardButton("⏰ ویرایش ساعت", callback_data=f"editsess_time_{session_id}"),
        types.InlineKeyboardButton("🗓 ویرایش روز هفته", callback_data=f"editsess_day_{session_id}")
    )
    toggle_txt = "🟢 فعال‌سازی مجدد جلسه" if is_canc else "❌ لغو این جلسه (تعطیلی)"
    markup.add(types.InlineKeyboardButton(toggle_txt, callback_data=f"togglesess_{session_id}"))
    markup.add(
        types.InlineKeyboardButton("🔙 بازگشت به لیست جلسات", callback_data="menu_manage_sessions"),
        types.InlineKeyboardButton("🏠 منوی اصلی پروژه", callback_data="start_menu")
    )
    if TELEBOT_AVAILABLE and bot:
        if message_id:
            try:
                bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode="Markdown")
                return
            except Exception:
                try:
                    bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)
                    return
                except Exception:
                    try:
                        bot.edit_message_reply_markup(chat_id, message_id, reply_markup=markup)
                        return
                    except Exception:
                        pass
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

def process_edit_session_field(message, session_id, field_name):
    chat_id = message.chat.id
    val = message.text.strip()
    log_message(chat_id, val, f"process_edit_session_field_{field_name}")
    pid, _ = get_user_active_project_and_session(chat_id)
    attendance_manager.update_session_field(session_id, field_name, val)
    log_action(chat_id, pid, f"Edit Session {field_name}", f"ID: {session_id}, Val: {val}")
    bot.send_message(chat_id, "✅ مقدار جدید با موفقیت ذخیره شد.")
    show_session_details(chat_id, pid, session_id)

def process_add_session_name(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'add_session': return
    pid = state['pid']
    name = message.text.strip()
    log_message(chat_id, name, "process_add_session_name")
    sid = attendance_manager.create_session(pid, name)
    permission_manager.set_user_context(chat_id, project_id=pid, session_id=sid)
    log_action(chat_id, pid, "Create Session", f"Session: {name} (ID: {sid})")
    bot.send_message(chat_id, f"✅ جلسه «{name}» ایجاد و فعال شد.",
                     reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="start_menu")))
    del bot_state[chat_id]

def process_add_staff_name(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'add_staff': return
    name = message.text.strip()
    log_message(chat_id, name, "process_add_staff_name")
    state['name'] = name
    pid = state['pid']
    chart = project_manager.get_project_org_chart(pid)
    units = sorted(list(chart.keys()))
    if not units:
        all_s = staff_manager.list_staff(pid)
        units = sorted(list(set(s['unit'] for s in all_s if s.get('unit'))))
    markup = types.InlineKeyboardMarkup(row_width=2)
    for u in units: markup.add(types.InlineKeyboardButton(u, callback_data=f"addu_unit_{u}"))
    markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="start_menu"))
    bot.send_message(chat_id, f"نیرو: **{name}**\n\n**مرحله ۲:** واحد مربوطه را انتخاب فرمایید:", reply_markup=markup, parse_mode="Markdown")

def process_add_staff_phone(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'add_staff': return
    phone = message.text.strip()
    log_message(chat_id, phone, "process_add_staff_phone")
    state['phone'] = phone
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("👨🏻‍🦱 آقا", callback_data="addu_gen_آقا"),
               types.InlineKeyboardButton("👩🏻 خانم", callback_data="addu_gen_خانم"))
    bot.send_message(chat_id, "**مرحله ۵:** جنسیت نیرو را مشخص فرمایید:", reply_markup=markup)

def finalize_add_staff(chat_id, gender):
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'add_staff': return
    pid = state['pid']; sid = state['sid']
    staff_id = staff_manager.add_staff_member(pid, state['name'], state['phone'], state['unit'], state['section'], gender=gender, notes="حین پروژه اضافه شده")
    attendance_manager.update_attendance_status(pid, sid, staff_id, "حاضر")
    log_action(chat_id, pid, "Add Staff", f"Name: {state['name']}, Unit: {state['unit']}, Sec: {state['section']}")
    bot.send_message(chat_id, f"✅ نیروی جدید با موفقیت ثبت شد و حضور ایشان برای جلسه فعال گردید.",
                     reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="start_menu")))
    del bot_state[chat_id]

def process_suggest_shrt_name(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'suggest_shrt': return
    state['candidate_name'] = message.text.strip()
    log_message(chat_id, message.text, "process_suggest_shrt_name")
    msg = bot.send_message(chat_id, "شماره تماس نیروی پیشنهادی را ارسال فرمایید:")
    bot.register_next_step_handler(msg, process_suggest_shrt_phone)

def process_suggest_shrt_phone(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'suggest_shrt': return
    phone = message.text.strip()
    log_message(chat_id, phone, "process_suggest_shrt_phone")
    sh_id = state['shrt_id']
    name = state['candidate_name']
    shortage_manager.suggest_shortage(sh_id, name, phone)
    log_action(chat_id, None, "Suggest Shortage Candidate", f"ShortageID: {sh_id}, Candidate: {name}, Phone: {phone}")

    bot.send_message(chat_id, f"✅ مشخصات نیروی پیشنهادی ثبت شد و برای ادمین‌های پروژه ارسال گردید.",
                     reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="start_menu")))

    sh = shortage_manager.get_shortage(sh_id)
    suggester = permission_manager.get_user(chat_id)
    suggester_name = (suggester.get('staff_name') or f"کاربر {chat_id}") if suggester else f"کاربر {chat_id}"

    if sh:
        admin_msg = (
            f"🔔 **پیشنهاد نیروی جدید برای تأمین کمبود**\n\n"
            f"🏢 پروژه: **{sh.get('project_name', '')}**\n"
            f"📍 واحد: **{sh['unit']}** | بخش: **{sh['section']}**\n"
            f"👥 جنسیت مورد نیاز: {sh.get('target_group', 'عمومی')}\n"
            f"📝 توضیحات کمبود: {sh.get('description') or 'ندارد'}\n\n"
            f"👤 **مشخصات نیروی پیشنهادی:**\n"
            f"نام و نام خانوادگی: **{name}**\n"
            f"شماره تماس: `{phone}`\n"
            f"🗣 معرفی‌کننده: {suggester_name} (`{chat_id}`)\n\n"
            f"جهت بررسی و تعیین وضعیت انتخاب فرمایید:"
        )
        admin_markup = types.InlineKeyboardMarkup(row_width=2)
        admin_markup.add(
            types.InlineKeyboardButton("✅ تایید و جذب به کادر", callback_data=f"apprshrt_{sh_id}_{chat_id}"),
            types.InlineKeyboardButton("❌ رد پیشنهاد", callback_data=f"rejshrt_{sh_id}_{chat_id}")
        )
        members = permission_manager.get_project_members(sh['project_id'])
        for m in members:
            if m['is_active'] and m['role'] in ('admin', 'super_admin'):
                try:
                    bot.send_message(m['user_id'], admin_msg, reply_markup=admin_markup, parse_mode="Markdown")
                except Exception:
                    pass

    del bot_state[chat_id]

def process_shortage_section(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'create_shortage': return
    state['section'] = message.text.strip()
    log_message(chat_id, message.text, "process_shortage_section")
    msg = bot.send_message(chat_id, "تعداد نیروی مورد نیاز را وارد نمایید (مثلاً: `2`):", parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_shortage_count)

def process_shortage_count(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'create_shortage': return
    log_message(chat_id, message.text, "process_shortage_count")
    try:
        cnt = int(message.text.strip())
        state['count'] = cnt
        msg = bot.send_message(chat_id, "توضیحات و ویژگی‌های نیروی مورد نیاز را وارد فرمایید:")
        bot.register_next_step_handler(msg, process_shortage_desc)
    except ValueError:
        msg = bot.send_message(chat_id, "لطفاً فقط عدد وارد نمایید:")
        bot.register_next_step_handler(msg, process_shortage_count)

def process_shortage_desc(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'create_shortage': return
    state['desc'] = message.text.strip()
    log_message(chat_id, message.text, "process_shortage_desc")
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("👨🏻‍🦱 آقا", callback_data="shrt_grp_آقا"),
               types.InlineKeyboardButton("👩🏻 خانم", callback_data="shrt_grp_خانم"))
    bot.send_message(chat_id, "جنسیت مورد نیاز را انتخاب فرمایید:", reply_markup=markup)

def process_group_shift_time(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'group_shift': return
    new_time = message.text.strip()
    log_message(chat_id, new_time, "process_group_shift_time")
    if ":" not in new_time or len(new_time) > 5:
        msg = bot.send_message(chat_id, "❌ فرمت ساعت اشتباه است. لطفاً مانند `08:30` ارسال فرمایید:", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_group_shift_time)
        return
    pid = state['pid']; u = state['unit']; s = state['section']
    staff_manager.update_section_shift_time(pid, u, s, new_time)
    log_action(chat_id, pid, "Group Shift Updated", f"Unit: {u}, Section: {s}, Time: {new_time}")
    bot.send_message(chat_id, f"✅ ساعت شیفت تمام نیروهای بخش **{s}** در واحد **{u}** به `{new_time}` تغییر یافت.",
                     reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="start_menu")), parse_mode="Markdown")
    del bot_state[chat_id]

def process_perm_new_user(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'add_perm_user': return
    txt = message.text.strip()
    log_message(chat_id, txt, "process_perm_new_user")
    from utils import normalize_persian
    norm_txt = normalize_persian(txt)
    if not norm_txt.isdigit():
        msg = bot.send_message(chat_id, "❌ لطفاً شناسه عددی (Chat ID) بله را فقط به صورت عدد وارد فرمایید:")
        bot.register_next_step_handler(msg, process_perm_new_user)
        return
    t_uid = int(norm_txt)
    state['uid'] = t_uid
    pid = state['pid']
    t_user = permission_manager.get_user(t_uid)
    if t_user:
        state['name'] = t_user.get('staff_name') or f"کاربر {t_uid}"
        state['gender'] = t_user.get('gender', 'خانم')
        ask_perm_role(chat_id, pid, state['name'], state['gender'])
    else:
        msg = bot.send_message(chat_id, f"شناسه عددی: `{t_uid}`\n\n**مرحله ۲:** لطفاً **نام و نام خانوادگی** نیرو را ارسال فرمایید:", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_perm_new_user_name)

def process_perm_new_user_name(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'add_perm_user': return
    name = message.text.strip()
    log_message(chat_id, name, "process_perm_new_user_name")
    state['name'] = name
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("👩🏻 خانم", callback_data="perm_gen_خانم"),
        types.InlineKeyboardButton("👨🏻 آقا", callback_data="perm_gen_آقا")
    )
    bot.send_message(chat_id, f"نیرو: **{name}**\n\n**مرحله ۳:** لطفاً **جنسیت** نیرو را مشخص فرمایید:\n_(اپراتورهای خانم فقط کادر خانم و اپراتورهای آقا فقط کادر آقا را مشاهده خواهند کرد)_", reply_markup=markup, parse_mode="Markdown")

def ask_perm_role(chat_id, pid, name, gender):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🛡 مدیر پروژه (Admin)", callback_data="perm_setrole_admin"),
        types.InlineKeyboardButton("🎖 مسئول واحد (Unit Head)", callback_data="perm_setrole_unithead"),
        types.InlineKeyboardButton("👤 اپراتور کادر (Operator)", callback_data="perm_setrole_operator"),
        types.InlineKeyboardButton("🔙 انصراف", callback_data="menu_permissions")
    )
    bot.send_message(chat_id, f"نیرو: **{name}** ({gender})\n\n**مرحله ۴:** لطفاً **نقش این نیرو در پروژه** را انتخاب فرمایید:", reply_markup=markup, parse_mode="Markdown")

def process_update_excel_file(message, project_id):
    chat_id = message.chat.id
    if not message.document:
        bot.send_message(chat_id, "❌ فایل دریافت نشد. عملیات لغو شد.")
        return
    fname = message.document.file_name
    log_message(chat_id, f"Uploaded document: {fname}", "process_update_excel_file")
    if fname.lower().endswith('.xls') and not fname.lower().endswith('.xlsx'):
        bot.send_message(chat_id, "⚠️ فرمت قدیمی `.xls` پشتیبانی نمی‌شود.\nلطفاً فایل را در نرم‌افزار اکسل با فرمت **.xlsx** ذخیره کرده و ارسال فرمایید.", parse_mode="Markdown")
        return
    if not fname.lower().endswith('.xlsx'):
        bot.send_message(chat_id, "❌ فقط فایل‌های با فرمت .xlsx مجاز هستند.")
        return
    bot.send_message(chat_id, "⏳ در حال دریافت فایل اکسل و به‌روزرسانی اطلاعات پروژه...")
    try:
        f_info = bot.get_file(message.document.file_id)
        f_url = f"{BALE_FILE_URL.format(BOT_TOKEN, f_info.file_path)}"
        resp = requests.get(f_url, timeout=(10, 60))
        resp.raise_for_status()
        temp_p = os.path.join(excel_manager.get_project_excel_path(project_id) + ".incoming")
        with open(temp_p, "wb") as f: f.write(resp.content)
        if excel_manager.import_project_excel(project_id, temp_p):
            if os.path.exists(temp_p): os.remove(temp_p)
            log_action(chat_id, project_id, "Update Excel Sync", f"File: {fname}")
            bot.send_message(chat_id, "✅ اطلاعات پروژه با موفقیت از فایل اکسل همگام‌سازی شد.",
                             reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="start_menu")))
        else:
            log_error(chat_id, f"Failed to validate excel file {fname}")
            bot.send_message(chat_id, "❌ خطا در اعتبارسنجی و خواندن اطلاعات اکسل.")
    except requests.exceptions.Timeout:
        bot.send_message(chat_id, "❌ مهلت دریافت فایل از سرور بله به پایان رسید (Timeout).\nلطفاً از اتصال اینترنت اطمینان حاصل کرده و مجدداً فایل را ارسال فرمایید.")
    except requests.exceptions.RequestException as req_err:
        bot.send_message(chat_id, f"❌ خطا در دانلود فایل از سرور پیام‌رسان بله: {req_err}\nلطفاً چند لحظه بعد دوباره تلاش فرمایید.")
    except ValueError as ve:
        bot.send_message(chat_id, f"⚠️ {ve}")
    except Exception as e:
        log_error(chat_id, f"Error processing excel upload: {e}", e)
        bot.send_message(chat_id, f"❌ خطا در پردازش فایل: {e}\n(تراکنش لغو شد و اطلاعات قبلی دست‌نخورده باقی ماند)")

# Register handlers
if TELEBOT_AVAILABLE and bot:
    bot.message_handler(commands=['start'])(send_welcome)
    bot.callback_query_handler(func=lambda call: True)(handle_callbacks)

def start_bot():
    if not TELEBOT_AVAILABLE or not bot:
        print("❌ کتابخانه pyTelegramBotAPI نصب نشده است.")
        print("لطفاً با دستور زیر پیش‌نیازها را نصب فرمایید: pip install -r requirements.txt")
        return

    print("=" * 65)
    print("🤖 سامانه مدیریت کادر و حضور و غیاب (نسخه جدید چندپروژه‌ای)")
    print("📋 لاگر پیشرفته (کنسول و فایل logs/bot.log) فعال شد.")
    print("✅ در حال شروع ورکر‌های پس‌زمینه (یادآوری تأخیر، گزارش شبانه، بکاپ)...")
    worker_manager.set_bot(bot)
    worker_manager.start_workers()
    print("🚀 ربات بله با موفقیت آماده به کار شد و در حال شنود پیام‌هاست...")
    print("=" * 65)

    try:
        bot.infinity_polling(timeout=20, long_polling_timeout=15)
    except Exception as e:
        logger.exception(f"Fatal Bot Error: {e}")
        print(f"❌ خطای ربات: {e}")

# Main moved to EOF


def process_edit_project_name(message, project_id):
    chat_id = message.chat.id
    val = message.text.strip()
    log_message(chat_id, val, "process_edit_project_name")
    project_manager.update_project_details(project_id, name=val)
    log_action(chat_id, project_id, "Edit Project Name", f"New Name: {val}")
    bot.send_message(chat_id, f"✅ نام پروژه با موفقیت به «{val}» تغییر یافت.",
                     reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⚙️ مدیریت پروژه", callback_data="menu_manage_project")))

def process_edit_project_desc(message, project_id):
    chat_id = message.chat.id
    val = message.text.strip()
    log_message(chat_id, val, "process_edit_project_desc")
    project_manager.update_project_details(project_id, description=val)
    log_action(chat_id, project_id, "Edit Project Description", f"Desc: {val}")
    bot.send_message(chat_id, "✅ توضیحات پروژه با موفقیت ذخیره شد.",
                     reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⚙️ مدیریت پروژه", callback_data="menu_manage_project")))


def process_uh_manual_section(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'uh_shortage': return
    sec = message.text.strip()
    log_message(chat_id, sec, "process_uh_manual_section")
    state['section'] = sec
    msg = bot.send_message(chat_id, f"🗂 بخش: **{sec}**\n\nتعداد نیروی مورد نیاز را وارد فرمایید (مثلاً: `2`):", parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_uh_shortage_count)

def process_uh_shortage_count(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'uh_shortage': return
    txt = message.text.strip()
    log_message(chat_id, txt, "process_uh_shortage_count")
    from utils import normalize_persian
    cnt = normalize_persian(txt)
    if not cnt.isdigit() or int(cnt) <= 0:
        msg = bot.send_message(chat_id, "❌ لطفاً فقط یک عدد معتبر (مثلاً `2`) وارد فرمایید:")
        bot.register_next_step_handler(msg, process_uh_shortage_count)
        return
    state['count'] = int(cnt)
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        types.InlineKeyboardButton("👩🏻 خانم", callback_data="uh_grp_خانم"),
        types.InlineKeyboardButton("👨🏻 آقا", callback_data="uh_grp_آقا"),
        types.InlineKeyboardButton("👥 فرقی ندارد", callback_data="uh_grp_عمومی")
    )
    bot.send_message(chat_id, f"تعداد: **{cnt} نفر**\n\nلطفاً جنسیت مورد نیاز را انتخاب فرمایید:", reply_markup=markup, parse_mode="Markdown")

def process_uh_shortage_desc(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'uh_shortage': return
    desc = message.text.strip()
    log_message(chat_id, desc, "process_uh_shortage_desc")
    pid = state['pid']
    unit = state['unit']
    section = state['section']
    count_val = state['count']
    target_grp = state['target_group']

    sh_id = shortage_manager.add_unit_head_shortage(pid, unit, section, count_val, target_grp, desc, requested_by=chat_id)
    log_action(chat_id, pid, "Unit Head Shortage Request", f"Unit: {unit}, Sec: {section}, Count: {count_val}")

    bot.send_message(chat_id, f"✅ درخواست کمبود **{count_val} نفر** در واحد **{unit}** (بخش {section}) ثبت گردید و جهت بررسی و تایید به مدیران پروژه ارسال شد.",
                     reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="start_menu")), parse_mode="Markdown")

    # Send Notification to Project Admins
    proj = project_manager.get_project(pid)
    pname = proj['name'] if proj else ""
    user = permission_manager.get_user(chat_id)
    uh_name = user.get('staff_name') or f"کاربر {chat_id}"

    admin_msg = (
        f"🔔 **درخواست اعلام کمبود نیرو از طرف مسئول واحد**\n\n"
        f"🏢 پروژه: **{pname}**\n"
        f"👤 مسئول واحد: **{uh_name}** (واحد **{unit}**)\n"
        f"🗂 بخش: **{section}**\n"
        f"👥 تعداد درخواستی: **{count_val} نفر** ({target_grp})\n"
        f"📝 توضیحات: {desc}\n\n"
        f"جهت بررسی و تعیین وضعیت انتخاب فرمایید:"
    )
    admin_markup = types.InlineKeyboardMarkup(row_width=2)
    admin_markup.add(
        types.InlineKeyboardButton("✅ تایید و ارسال عمومی برای کادر", callback_data=f"appr_uh_shrt_{sh_id}_{chat_id}"),
        types.InlineKeyboardButton("✏️ اصلاح تعداد", callback_data=f"edit_uh_count_{sh_id}_{chat_id}"),
        types.InlineKeyboardButton("❌ رد درخواست", callback_data=f"rej_uh_shrt_{sh_id}_{chat_id}")
    )
    # Fetch ALL project managers, project leads, and global super admins
    admins = permission_manager.get_project_admins(pid, include_global=True)
    logger.info(f"Broadcasting unit head shortage {sh_id} to {len(admins)} project managers/leads: {[a['user_id'] for a in admins]}")
    for a in admins:
        # Never notify unit heads
        if a.get('role') == 'unit_head' or a['user_id'] == chat_id:
            continue
        try:
            bot.send_message(a['user_id'], admin_msg, reply_markup=admin_markup, parse_mode="Markdown")
            logger.info(f"✅ Notified project lead/admin {a['user_id']} ({a.get('staff_name')}) of shortage {sh_id}")
        except Exception as ex:
            logger.warning(f"Failed to send shortage notification to admin {a['user_id']}: {ex}")

    del bot_state[chat_id]

def process_admin_edit_uh_count(message, shortage_id, uh_id):
    chat_id = message.chat.id
    txt = message.text.strip()
    from utils import normalize_persian
    cnt = normalize_persian(txt)
    if not cnt.isdigit() or int(cnt) <= 0:
        msg = bot.send_message(chat_id, "❌ لطفاً فقط یک عدد معتبر وارد فرمایید:")
        bot.register_next_step_handler(msg, process_admin_edit_uh_count, shortage_id, uh_id)
        return

    new_count = int(cnt)
    shortage_manager.update_shortage_count(shortage_id, new_count)
    shortage_manager.approve_unit_head_shortage(shortage_id)
    sh = shortage_manager.get_shortage(shortage_id)
    bot.send_message(chat_id, f"✅ تعداد به **{new_count} نفر** اصلاح گردید و پیام فراخوان کمبود برای کادر برودکست شد.", parse_mode="Markdown")

    # Broadcast
    if sh:
        proj = project_manager.get_project(sh['project_id'])
        pname = proj['name'] if proj else ""
        grp = sh.get('target_group', 'عمومی')
        broadcast_msg = (
            f"🔔 **اعلام کمبود نیرو - {pname}**\n\n"
            f"🏢 واحد: **{sh['unit']}**\n"
            f"🗂 بخش: **{sh['section']}**\n"
            f"👥 تعداد: **{new_count} نفر** ({grp})\n"
            f"📝 توضیحات: {sh.get('description') or 'ندارد'}\n\n"
            f"لطفاً در صورت شناخت فرد مناسب، جهت تأمین کمبود از منوی ربات اقدام فرمایید."
        )
        members = permission_manager.get_project_members(sh['project_id'])
        for m in members:
            # Strictly exclude unit heads from shortage broadcast announcements
            if m['is_active'] and m['role'] != 'unit_head' and (m['role'] in ('admin', 'super_admin') or (grp == 'عمومی' or m['project_gender'] == grp)):
                try:
                    bot.send_message(m['user_id'], broadcast_msg, parse_mode="Markdown")
                except Exception: pass

        if uh_id:
            try:
                bot.send_message(uh_id, f"🎉 درخواست کمبود نیروی شما برای واحد **{sh['unit']}** (بخش {sh['section']}) پس از اصلاح تعداد به **{new_count} نفر** توسط مدیر پروژه تایید شد و برای کادر ارسال گردید.", parse_mode="Markdown")
            except Exception: pass


# ------------------------------------------------------------------
# Project-Level Unit Head Management (مسئولین واحد با دسترسی محدود)
# ------------------------------------------------------------------
def process_proj_add_uh_uid(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'add_project_uh': return
    txt = message.text.strip()
    log_message(chat_id, txt, "process_proj_add_uh_uid")
    from utils import normalize_persian
    norm_txt = normalize_persian(txt)
    if not norm_txt.isdigit():
        msg = bot.send_message(chat_id, "❌ لطفاً شناسه عددی (Chat ID) بله را به صورت رقم لاتین یا فارسی وارد فرمایید:")
        bot.register_next_step_handler(msg, process_proj_add_uh_uid)
        return

    t_uid = int(norm_txt)
    state['uid'] = t_uid
    pid = state['pid']

    t_user = permission_manager.get_user(t_uid)
    if t_user:
        state['staff_name'] = t_user.get('staff_name') or f"کاربر {t_uid}"
        state['gender'] = t_user.get('gender', 'خانم')
        ask_proj_uh_unit(chat_id, pid, state['staff_name'])
    else:
        msg = bot.send_message(chat_id, f"شناسه: `{t_uid}`\n\nلطفاً **نام و نام خانوادگی** مسئول واحد را ارسال فرمایید:", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_proj_add_uh_name)

def process_proj_add_uh_name(message):
    chat_id = message.chat.id
    state = bot_state.get(chat_id)
    if not state or state.get('type') != 'add_project_uh': return
    name = message.text.strip()
    log_message(chat_id, name, "process_proj_add_uh_name")
    state['staff_name'] = name
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("👩🏻 خانم", callback_data="proj_uh_gen_خانم"),
        types.InlineKeyboardButton("👨🏻 آقا", callback_data="proj_uh_gen_آقا")
    )
    bot.send_message(chat_id, f"مسئول واحد: **{name}**\n\nلطفاً **جنسیت** ایشان را انتخاب فرمایید:", reply_markup=markup, parse_mode="Markdown")

def ask_proj_uh_unit(chat_id, pid, staff_name):
    chart = project_manager.get_project_org_chart(pid)
    units = sorted(list(chart.keys()))
    if not units:
        all_s = staff_manager.list_staff(pid)
        units = sorted(list(set(s['unit'] for s in all_s if s.get('unit'))))
    markup = types.InlineKeyboardMarkup(row_width=2)
    for u in units:
        markup.add(types.InlineKeyboardButton(f"🏢 {u}", callback_data=f"proj_uh_setunit_{u}"))
    markup.add(types.InlineKeyboardButton("🔙 بازگشت به دسترسی‌ها", callback_data="menu_permissions"))
    bot.send_message(chat_id, f"مسئول واحد: **{staff_name}**\n\nمسئولیت کدام واحد این پروژه به ایشان سپرده شود؟", reply_markup=markup, parse_mode="Markdown")


# ==================================================================
# Entry Point - Must strictly be the last lines of bot.py
# ==================================================================
if __name__ == '__main__':
    logger.info("==================================================================")
    logger.info("🚀 ATTENDANCE BOT V2 - RUNNING POLLING WITH FULL SCOPES")
    logger.info("==================================================================")
    start_bot()
