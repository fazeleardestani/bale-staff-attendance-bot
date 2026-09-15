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
from db_manager import db_instance
from utils import calculate_status_with_delay, format_delay_minutes
from staff_manager import staff_manager

class AttendanceManager:
    def __init__(self, db=db_instance):
        self.db = db

    def create_schedule(self, project_id, name, day_of_week, time_str, location=""):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO schedules (project_id, name, day_of_week, time_str, location, is_active)
        VALUES (?, ?, ?, ?, ?, 1)
        ''', (project_id, name.strip(), day_of_week.strip(), time_str.strip(), location.strip()))
        schedule_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return schedule_id

    def list_schedules(self, project_id):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM schedules WHERE project_id = ? AND is_active = 1", (project_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def create_session(self, project_id, name, session_date=None, time_str='', day_of_week='',
                       schedule_id=None, copy_from_prev_session=True, sync_excel_sheet=True):
        '''
        Creates a session.
        If copy_from_prev_session=True:
           Copies the previous session's staff roster into the new session.
        If sync_excel_sheet=True:
           Creates a new worksheet in the project Excel file with previous day's staff.
        '''
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        
        # 1. Find previous session before creating new one
        cursor.execute("SELECT id FROM sessions WHERE project_id = ? ORDER BY id DESC LIMIT 1", (project_id,))
        prev_row = cursor.fetchone()
        prev_sid = prev_row['id'] if prev_row else None

        now_iso = datetime.now().isoformat()
        date_str = session_date or datetime.now().strftime("%Y-%m-%d")

        cursor.execute('''
        INSERT INTO sessions (project_id, schedule_id, name, session_date, time_str, day_of_week, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'SCHEDULED', ?)
        ''', (project_id, schedule_id, name.strip(), date_str, str(time_str or '').strip(), str(day_of_week or '').strip(), now_iso))
        session_id = cursor.lastrowid
        conn.commit()

        # 2. Copy staff from previous session into new session attendance
        if copy_from_prev_session:
            if prev_sid:
                cursor.execute("SELECT staff_id FROM attendance WHERE session_id = ?", (prev_sid,))
                prev_staff = cursor.fetchall()
                for r in prev_staff:
                    cursor.execute('''
                    INSERT INTO attendance (project_id, session_id, staff_id, status, card_status, updated_at)
                    VALUES (?, ?, ?, '', '', ?)
                    ON CONFLICT(project_id, session_id, staff_id) DO NOTHING
                    ''', (project_id, session_id, r['staff_id'], now_iso))
            else:
                # If first session, add all active project staff
                cursor.execute("SELECT id FROM project_staff WHERE project_id = ? AND is_active = 1", (project_id,))
                all_staff = cursor.fetchall()
                for r in all_staff:
                    cursor.execute('''
                    INSERT INTO attendance (project_id, session_id, staff_id, status, card_status, updated_at)
                    VALUES (?, ?, ?, '', '', ?)
                    ON CONFLICT(project_id, session_id, staff_id) DO NOTHING
                    ''', (project_id, session_id, r['id'], now_iso))
            conn.commit()

        conn.close()

        # 3. Synchronize Excel sheet
        if sync_excel_sheet:
            try:
                from excel_manager import excel_manager
                excel_manager.add_session_sheet(project_id, name.strip())
            except Exception:
                pass

        return session_id

    def get_session(self, session_id):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_session_by_name(self, project_id, name):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sessions WHERE project_id = ? AND name = ?", (project_id, name.strip()))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def list_sessions(self, project_id, include_cancelled=False):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        if include_cancelled:
            cursor.execute("SELECT * FROM sessions WHERE project_id = ? ORDER BY id ASC", (project_id,))
        else:
            cursor.execute("SELECT * FROM sessions WHERE project_id = ? AND status != 'CANCELLED' ORDER BY id ASC", (project_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_session_field(self, session_id, field_name, value):
        allowed = ['name', 'session_date', 'time_str', 'day_of_week', 'status']
        if field_name not in allowed:
            return False
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute(f"UPDATE sessions SET {field_name} = ? WHERE id = ?", (value.strip(), session_id))
        conn.commit()
        conn.close()
        return True

    def toggle_session_status(self, session_id):
        sess = self.get_session(session_id)
        if not sess:
            return False
        new_status = 'SCHEDULED' if sess['status'] == 'CANCELLED' else 'CANCELLED'
        return self.update_session_field(session_id, 'status', new_status)

    def cancel_session(self, session_id):
        return self.update_session_field(session_id, 'status', 'CANCELLED')

    def get_session_attendance(self, project_id, session_id):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute('''
        SELECT 
            s.id as staff_id, s.project_id, s.name, s.phone, s.unit, s.section,
            s.position, s.card_title, s.shift_time, s.gender, s.notes as staff_notes,
            s.is_multi_section, s._excel_row,
            COALESCE(a.id, 0) as attendance_id,
            COALESCE(a.status, '') as status,
            COALESCE(a.card_status, '') as card_status,
            COALESCE(a.late_tracking, '') as late_tracking,
            COALESCE(a.description, '') as description,
            a.updated_at
        FROM project_staff s
        LEFT JOIN attendance a ON s.id = a.staff_id AND a.session_id = ?
        WHERE s.project_id = ? AND s.is_active = 1
        ORDER BY s.id ASC
        ''', (session_id, project_id))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_staff_session_attendance(self, project_id, session_id, staff_id):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute('''
        SELECT 
            s.id as staff_id, s.project_id, s.name, s.phone, s.unit, s.section,
            s.position, s.card_title, s.shift_time, s.gender, s.notes as staff_notes,
            s.is_multi_section, s._excel_row,
            COALESCE(a.status, '') as status,
            COALESCE(a.card_status, '') as card_status,
            COALESCE(a.late_tracking, '') as late_tracking,
            COALESCE(a.description, '') as description
        FROM project_staff s
        LEFT JOIN attendance a ON s.id = a.staff_id AND a.session_id = ?
        WHERE s.id = ? AND s.project_id = ?
        ''', (session_id, staff_id, project_id))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def update_attendance_status(self, project_id, session_id, staff_id, status_val, sync_same_phone=True):
        staff = staff_manager.get_staff_member(staff_id)
        if not staff:
            return False
        if status_val == "حاضر":
            status_val = calculate_status_with_delay(staff.get("shift_time", ""))

        target_staff_ids = [staff_id]
        phone = str(staff.get("phone", "")).strip()
        if sync_same_phone and phone and phone not in ("None", "-", ""):
            phone_staff = staff_manager.list_staff(project_id, active_only=True)
            target_staff_ids = [s["id"] for s in phone_staff if str(s.get("phone", "")).strip() == phone]

        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        try:
            for s_id in target_staff_ids:
                cursor.execute('''
                INSERT INTO attendance (project_id, session_id, staff_id, status, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id, session_id, staff_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at
                ''', (project_id, session_id, s_id, status_val, now_iso))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def update_card_status(self, project_id, session_id, staff_id, card_val, sync_same_phone=True):
        staff = staff_manager.get_staff_member(staff_id)
        if not staff:
            return False
        target_staff_ids = [staff_id]
        phone = str(staff.get("phone", "")).strip()
        if sync_same_phone and phone and phone not in ("None", "-", ""):
            phone_staff = staff_manager.list_staff(project_id, active_only=True)
            target_staff_ids = [s["id"] for s in phone_staff if str(s.get("phone", "")).strip() == phone]

        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        try:
            for s_id in target_staff_ids:
                cursor.execute('''
                INSERT INTO attendance (project_id, session_id, staff_id, card_status, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id, session_id, staff_id) DO UPDATE SET
                    card_status = excluded.card_status,
                    updated_at = excluded.updated_at
                ''', (project_id, session_id, s_id, card_val, now_iso))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def add_late_tracking(self, project_id, session_id, staff_id, note):
        record = self.get_staff_session_attendance(project_id, session_id, staff_id)
        if not record:
            return False
        current_note = record.get("late_tracking", "")
        now_time = datetime.now().strftime("%H:%M")
        new_entry = f"[📞 {now_time} - {note.strip()}]"
        final_note = f"{current_note} | {new_entry}" if current_note and current_note not in ("None", "-", "") else new_entry

        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        try:
            cursor.execute('''
            INSERT INTO attendance (project_id, session_id, staff_id, late_tracking, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_id, session_id, staff_id) DO UPDATE SET
                late_tracking = excluded.late_tracking,
                updated_at = excluded.updated_at
            ''', (project_id, session_id, staff_id, final_note, now_iso))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def add_description(self, project_id, session_id, staff_id, note):
        record = self.get_staff_session_attendance(project_id, session_id, staff_id)
        if not record:
            return False
        current_desc = record.get("description", "")
        now_time = datetime.now().strftime("%H:%M")
        new_entry = f"({now_time}) {note.strip()}"
        final_desc = f"{current_desc} | {new_entry}" if current_desc and current_desc not in ("None", "-", "") else new_entry

        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        try:
            cursor.execute('''
            INSERT INTO attendance (project_id, session_id, staff_id, description, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_id, session_id, staff_id) DO UPDATE SET
                description = excluded.description,
                updated_at = excluded.updated_at
            ''', (project_id, session_id, staff_id, final_desc, now_iso))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def get_latecomers(self, project_id, session_id):
        all_att = self.get_session_attendance(project_id, session_id)
        now = datetime.now()
        latecomers = []
        for u in all_att:
            status = str(u.get("status", "")).strip()
            shift_str = str(u.get("shift_time", "")).strip()
            if status in ["حاضر", "غایب", "بدون شیفت", "هماهنگ شده"] or "تاخیر" in status:
                continue
            if not shift_str or ":" not in shift_str:
                continue
            delay_mins = format_delay_minutes(shift_str, now)
            if delay_mins >= 10:
                u["delay_mins"] = delay_mins
                latecomers.append(u)
        return latecomers

    def mark_empty_as_absent(self, project_id, session_id):
        all_att = self.get_session_attendance(project_id, session_id)
        now_iso = datetime.now().isoformat()
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        updated_count = 0
        for u in all_att:
            status = str(u.get("status", "")).strip()
            if not status or status == "None":
                cursor.execute('''
                INSERT INTO attendance (project_id, session_id, staff_id, status, updated_at)
                VALUES (?, ?, ?, 'غایب', ?)
                ON CONFLICT(project_id, session_id, staff_id) DO UPDATE SET
                    status = 'غایب',
                    updated_at = excluded.updated_at
                ''', (project_id, session_id, u["staff_id"], now_iso))
                updated_count += 1
        conn.commit()
        conn.close()
        return updated_count

attendance_manager = AttendanceManager()
