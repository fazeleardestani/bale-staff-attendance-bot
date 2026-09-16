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
from config import PROJECT_FILES_DIR, ARCHIVES_DIR, BASE_PATH
from db_manager import db_instance
import shutil

class ProjectManager:
    def __init__(self, db=db_instance):
        self.db = db

    def create_project(self, name, project_type='عمومی', description='', excel_path=None,
                       total_sessions=0, recurring_days='', activation_time='',
                       start_date='', end_date='', has_prep_day=0):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        try:
            cursor.execute('''
            INSERT INTO projects (
                name, type, description, status, excel_path, 
                total_sessions, recurring_days, activation_time, 
                start_date, end_date, has_prep_day, 
                created_at, updated_at
            )
            VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                name.strip(), project_type.strip(), description.strip(), excel_path,
                int(total_sessions or 0), str(recurring_days or '').strip(), str(activation_time or '').strip(),
                str(start_date or '').strip(), str(end_date or '').strip(), 1 if has_prep_day else 0,
                now_iso, now_iso
            ))
            project_id = cursor.lastrowid
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
            # Remove sample/example sheets from cloned file so new project has only base sheets
            try:
                import openpyxl
                wb = openpyxl.load_workbook(target_file)
                for sname in ['روز آماده سازی', 'روز 1', 'روز 2']:
                    if sname in wb.sheetnames:
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
                                           total_sessions=0, recurring_days='', activation_time='',
                                           start_date='', end_date='', has_prep_day=0):
        from attendance_manager import attendance_manager

        project_id = self.create_project(
            name=name, project_type=project_type, description=description,
            total_sessions=total_sessions, recurring_days=recurring_days, activation_time=activation_time,
            start_date=start_date, end_date=end_date, has_prep_day=has_prep_day
        )

        first_session_id = None

        from datetime import datetime, timedelta

        num_sessions = int(total_sessions) if total_sessions and int(total_sessions) > 0 else 1

        # Determine base starting date
        base_dt = None
        if start_date:
            try:
                base_dt = datetime.strptime(start_date.strip(), "%Y-%m-%d")
            except ValueError:
                base_dt = datetime.now()
        else:
            base_dt = datetime.now()

        if project_type == 'کلاس':
            schedule_name = f"کلاس {name}"
            sched_id = attendance_manager.create_schedule(
                project_id, name=schedule_name, day_of_week=recurring_days or "هفتگی",
                time_str=activation_time or "16:00"
            )
            
            for i in range(1, num_sessions + 1):
                sess_dt = base_dt + timedelta(days=7 * (i - 1))
                sess_date_str = sess_dt.strftime("%Y-%m-%d")
                sess_id = attendance_manager.create_session(
                    project_id, name=f"جلسه {i}", session_date=sess_date_str,
                    time_str=activation_time, day_of_week=recurring_days,
                    schedule_id=sched_id, copy_from_prev_session=True, sync_excel_sheet=True
                )
                if i == 1:
                    first_session_id = sess_id

        else:
            if has_prep_day:
                prep_dt = base_dt - timedelta(days=1)
                prep_id = attendance_manager.create_session(
                    project_id, name="روز آماده سازی", session_date=prep_dt.strftime("%Y-%m-%d"),
                    copy_from_prev_session=True, sync_excel_sheet=True
                )
                first_session_id = prep_id

            for i in range(1, num_sessions + 1):
                day_dt = base_dt + timedelta(days=(i - 1))
                sess_id = attendance_manager.create_session(
                    project_id, name=f"روز {i}", session_date=day_dt.strftime("%Y-%m-%d"),
                    copy_from_prev_session=True, sync_excel_sheet=True
                )
                if first_session_id is None:
                    first_session_id = sess_id

        try:
            from excel_manager import excel_manager
            conn = self.db.get_sqlite_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sessions WHERE project_id = ?", (project_id,))
            active_s_names = [r['name'] for r in cursor.fetchall()]
            conn.close()
            excel_manager.cleanup_sample_sheets(project_id, active_s_names)
        except Exception:
            pass

        return project_id, first_session_id

    def get_project(self, project_id):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_project_by_name(self, name):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM projects WHERE name = ?", (name.strip(),))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def list_projects(self, status=None):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        if status:
            cursor.execute("SELECT * FROM projects WHERE status = ? ORDER BY id DESC", (status,))
        else:
            cursor.execute("SELECT * FROM projects ORDER BY id DESC")
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_project_status(self, project_id, new_status):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        cursor.execute('''
        UPDATE projects SET status = ?, updated_at = ? WHERE id = ?
        ''', (new_status, now_iso, project_id))
        conn.commit()
        conn.close()

    def update_project_excel_path(self, project_id, excel_path):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        cursor.execute('''
        UPDATE projects SET excel_path = ?, updated_at = ? WHERE id = ?
        ''', (excel_path, now_iso, project_id))
        conn.commit()
        conn.close()

    def archive_project(self, project_id):
        self.update_project_status(project_id, 'ARCHIVED')

    def unarchive_project(self, project_id):
        self.update_project_status(project_id, 'ACTIVE')

    def update_project_details(self, project_id, name=None, description=None, project_type=None):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        updates = []
        params = []
        if name is not None:
            updates.append("name = ?")
            params.append(str(name).strip())
        if description is not None:
            updates.append("description = ?")
            params.append(str(description).strip())
        if project_type is not None:
            updates.append("type = ?")
            params.append(str(project_type).strip())
        if updates:
            updates.append("updated_at = ?")
            params.append(now_iso)
            params.append(project_id)
            sql = f"UPDATE projects SET {', '.join(updates)} WHERE id = ?"
            cursor.execute(sql, tuple(params))
            conn.commit()
        conn.close()

    def delete_project(self, project_id):
        # Mandatory safety backup before permanent deletion
        try:
            self.db.backup_database(label=f"pre_delete_project_{project_id}")
            from excel_manager import excel_manager
            excel_manager.backup_project_excel(project_id, label="pre_delete")
        except Exception as b_err:
            logging.warning(f"Pre-delete backup warning: {b_err}")

        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM attendance WHERE staff_id IN (SELECT id FROM project_staff WHERE project_id = ?)", (project_id,))
            cursor.execute("DELETE FROM project_staff WHERE project_id = ?", (project_id,))
            cursor.execute("DELETE FROM sessions WHERE project_id = ?", (project_id,))
            cursor.execute("DELETE FROM schedules WHERE project_id = ?", (project_id,))
            cursor.execute("DELETE FROM org_chart WHERE project_id = ?", (project_id,))
            cursor.execute("DELETE FROM shortages WHERE project_id = ?", (project_id,))
            cursor.execute("DELETE FROM project_users WHERE project_id = ?", (project_id,))
            cursor.execute("DELETE FROM staff_logs WHERE project_id = ?", (project_id,))
            cursor.execute("UPDATE user_context SET current_project_id = NULL, current_session_id = NULL WHERE current_project_id = ?", (project_id,))
            cursor.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            conn.commit()
        finally:
            conn.close()

        proj_dir = os.path.join(PROJECT_FILES_DIR, f"project_{project_id}")
        if os.path.exists(proj_dir):
            try:
                shutil.rmtree(proj_dir)
            except Exception:
                pass
        return True

    def set_project_org_chart(self, project_id, unit_section_pairs):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM org_chart WHERE project_id = ?", (project_id,))
        for idx, item in enumerate(unit_section_pairs, start=1):
            if len(item) == 3:
                unit, section, order_val = item
            else:
                unit, section = item
                order_val = idx
            u = str(unit or '').strip()
            s = str(section or '').strip()
            if u and u not in ('None', ''):
                cursor.execute("INSERT INTO org_chart (project_id, unit, section, display_order) VALUES (?, ?, ?, ?)", (project_id, u, s, int(order_val)))
        conn.commit()
        conn.close()

    def get_project_org_chart(self, project_id):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT unit, section, display_order FROM org_chart WHERE project_id = ? ORDER BY display_order ASC, id ASC", (project_id,))
        rows = cursor.fetchall()
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
