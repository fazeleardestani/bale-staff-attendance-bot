import os
import sys
import threading
import time
import logging
from datetime import datetime
from config import (
    DB_BACKUP_INTERVAL, LATE_REMINDER_INTERVAL, SHORTAGE_CHECK_INTERVAL,
    DAILY_REPORT_HOUR, DAILY_REPORT_MINUTE, LOGS_DIR
)
from db_manager import db_instance
from project_manager import project_manager
from attendance_manager import attendance_manager
from shortage_manager import shortage_manager
from permission_manager import permission_manager
from excel_manager import excel_manager

logger = logging.getLogger("worker_manager")

class WorkerManager:
    def __init__(self, bot_instance=None):
        self.bot = bot_instance
        self.last_daily_report_date = None
        self.is_running = False

    def set_bot(self, bot_instance):
        self.bot = bot_instance

    def start_workers(self):
        if self.is_running:
            return
        self.is_running = True
        threading.Thread(target=self._daily_report_loop, daemon=True).start()
        threading.Thread(target=self._late_reminder_loop, daemon=True).start()
        threading.Thread(target=self._check_shortages_loop, daemon=True).start()
        threading.Thread(target=self._auto_backup_loop, daemon=True).start()
        logger.info("Background workers started successfully.")

    def _daily_report_loop(self):
        while True:
            try:
                now = datetime.now()
                if (now.hour == DAILY_REPORT_HOUR and 
                    now.minute == DAILY_REPORT_MINUTE and 
                    self.last_daily_report_date != now.date()):
                    
                    active_projects = project_manager.list_projects(status='ACTIVE')
                    for proj in active_projects:
                        pid = proj['id']
                        sessions = attendance_manager.list_sessions(pid, include_cancelled=False)
                        if not sessions:
                            continue
                        active_session = sessions[-1]
                        sid = active_session['id']

                        attendance_manager.mark_empty_as_absent(pid, sid)
                        excel_path = excel_manager.export_project_excel(pid)

                        if self.bot and excel_path and os.path.exists(excel_path):
                            members = permission_manager.get_project_members(pid)
                            recipients = [m['user_id'] for m in members if m['role'] in ('admin', 'super_admin') and m['is_active']]
                            caption = "📊 **گزارش پایانی و فایل نهایی - " + str(proj['name']) + " (" + str(active_session['name']) + ")**\nافراد تعیین تکلیف نشده خودکار غایب خوردند."
                            for uid in set(recipients):
                                try:
                                    with open(excel_path, "rb") as f:
                                        self.bot.send_document(uid, f, caption=caption, parse_mode="Markdown")
                                except Exception as err:
                                    logger.error(f"Failed to send daily report to {uid}: {err}")

                    self.last_daily_report_date = now.date()
            except Exception as e:
                logger.error(f"Error in daily_report_loop: {e}")
            time.sleep(60)

    def _late_reminder_loop(self):
        while True:
            time.sleep(LATE_REMINDER_INTERVAL)
            try:
                active_projects = project_manager.list_projects(status='ACTIVE')
                for proj in active_projects:
                    pid = proj['id']
                    sessions = attendance_manager.list_sessions(pid, include_cancelled=False)
                    if not sessions:
                        continue
                    active_session = sessions[-1]
                    sid = active_session['id']

                    latecomers = attendance_manager.get_latecomers(pid, sid)
                    untracked_late = [l for l in latecomers if not str(l.get('late_tracking', '')).strip()]

                    if untracked_late and self.bot:
                        msg = f"⚠️ **یادآوری لیست متأخرین - {proj['name']}:**\nهم‌اکنون **{len(untracked_late)} نفر** در جلسه «{active_session['name']}» بدون پیگیری تأخیر هستند. لطفاً جهت پیگیری تماس بگیرید."
                        members = permission_manager.get_project_members(pid)
                        for m in members:
                            if m['is_active']:
                                try:
                                    self.bot.send_message(m['user_id'], msg, parse_mode="Markdown")
                                except Exception:
                                    pass
            except Exception as e:
                logger.error(f"Error in late_reminder_loop: {e}")

    def _check_shortages_loop(self):
        while True:
            time.sleep(SHORTAGE_CHECK_INTERVAL)
            try:
                unnotified = shortage_manager.get_unnotified_shortages()
                if not unnotified:
                    continue
                # Group unnotified shortages by batch parameters
                grouped = {}
                for s in unnotified:
                    key = (s['project_id'], s['unit'], s['section'], s.get('target_group', 'عمومی'), s.get('description', ''))
                    if key not in grouped:
                        grouped[key] = []
                    grouped[key].append(s)

                for (pid, unit, section, target_grp, desc), items in grouped.items():
                    pname = items[0].get('project_name', f'پروژه {pid}')
                    total_count = sum(int(item.get('count', 1)) for item in items)
                    msg = (
                        f"🔔 **اعلام کمبود نیرو - {pname}**\n\n"
                        f"🏢 واحد: **{unit}**\n"
                        f"🗂 بخش: **{section}**\n"
                        f"👥 تعداد: **{total_count} نفر** ({target_grp})\n"
                        f"📝 توضیحات: {desc}\n\n"
                        f"لطفاً جهت تأمین کمبود اقدام فرمایید."
                    )
                    if self.bot:
                        members = permission_manager.get_project_members(pid)
                        for m in members:
                            if m['is_active'] and m.get('role') != 'unit_head' and (m['role'] in ('admin', 'super_admin') or m['project_gender'] == target_grp):
                                try:
                                    self.bot.send_message(m['user_id'], msg, parse_mode="Markdown")
                                except Exception:
                                    pass
                    for item in items:
                        shortage_manager.mark_shortage_notified(item['id'])
            except Exception as e:
                logger.error(f"Error in check_shortages_loop: {e}")

    def _auto_backup_loop(self):
        while True:
            time.sleep(DB_BACKUP_INTERVAL)
            try:
                db_instance.backup_database(label="hourly")
                active_projects = project_manager.list_projects(status='ACTIVE')
                for proj in active_projects:
                    excel_manager.backup_project_excel(proj['id'])
            except Exception as e:
                logger.error(f"Error in auto_backup_loop: {e}")

worker_manager = WorkerManager()
