import os
import sys
from datetime import datetime
from db_manager import db_instance


class ShortageManager:
    def __init__(self, db=db_instance):
        self.db = db

    def add_shortage(self, project_id, unit, section, count, target_group, description):
        conn = self.db.get_connection()
        now_iso = datetime.now()
        created_ids = []
        try:
            cursor = conn.cursor()
            for _ in range(int(count)):
                cursor.execute('''
                INSERT INTO shortages 
                (project_id, unit, section, count, target_group, description, status, is_notified, created_at)
                VALUES (%s, %s, %s, 1, %s, %s, 'تامین نشده', 0, %s)
                RETURNING id;
                ''', (project_id, unit.strip(), section.strip(), target_group.strip(), description.strip(), now_iso))
                created_ids.append(cursor.fetchone()['id'])
            conn.commit()
            return created_ids
        except Exception:
            conn.rollback()
            return []
        finally:
            conn.close()

    def get_unnotified_shortages(self):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
            SELECT s.*, p.name as project_name 
            FROM shortages s
            JOIN projects p ON s.project_id = p.id
            WHERE s.is_notified = 0 AND s.status = 'تامین نشده' AND p.status = 'ACTIVE'
            ''')
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def mark_shortage_notified(self, shortage_id):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE shortages SET is_notified = 1 WHERE id = %s", (shortage_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_all_unresolved_shortages(self, project_id=None):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            if project_id:
                cursor.execute('''
                SELECT s.*, p.name as project_name
                FROM shortages s
                JOIN projects p ON s.project_id = p.id
                WHERE s.project_id = %s AND s.status IN ('تامین نشده', 'در انتظار تایید', 'در انتظار تایید ادمین')
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
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def suggest_shortage(self, shortage_id, candidate_name, candidate_phone):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
            UPDATE shortages 
            SET status = 'در انتظار تایید', assigned_name = %s, phone = %s 
            WHERE id = %s
            ''', (candidate_name.strip(), str(candidate_phone).strip(), shortage_id))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    def approve_shortage(self, shortage_id):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE shortages SET status = 'تامین شده' WHERE id = %s", (shortage_id,))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    def reject_shortage(self, shortage_id):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
            UPDATE shortages 
            SET status = 'تامین نشده', assigned_name = NULL, phone = NULL 
            WHERE id = %s
            ''', (shortage_id,))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    def cancel_shortage(self, shortage_id, project_id=None):
        """لغو کمبود با تغییر وضعیت به 'لغو شده' در چهارچوب پروژه مشخص"""
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            if project_id is not None:
                cursor.execute(
                    "UPDATE shortages SET status = 'لغو شده' WHERE id = %s AND project_id = %s",
                    (shortage_id, project_id)
                )
            else:
                cursor.execute(
                    "UPDATE shortages SET status = 'لغو شده' WHERE id = %s",
                    (shortage_id,)
                )
            updated = cursor.rowcount > 0
            conn.commit()
            return updated
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    def delete_shortage(self, shortage_id, project_id=None):
        """حذف کامل ردیف کمبود از دیتابیس با رعایت ایزولاسیون پروژه"""
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            if project_id is not None:
                cursor.execute(
                    "DELETE FROM shortages WHERE id = %s AND project_id = %s",
                    (shortage_id, project_id)
                )
            else:
                cursor.execute(
                    "DELETE FROM shortages WHERE id = %s",
                    (shortage_id,)
                )
            deleted = cursor.rowcount > 0
            conn.commit()
            return deleted
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    def get_shortage(self, shortage_id):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
            SELECT s.*, p.name as project_name
            FROM shortages s
            JOIN projects p ON s.project_id = p.id
            WHERE s.id = %s
            ''', (shortage_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def add_unit_head_shortage(self, project_id, unit, section, count, target_group, description, requested_by):
        conn = self.db.get_connection()
        now_iso = datetime.now()
        try:
            cursor = conn.cursor()
            cursor.execute('''
            INSERT INTO shortages 
            (project_id, unit, section, count, target_group, description, status, is_notified, requested_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, 'در انتظار تایید ادمین', 0, %s, %s)
            RETURNING id;
            ''', (project_id, unit.strip(), section.strip(), int(count), target_group.strip(), description.strip(), requested_by, now_iso))
            sh_id = cursor.fetchone()['id']
            conn.commit()
            return sh_id
        except Exception:
            conn.rollback()
            return None
        finally:
            conn.close()

    def approve_unit_head_shortage(self, shortage_id):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE shortages SET status = 'تامین نشده', is_notified = 0 WHERE id = %s", (shortage_id,))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    def update_shortage_count(self, shortage_id, new_count):
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE shortages SET count = %s WHERE id = %s", (int(new_count), shortage_id))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()


shortage_manager = ShortageManager()
