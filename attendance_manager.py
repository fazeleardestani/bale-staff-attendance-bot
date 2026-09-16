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

    def list_schedules(self, project_id, active_only=True):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        query = "SELECT * FROM schedules WHERE project_id = ?"
        if active_only:
            query += " AND is_active = 1"
        query += " ORDER BY id ASC"
        cursor.execute(query, (project_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def set_schedule_active(self, schedule_id, is_active):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE schedules SET is_active = ? WHERE id = ?", (1 if is_active else 0, schedule_id))
        conn.commit()
        conn.close()
        return True

    def create_session(self, project_id, name, session_date=None, time_str='', day_of_week='',
                       schedule_id=None, copy_from_prev_session=True, sync_excel_sheet=True,
                       return_details=False):
        """
        Creates a session strictly owned by its schedule if provided.
        Enforces schedule project-ownership and unique calendar identity (project_id, schedule_id, session_date).
        If existing session found, returns (existing_id, False) when return_details=True, or existing_id.
        If created, returns (session_id, True) when return_details=True, or session_id.
        STRICT REQUIREMENT: If schedule_id is provided, only schedule_staff is used.
        NO FALLBACK to project_staff if schedule_staff is empty.
        Atomic transaction: session + initial attendance snapshot commit together.
        """
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()

        # Enforce schedule ownership: schedule must belong to this project and be active
        if schedule_id is not None:
            cursor.execute("SELECT id FROM schedules WHERE id = ? AND project_id = ? AND is_active = 1", (schedule_id, project_id))
            if not cursor.fetchone():
                conn.close()
                raise ValueError(f"برنامه با شناسه {schedule_id} متعلق به این پروژه نیست یا فعال نمی‌باشد.")

        now_iso = datetime.now().isoformat()
        date_str = str(session_date or '').strip()

        # Check existing session by unique schedule + date constraint
        if schedule_id is not None and date_str:
            cursor.execute("""
            SELECT id FROM sessions 
            WHERE project_id = ? AND schedule_id = ? AND session_date = ? AND status != 'CANCELLED'
            """, (project_id, schedule_id, date_str))
            existing = cursor.fetchone()
            if existing:
                conn.close()
                logging.info(f"Existing session found for project {project_id}, schedule {schedule_id}, date {date_str} (ID {existing['id']})")
                if return_details:
                    return existing['id'], False
                return existing['id']

        try:
            with conn:
                cursor.execute("""
                INSERT INTO sessions (project_id, schedule_id, name, session_date, time_str, day_of_week, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'SCHEDULED', ?)
                """, (project_id, schedule_id, name.strip(), date_str, str(time_str or '').strip(), str(day_of_week or '').strip(), now_iso))
                session_id = cursor.lastrowid

                # STRICT ROSTER DETERMINATION:
                # If schedule_id is provided, use ONLY schedule_staff. Zero fallback to project_staff!
                if schedule_id is not None:
                    cursor.execute("""
                    SELECT ps.id, ps.name, ps.unit, ps.section, ps.position, ps.gender,
                           ps.phone, ps.card_title, ps.shift_time, ps.is_multi_section, ps.staff_code
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
                else:
                    # General project without schedule: use project_staff
                    cursor.execute("""
                    SELECT id, name, unit, section, position, gender,
                           phone, card_title, shift_time, is_multi_section, staff_code
                    FROM project_staff 
                    WHERE project_id = ? AND is_active = 1
                      AND (start_session_id IS NULL OR start_session_id <= ?)
                      AND (end_session_id IS NULL OR end_session_id >= ?)
                    ORDER BY id ASC
                    """, (project_id, session_id, session_id))
                    eligible_staff = cursor.fetchall()

                # Snapshot initial roster into attendance table with all historical fields
                for s in eligible_staff:
                    cursor.execute("""
                    INSERT INTO attendance (
                        project_id, session_id, staff_id, status, card_status,
                        staff_name_snapshot, unit_snapshot, section_snapshot, position_snapshot, gender_snapshot,
                        phone_snapshot, card_title_snapshot, shift_time_snapshot, is_multi_section_snapshot, staff_code_snapshot,
                        updated_at
                    )
                    VALUES (?, ?, ?, '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, session_id, staff_id) DO NOTHING
                    """, (
                        project_id, session_id, s['id'],
                        s['name'], s['unit'], s['section'], s['position'], s['gender'],
                        s['phone'], s['card_title'], s['shift_time'], s['is_multi_section'], s['staff_code'],
                        now_iso
                    ))
        except Exception as e:
            logging.error(f"Atomic session creation failed: {e}")
            raise
        finally:
            conn.close()

        if sync_excel_sheet:
            try:
                from excel_manager import excel_manager
                excel_manager.add_session_sheet(project_id, name.strip())
            except Exception as ex_err:
                logging.warning(f"Excel sheet sync notice: {ex_err}")

        if return_details:
            return session_id, True
        return session_id

    def get_or_create_session(self, project_id, name, session_date=None, time_str='', day_of_week='',
                              schedule_id=None, copy_from_prev_session=True, sync_excel_sheet=True):
        """Returns (session_id, is_created: bool) clearly indicating whether a new session was created."""
        return self.create_session(
            project_id, name, session_date=session_date, time_str=time_str,
            day_of_week=day_of_week, schedule_id=schedule_id,
            copy_from_prev_session=copy_from_prev_session,
            sync_excel_sheet=sync_excel_sheet, return_details=True
        )

    def get_session(self, session_id):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_session_by_name(self, project_id, name, schedule_id=None):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        if schedule_id is not None:
            cursor.execute("""
            SELECT * FROM sessions 
            WHERE project_id = ? AND name = ? AND schedule_id = ?
            """, (project_id, name.strip(), schedule_id))
        else:
            cursor.execute("""
            SELECT * FROM sessions 
            WHERE project_id = ? AND name = ?
            """, (project_id, name.strip()))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_session_by_date_and_schedule(self, project_id, schedule_id, session_date):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT * FROM sessions 
        WHERE project_id = ? AND schedule_id = ? AND session_date = ?
        """, (project_id, schedule_id, session_date.strip()))
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
        """
        Updates session fields strictly preserving architectural invariants:
        1. Immutability of schedule_id if session already has attendance records.
        2. Project ownership & active check when updating schedule_id.
        3. Collision prevention on (project_id, schedule_id, session_date) when updating schedule_id or session_date.
        """
        allowed = ['name', 'session_date', 'time_str', 'day_of_week', 'status', 'schedule_id']
        if field_name not in allowed:
            return False
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        sess = cursor.fetchone()
        if not sess:
            conn.close()
            raise ValueError("جلسه مورد نظر یافت نشد.")

        project_id = sess['project_id']
        current_sched_id = sess['schedule_id']
        current_date = sess['session_date']

        # P0 Requirement 1: Schedule Immutability if Attendance Exists & Project Ownership
        if field_name == 'schedule_id':
            if value != current_sched_id:
                cursor.execute("SELECT COUNT(*) FROM attendance WHERE session_id = ?", (session_id,))
                att_cnt = cursor.fetchone()[0]
                if att_cnt > 0:
                    conn.close()
                    raise ValueError("تغییر برنامه/کلاس برای جلسه‌ای که دارای سابقه حضور و غیاب است غیرمجاز می‌باشد.")

            if value is not None:
                cursor.execute("SELECT id, project_id, is_active FROM schedules WHERE id = ?", (value,))
                sched = cursor.fetchone()
                if not sched or sched['project_id'] != project_id:
                    conn.close()
                    raise ValueError("برنامه انتخابی متعلق به این پروژه نیست.")
                if not sched['is_active']:
                    conn.close()
                    raise ValueError("برنامه انتخابی غیرفعال است و امکان اتصال جلسه به آن وجود ندارد.")

                # Collision check on (project_id, schedule_id, session_date)
                if current_date:
                    cursor.execute("""
                    SELECT id FROM sessions 
                    WHERE project_id = ? AND schedule_id = ? AND session_date = ? AND id != ? AND status != 'CANCELLED'
                    """, (project_id, value, current_date, session_id))
                    if cursor.fetchone():
                        conn.close()
                        raise ValueError(f"در تاریخ {current_date} برای این برنامه جلسه دیگری از قبل وجود دارد.")

        # P0 Requirement 2: Collision check on session_date
        if field_name == 'session_date':
            new_date = str(value or '').strip()
            if current_sched_id is not None and new_date:
                cursor.execute("""
                SELECT id FROM sessions 
                WHERE project_id = ? AND schedule_id = ? AND session_date = ? AND id != ? AND status != 'CANCELLED'
                """, (project_id, current_sched_id, new_date, session_id))
                if cursor.fetchone():
                    conn.close()
                    raise ValueError(f"در تاریخ {new_date} برای این برنامه جلسه دیگری از قبل وجود دارد.")

        cursor.execute(f"UPDATE sessions SET {field_name} = ? WHERE id = ?", (value, session_id))
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
        Reads exclusively from attendance snapshot fields. Logs warning if snapshot is missing.
        """
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()

        cursor.execute("""
        SELECT 
            a.staff_id, 
            a.project_id, 
            a.session_id,
            a.staff_name_snapshot,
            a.unit_snapshot,
            a.section_snapshot,
            a.position_snapshot,
            a.gender_snapshot,
            a.phone_snapshot,
            a.card_title_snapshot,
            a.shift_time_snapshot,
            a.is_multi_section_snapshot,
            a.staff_code_snapshot,
            COALESCE(a.id, 0) as attendance_id,
            COALESCE(a.status, '') as status,
            COALESCE(a.card_status, '') as card_status,
            COALESCE(a.late_tracking, '') as late_tracking,
            COALESCE(a.description, '') as description,
            a.updated_at
        FROM attendance a
        WHERE a.project_id = ? AND a.session_id = ?
        ORDER BY a.staff_id ASC
        """, (project_id, session_id))
        rows = cursor.fetchall()
        conn.close()

        result = []
        for r in rows:
            d = dict(r)
            if not d.get('staff_name_snapshot'):
                logging.warning(f"Data integrity warning: attendance record {d.get('attendance_id')} for staff {d.get('staff_id')} in session {session_id} has missing snapshot!")
                d['name'] = '[ثبت نشده]'
            else:
                d['name'] = d['staff_name_snapshot']

            d['unit'] = d.get('unit_snapshot') or '-'
            d['section'] = d.get('section_snapshot') or '-'
            d['position'] = d.get('position_snapshot') or 'نیرو'
            d['gender'] = d.get('gender_snapshot') or ''
            d['phone'] = d.get('phone_snapshot') or ''
            d['card_title'] = d.get('card_title_snapshot') or d['section']
            d['shift_time'] = d.get('shift_time_snapshot') or ''
            d['is_multi_section'] = d.get('is_multi_section_snapshot') or 'خیر'
            d['staff_code'] = d.get('staff_code_snapshot') or ''
            d['notes'] = ''
            d['staff_notes'] = ''
            d['_excel_row'] = None
            d['start_session_id'] = None
            d['end_session_id'] = None
            result.append(d)

        return result

    def seed_session_roster_explicit(self, project_id, session_id, actor_user_id):
        """
        Explicit admin-only tool to seed an empty session if authorized.
        STRICT REQUIREMENT: If session has schedule_id, ONLY schedule_staff is used.
        NO fallback to project_staff.
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

        if schedule_id is not None:
            cursor.execute("""
            SELECT ps.id, ps.name, ps.unit, ps.section, ps.position, ps.gender,
                   ps.phone, ps.card_title, ps.shift_time, ps.is_multi_section, ps.staff_code
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
        else:
            cursor.execute("""
            SELECT id, name, unit, section, position, gender,
                   phone, card_title, shift_time, is_multi_section, staff_code
            FROM project_staff 
            WHERE project_id = ? AND is_active = 1
              AND (start_session_id IS NULL OR start_session_id <= ?)
              AND (end_session_id IS NULL OR end_session_id >= ?)
            ORDER BY id ASC
            """, (project_id, session_id, session_id))
            eligible_staff = cursor.fetchall()

        added_cnt = 0
        with conn:
            for s in eligible_staff:
                cursor.execute("""
                INSERT INTO attendance (
                    project_id, session_id, staff_id, status, card_status,
                    staff_name_snapshot, unit_snapshot, section_snapshot, position_snapshot, gender_snapshot,
                    phone_snapshot, card_title_snapshot, shift_time_snapshot, is_multi_section_snapshot, staff_code_snapshot,
                    updated_at
                )
                VALUES (?, ?, ?, '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, session_id, staff_id) DO NOTHING
                """, (
                    project_id, session_id, s['id'],
                    s['name'], s['unit'], s['section'], s['position'], s['gender'],
                    s['phone'], s['card_title'], s['shift_time'], s['is_multi_section'], s['staff_code'],
                    now_iso
                ))
                added_cnt += cursor.rowcount

        conn.close()
        return True, f"{added_cnt} رکورد کادر با موفقیت افزوده شد"

    def add_staff_to_session_roster(self, project_id, session_id, staff_ids):
        """
        Adds specific staff members to a session's attendance roster with complete immutable snapshots.
        """
        if not staff_ids:
            return True, 0
        if isinstance(staff_ids, int):
            staff_ids = [staff_ids]

        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        placeholders = ','.join(['?'] * len(staff_ids))
        cursor.execute(f"""
        SELECT id, name, unit, section, position, gender,
               phone, card_title, shift_time, is_multi_section, staff_code
        FROM project_staff
        WHERE project_id = ? AND id IN ({placeholders})
        """, [project_id] + list(staff_ids))
        staff_rows = cursor.fetchall()

        added_cnt = 0
        with conn:
            for s in staff_rows:
                cursor.execute("""
                INSERT INTO attendance (
                    project_id, session_id, staff_id, status, card_status,
                    staff_name_snapshot, unit_snapshot, section_snapshot, position_snapshot, gender_snapshot,
                    phone_snapshot, card_title_snapshot, shift_time_snapshot, is_multi_section_snapshot, staff_code_snapshot,
                    updated_at
                )
                VALUES (?, ?, ?, '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, session_id, staff_id) DO NOTHING
                """, (
                    project_id, session_id, s['id'],
                    s['name'], s['unit'], s['section'], s['position'], s['gender'],
                    s['phone'], s['card_title'], s['shift_time'], s['is_multi_section'], s['staff_code'],
                    now_iso
                ))
                added_cnt += cursor.rowcount
        conn.close()
        return True, added_cnt

    def get_staff_session_attendance(self, project_id, session_id, staff_id):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT 
            a.staff_id, 
            a.project_id, 
            a.session_id,
            a.staff_name_snapshot,
            a.unit_snapshot,
            a.section_snapshot,
            a.position_snapshot,
            a.gender_snapshot,
            a.phone_snapshot,
            a.card_title_snapshot,
            a.shift_time_snapshot,
            a.is_multi_section_snapshot,
            a.staff_code_snapshot,
            COALESCE(a.id, 0) as attendance_id,
            COALESCE(a.status, '') as status,
            COALESCE(a.card_status, '') as card_status,
            COALESCE(a.late_tracking, '') as late_tracking,
            COALESCE(a.description, '') as description,
            a.updated_at
        FROM attendance a
        WHERE a.project_id = ? AND a.session_id = ? AND a.staff_id = ?
        """, (project_id, session_id, staff_id))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        d = dict(row)
        d['name'] = d.get('staff_name_snapshot') or '[ثبت نشده]'
        d['unit'] = d.get('unit_snapshot') or '-'
        d['section'] = d.get('section_snapshot') or '-'
        d['position'] = d.get('position_snapshot') or 'نیرو'
        d['gender'] = d.get('gender_snapshot') or ''
        d['phone'] = d.get('phone_snapshot') or ''
        d['card_title'] = d.get('card_title_snapshot') or d['section']
        d['shift_time'] = d.get('shift_time_snapshot') or ''
        d['is_multi_section'] = d.get('is_multi_section_snapshot') or 'خیر'
        d['staff_code'] = d.get('staff_code_snapshot') or ''
        d['notes'] = ''
        d['staff_notes'] = ''
        d['_excel_row'] = None
        d['start_session_id'] = None
        d['end_session_id'] = None
        return d

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

        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()

        # STRICT PHONE SYNC: Query attendance table of THIS session only
        if sync_same_phone and phone and phone not in ("None", "-", ""):
            cursor.execute("""
            SELECT a.staff_id
            FROM attendance a
            JOIN project_staff s ON s.id = a.staff_id
            WHERE a.project_id = ?
              AND a.session_id = ?
              AND s.phone = ?
              AND s.phone != ''
              AND s.phone IS NOT NULL
              AND s.is_active = 1
            """, (project_id, session_id, phone))
            matched = [r[0] for r in cursor.fetchall()]
            if matched:
                target_staff_ids = matched
            if staff_id not in target_staff_ids:
                target_staff_ids.append(staff_id)

        now_iso = datetime.now().isoformat()
        try:
            with conn:
                for s_id in target_staff_ids:
                    cursor.execute("""
                    UPDATE attendance 
                    SET status = ?, updated_at = ?
                    WHERE project_id = ? AND session_id = ? AND staff_id = ?
                    """, (status_val, now_iso, project_id, session_id, s_id))
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

        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()

        # STRICT PHONE SYNC: Query attendance table of THIS session only
        if sync_same_phone and phone and phone not in ("None", "-", ""):
            cursor.execute("""
            SELECT a.staff_id
            FROM attendance a
            JOIN project_staff s ON s.id = a.staff_id
            WHERE a.project_id = ?
              AND a.session_id = ?
              AND s.phone = ?
              AND s.phone != ''
              AND s.phone IS NOT NULL
              AND s.is_active = 1
            """, (project_id, session_id, phone))
            matched = [r[0] for r in cursor.fetchall()]
            if matched:
                target_staff_ids = matched
            if staff_id not in target_staff_ids:
                target_staff_ids.append(staff_id)

        now_iso = datetime.now().isoformat()
        try:
            with conn:
                for s_id in target_staff_ids:
                    cursor.execute("""
                    UPDATE attendance 
                    SET card_status = ?, updated_at = ?
                    WHERE project_id = ? AND session_id = ? AND staff_id = ?
                    """, (card_val, now_iso, project_id, session_id, s_id))
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
