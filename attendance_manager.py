import os
import sys
import logging
from datetime import datetime
from db_manager import db_instance
from utils import calculate_status_with_delay, format_delay_minutes
from staff_manager import staff_manager
from permission_manager import permission_manager


class AttendanceManager:
    def __init__(self, db=db_instance):
        self.db = db

    def create_schedule(self, project_id, name, day_of_week, time_str, location=""):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO schedules (project_id, name, day_of_week, time_str, location, is_active)
        VALUES (%s, %s, %s, %s, %s, TRUE)
        RETURNING id;
        """, (project_id, name.strip(), day_of_week.strip(), time_str.strip(), location.strip()))
        schedule_id = cursor.fetchone()['id']
        conn.commit()
        conn.close()
        return schedule_id

    def list_schedules(self, project_id, active_only=True):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        query = "SELECT * FROM schedules WHERE project_id = %s"
        if active_only:
            query += " AND is_active = TRUE"
        query += " ORDER BY id ASC"
        cursor.execute(query, (project_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def set_schedule_active(self, schedule_id, is_active):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE schedules SET is_active = %s WHERE id = %s",
                       (bool(is_active), schedule_id))
        conn.commit()
        conn.close()
        return True

    def create_session(self, project_id, name, session_date=None, time_str='', day_of_week='',
                       schedule_id=None, copy_from_prev_session=True, sync_excel_sheet=True,
                       return_details=False):
        conn = self.db.get_connection()
        cursor = conn.cursor()

        if schedule_id is not None:
            cursor.execute(
                "SELECT id FROM schedules WHERE id = %s AND project_id = %s AND is_active = TRUE", (schedule_id, project_id))
            if not cursor.fetchone():
                conn.close()
                raise ValueError(
                    f"برنامه با شناسه {schedule_id} متعلق به این پروژه نیست یا فعال نمی‌باشد.")

        now_iso = datetime.now().isoformat()
        date_str = str(session_date or '').strip()

        if schedule_id is not None and date_str:
            cursor.execute("""
            SELECT id FROM sessions 
            WHERE project_id = %s AND schedule_id = %s AND session_date = %s AND status != 'CANCELLED'
            """, (project_id, schedule_id, date_str))
            existing = cursor.fetchone()
            if existing:
                conn.close()
                if return_details:
                    return existing['id'], False
                return existing['id']

        try:
            cursor.execute("""
            INSERT INTO sessions (project_id, schedule_id, name, session_date, time_str, day_of_week, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, 'SCHEDULED', %s)
            RETURNING id;
            """, (project_id, schedule_id, name.strip(), date_str, str(time_str or '').strip(), str(day_of_week or '').strip(), now_iso))
            session_id = cursor.fetchone()['id']

            if schedule_id is not None:
                cursor.execute("""
                SELECT ps.id, ps.name, ps.unit, ps.section, ps.position, ps.gender,
                       ps.phone, ps.card_title, ps.shift_time, ps.is_multi_section, ps.staff_code
                FROM schedule_staff ss
                JOIN project_staff ps ON ss.staff_id = ps.id
                WHERE ss.schedule_id = %s AND ss.is_active = TRUE AND ps.is_active = TRUE
                  AND (ss.start_session_id IS NULL OR ss.start_session_id <= %s)
                  AND (ss.end_session_id IS NULL OR ss.end_session_id >= %s)
                  AND (ps.start_session_id IS NULL OR ps.start_session_id <= %s)
                  AND (ps.end_session_id IS NULL OR ps.end_session_id >= %s)
                ORDER BY ps.id ASC
                """, (schedule_id, session_id, session_id, session_id, session_id))
                eligible_staff = cursor.fetchall()
            else:
                cursor.execute("""
                SELECT id, name, unit, section, position, gender,
                       phone, card_title, shift_time, is_multi_section, staff_code
                FROM project_staff 
                WHERE project_id = %s AND is_active = TRUE
                  AND (start_session_id IS NULL OR start_session_id <= %s)
                  AND (end_session_id IS NULL OR end_session_id >= %s)
                ORDER BY id ASC
                """, (project_id, session_id, session_id))
                eligible_staff = cursor.fetchall()

            for s in eligible_staff:
                cursor.execute("""
                INSERT INTO attendance (
                    project_id, session_id, staff_id, status, card_status,
                    staff_name_snapshot, unit_snapshot, section_snapshot, position_snapshot, gender_snapshot,
                    phone_snapshot, card_title_snapshot, shift_time_snapshot, is_multi_section_snapshot, staff_code_snapshot,
                    updated_at
                )
                VALUES (%s, %s, %s, '', '', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(project_id, session_id, staff_id) DO NOTHING;
                """, (
                    project_id, session_id, s['id'],
                    s['name'], s['unit'], s['section'], s['position'], s['gender'],
                    s['phone'], s['card_title'], s['shift_time'], s['is_multi_section'], s['staff_code'],
                    now_iso
                ))
            conn.commit()
        except Exception as e:
            conn.rollback()
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
        return self.create_session(
            project_id, name, session_date=session_date, time_str=time_str,
            day_of_week=day_of_week, schedule_id=schedule_id,
            copy_from_prev_session=copy_from_prev_session,
            sync_excel_sheet=sync_excel_sheet, return_details=True
        )

    def get_session(self, session_id):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sessions WHERE id = %s", (session_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_session_by_name(self, project_id, name, schedule_id=None):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        if schedule_id is not None:
            cursor.execute("""
            SELECT * FROM sessions 
            WHERE project_id = %s AND name = %s AND schedule_id = %s
            """, (project_id, name.strip(), schedule_id))
        else:
            cursor.execute("""
            SELECT * FROM sessions 
            WHERE project_id = %s AND name = %s
            """, (project_id, name.strip()))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_session_by_date_and_schedule(self, project_id, schedule_id, session_date):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT * FROM sessions 
        WHERE project_id = %s AND schedule_id = %s AND session_date = %s
        """, (project_id, schedule_id, session_date.strip()))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def list_sessions(self, project_id, schedule_id=None, include_cancelled=False):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        query = "SELECT * FROM sessions WHERE project_id = %s"
        params = [project_id]
        if schedule_id is not None:
            query += " AND schedule_id = %s"
            params.append(schedule_id)
        if not include_cancelled:
            query += " AND status != 'CANCELLED'"
        query += " ORDER BY id ASC"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_session_field(self, session_id, field_name, value):
        allowed = ['name', 'session_date', 'time_str',
                   'day_of_week', 'status', 'schedule_id']
        if field_name not in allowed:
            return False
        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM sessions WHERE id = %s", (session_id,))
        sess = cursor.fetchone()
        if not sess:
            conn.close()
            raise ValueError("جلسه مورد نظر یافت نشد.")

        project_id = sess['project_id']
        current_sched_id = sess['schedule_id']
        current_date = sess['session_date']

        if field_name == 'schedule_id':
            if value != current_sched_id:
                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM attendance WHERE session_id = %s", (session_id,))
                att_cnt = cursor.fetchone()['cnt']
                if att_cnt > 0:
                    conn.close()
                    raise ValueError(
                        "تغییر برنامه/کلاس برای جلسه‌ای که دارای سابقه حضور و غیاب است غیرمجاز می‌باشد.")

            if value is not None:
                cursor.execute(
                    "SELECT id, project_id, is_active FROM schedules WHERE id = %s", (value,))
                sched = cursor.fetchone()
                if not sched or sched['project_id'] != project_id:
                    conn.close()
                    raise ValueError("برنامه انتخابی متعلق به این پروژه نیست.")
                if not sched['is_active']:
                    conn.close()
                    raise ValueError(
                        "برنامه انتخابی غیرفعال است و امکان اتصال جلسه به آن وجود ندارد.")

                if current_date:
                    cursor.execute("""
                    SELECT id FROM sessions 
                    WHERE project_id = %s AND schedule_id = %s AND session_date = %s AND id != %s AND status != 'CANCELLED'
                    """, (project_id, value, current_date, session_id))
                    if cursor.fetchone():
                        conn.close()
                        raise ValueError(
                            f"در تاریخ {current_date} برای این برنامه جلسه دیگری از قبل وجود دارد.")

        if field_name == 'session_date':
            new_date = str(value or '').strip()
            if current_sched_id is not None and new_date:
                cursor.execute("""
                SELECT id FROM sessions 
                WHERE project_id = %s AND schedule_id = %s AND session_date = %s AND id != %s AND status != 'CANCELLED'
                """, (project_id, current_sched_id, new_date, session_id))
                if cursor.fetchone():
                    conn.close()
                    raise ValueError(
                        f"در تاریخ {new_date} برای این برنامه جلسه دیگری از قبل وجود دارد.")

        cursor.execute(
            f"UPDATE sessions SET {field_name} = %s WHERE id = %s", (value, session_id))
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
        conn = self.db.get_connection()
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
        WHERE a.project_id = %s AND a.session_id = %s
        ORDER BY a.staff_id ASC
        """, (project_id, session_id))
        rows = cursor.fetchall()
        conn.close()

        result = []
        for r in rows:
            d = dict(r)
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
            result.append(d)

        return result

    def get_staff_session_attendance(self, project_id, session_id, staff_id):
        conn = self.db.get_connection()
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
        WHERE a.project_id = %s AND a.session_id = %s AND a.staff_id = %s
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
                return False

        staff = staff_manager.get_staff_member(staff_id)
        if not staff:
            return False
        if status_val == "حاضر":
            status_val = calculate_status_with_delay(
                staff.get("shift_time", ""))

        target_staff_ids = [staff_id]
        phone = str(staff.get("phone", "")).strip()

        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT phone_snapshot FROM attendance WHERE project_id = %s AND session_id = %s AND staff_id = %s",
            (project_id, session_id, staff_id)
        )
        snap_row = cursor.fetchone()
        if snap_row and snap_row['phone_snapshot']:
            phone = str(snap_row['phone_snapshot']).strip()

        if sync_same_phone and phone and phone not in ("None", "-", ""):
            cursor.execute("""
            SELECT a.staff_id
            FROM attendance a
            WHERE a.project_id = %s
              AND a.session_id = %s
              AND a.phone_snapshot = %s
              AND a.phone_snapshot != ''
              AND a.phone_snapshot IS NOT NULL
            """, (project_id, session_id, phone))
            matched = [r['staff_id'] for r in cursor.fetchall()]
            if matched:
                target_staff_ids = matched
            if staff_id not in target_staff_ids:
                target_staff_ids.append(staff_id)

        now_iso = datetime.now().isoformat()
        try:
            for s_id in target_staff_ids:
                cursor.execute("""
                UPDATE attendance 
                SET status = %s, updated_at = %s
                WHERE project_id = %s AND session_id = %s AND staff_id = %s;
                """, (status_val, now_iso, project_id, session_id, s_id))
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
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
                return False

        staff = staff_manager.get_staff_member(staff_id)
        if not staff:
            return False

        target_staff_ids = [staff_id]
        phone = str(staff.get("phone", "")).strip()

        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT phone_snapshot FROM attendance WHERE project_id = %s AND session_id = %s AND staff_id = %s",
            (project_id, session_id, staff_id)
        )
        snap_row = cursor.fetchone()
        if snap_row and snap_row['phone_snapshot']:
            phone = str(snap_row['phone_snapshot']).strip()

        if sync_same_phone and phone and phone not in ("None", "-", ""):
            cursor.execute("""
            SELECT a.staff_id
            FROM attendance a
            WHERE a.project_id = %s
              AND a.session_id = %s
              AND a.phone_snapshot = %s
              AND a.phone_snapshot != ''
              AND a.phone_snapshot IS NOT NULL
            """, (project_id, session_id, phone))
            matched = [r['staff_id'] for r in cursor.fetchall()]
            if matched:
                target_staff_ids = matched
            if staff_id not in target_staff_ids:
                target_staff_ids.append(staff_id)

        now_iso = datetime.now().isoformat()
        try:
            for s_id in target_staff_ids:
                cursor.execute("""
                UPDATE attendance 
                SET card_status = %s, updated_at = %s
                WHERE project_id = %s AND session_id = %s AND staff_id = %s;
                """, (card_val, now_iso, project_id, session_id, s_id))
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logging.error(f"update_card_status error: {e}")
            return False
        finally:
            conn.close()

    def add_late_tracking(self, project_id, session_id, staff_id, note, actor_user_id=None):
        if actor_user_id is not None:
            allowed, _, _ = permission_manager.authorize_attendance_action(
                actor_user_id, project_id, session_id=session_id, staff_id=staff_id, action="add_late"
            )
            if not allowed:
                return False

        record = self.get_staff_session_attendance(
            project_id, session_id, staff_id)
        if not record:
            return False
        current_note = record.get("late_tracking", "")
        now_time = datetime.now().strftime("%H:%M")
        new_entry = f"[📞 {now_time} - {note.strip()}]"
        final_note = f"{current_note} | {new_entry}" if current_note and current_note not in (
            "None", "-", "") else new_entry

        conn = self.db.get_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        try:
            cursor.execute("""
            UPDATE attendance 
            SET late_tracking = %s, updated_at = %s
            WHERE project_id = %s AND session_id = %s AND staff_id = %s;
            """, (final_note, now_iso, project_id, session_id, staff_id))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    def add_description(self, project_id, session_id, staff_id, note, actor_user_id=None):
        if actor_user_id is not None:
            allowed, _, _ = permission_manager.authorize_attendance_action(
                actor_user_id, project_id, session_id=session_id, staff_id=staff_id, action="edit_desc"
            )
            if not allowed:
                return False

        record = self.get_staff_session_attendance(
            project_id, session_id, staff_id)
        if not record:
            return False
        current_desc = record.get("description", "")
        now_time = datetime.now().strftime("%H:%M")
        new_entry = f"({now_time}) {note.strip()}"
        final_desc = f"{current_desc} | {new_entry}" if current_desc and current_desc not in (
            "None", "-", "") else new_entry

        conn = self.db.get_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        try:
            cursor.execute("""
            UPDATE attendance 
            SET description = %s, updated_at = %s
            WHERE project_id = %s AND session_id = %s AND staff_id = %s;
            """, (final_desc, now_iso, project_id, session_id, staff_id))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
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

    def add_staff_to_session_roster(self, project_id, session_id, staff_ids):
        if not staff_ids:
            return True, 0
        if isinstance(staff_ids, int):
            staff_ids = [staff_ids]

        conn = self.db.get_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()
        cursor.execute("""
        SELECT id, name, unit, section, position, gender,
               phone, card_title, shift_time, is_multi_section, staff_code
        FROM project_staff
        WHERE project_id = %s AND id = ANY(%s)
        """, (project_id, list(staff_ids)))
        staff_rows = cursor.fetchall()

        added_cnt = 0
        try:
            for s in staff_rows:
                cursor.execute("""
                INSERT INTO attendance (
                    project_id, session_id, staff_id, status, card_status,
                    staff_name_snapshot, unit_snapshot, section_snapshot, position_snapshot, gender_snapshot,
                    phone_snapshot, card_title_snapshot, shift_time_snapshot, is_multi_section_snapshot, staff_code_snapshot,
                    updated_at
                )
                VALUES (%s, %s, %s, '', '', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(project_id, session_id, staff_id) DO NOTHING;
                """, (
                    project_id, session_id, s['id'],
                    s['name'], s['unit'], s['section'], s['position'], s['gender'],
                    s['phone'], s['card_title'], s['shift_time'], s['is_multi_section'], s['staff_code'],
                    now_iso
                ))
                added_cnt += cursor.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return True, added_cnt


attendance_manager = AttendanceManager()
