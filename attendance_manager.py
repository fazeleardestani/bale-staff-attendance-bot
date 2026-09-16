import os
import sys
import importlib.abc
import importlib.util
import logging

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
from permission_manager import permission_manager

class AttendanceManager:
    def __init__(self, db=db_instance):
        self.db = db

    def create_schedule(self, project_id, name, day_of_week, time_str, location=""):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO schedules (project_id, name, day_of_week, time_str, location, is_active)
        VALUES (?, ?, ?, ?, ?, 1)
        """, (project_id, name.strip(), day_of_week.strip(), time_str.strip(), location.strip()))
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
        """
        Creates a session strictly owned by its schedule if provided.
        Seeds eligible staff roster at creation time and creates immutable historical snapshot.
        """
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        
        now_iso = datetime.now().isoformat()
        date_str = session_date or datetime.now().strftime("%Y-%m-%d")

        cursor.execute("""
        INSERT INTO sessions (project_id, schedule_id, name, session_date, time_str, day_of_week, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'SCHEDULED', ?)
        """, (project_id, schedule_id, name.strip(), date_str, str(time_str or '').strip(), str(day_of_week or '').strip(), now_iso))
        session_id = cursor.lastrowid
        conn.commit()

        # Determine eligible staff for this session:
        # If schedule_id is provided and schedule has explicit staff in schedule_staff, use them.
        # Otherwise fallback to active project staff whose lifecycle encompasses session_id.
        eligible_staff = []
        if schedule_id is not None:
            cursor.execute("""
            SELECT ps.id, ps.name, ps.unit, ps.section, ps.position, ps.gender
            FROM schedule_staff ss
            JOIN project_staff ps ON ss.staff_id = ps.id
            WHERE ss.schedule_id = ? AND ss.is_active = 1 AND ps.is_active = 1
              AND (ss.start_session_id IS NULL OR ss.start_session_id <= ?)
              AND (ss.end_session_id IS NULL OR ss.end_session_id >= ?)
              AND (ps.start_session_id IS NULL OR ps.start_session_id <= ?)
              AND (ps.end_session_id IS NULL OR ps.end_session_id >= ?)
            ORDER BY ps.id ASC
            """, (schedule_id, session_id, session_id, session_id, session_id))
            eligible_staff = cursor.fetchall()

        if not eligible_staff:
            cursor.execute("""
            SELECT id, name, unit, section, position, gender 
            FROM project_staff 
            WHERE project_id = ? AND is_active = 1
              AND (start_session_id IS NULL OR start_session_id <= ?)
              AND (end_session_id IS NULL OR end_session_id >= ?)
            ORDER BY id ASC
            """, (project_id, session_id, session_id))
            eligible_staff = cursor.fetchall()

        for s in eligible_staff:
            cursor.execute("""
            INSERT INTO attendance (
                project_id, session_id, staff_id, status, card_status,
                staff_name_snapshot, unit_snapshot, section_snapshot, position_snapshot, gender_snapshot,
                updated_at
            )
            VALUES (?, ?, ?, '', '', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, session_id, staff_id) DO NOTHING
            """, (
                project_id, session_id, s['id'],
                s['name'], s['unit'], s['section'], s['position'], s['gender'],
                now_iso
            ))
        conn.commit()
        conn.close()

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

    def list_sessions(self, project_id, schedule_id=None, include_cancelled=False):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        query = "SELECT * FROM sessions WHERE project_id = ?"
        params = [project_id]
        if schedule_id is not None:
            query += " AND schedule_id = ?"
            params.append(schedule_id)
        if not include_cancelled:
            query += " AND status != 'CANCELLED'"
        query += " ORDER BY id ASC"
        cursor.execute(query, params)
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
        """
        Pure read of historical attendance with immutable snapshots.
        STRICT REQUIREMENT: Absolutely NO silent automatic re-seeding of old sessions.
        """
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()

        cursor.execute("""
        SELECT 
            s.id as staff_id, s.project_id, 
            COALESCE(NULLIF(a.staff_name_snapshot, ''), s.name) as name,
            s.phone, 
            COALESCE(NULLIF(a.unit_snapshot, ''), s.unit) as unit,
            COALESCE(NULLIF(a.section_snapshot, ''), s.section) as section,
            COALESCE(NULLIF(a.position_snapshot, ''), s.position) as position,
            s.card_title, s.shift_time, 
            COALESCE(NULLIF(a.gender_snapshot, ''), s.gender) as gender,
            s.notes as staff_notes,
            s.is_multi_section, s._excel_row, s.staff_code,
            s.start_session_id, s.end_session_id,
            COALESCE(a.id, 0) as attendance_id,
            COALESCE(a.status, '') as status,
            COALESCE(a.card_status, '') as card_status,
            COALESCE(a.late_tracking, '') as late_tracking,
            COALESCE(a.description, '') as description,
            a.updated_at
        FROM attendance a
        JOIN project_staff s ON a.staff_id = s.id
        WHERE a.project_id = ? AND a.session_id = ?
        ORDER BY s.id ASC
        """, (project_id, session_id))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def seed_session_roster_explicit(self, project_id, session_id, actor_user_id):
        """
        Explicit admin-only tool to seed or re-seed an empty session if authorized.
        Prevents unauthorized or implicit mutation of historical records.
        """
        allowed, reason, role = permission_manager.authorize_attendance_action(
            actor_user_id, project_id, session_id=session_id, action="manage_project"
        )
        if not allowed or role not in ('super_admin', 'admin'):
            logging.warning(f"Explicit re-seed denied for {actor_user_id}: {reason}")
            return False, "دسترسی فقط برای مدیران پروژه مجاز است"

        sess = self.get_session(session_id)
        if not sess:
            return False, "جلسه یافت نشد"

        schedule_id = sess.get('schedule_id')
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()

        eligible_staff = []
        if schedule_id is not None:
            cursor.execute("""
            SELECT ps.id, ps.name, ps.unit, ps.section, ps.position, ps.gender
            FROM schedule_staff ss
            JOIN project_staff ps ON ss.staff_id = ps.id
            WHERE ss.schedule_id = ? AND ss.is_active = 1 AND ps.is_active = 1
              AND (ss.start_session_id IS NULL OR ss.start_session_id <= ?)
              AND (ss.end_session_id IS NULL OR ss.end_session_id >= ?)
              AND (ps.start_session_id IS NULL OR ps.start_session_id <= ?)
              AND (ps.end_session_id IS NULL OR ps.end_session_id >= ?)
            ORDER BY ps.id ASC
            """, (schedule_id, session_id, session_id, session_id, session_id))
            eligible_staff = cursor.fetchall()

        if not eligible_staff:
            cursor.execute("""
            SELECT id, name, unit, section, position, gender 
            FROM project_staff 
            WHERE project_id = ? AND is_active = 1
              AND (start_session_id IS NULL OR start_session_id <= ?)
              AND (end_session_id IS NULL OR end_session_id >= ?)
            ORDER BY id ASC
            """, (project_id, session_id, session_id))
            eligible_staff = cursor.fetchall()

        added_cnt = 0
        for s in eligible_staff:
            cursor.execute("""
            INSERT INTO attendance (
                project_id, session_id, staff_id, status, card_status,
                staff_name_snapshot, unit_snapshot, section_snapshot, position_snapshot, gender_snapshot,
                updated_at
            )
            VALUES (?, ?, ?, '', '', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, session_id, staff_id) DO NOTHING
            """, (
                project_id, session_id, s['id'],
                s['name'], s['unit'], s['section'], s['position'], s['gender'],
                now_iso
            ))
            added_cnt += cursor.rowcount

        conn.commit()
        conn.close()
        return True, f"{added_cnt} رکورد کادر با موفقیت افزوده شد"

    def get_staff_session_attendance(self, project_id, session_id, staff_id):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT 
            s.id as staff_id, s.project_id, 
            COALESCE(NULLIF(a.staff_name_snapshot, ''), s.name) as name,
            s.phone, 
            COALESCE(NULLIF(a.unit_snapshot, ''), s.unit) as unit,
            COALESCE(NULLIF(a.section_snapshot, ''), s.section) as section,
            COALESCE(NULLIF(a.position_snapshot, ''), s.position) as position,
            s.card_title, s.shift_time, 
            COALESCE(NULLIF(a.gender_snapshot, ''), s.gender) as gender,
            s.notes as staff_notes,
            s.is_multi_section, s._excel_row, s.staff_code,
            s.start_session_id, s.end_session_id,
            COALESCE(a.status, '') as status,
            COALESCE(a.card_status, '') as card_status,
            COALESCE(a.late_tracking, '') as late_tracking,
            COALESCE(a.description, '') as description
        FROM attendance a
        JOIN project_staff s ON a.staff_id = s.id
        WHERE a.project_id = ? AND a.session_id = ? AND a.staff_id = ?
        """, (project_id, session_id, staff_id))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def update_attendance_status(self, project_id, session_id, staff_id, status_val, actor_user_id=None, sync_same_phone=True):
        if actor_user_id is not None:
            allowed, reason, _ = permission_manager.authorize_attendance_action(
                actor_user_id, project_id, session_id=session_id, staff_id=staff_id, action="update_attendance"
            )
            if not allowed:
                logging.warning(f"update_attendance_status denied for actor {actor_user_id} on staff {staff_id}: {reason}")
                return False

        staff = staff_manager.get_staff_member(staff_id)
        if not staff:
            return False
        if status_val == "حاضر":
            status_val = calculate_status_with_delay(staff.get("shift_time", ""))

        target_staff_ids = [staff_id]
        phone = str(staff.get("phone", "")).strip()

        # Strict phone synchronization boundary: ONLY same project AND same session AND valid attendance record
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()

        if sync_same_phone and phone and phone not in ("None", "-", ""):
            cursor.execute("""
            SELECT s.id 
            FROM project_staff s
            WHERE s.project_id = ? AND s.phone = ? AND s.is_active = 1
              AND (s.start_session_id IS NULL OR s.start_session_id <= ?)
              AND (s.end_session_id IS NULL OR s.end_session_id >= ?)
            """, (project_id, phone, session_id, session_id))
            matched = [r[0] for r in cursor.fetchall()]
            if matched:
                target_staff_ids = matched
            if staff_id not in target_staff_ids:
                target_staff_ids.append(staff_id)

        now_iso = datetime.now().isoformat()
        try:
            for s_id in target_staff_ids:
                s_rec = staff_manager.get_staff_member(s_id)
                cursor.execute("""
                INSERT INTO attendance (
                    project_id, session_id, staff_id, status, 
                    staff_name_snapshot, unit_snapshot, section_snapshot, position_snapshot, gender_snapshot, 
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, session_id, staff_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """, (
                    project_id, session_id, s_id, status_val,
                    s_rec['name'] if s_rec else '',
                    s_rec['unit'] if s_rec else '',
                    s_rec['section'] if s_rec else '',
                    s_rec['position'] if s_rec else '',
                    s_rec['gender'] if s_rec else '',
                    now_iso
                ))
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"update_attendance_status error: {e}")
            return False
        finally:
            conn.close()

    def update_card_status(self, project_id, session_id, staff_id, card_val, actor_user_id=None, sync_same_phone=True):
        if actor_user_id is not None:
            allowed, reason, _ = permission_manager.authorize_attendance_action(
                actor_user_id, project_id, session_id=session_id, staff_id=staff_id, action="update_card"
            )
            if not allowed:
                logging.warning(f"update_card_status denied for actor {actor_user_id} on staff {staff_id}: {reason}")
                return False

        staff = staff_manager.get_staff_member(staff_id)
        if not staff:
            return False

        target_staff_ids = [staff_id]
        phone = str(staff.get("phone", "")).strip()

        # Strict phone synchronization boundary: ONLY same project AND same session AND valid attendance record
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()

        if sync_same_phone and phone and phone not in ("None", "-", ""):
            cursor.execute("""
            SELECT s.id 
            FROM project_staff s
            WHERE s.project_id = ? AND s.phone = ? AND s.is_active = 1
              AND (s.start_session_id IS NULL OR s.start_session_id <= ?)
              AND (s.end_session_id IS NULL OR s.end_session_id >= ?)
            """, (project_id, phone, session_id, session_id))
            matched = [r[0] for r in cursor.fetchall()]
            if matched:
                target_staff_ids = matched
            if staff_id not in target_staff_ids:
                target_staff_ids.append(staff_id)

        now_iso = datetime.now().isoformat()
        try:
            for s_id in target_staff_ids:
                s_rec = staff_manager.get_staff_member(s_id)
                cursor.execute("""
                INSERT INTO attendance (
                    project_id, session_id, staff_id, card_status,
                    staff_name_snapshot, unit_snapshot, section_snapshot, position_snapshot, gender_snapshot,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, session_id, staff_id) DO UPDATE SET
                    card_status = excluded.card_status,
                    updated_at = excluded.updated_at
                """, (
                    project_id, session_id, s_id, card_val,
                    s_rec['name'] if s_rec else '',
                    s_rec['unit'] if s_rec else '',
                    s_rec['section'] if s_rec else '',
                    s_rec['position'] if s_rec else '',
                    s_rec['gender'] if s_rec else '',
                    now_iso
                ))
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"update_card_status error: {e}")
            return False
        finally:
            conn.close()

    def add_late_tracking(self, project_id, session_id, staff_id, note, actor_user_id=None):
        if actor_user_id is not None:
            allowed, reason, _ = permission_manager.authorize_attendance_action(
                actor_user_id, project_id, session_id=session_id, staff_id=staff_id, action="add_late"
            )
            if not allowed:
                logging.warning(f"add_late_tracking denied for actor {actor_user_id} on staff {staff_id}: {reason}")
                return False

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
            cursor.execute("""
            UPDATE attendance 
            SET late_tracking = ?, updated_at = ?
            WHERE project_id = ? AND session_id = ? AND staff_id = ?
            """, (final_note, now_iso, project_id, session_id, staff_id))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def add_description(self, project_id, session_id, staff_id, note, actor_user_id=None):
        if actor_user_id is not None:
            allowed, reason, _ = permission_manager.authorize_attendance_action(
                actor_user_id, project_id, session_id=session_id, staff_id=staff_id, action="edit_desc"
            )
            if not allowed:
                logging.warning(f"add_description denied for actor {actor_user_id} on staff {staff_id}: {reason}")
                return False

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
            cursor.execute("""
            UPDATE attendance 
            SET description = ?, updated_at = ?
            WHERE project_id = ? AND session_id = ? AND staff_id = ?
            """, (final_desc, now_iso, project_id, session_id, staff_id))
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
                cursor.execute("""
                UPDATE attendance
                SET status = 'غایب', updated_at = ?
                WHERE project_id = ? AND session_id = ? AND staff_id = ?
                """, (now_iso, project_id, session_id, u["staff_id"]))
                updated_count += 1
        conn.commit()
        conn.close()
        return updated_count

attendance_manager = AttendanceManager()
