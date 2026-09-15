import os
import sys
from datetime import datetime
from db_manager import db_instance

class ShortageManager:
    def __init__(self, db=db_instance):
        self.db = db

    def add_shortage(self, project_id, unit, section, count, target_group, description):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        created_ids = []
        try:
            for _ in range(int(count)):
                cursor.execute('''
                INSERT INTO shortages 
                (project_id, unit, section, count, target_group, description, status, is_notified, created_at)
                VALUES (?, ?, ?, 1, ?, ?, 'تامین نشده', 0, ?)
                ''', (project_id, unit.strip(), section.strip(), target_group.strip(), description.strip(), now_iso))
                created_ids.append(cursor.lastrowid)
            conn.commit()
            return created_ids
        except Exception:
            conn.rollback()
            return []
        finally:
            conn.close()

    def get_unnotified_shortages(self):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute('''
        SELECT s.*, p.name as project_name 
        FROM shortages s
        JOIN projects p ON s.project_id = p.id
        WHERE s.is_notified = 0 AND s.status = 'تامین نشده'
        ''')
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def mark_shortage_notified(self, shortage_id):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE shortages SET is_notified = 1 WHERE id = ?", (shortage_id,))
        conn.commit()
        conn.close()

    def get_all_unresolved_shortages(self, project_id=None):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        if project_id:
            cursor.execute('''
            SELECT s.*, p.name as project_name
            FROM shortages s
            JOIN projects p ON s.project_id = p.id
            WHERE s.project_id = ? AND s.status IN ('تامین نشده', 'در انتظار تایید', 'در انتظار تایید ادمین')
            ORDER BY s.id DESC
            ''', (project_id,))
        else:
            cursor.execute('''
            SELECT s.*, p.name as project_name
            FROM shortages s
            JOIN projects p ON s.project_id = p.id
            WHERE s.status IN ('تامین نشده', 'در انتظار تایید', 'در انتظار تایید ادمین')
            ORDER BY s.id DESC
            ''')
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def suggest_shortage(self, shortage_id, candidate_name, candidate_phone):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
            UPDATE shortages 
            SET status = 'در انتظار تایید', assigned_name = ?, phone = ? 
            WHERE id = ?
            ''', (candidate_name.strip(), str(candidate_phone).strip(), shortage_id))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def approve_shortage(self, shortage_id):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE shortages SET status = 'تامین شده' WHERE id = ?", (shortage_id,))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def reject_shortage(self, shortage_id):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
            UPDATE shortages 
            SET status = 'تامین نشده', assigned_name = NULL, phone = NULL 
            WHERE id = ?
            ''', (shortage_id,))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def get_shortage(self, shortage_id):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute('''
        SELECT s.*, p.name as project_name
        FROM shortages s
        JOIN projects p ON s.project_id = p.id
        WHERE s.id = ?
        ''', (shortage_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def add_unit_head_shortage(self, project_id, unit, section, count, target_group, description, requested_by):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        try:
            cursor.execute('''
            INSERT INTO shortages 
            (project_id, unit, section, count, target_group, description, status, is_notified, requested_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'در انتظار تایید ادمین', 0, ?, ?)
            ''', (project_id, unit.strip(), section.strip(), int(count), target_group.strip(), description.strip(), requested_by, now_iso))
            sh_id = cursor.lastrowid
            conn.commit()
            return sh_id
        except Exception:
            conn.rollback()
            return None
        finally:
            conn.close()

    def approve_unit_head_shortage(self, shortage_id):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE shortages SET status = 'تامین نشده', is_notified = 1 WHERE id = ?", (shortage_id,))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def update_shortage_count(self, shortage_id, new_count):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE shortages SET count = ? WHERE id = ?", (int(new_count), shortage_id))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

shortage_manager = ShortageManager()


