import os
import sys
from datetime import datetime
from db_manager import db_instance

class PermissionManager:
    def __init__(self, db=db_instance):
        self.db = db

    def get_user(self, user_id):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def upsert_user(self, user_id, staff_name, gender, is_global_super_admin=False, is_active=True, is_hr_member=True):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        cursor.execute("""
        INSERT INTO users (user_id, staff_name, gender, is_global_super_admin, is_active, is_hr_member, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(user_id) DO UPDATE SET
            staff_name = EXCLUDED.staff_name,
            gender = EXCLUDED.gender,
            is_global_super_admin = (users.is_global_super_admin OR EXCLUDED.is_global_super_admin),
            is_active = EXCLUDED.is_active,
            is_hr_member = CASE WHEN EXCLUDED.is_hr_member = FALSE THEN FALSE ELSE users.is_hr_member END;
        """, (user_id, staff_name, gender, bool(is_global_super_admin), bool(is_active), bool(is_hr_member), now_iso))
        conn.commit()
        conn.close()

    def update_staff_name(self, user_id, new_name):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET staff_name = %s WHERE user_id = %s", (new_name, user_id))
        conn.commit()
        conn.close()

    def set_user_active(self, user_id, is_active):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_active = %s WHERE user_id = %s", (bool(is_active), user_id))
        conn.commit()
        conn.close()

    def set_global_super_admin(self, user_id, is_global):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_global_super_admin = %s WHERE user_id = %s", (bool(is_global), user_id))
        conn.commit()
        conn.close()

    def list_all_users(self, hr_only=True):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cond = "WHERE (u.is_hr_member = TRUE OR u.is_global_super_admin = TRUE)" if hr_only else ""
        cursor.execute(f"""
        SELECT u.*, 
               (SELECT COUNT(*) FROM project_users pu WHERE pu.user_id = u.user_id AND pu.is_active = TRUE) as active_projects_count
        FROM users u
        {cond}
        ORDER BY u.is_global_super_admin DESC, u.user_id ASC;
        """)
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_user_project_role(self, project_id, user_id):
        user = self.get_user(user_id)
        if not user or not user.get('is_active', True):
            return None, None, None, False, None
        if user.get('is_global_super_admin', False):
            return 'super_admin', user.get('gender', 'خانم'), user.get('staff_name', f"کاربر {user_id}"), True, None
        if not project_id:
            return None, user.get('gender'), user.get('staff_name'), False, None

        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT role, gender, is_active, assigned_unit FROM project_users 
        WHERE project_id = %s AND user_id = %s
        """, (project_id, user_id))
        row = cursor.fetchone()
        conn.close()

        if row and row['is_active']:
            gender = row['gender'] or user.get('gender', 'خانم')
            return row['role'], gender, user.get('staff_name', f"کاربر {user_id}"), True, row['assigned_unit']
        return None, user.get('gender'), user.get('staff_name'), False, None

    def list_accessible_projects(self, user_id, include_archived=False):
        user = self.get_user(user_id)
        if not user or not user.get('is_active', True):
            return []
        conn = self.db.get_connection()
        cursor = conn.cursor()
        if user.get('is_global_super_admin', False):
            if include_archived:
                cursor.execute("SELECT * FROM projects ORDER BY id DESC")
            else:
                cursor.execute("SELECT * FROM projects WHERE status = 'ACTIVE' ORDER BY id DESC")
        else:
            status_clause = "" if include_archived else "AND p.status = 'ACTIVE'"
            cursor.execute(f"""
            SELECT p.* FROM projects p
            JOIN project_users pu ON p.id = pu.project_id
            WHERE pu.user_id = %s AND pu.is_active = TRUE {status_clause}
            ORDER BY p.id DESC;
            """, (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def set_project_user(self, project_id, user_id, role='user', gender=None, is_active=1, assigned_unit=None):
        user = self.get_user(user_id)
        if not user:
            self.upsert_user(user_id, f"کاربر {user_id}", gender or 'خانم')
            user = self.get_user(user_id)
        user_gender = gender or user.get('gender', 'خانم')
        now_iso = datetime.now().isoformat()

        if role == 'unit_head' and user and not user.get('is_global_super_admin'):
            c_tmp = self.db.get_connection()
            c_cur = c_tmp.cursor()
            c_cur.execute("UPDATE users SET is_hr_member = FALSE WHERE user_id = %s AND is_global_super_admin = FALSE", (user_id,))
            c_tmp.commit()
            c_tmp.close()

        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO project_users (project_id, user_id, role, gender, is_active, assigned_unit, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(project_id, user_id) DO UPDATE SET
            role = EXCLUDED.role,
            gender = EXCLUDED.gender,
            is_active = EXCLUDED.is_active,
            assigned_unit = CASE WHEN EXCLUDED.assigned_unit IS NOT NULL THEN EXCLUDED.assigned_unit ELSE project_users.assigned_unit END;
        """, (project_id, user_id, role, user_gender, bool(is_active), assigned_unit, now_iso))
        conn.commit()
        conn.close()

    def remove_project_user(self, project_id, user_id):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM project_users WHERE project_id = %s AND user_id = %s", (project_id, user_id))
        conn.commit()
        conn.close()

    def get_project_admins(self, project_id, include_global=True):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        admin_ids = set()
        admins = []

        cursor.execute("""
        SELECT pu.user_id, pu.role, u.staff_name, u.gender, u.is_active
        FROM project_users pu
        JOIN users u ON pu.user_id = u.user_id
        WHERE pu.project_id = %s AND pu.role IN ('admin', 'super_admin') AND pu.is_active = TRUE AND u.is_active = TRUE
        """, (project_id,))
        for r in cursor.fetchall():
            d = dict(r)
            if d['user_id'] not in admin_ids:
                admins.append(d)
                admin_ids.add(d['user_id'])

        if include_global:
            cursor.execute("""
            SELECT user_id, 'super_admin' as role, staff_name, gender, is_active
            FROM users
            WHERE is_global_super_admin = TRUE AND is_active = TRUE
            """)
            for r in cursor.fetchall():
                d = dict(r)
                if d['user_id'] not in admin_ids:
                    admins.append(d)
                    admin_ids.add(d['user_id'])

        conn.close()
        return admins

    def get_project_members(self, project_id):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT pu.user_id, pu.role, pu.is_active, pu.gender as project_gender, pu.assigned_unit,
               u.staff_name, u.gender as user_gender, u.is_global_super_admin
        FROM project_users pu
        JOIN users u ON pu.user_id = u.user_id
        WHERE pu.project_id = %s
        ORDER BY pu.role DESC, u.staff_name ASC
        """, (project_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def apply_gender_filter(self, items, user_id, project_id):
        role, gender, _, is_active, _ = self.get_user_project_role(project_id, user_id)
        if role in ('super_admin', 'admin'):
            return items
        return [item for item in items if str(item.get('جنسیت', item.get('gender', ''))).strip() == gender]

    def get_user_context(self, user_id):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT current_project_id, current_session_id FROM user_context WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return row['current_project_id'], row['current_session_id']
        return None, None

    def set_user_context(self, user_id, project_id=None, session_id=None):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        cursor.execute("""
        INSERT INTO user_context (user_id, current_project_id, current_session_id, updated_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT(user_id) DO UPDATE SET
            current_project_id = EXCLUDED.current_project_id,
            current_session_id = EXCLUDED.current_session_id,
            updated_at = EXCLUDED.updated_at;
        """, (user_id, project_id, session_id, now_iso))
        conn.commit()
        conn.close()

    def list_user_assigned_projects(self, user_id):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT p.id as project_id, p.name as project_name, p.status as project_status,
               pu.role, pu.assigned_unit, pu.is_active
        FROM project_users pu
        JOIN projects p ON pu.project_id = p.id
        WHERE pu.user_id = %s AND pu.is_active = TRUE
        ORDER BY p.id DESC
        """, (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_available_hr_members_for_project(self, project_id):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT u.user_id, u.staff_name, u.gender, u.is_global_super_admin
        FROM users u
        WHERE (u.is_hr_member = TRUE OR u.is_global_super_admin = TRUE)
          AND u.is_active = TRUE 
          AND u.user_id NOT IN (
              SELECT pu.user_id FROM project_users pu WHERE pu.project_id = %s AND pu.is_active = TRUE
          )
        ORDER BY u.staff_name ASC
        """, (project_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def set_project_unit_head(self, project_id, user_id, assigned_unit, staff_name=None, gender='خانم'):
        user = self.get_user(user_id)
        if not user:
            self.upsert_user(user_id, staff_name or f"مسئول واحد {assigned_unit}", gender, is_hr_member=False)
        else:
            if not user.get('is_global_super_admin'):
                conn = self.db.get_connection()
                c = conn.cursor()
                c.execute("UPDATE users SET is_hr_member = FALSE WHERE user_id = %s AND is_global_super_admin = FALSE", (user_id,))
                c.commit()
                c.close()
        self.set_project_user(project_id, user_id, role='unit_head', gender=gender, assigned_unit=assigned_unit, is_active=1)

    def authorize_attendance_action(self, actor_user_id, project_id, session_id=None, staff_id=None, action="view"):
        user = self.get_user(actor_user_id)
        if not user or not user.get('is_active', True):
            return False, "کاربر در سامانه فعال نیست", None

        if user.get('is_global_super_admin', False):
            if session_id is not None and staff_id is not None and action in ("update_attendance", "update_card", "add_late", "edit_desc"):
                conn = self.db.get_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM attendance WHERE project_id = %s AND session_id = %s AND staff_id = %s", (project_id, session_id, staff_id))
                in_roster = cursor.fetchone()
                conn.close()
                if not in_roster:
                    return False, "نیرو در لیست حضور و غیاب این جلسه عضو نیست", "super_admin"
            return True, "دسترسی مدیر ارشد کل سامانه", "super_admin"

        if not project_id:
            return False, "پروژه مشخص نشده است", None

        role, actor_gender, staff_name, is_active, assigned_unit = self.get_user_project_role(project_id, actor_user_id)
        if not is_active or not role:
            return False, "کاربر عضو این پروژه نیست یا دسترسی او معلق شده است", None

        if role == 'unit_head':
            allowed_unit_actions = ["view_unit_staff", "view_unit_attendance", "request_shortage", "view"]
            if action not in allowed_unit_actions and not action.startswith("view"):
                return False, "مسئول واحد مجاز به تغییر مستقیم حضور یا کارت نیست", role

        if session_id is not None:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id, project_id, status FROM sessions WHERE id = %s", (session_id,))
            sess_row = cursor.fetchone()
            conn.close()
            if not sess_row or sess_row['project_id'] != project_id:
                return False, "جلسه متعلق به این پروژه نیست", role
            if sess_row['status'] == 'CANCELLED' and action in ("update_attendance", "update_card", "add_late", "edit_desc"):
                return False, "امکان ثبت وضعیت در جلسه لغو شده وجود ندارد", role

        if staff_id is not None:
            from staff_manager import staff_manager
            staff = staff_manager.get_staff_member(staff_id)
            if not staff or staff.get('project_id') != project_id:
                return False, "نیرو متعلق به این پروژه نیست", role

            staff_gender = str(staff.get('gender', '')).strip()
            staff_unit = str(staff.get('unit', '')).strip()

            if role == 'unit_head':
                if assigned_unit and staff_unit != assigned_unit:
                    return False, f"دسترسی شما فقط محدود به واحد «{assigned_unit}» است", role

            if role in ('operator', 'user'):
                if actor_gender and staff_gender and actor_gender != staff_gender:
                    return False, f"عدم تطابق جنسیتی: اپراتور {actor_gender} مجاز به تغییر وضعیت نیروی {staff_gender} نیست", role

            if session_id is not None and action in ("update_attendance", "update_card", "add_late", "edit_desc", "view_staff_attendance"):
                conn = self.db.get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                SELECT 1 FROM attendance 
                WHERE project_id = %s AND session_id = %s AND staff_id = %s
                """, (project_id, session_id, staff_id))
                in_roster = cursor.fetchone()

                cursor.execute("SELECT schedule_id FROM sessions WHERE id = %s", (session_id,))
                s_row = cursor.fetchone()
                sched_id = s_row['schedule_id'] if s_row else None
                sched_ok = True
                if sched_id is not None:
                    cursor.execute("""
                    SELECT 1 FROM schedule_staff 
                    WHERE schedule_id = %s AND staff_id = %s AND is_active = TRUE
                    """, (sched_id, staff_id))
                    sched_ok = bool(cursor.fetchone())

                conn.close()
                if not in_roster or not sched_ok:
                    return False, "نیرو در لیست کادر مجاز این جلسه/کلاس عضو نیست", role

        return True, "دسترسی مجاز", role

    def record_operator_daily_absence(self, project_id, user_id, absent_date=None):
        dt = absent_date or datetime.now().strftime("%Y-%m-%d")
        now_iso = datetime.now().isoformat()
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO operator_daily_absence (project_id, user_id, absent_date, created_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT(project_id, user_id, absent_date) DO NOTHING;
        """, (project_id, user_id, dt, now_iso))
        conn.commit()
        conn.close()
        return True

    def clear_operator_daily_absence(self, project_id, user_id, absent_date=None):
        dt = absent_date or datetime.now().strftime("%Y-%m-%d")
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        DELETE FROM operator_daily_absence
        WHERE project_id = %s AND user_id = %s AND absent_date = %s;
        """, (project_id, user_id, dt))
        conn.commit()
        conn.close()
        return True

    def is_operator_absent_today(self, project_id, user_id, date_str=None):
        dt = date_str or datetime.now().strftime("%Y-%m-%d")
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT 1 FROM operator_daily_absence
        WHERE project_id = %s AND user_id = %s AND absent_date = %s;
        """, (project_id, user_id, dt))
        row = cursor.fetchone()
        conn.close()
        return bool(row)

permission_manager = PermissionManager()