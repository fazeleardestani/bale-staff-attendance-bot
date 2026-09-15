import os
import sys
from db_manager import db_instance
from utils import normalize_persian

class StaffManager:
    def __init__(self, db=db_instance):
        self.db = db

    def add_staff_member(self, project_id, name, phone, unit, section, position='نیرو', 
                         card_title=None, shift_time='', gender='', notes='', 
                         is_multi_section='خیر', excel_row=None):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        c_title = card_title if card_title is not None else section
        cursor.execute('''
        INSERT INTO project_staff 
        (project_id, name, phone, unit, section, position, card_title, shift_time, gender, is_active, notes, is_multi_section, _excel_row)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        ''', (project_id, name.strip(), str(phone or '').strip(), unit.strip(), section.strip(), 
              position.strip(), str(c_title).strip(), str(shift_time or '').strip(), 
              gender.strip(), notes.strip(), is_multi_section, excel_row))
        staff_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return staff_id

    def get_staff_member(self, staff_id):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM project_staff WHERE id = ?", (staff_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def list_staff(self, project_id, unit=None, section=None, active_only=True):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
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
        query += " ORDER BY id ASC"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def search_staff(self, project_id, query_str, active_only=True):
        all_staff = self.list_staff(project_id, active_only=active_only)
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
            if (clean_q in name_norm or clean_q in phone_norm or 
                clean_q in unit_norm or clean_q in sec_norm or clean_q in card_norm):
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
        allowed_fields = ['name', 'phone', 'unit', 'section', 'position', 'card_title', 'shift_time', 'gender', 'notes', 'is_active', 'is_multi_section', '_excel_row']
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
        except Exception:
            return False
        finally:
            conn.close()

    def update_section_shift_time(self, project_id, unit, section, new_time):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
            UPDATE project_staff 
            SET shift_time = ? 
            WHERE project_id = ? AND unit = ? AND section = ?
            ''', (new_time.strip(), project_id, unit.strip(), section.strip()))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

staff_manager = StaffManager()
