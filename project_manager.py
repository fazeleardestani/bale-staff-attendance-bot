import os
import sys
import logging
import shutil
from datetime import datetime, timedelta
from config import PROJECT_FILES_DIR, ARCHIVES_DIR, BASE_PATH
from db_manager import db_instance
from utils import (
    get_iran_now, get_today_jalali_str, normalize_date_to_jalali,
    parse_any_date_to_datetime, datetime_to_jalali_str, get_persian_weekday,
    is_date_in_jalali_range, is_time_in_window
)


class ProjectManager:
    def __init__(self, db=db_instance):
        self.db = db

    def create_project(self, name, project_type='عمومی', description='', excel_path=None,
                       total_sessions=0, recurring_days='', activation_time='07:00', end_time='23:00',
                       start_date='', end_date='', has_prep_day=0, auto_schedule_enabled=True):
        conn = self.db.get_connection()
        now_iso = get_iran_now().isoformat()
        norm_start_date = normalize_date_to_jalali(start_date) if start_date else ''
        norm_end_date = normalize_date_to_jalali(end_date) if end_date else ''
        act_t = str(activation_time or '07:00').strip() or '07:00'
        end_t = str(end_time or '23:00').strip() or '23:00'

        try:
            cursor = conn.cursor()
            cursor.execute('''
            INSERT INTO projects (
                name, type, description, status, excel_path, 
                total_sessions, recurring_days, activation_time, end_time,
                start_date, end_date, has_prep_day, auto_schedule_enabled,
                manual_session_override, created_at, updated_at
            )
            VALUES (%s, %s, %s, 'ACTIVE', %s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE, %s, %s)
            RETURNING id;
            ''', (
                name.strip(), project_type.strip(), description.strip(), excel_path,
                int(total_sessions or 0), str(recurring_days or '').strip(), act_t, end_t,
                norm_start_date, norm_end_date, bool(has_prep_day), bool(auto_schedule_enabled),
                now_iso, now_iso
            ))
            project_id = cursor.fetchone()['id']
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

        proj_dir = os.path.join(PROJECT_FILES_DIR, f"project_{project_id}")
        os.makedirs(proj_dir, exist_ok=True)

        template_file = os.path.join(BASE_PATH, "قالب اکسل پروژه.xlsx")
        target_file = os.path.join(proj_dir, "project_data.xlsx")

        if not excel_path and os.path.exists(template_file):
            shutil.copy2(template_file, target_file)
            try:
                import openpyxl
                wb = openpyxl.load_workbook(target_file)
                for sname in ['روز آماده سازی', 'روز 1', 'روز 2']:
                    if sname in wb.sheetnames and len(wb.sheetnames) > 1:
                        del wb[sname]
                wb.save(target_file)
                wb.close()
            except Exception as ex_err:
                logging.error(f"Failed to prepare project template Excel: {ex_err}")
                self.delete_project(project_id)
                raise RuntimeError(f"خطا در آماده‌سازی قالب اکسل پروژه: {ex_err}")

            self.update_project_excel_path(project_id, target_file)
            try:
                from excel_manager import excel_manager
                success = excel_manager.import_project_excel(project_id, target_file)
                if not success:
                    raise RuntimeError("اعتبارسنجی قالب اکسل پروژه با شکست مواجه شد.")
            except Exception as imp_err:
                logging.error(f"Initial project Excel import failed: {imp_err}")
                self.delete_project(project_id)
                raise RuntimeError(f"خطا در بارگذاری اولیه اکسل پروژه: {imp_err}")

        return project_id

    def create_project_with_smart_sessions(self, name, project_type='عمومی', description='',
                                           total_sessions=0, recurring_days='', activation_time='07:00', end_time='23:00',
                                           start_date='', end_date='', has_prep_day=0):
        from attendance_manager import attendance_manager

        base_dt = parse_any_date_to_datetime(start_date) or get_iran_now()
        end_dt = parse_any_date_to_datetime(end_date) if end_date else None

        num_sessions = int(total_sessions) if total_sessions and int(total_sessions) > 0 else 1
        if project_type != 'کلاس' and end_dt and end_dt >= base_dt:
            diff_days = (end_dt - base_dt).days + 1
            if diff_days > 0:
                num_sessions = diff_days

        jalali_start = datetime_to_jalali_str(base_dt)
        jalali_end = datetime_to_jalali_str(end_dt) if end_dt else jalali_start

        project_id = self.create_project(
            name=name, project_type=project_type, description=description,
            total_sessions=num_sessions, recurring_days=recurring_days,
            activation_time=activation_time or '07:00', end_time=end_time or '23:00',
            start_date=jalali_start, end_date=jalali_end, has_prep_day=has_prep_day
        )

        first_session_id = None

        if project_type == 'کلاس':
            schedule_name = f"کلاس {name}"
            sched_id = attendance_manager.create_schedule(
                project_id, name=schedule_name, day_of_week=recurring_days or "هفتگی",
                time_str=activation_time or "15:00", end_time_str=end_time or "20:00"
            )

            class_start_dt = base_dt
            valid_days = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه"]
            if recurring_days and recurring_days.strip() in valid_days:
                for offset in range(7):
                    candidate = base_dt + timedelta(days=offset)
                    if get_persian_weekday(candidate) == recurring_days.strip():
                        class_start_dt = candidate
                        break

            last_sess_jalali = jalali_start
            for i in range(1, num_sessions + 1):
                sess_dt = class_start_dt + timedelta(days=7 * (i - 1))
                sess_date_str = datetime_to_jalali_str(sess_dt)
                last_sess_jalali = sess_date_str
                sess_id = attendance_manager.create_session(
                    project_id, name=f"جلسه {i}", session_date=sess_date_str,
                    time_str=activation_time or "15:00", end_time_str=end_time or "20:00",
                    day_of_week=recurring_days or get_persian_weekday(sess_dt),
                    schedule_id=sched_id, copy_from_prev_session=True, sync_excel_sheet=True
                )
                if i == 1:
                    first_session_id = sess_id

            self.update_project_schedule_config(
                project_id,
                start_date=datetime_to_jalali_str(class_start_dt),
                end_date=last_sess_jalali
            )

        else:
            if has_prep_day:
                prep_dt = base_dt - timedelta(days=1)
                prep_jalali = datetime_to_jalali_str(prep_dt)
                prep_id = attendance_manager.create_session(
                    project_id, name="روز آماده سازی", session_date=prep_jalali,
                    time_str=activation_time or "07:00", end_time_str=end_time or "23:00",
                    day_of_week=get_persian_weekday(prep_dt),
                    copy_from_prev_session=True, sync_excel_sheet=True
                )
                first_session_id = prep_id

            last_day_jalali = jalali_start
            for i in range(1, num_sessions + 1):
                day_dt = base_dt + timedelta(days=(i - 1))
                day_jalali = datetime_to_jalali_str(day_dt)
                last_day_jalali = day_jalali
                sess_id = attendance_manager.create_session(
                    project_id, name=f"روز {i}", session_date=day_jalali,
                    time_str=activation_time or "07:00", end_time_str=end_time or "23:00",
                    day_of_week=get_persian_weekday(day_dt),
                    copy_from_prev_session=True, sync_excel_sheet=True
                )
                if first_session_id is None:
                    first_session_id = sess_id

            self.update_project_schedule_config(
                project_id,
                start_date=datetime_to_jalali_str(base_dt - timedelta(days=1)) if has_prep_day else jalali_start,
                end_date=last_day_jalali
            )

        if first_session_id:
            self.set_project_active_session(project_id, first_session_id, is_manual=False)

        try:
            from excel_manager import excel_manager
            conn = self.db.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sessions WHERE project_id = %s", (project_id,))
                active_s_names = [r['name'] for r in cursor.fetchall()]
            finally:
                conn.close()
            excel_manager.cleanup_sample_sheets(project_id, active_s_names)
        except Exception:
            pass

        return project_id, first_session_id

    def get_project(self, project_id):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_project_by_name(self, name):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM projects WHERE name = %s", (name.strip(),))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_projects(self, status=None):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            if status:
                cursor.execute("SELECT * FROM projects WHERE status = %s ORDER BY id DESC", (status,))
            else:
                cursor.execute("SELECT * FROM projects ORDER BY id DESC")
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def update_project_status(self, project_id, new_status):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            now_iso = get_iran_now().isoformat()
            cursor.execute('''
            UPDATE projects SET status = %s, updated_at = %s WHERE id = %s
            ''', (new_status, now_iso, project_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_project_excel_path(self, project_id, excel_path):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            now_iso = get_iran_now().isoformat()
            cursor.execute('''
            UPDATE projects SET excel_path = %s, updated_at = %s WHERE id = %s
            ''', (excel_path, now_iso, project_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def archive_project(self, project_id):
        self.update_project_status(project_id, 'ARCHIVED')

    def unarchive_project(self, project_id):
        self.update_project_status(project_id, 'ACTIVE')

    def update_project_details(self, project_id, name=None, description=None, project_type=None):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            now_iso = get_iran_now().isoformat()
            updates = []
            params = []
            if name is not None:
                updates.append("name = %s")
                params.append(str(name).strip())
            if description is not None:
                updates.append("description = %s")
                params.append(str(description).strip())
            if project_type is not None:
                updates.append("type = %s")
                params.append(str(project_type).strip())
            if updates:
                updates.append("updated_at = %s")
                params.append(now_iso)
                params.append(project_id)
                sql = f"UPDATE projects SET {', '.join(updates)} WHERE id = %s"
                cursor.execute(sql, tuple(params))
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_project_schedule_config(self, project_id, start_date=None, end_date=None,
                                       activation_time=None, end_time=None,
                                       recurring_days=None, auto_schedule_enabled=None):
        """بروزرسانی تنظیمات تاریخ شمسی و ساعت فعالیت پروژه توسط ادمین پروژه"""
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            now_iso = get_iran_now().isoformat()
            updates = []
            params = []

            if start_date is not None:
                updates.append("start_date = %s")
                params.append(normalize_date_to_jalali(start_date) if start_date else '')
            if end_date is not None:
                updates.append("end_date = %s")
                params.append(normalize_date_to_jalali(end_date) if end_date else '')
            if activation_time is not None:
                updates.append("activation_time = %s")
                params.append(str(activation_time).strip())
            if end_time is not None:
                updates.append("end_time = %s")
                params.append(str(end_time).strip())
            if recurring_days is not None:
                updates.append("recurring_days = %s")
                params.append(str(recurring_days).strip())
            if auto_schedule_enabled is not None:
                updates.append("auto_schedule_enabled = %s")
                params.append(bool(auto_schedule_enabled))

            if updates:
                updates.append("updated_at = %s")
                params.append(now_iso)
                params.append(project_id)
                sql = f"UPDATE projects SET {', '.join(updates)} WHERE id = %s"
                cursor.execute(sql, tuple(params))
                conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def set_project_active_session(self, project_id, session_id, is_manual=False):
        """تنظیم جلسه فعال پروژه و مشخص کردن اینکه آیا توسط ادمین به صورت دستی قفل شده یا خودکار است"""
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            now_iso = get_iran_now().isoformat()
            cursor.execute("""
            UPDATE projects
            SET active_session_id = %s,
                manual_session_override = %s,
                updated_at = %s
            WHERE id = %s
            """, (session_id, bool(is_manual), now_iso, project_id))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    def is_project_currently_in_active_window(self, project_id, session_id=None, now=None):
        """
        بررسی دقیق اینکه آیا پروژه در این لحظه (بر اساس تاریخ شمسی روز و ساعت فعلی ایران)
        در زمان فعالیت مجاز خود قرار دارد یا خیر.
        خروجی: (is_active_window: bool, status_reason_fa: str)
        """
        if now is None:
            now = get_iran_now()
        proj = self.get_project(project_id)
        if not proj or proj.get('status') != 'ACTIVE':
            return False, "پروژه غیرفعال یا آرشیو شده است"

        today_jalali = get_today_jalali_str(now)
        today_weekday = get_persian_weekday(now)

        # ۱. بررسی بازه تاریخ شروع و پایان پروژه (در صورت ثبت شدن)
        p_start = proj.get('start_date') or ''
        p_end = proj.get('end_date') or ''
        if not is_date_in_jalali_range(today_jalali, p_start, p_end):
            return False, f"خارج از بازه تاریخ پروژه ({p_start or '...'} تا {p_end or '...'})"

        # ۲. بررسی روز هفته برای پروژه‌های کلاسی (مگر اینکه جلسه مشخصی برای امروز تعریف شده باشد)
        rec_days = str(proj.get('recurring_days') or '').strip()
        start_t = str(proj.get('activation_time') or '07:00').strip()
        end_t = str(proj.get('end_time') or '23:00').strip()

        if session_id:
            from attendance_manager import attendance_manager
            sess = attendance_manager.get_session(session_id)
            if sess:
                if sess.get('status') == 'CANCELLED':
                    return False, "این جلسه لغو (تعطیل) شده است"
                if sess.get('time_str'):
                    start_t = sess['time_str'].strip()
                if sess.get('end_time_str'):
                    end_t = sess['end_time_str'].strip()

                sess_date_norm = normalize_date_to_jalali(sess.get('session_date') or '')
                if sess_date_norm and sess_date_norm != today_jalali:
                    return False, f"تاریخ این جلسه ({sess_date_norm}) مربوط به امروز ({today_jalali}) نیست"
        else:
            if proj.get('type') == 'کلاس' and rec_days and rec_days not in ('هفتگی', 'هرروز', ''):
                if today_weekday not in rec_days:
                    return False, f"امروز ({today_weekday}) روز برگزاری کلاس ({rec_days}) نیست"

        # ۳. بررسی بازه ساعت فعالیت در روز جاری
        if not is_time_in_window(start_t, end_t, now=now):
            return False, f"خارج از ساعت فعالیت ({start_t} تا {end_t})"

        return True, f"فعال ({start_t} تا {end_t})"

    def delete_project(self, project_id):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM attendance WHERE staff_id IN (SELECT id FROM project_staff WHERE project_id = %s)", (project_id,))
            cursor.execute("DELETE FROM project_staff WHERE project_id = %s", (project_id,))
            cursor.execute("DELETE FROM sessions WHERE project_id = %s", (project_id,))
            cursor.execute("DELETE FROM schedules WHERE project_id = %s", (project_id,))
            cursor.execute("DELETE FROM org_chart WHERE project_id = %s", (project_id,))
            cursor.execute("DELETE FROM shortages WHERE project_id = %s", (project_id,))
            cursor.execute("DELETE FROM project_users WHERE project_id = %s", (project_id,))
            cursor.execute("DELETE FROM staff_logs WHERE project_id = %s", (project_id,))
            cursor.execute("UPDATE user_context SET current_project_id = NULL, current_session_id = NULL WHERE current_project_id = %s", (project_id,))
            cursor.execute("DELETE FROM projects WHERE id = %s", (project_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        proj_dir = os.path.join(PROJECT_FILES_DIR, f"project_{project_id}")
        if os.path.exists(proj_dir):
            try:
                shutil.rmtree(proj_dir)
            except Exception:
                pass
        return True

    def set_project_org_chart(self, project_id, unit_section_pairs, cursor=None):
        own_conn = None
        if cursor is None:
            own_conn = self.db.get_connection()
            cursor = own_conn.cursor()
        try:
            cursor.execute("DELETE FROM org_chart WHERE project_id = %s", (project_id,))
            for idx, item in enumerate(unit_section_pairs, start=1):
                if len(item) == 3:
                    unit, section, order_val = item
                else:
                    unit, section = item
                    order_val = idx
                u = str(unit or '').strip()
                s = str(section or '').strip()
                if u and u not in ('None', ''):
                    cursor.execute("INSERT INTO org_chart (project_id, unit, section, display_order) VALUES (%s, %s, %s, %s)", (project_id, u, s, int(order_val)))
            if own_conn:
                own_conn.commit()
        except Exception:
            if own_conn:
                own_conn.rollback()
            raise
        finally:
            if own_conn:
                own_conn.close()

    def get_project_org_chart(self, project_id):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT unit, section, display_order FROM org_chart WHERE project_id = %s ORDER BY display_order ASC, id ASC", (project_id,))
            rows = cursor.fetchall()
        finally:
            conn.close()
        chart = {}
        for r in rows:
            u = r['unit']
            s = r['section']
            if u not in chart:
                chart[u] = []
            if s and s not in ('None', '') and s not in chart[u]:
                chart[u].append(s)
        return chart


project_manager = ProjectManager()