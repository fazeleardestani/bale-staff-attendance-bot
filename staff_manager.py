import os
import sys
import logging
from datetime import datetime
from db_manager import db_instance
from utils import normalize_persian

class StaffManager:
    def __init__(self, db=db_instance):
        self.db = db

    def add_staff_member(self, project_id, name, phone="", unit="", section="", position='نیرو', 
                         card_title=None, shift_time='', gender='', notes='', 
                         is_multi_section='خیر', excel_row=None, staff_code=None,
                         start_session_id=None, end_session_id=None, schedule_id=None):
        """
        Atomically creates or updates staff member and assigns to schedule within a single transaction.
        If schedule validation or assignment fails, entire operation rolls back.
        """
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        c_title = card_title if card_title is not None else section
        clean_code = str(staff_code).strip() if staff_code else None

        try:
            with conn:
                # If schedule_id is provided, verify it belongs to this project
                if schedule_id is not None:
                    cursor.execute("SELECT id FROM schedules WHERE id = ? AND project_id = ? AND is_active = 1", (schedule_id, project_id))
                    if not cursor.fetchone():
                        raise ValueError(f"برنامه {schedule_id} متعلق به پروژه {project_id} نیست.")

                staff_id = None
                # If clean_code exists, check if staff already exists in this project
                if clean_code:
                    cursor.execute("SELECT id FROM project_staff WHERE project_id = ? AND staff_code = ?", (project_id, clean_code))
                    existing = cursor.fetchone()
                    if existing:
                        staff_id = existing['id']
                        cursor.execute("""
                        UPDATE project_staff SET
                            name = ?, phone = COALESCE(NULLIF(?, ''), phone),
                            unit = ?, section = ?, position = ?,
                            card_title = ?, shift_time = COALESCE(NULLIF(?, ''), shift_time),
                            gender = COALESCE(NULLIF(?, ''), gender),
                            notes = COALESCE(NULLIF(?, ''), notes),
                            is_active = 1
                        WHERE id = ?
                        """, (name.strip(), str(phone or '').strip(), unit.strip(), section.strip(), position.strip(),
                              str(c_title).strip(), str(shift_time or '').strip(), gender.strip(), notes.strip(), staff_id))

                if not staff_id:
                    cursor.execute("""
                    INSERT INTO project_staff 
                    (project_id, staff_code, name, phone, unit, section, position, card_title, shift_time, gender, is_active, notes, is_multi_section, start_session_id, end_session_id, _excel_row)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    """, (project_id, clean_code, name.strip(), str(phone or '').strip(), unit.strip(), section.strip(), 
                          position.strip(), str(c_title).strip(), str(shift_time or '').strip(), 
                          gender.strip(), notes.strip(), is_multi_section, start_session_id, end_session_id, excel_row))
                    staff_id = cursor.lastrowid

                    if not clean_code:
                        auto_code = f"STF-{staff_id:05d}"
                        cursor.execute("UPDATE project_staff SET staff_code = ? WHERE id = ?", (auto_code, staff_id))

                if schedule_id is not None:
                    now_iso = datetime.now().isoformat()
                    cursor.execute("""
                    INSERT INTO schedule_staff (schedule_id, staff_id, start_session_id, end_session_id, is_active, created_at)
                    VALUES (?, ?, ?, ?, 1, ?)
                    ON CONFLICT(schedule_id, staff_id) DO UPDATE SET
                        start_session_id = COALESCE(excluded.start_session_id, schedule_staff.start_session_id),
                        end_session_id = excluded.end_session_id,
                        is_active = 1
                    """, (schedule_id, staff_id, start_session_id, end_session_id, now_iso))

            return staff_id
        except Exception as e:
            logging.error(f"add_staff_member failed and rolled back: {e}")
            raise
        finally:
            conn.close()

    def get_staff_member(self, staff_id):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM project_staff WHERE id = ?", (staff_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_staff_by_code(self, project_id, staff_code):
        if not staff_code:
            return None
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM project_staff WHERE project_id = ? AND staff_code = ?", (project_id, str(staff_code).strip()))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def assign_staff_to_schedule(self, schedule_id, staff_id, start_session_id=None, end_session_id=None):
        """Explicitly assigns a staff member to a specific schedule. Verifies project matching."""
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()

        # Enforce project alignment: schedule and staff must share the same project_id
        cursor.execute("""
        SELECT s.project_id as sched_proj, ps.project_id as staff_proj
        FROM schedules s, project_staff ps
        WHERE s.id = ? AND ps.id = ?
        """, (schedule_id, staff_id))
        row = cursor.fetchone()
        if not row or row['sched_proj'] != row['staff_proj']:
            conn.close()
            raise ValueError("برنامه و نیرو متعلق به یک پروژه یکسان نیستند و امکان انتساب وجود ندارد.")

        now_iso = datetime.now().isoformat()
        cursor.execute("""
        INSERT INTO schedule_staff (schedule_id, staff_id, start_session_id, end_session_id, is_active, created_at)
        VALUES (?, ?, ?, ?, 1, ?)
        ON CONFLICT(schedule_id, staff_id) DO UPDATE SET
            start_session_id = COALESCE(excluded.start_session_id, schedule_staff.start_session_id),
            end_session_id = NULL,
            is_active = 1
        """, (schedule_id, staff_id, start_session_id, end_session_id, now_iso))
        conn.commit()
        conn.close()
        return True

    def remove_staff_from_schedule(self, schedule_id, staff_id, end_session_id=None):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE schedule_staff 
        SET is_active = 0, end_session_id = ? 
        WHERE schedule_id = ? AND staff_id = ?
        """, (end_session_id, schedule_id, staff_id))
        conn.commit()
        conn.close()
        return True

    def list_schedule_staff(self, schedule_id, session_id=None, active_only=True):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        query = """
        SELECT ps.*, ss.start_session_id as sched_start_session, ss.end_session_id as sched_end_session
        FROM schedule_staff ss
        JOIN project_staff ps ON ss.staff_id = ps.id
        WHERE ss.schedule_id = ?
        """
        params = [schedule_id]
        if active_only:
            query += " AND ss.is_active = 1 AND ps.is_active = 1"
        if session_id is not None:
            query += " AND (ss.start_session_id IS NULL OR ss.start_session_id <= ?) AND (ss.end_session_id IS NULL OR ss.end_session_id >= ?)"
            params.extend([session_id, session_id])
        query += " ORDER BY ps.id ASC"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def list_staff(self, project_id, unit=None, section=None, session_id=None, schedule_id=None, active_only=True):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        
        # If schedule_id is provided, resolve exclusively from schedule_staff
        if schedule_id is not None:
            conn.close()
            s_list = self.list_schedule_staff(schedule_id, session_id=session_id, active_only=active_only)
            if unit:
                s_list = [s for s in s_list if s.get('unit') == unit]
            if section:
                s_list = [s for s in s_list if s.get('section') == section]
            return s_list

        query = "SELECT * FROM project_staff WHERE project_id = ?"
        params = [project_id]
        if active_only:
            query += " AND is_active = 1"
        if unit:
            query += " AND unit = ?"
            params.append(unit)
        if section:
            query += " AND section = ?"
            params.append(section)
        if session_id is not None:
            query += " AND (start_session_id IS NULL OR start_session_id <= ?) AND (end_session_id IS NULL OR end_session_id >= ?)"
            params.extend([session_id, session_id])
        query += " ORDER BY id ASC"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def search_staff(self, project_id, query_str, active_only=True, session_id=None, schedule_id=None):
        all_staff = self.list_staff(project_id, active_only=active_only, session_id=session_id, schedule_id=schedule_id)
        clean_q = normalize_persian(query_str)
        if not clean_q:
            return all_staff
        results = []
        for s in all_staff:
            name_norm = normalize_persian(s.get('name', ''))
            phone_norm = normalize_persian(s.get('phone', ''))
            unit_norm = normalize_persian(s.get('unit', ''))
            sec_norm = normalize_persian(s.get('section', ''))
            card_norm = normalize_persian(s.get('card_title', ''))
            code_norm = normalize_persian(s.get('staff_code', ''))
            if (clean_q in name_norm or clean_q in phone_norm or 
                clean_q in unit_norm or clean_q in sec_norm or clean_q in card_norm or
                clean_q in code_norm):
                results.append(s)
        return results

    def get_staff_by_phone_group(self, project_id):
        all_staff = self.list_staff(project_id, active_only=False)
        mapping = {}
        for s in all_staff:
            p = str(s.get('phone', '')).strip()
            if p and p not in ('None', '-', ''):
                if p not in mapping:
                    mapping[p] = []
                mapping[p].append(s)
        return mapping

    def update_staff_field(self, staff_id, field_name, value, sync_same_phone=True):
        staff = self.get_staff_member(staff_id)
        if not staff:
            return False
        allowed_fields = [
            'name', 'phone', 'unit', 'section', 'position', 'card_title', 
            'shift_time', 'gender', 'notes', 'is_active', 'is_multi_section', 
            'staff_code', 'start_session_id', 'end_session_id', '_excel_row'
        ]
        if field_name not in allowed_fields:
            return False
        project_id = staff['project_id']
        phone = str(staff.get('phone', '')).strip()
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        try:
            if sync_same_phone and phone and phone not in ('None', '-', '') and field_name in ['name', 'phone', 'gender']:
                cursor.execute(f"UPDATE project_staff SET {field_name} = ? WHERE project_id = ? AND phone = ?", (value, project_id, phone))
            else:
                cursor.execute(f"UPDATE project_staff SET {field_name} = ? WHERE id = ?", (value, staff_id))
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"update_staff_field error: {e}")
            return False
        finally:
            conn.close()

    def update_section_shift_time(self, project_id, unit, section, new_time):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
            UPDATE project_staff 
            SET shift_time = ? 
            WHERE project_id = ? AND unit = ? AND section = ?
            """, (new_time.strip(), project_id, unit.strip(), section.strip()))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

staff_manager = StaffManager()
