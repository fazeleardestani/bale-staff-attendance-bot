import os
import sys
import time
import threading
import logging
from config import (
    DB_BACKUP_INTERVAL, LATE_REMINDER_INTERVAL, SHORTAGE_CHECK_INTERVAL,
    DAILY_REPORT_HOUR, DAILY_REPORT_MINUTE
)
from db_manager import db_instance
from project_manager import project_manager
from attendance_manager import attendance_manager
from shortage_manager import shortage_manager
from permission_manager import permission_manager
from report_manager import report_manager
from excel_manager import excel_manager
from utils import (
    get_iran_now, get_today_jalali_str, normalize_date_to_jalali,
    is_same_calendar_day, is_time_in_window, is_date_in_jalali_range,
    get_persian_weekday
)


class WorkerManager:
    def __init__(self, db=db_instance):
        self.db = db
        self.bot = None
        self._running = False
        self._threads = []
        self._last_daily_report_date = None
        self._last_synced_jalali_date = None

    def set_bot(self, bot_instance):
        self.bot = bot_instance

    def _safe_notify(self, user_id, text, reply_markup=None):
        """ارسال ایمن نوتیفیکیشن با وقفه کوتاه جهت جلوگیری از Flood/Rate-Limit سرور بله"""
        if not self.bot or not user_id:
            return False
        try:
            self.bot.send_message(user_id, text, reply_markup=reply_markup, parse_mode="Markdown")
            time.sleep(0.08)
            return True
        except Exception as e:
            logging.debug(f"Notification delivery notice for user {user_id}: {e}")
            return False

    def get_today_sessions(self, project_id, now=None):
        """
        دریافت جلسات فعال امروز پروژه (پشتیبانی همزمان از تاریخ شمسی 1405/MM/DD و میلادی YYYY-MM-DD)
        به همراه رعایت وضعیت فعال بودن پروژه و برنامه کلاسی (Schedule).
        """
        proj = project_manager.get_project(project_id)
        if not proj or proj.get('status') != 'ACTIVE':
            return []

        if now is None:
            now = get_iran_now()
        today_jalali = get_today_jalali_str(now)
        today_weekday = get_persian_weekday(now)

        # بررسی قرار داشتن تاریخ امروز در بازه کلی تاریخ پروژه
        if not is_date_in_jalali_range(today_jalali, proj.get('start_date'), proj.get('end_date')):
            return []

        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT s.*
            FROM sessions s
            LEFT JOIN schedules sc ON s.schedule_id = sc.id
            WHERE s.project_id = %s
              AND s.status != 'CANCELLED'
              AND (s.schedule_id IS NULL OR sc.is_active = TRUE)
            ORDER BY s.id ASC
            """, (project_id,))
            all_sessions = [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logging.error(f"Error in get_today_sessions for project {project_id}: {e}")
            return []
        finally:
            conn.close()

        matched_sessions = []
        for s in all_sessions:
            s_date = str(s.get('session_date') or '').strip()
            if s_date and is_same_calendar_day(s_date, today_jalali):
                matched_sessions.append(s)

        # اگر جلسات پروژه بدون تاریخ ثبت شده باشند، ولی جلسه فعال روی امروز تنظیم شده باشد
        if not matched_sessions:
            active_sid = proj.get('active_session_id')
            for s in all_sessions:
                if s['id'] == active_sid and not str(s.get('session_date') or '').strip():
                    rec_days = str(proj.get('recurring_days') or '').strip()
                    if not rec_days or rec_days in ('هفتگی', 'هرروز') or today_weekday in rec_days:
                        matched_sessions.append(s)

        return matched_sessions

    def is_session_in_active_hours(self, proj, sess, now=None):
        """بررسی اینکه آیا جلسه در ساعت فعالیت مجاز خود در روز جاری قرار دارد یا خیر"""
        if now is None:
            now = get_iran_now()
        start_t = str(sess.get('time_str') or proj.get('activation_time') or '07:00').strip()
        end_t = str(sess.get('end_time_str') or proj.get('end_time') or '23:00').strip()
        return is_time_in_window(start_t, end_t, now=now)

    def sync_all_projects_calendar(self, now=None):
        """
        همگام‌سازی خودکار تقویم شمسی پروژه‌ها:
        ۱. در آغاز هر روز شمسی، قفل دستی جلسات روز قبل را باز می‌کند تا جلسه روز جدید خودکار فعال شود.
        ۲. اگر جلسه‌ای دقیقاً منطبق با تاریخ شمسی امروز وجود داشته باشد، آن را به عنوان جلسه فعال پروژه تنظیم می‌کند.
        """
        if now is None:
            now = get_iran_now()
        today_jalali = get_today_jalali_str(now)
        is_new_day = (self._last_synced_jalali_date != today_jalali)

        active_projects = project_manager.list_projects(status='ACTIVE')
        for proj in active_projects:
            pid = proj['id']
            if not proj.get('auto_schedule_enabled', True):
                continue

            today_sess = attendance_manager.get_auto_session_for_today(pid, now=now)
            if today_sess:
                if is_new_day or not proj.get('manual_session_override', False):
                    if proj.get('active_session_id') != today_sess['id']:
                        project_manager.set_project_active_session(pid, today_sess['id'], is_manual=False)
                        logging.info(f"Auto-activated session '{today_sess['name']}' (ID {today_sess['id']}) for project {pid} on {today_jalali}")

        if is_new_day:
            self._last_synced_jalali_date = today_jalali

    def start_workers(self):
        if self._running:
            return
        self._running = True

        worker_targets = [
            ("CalendarSyncWorker", self._calendar_sync_loop),
            ("ShortageWorker", self._shortage_loop),
            ("LateReminderWorker", self._late_reminder_loop),
            ("BackupWorker", self._backup_loop),
            ("DailyReportWorker", self._daily_report_loop),
        ]

        for name, target in worker_targets:
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop_workers(self):
        self._running = False

    def _calendar_sync_loop(self):
        while self._running:
            try:
                self.sync_all_projects_calendar()
            except Exception as e:
                logging.error(f"CalendarSyncWorker error: {e}")
            time.sleep(60)

    def _shortage_loop(self):
        while self._running:
            try:
                if self.bot:
                    now = get_iran_now()
                    today_jalali = get_today_jalali_str(now)
                    unnotified = shortage_manager.get_unnotified_shortages()

                    for sh in unnotified:
                        pid = sh['project_id']
                        proj = project_manager.get_project(pid)
                        if not proj or proj.get('status') != 'ACTIVE':
                            shortage_manager.mark_shortage_notified(sh['id'])
                            continue

                        # اگر تاریخ پایان پروژه گذشته است، کمبود قدیمی ارسال نشود
                        p_end = normalize_date_to_jalali(proj.get('end_date') or '')
                        if p_end and len(p_end) == 10 and today_jalali > p_end:
                            shortage_manager.mark_shortage_notified(sh['id'])
                            continue

                        pname = sh.get('project_name') or proj.get('name', '')
                        grp = sh.get('target_group') or 'عمومی'
                        cnt = sh.get('count', 1)
                        msg = (
                            f"🔔 **اعلام کمبود نیرو - {pname}**\n\n"
                            f"🏢 واحد: **{sh['unit']}**\n"
                            f"🗂 بخش: **{sh['section']}**\n"
                            f"👥 تعداد: **{cnt} نفر** ({grp})\n"
                            f"📝 توضیحات: {sh.get('description') or 'ندارد'}\n\n"
                            f"لطفاً جهت معرفی نیروی جایگزین از منوی ربات اقدام فرمایید."
                        )
                        members = permission_manager.get_project_members(pid)
                        for m in members:
                            gender_match = (grp == 'عمومی') or (m.get('project_gender') == grp)
                            if m.get('is_active') and m.get('role') != 'unit_head' and (
                                m.get('role') in ('admin', 'super_admin') or gender_match
                            ):
                                self._safe_notify(m['user_id'], msg)

                        shortage_manager.mark_shortage_notified(sh['id'])
            except Exception as e:
                logging.error(f"ShortageWorker error: {e}")
            time.sleep(max(5, SHORTAGE_CHECK_INTERVAL))

    def _late_reminder_loop(self):
        while self._running:
            time.sleep(max(60, LATE_REMINDER_INTERVAL))
            try:
                if not self.bot:
                    continue
                now = get_iran_now()
                active_projects = project_manager.list_projects(status='ACTIVE')

                for proj in active_projects:
                    pid = proj['id']
                    today_sessions = self.get_today_sessions(pid, now=now)
                    if not today_sessions:
                        continue

                    for sess in today_sessions:
                        # فقط اگر در بازه ساعت فعالیت جلسه/پروژه هستیم نوتیفیکیشن متأخرین ارسال شود
                        if not self.is_session_in_active_hours(proj, sess, now=now):
                            continue

                        sid = sess['id']
                        latecomers = attendance_manager.get_latecomers(pid, sid)
                        untracked = [u for u in latecomers if not str(u.get('late_tracking', '')).strip()]
                        if not untracked:
                            continue

                        members = permission_manager.get_project_members(pid)
                        for m in members:
                            if not m.get('is_active') or m.get('role') == 'unit_head':
                                continue
                            uid = m['user_id']
                            user_late_list = permission_manager.apply_gender_filter(untracked, uid, pid)
                            if user_late_list:
                                start_t = sess.get('time_str') or proj.get('activation_time') or '07:00'
                                end_t = sess.get('end_time_str') or proj.get('end_time') or '23:00'
                                msg = (
                                    f"🚨 **یادآوری پیگیری متأخرین - {proj['name']}**\n"
                                    f"📅 جلسه: **{sess['name']}** (ساعت فعالیت: `{start_t}` تا `{end_t}`)\n\n"
                                    f"تعداد **{len(user_late_list)} نفر** از نیروهای شیفت هنوز وضعیت حضورشان ثبت نشده و پیگیری هم برایشان درج نگردیده است.\n"
                                    f"لطفاً از بخش «لیست متأخرین» پیگیری فرمایید."
                                )
                                self._safe_notify(uid, msg)
            except Exception as e:
                logging.error(f"LateReminderWorker error: {e}")

    def _backup_loop(self):
        while self._running:
            time.sleep(max(300, DB_BACKUP_INTERVAL))
            try:
                db_instance.backup_database(label="periodic")
                active_projects = project_manager.list_projects(status='ACTIVE')
                for proj in active_projects:
                    try:
                        excel_manager.backup_project_excel(proj['id'], label="periodic")
                    except Exception as ex_err:
                        logging.warning(f"Periodic excel backup warning for project {proj['id']}: {ex_err}")
            except Exception as e:
                logging.error(f"BackupWorker error: {e}")

    def _daily_report_loop(self):
        while self._running:
            try:
                now = get_iran_now()
                today_jalali = get_today_jalali_str(now)
                if (now.hour == DAILY_REPORT_HOUR and now.minute >= DAILY_REPORT_MINUTE and
                        self._last_daily_report_date != today_jalali):
                    self._last_daily_report_date = today_jalali
                    if self.bot:
                        active_projects = project_manager.list_projects(status='ACTIVE')
                        for proj in active_projects:
                            pid = proj['id']
                            today_sessions = self.get_today_sessions(pid, now=now)
                            for sess in today_sessions:
                                sid = sess['id']
                                attendance_manager.mark_empty_as_absent(pid, sid)
                                stats = report_manager.get_dashboard_stats(pid, sid)
                                summary = (
                                    f"🌙 **گزارش جمع‌بندی شبانه - {proj['name']}**\n"
                                    f"📅 جلسه: **{sess['name']}** (مورخ `{today_jalali}`)\n\n"
                                    f"👥 کل مورد انتظار: {stats['total_expected']}\n"
                                    f"✅ کل حاضرین: {stats['total_present']}\n"
                                    f"📊 درصد مشارکت: **{stats['overall_percentage']}%**"
                                )
                                admins = permission_manager.get_project_admins(pid, include_global=True)
                                for adm in admins:
                                    self._safe_notify(adm['user_id'], summary)
            except Exception as e:
                logging.error(f"DailyReportWorker error: {e}")
            time.sleep(30)


worker_manager = WorkerManager()