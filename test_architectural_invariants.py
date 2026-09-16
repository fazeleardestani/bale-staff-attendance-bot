import sys
import os
import sqlite3
import unittest
import tempfile
import shutil
import openpyxl
from datetime import datetime

sys.path.insert(0, '/working_dir/c_482d3e8b87a19f32')

from db_manager import db_instance, DatabaseManager, DatabaseUnavailableError
from permission_manager import permission_manager
from project_manager import project_manager
from staff_manager import staff_manager
from attendance_manager import attendance_manager
from shortage_manager import shortage_manager
from report_manager import report_manager
from excel_manager import excel_manager
from worker_manager import worker_manager

class TestArchitecturalInvariants(unittest.TestCase):

    def test_01_empty_schedule_roster_zero_fallback(self):
        """
        P0 Requirement 1:
        When a schedule has 0 assigned staff in schedule_staff, creating a session
        MUST produce an empty attendance roster (0 rows). It must NEVER fall back to project_staff.
        """
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه بدون کادر برنامه {ts}", project_type="کلاس")
        
        # Add staff to project generally
        st1 = staff_manager.add_staff_member(pid, "نیروی عمومی ۱", "09121111111", "آموزش", "پذیرش", gender="خانم")
        st2 = staff_manager.add_staff_member(pid, "نیروی عمومی ۲", "09122222222", "رسانه", "صوت", gender="آقا")

        # Create schedule with ZERO assigned staff
        sched_empty = attendance_manager.create_schedule(pid, "کلاس بدون کادر", "دوشنبه", "16:00")

        # Create session for this schedule
        sess_id = attendance_manager.create_session(pid, "جلسه اول خالی", schedule_id=sched_empty)
        att = attendance_manager.get_session_attendance(pid, sess_id)

        self.assertEqual(len(att), 0, "CRITICAL: Session of empty schedule must have 0 attendance rows and NEVER fallback to project_staff!")

        # Explicit seed on empty schedule session must also produce 0 and not fallback
        admin_uid = 991100
        permission_manager.upsert_user(admin_uid, "مدیر پروژه", "خانم")
        permission_manager.set_project_user(pid, admin_uid, role="admin")
        ok, msg = attendance_manager.seed_session_roster_explicit(pid, sess_id, actor_user_id=admin_uid)
        att_after_seed = attendance_manager.get_session_attendance(pid, sess_id)
        self.assertEqual(len(att_after_seed), 0, "Explicit seed on empty schedule must NEVER fallback to project_staff!")

    def test_02_schedule_staff_segregation_and_lifecycle(self):
        """Tests that staff allocated to specific schedules do not leak across schedules."""
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تفکیک کادر کلاس‌ها {ts}", project_type="کلاس")
        sched_mon = attendance_manager.create_schedule(pid, "دوشنبه سطح ۱", "دوشنبه", "16:00")
        sched_sun = attendance_manager.create_schedule(pid, "یکشنبه سطح ۳", "یکشنبه", "17:00")

        # Staff 1: Only in Monday
        st_mon = staff_manager.add_staff_member(pid, "سارا دوشنبه‌تبار", "09121111111", "آموزش", "پذیرش", gender="خانم", schedule_id=sched_mon)
        # Staff 2: Only in Sunday
        st_sun = staff_manager.add_staff_member(pid, "مریم یکشنبه‌تبار", "09122222222", "آموزش", "پذیرش", gender="خانم", schedule_id=sched_sun)
        # Staff 3: In both Monday and Sunday
        st_both = staff_manager.add_staff_member(pid, "زهرا هردوروز", "09123333333", "رسانه", "پخش", gender="خانم", schedule_id=sched_mon)
        staff_manager.assign_staff_to_schedule(sched_sun, st_both)

        # Create session for Monday
        sess_mon = attendance_manager.create_session(pid, "جلسه دوشنبه ۱", schedule_id=sched_mon)
        att_mon = attendance_manager.get_session_attendance(pid, sess_mon)
        staff_ids_mon = [s['staff_id'] for s in att_mon]

        self.assertIn(st_mon, staff_ids_mon)
        self.assertIn(st_both, staff_ids_mon)
        self.assertNotIn(st_sun, staff_ids_mon, "Staff only in Sunday must NOT appear in Monday session!")

        # Create session for Sunday
        sess_sun = attendance_manager.create_session(pid, "جلسه یکشنبه ۱", schedule_id=sched_sun)
        att_sun = attendance_manager.get_session_attendance(pid, sess_sun)
        staff_ids_sun = [s['staff_id'] for s in att_sun]

        self.assertIn(st_sun, staff_ids_sun)
        self.assertIn(st_both, staff_ids_sun)
        self.assertNotIn(st_mon, staff_ids_sun, "Staff only in Monday must NOT appear in Sunday session!")

    def test_03_authorization_rejects_staff_not_in_session_roster(self):
        """
        P0 Requirement 2:
        authorize_attendance_action MUST verify that staff is enrolled in this specific session's attendance.
        If not enrolled, write actions must be REJECTED.
        """
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه کنترل دسترسی جلسه {ts}", project_type="کلاس")
        sched_mon = attendance_manager.create_schedule(pid, "دوشنبه", "دوشنبه", "16:00")
        sched_tue = attendance_manager.create_schedule(pid, "سه‌شنبه", "سه‌شنبه", "16:00")

        st_mon = staff_manager.add_staff_member(pid, "علی دوشنبه", "09124444444", "آموزش", "پذیرش", gender="آقا", schedule_id=sched_mon)
        st_tue = staff_manager.add_staff_member(pid, "رضا سه‌شنبه", "09125555555", "آموزش", "پذیرش", gender="آقا", schedule_id=sched_tue)

        sess_mon = attendance_manager.create_session(pid, "جلسه دوشنبه ۱", schedule_id=sched_mon)

        # Operator (Male)
        op_uid = 992200
        permission_manager.upsert_user(op_uid, "اپراتور برادران", "آقا")
        permission_manager.set_project_user(pid, op_uid, role="operator", gender="آقا")

        # 1. Action on staff present in session (st_mon) -> ALLOWED
        allowed, _, _ = permission_manager.authorize_attendance_action(op_uid, pid, sess_mon, st_mon, "update_attendance")
        self.assertTrue(allowed, "Enrolled staff must be allowed")

        # 2. Action on staff of another schedule (st_tue) in Monday session -> REJECTED
        allowed, reason, _ = permission_manager.authorize_attendance_action(op_uid, pid, sess_mon, st_tue, "update_attendance")
        self.assertFalse(allowed, "Staff not in session attendance roster must be REJECTED")
        self.assertIn("عضو نیست", reason)

        # 3. Manager level update_attendance_status must reject
        res = attendance_manager.update_attendance_status(pid, sess_mon, st_tue, "حاضر", actor_user_id=op_uid)
        self.assertFalse(res, "update_attendance_status must reject staff not in session roster")

    def test_04_strict_session_bounded_phone_sync(self):
        """
        P0 Requirement 3:
        sync_same_phone must strictly query the attendance table of THAT session.
        A staff member sharing a phone who belongs to another schedule/session MUST NOT be updated or injected.
        """
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تست سینک تلفن {ts}", project_type="کلاس")
        sched_mon = attendance_manager.create_schedule(pid, "دوشنبه", "دوشنبه", "16:00")
        sched_sun = attendance_manager.create_schedule(pid, "یکشنبه", "یکشنبه", "17:00")

        shared_phone = "09129990011"
        # Ali is in Monday
        st_ali = staff_manager.add_staff_member(pid, "علی احمدی", shared_phone, "آموزش", "پذیرش", gender="آقا", schedule_id=sched_mon)
        # Reza is in Sunday (shares phone with Ali)
        st_reza = staff_manager.add_staff_member(pid, "رضا احمدی", shared_phone, "خدمات", "پذیرایی", gender="آقا", schedule_id=sched_sun)

        sess_mon = attendance_manager.create_session(pid, "جلسه دوشنبه ۱", schedule_id=sched_mon)
        sess_sun = attendance_manager.create_session(pid, "جلسه یکشنبه ۱", schedule_id=sched_sun)

        # Update Ali in Monday session with sync_same_phone=True
        attendance_manager.update_attendance_status(pid, sess_mon, st_ali, "حاضر", sync_same_phone=True)

        # Check Monday session
        rec_ali_mon = attendance_manager.get_staff_session_attendance(pid, sess_mon, st_ali)
        self.assertEqual(rec_ali_mon['status'], "حاضر")

        # Reza MUST NOT be in Monday session at all!
        rec_reza_mon = attendance_manager.get_staff_session_attendance(pid, sess_mon, st_reza)
        self.assertIsNone(rec_reza_mon, "Reza must NOT have any attendance row in Monday session!")

        # Sunday session must remain untouched
        rec_reza_sun = attendance_manager.get_staff_session_attendance(pid, sess_sun, st_reza)
        self.assertEqual(rec_reza_sun['status'], "", "Reza's status in Sunday session must remain untouched!")

    def test_05_schedule_project_ownership_enforcement(self):
        """
        P1 Requirement:
        Creating a session or assigning staff with a schedule belonging to another project must be rejected.
        """
        ts = str(datetime.now().timestamp())
        pid1 = project_manager.create_project(f"پروژه اصلی {ts}")
        pid2 = project_manager.create_project(f"پروژه ثانویه {ts}")

        sched1 = attendance_manager.create_schedule(pid1, "برنامه پروژه ۱", "شنبه", "10:00")
        st2 = staff_manager.add_staff_member(pid2, "کادر پروژه ۲", "09127778899", "بخش", "واحد", gender="آقا")

        # 1. create_session in Project 2 with Schedule from Project 1 -> ValueError
        with self.assertRaises(ValueError) as ctx:
            attendance_manager.create_session(pid2, "جلسه نامعتبر", schedule_id=sched1)
        self.assertIn("متعلق به این پروژه نیست", str(ctx.exception))

        # 2. assign_staff_to_schedule cross project -> ValueError
        with self.assertRaises(ValueError) as ctx:
            staff_manager.assign_staff_to_schedule(sched1, st2)
        self.assertIn("یکسان نیستند", str(ctx.exception))

    def test_06_no_tmp_sqlite_fallback_on_unwritable_path(self):
        """
        P1 Requirement 6:
        DatabaseManager must NOT silently fall back to /tmp.
        When initialized on an unwritable path, it must fail fast with DatabaseUnavailableError
        and verify that no secondary database file is created in /tmp.
        """
        temp_parent = tempfile.mkdtemp()
        os.chmod(temp_parent, 0o555)  # Read & Execute only, no Write permission
        unwritable_db = os.path.join(temp_parent, "test_locked_dir", "test_fail.db")
        expected_tmp_fallback = "/tmp/test_fail.db"

        if os.path.exists(expected_tmp_fallback):
            os.remove(expected_tmp_fallback)

        try:
            with self.assertRaises(DatabaseUnavailableError):
                DatabaseManager(db_path=unwritable_db)

            # CRITICAL ASSERTION: No database must EVER be created in /tmp as a hidden fallback!
            self.assertFalse(os.path.exists(expected_tmp_fallback), "Hidden /tmp database fallback must NOT be created!")
        finally:
            os.chmod(temp_parent, 0o777)
            shutil.rmtree(temp_parent)

    def test_07_create_session_atomicity(self):
        """
        P1 Requirement:
        create_session must be atomic. Session insert and attendance snapshot commit together.
        """
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه اتمیک {ts}")
        st = staff_manager.add_staff_member(pid, "نیروی تست اتمیک", "09120000000", "آموزش", "کلاس", gender="خانم")

        sess_id = attendance_manager.create_session(pid, "جلسه اتمیک ۱")
        sess = attendance_manager.get_session(sess_id)
        att = attendance_manager.get_session_attendance(pid, sess_id)

        self.assertIsNotNone(sess)
        self.assertEqual(len(att), 1)
        self.assertEqual(att[0]['staff_id'], st)

    def test_08_backup_integrity_and_operational_restore(self):
        """
        P2 Requirement:
        Safe SQLite online backup integrity verification and real operational query test on restored DB.
        """
        backup_path = db_instance.backup_database(label="operational_test")
        self.assertIsNotNone(backup_path)
        self.assertTrue(os.path.exists(backup_path))
        self.assertTrue(db_instance.verify_backup_integrity(backup_path))

        test_restore_path = "/tmp/test_restore_operational.db"
        if os.path.exists(test_restore_path):
            os.remove(test_restore_path)

        # Restore
        self.assertTrue(db_instance.restore_backup(backup_path, destination_path=test_restore_path))

        # Run operational queries against the restored DB
        restored_conn = sqlite3.connect(test_restore_path)
        cur = restored_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM projects")
        p_cnt = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users")
        u_cnt = cur.fetchone()[0]
        restored_conn.close()

        self.assertGreaterEqual(p_cnt, 1)
        self.assertGreaterEqual(u_cnt, 1)

        if os.path.exists(test_restore_path):
            os.remove(test_restore_path)

    def test_09_excel_schedule_roster_lifecycle(self):
        """
        P0 Requirement 2:
        Excel Sheet manages its Schedule Roster lifecycle:
        1. Initial import adds staff to schedule_staff and records attendance.
        2. Adding a new staff (Sara) in updated Excel adds her to schedule_staff.
        3. Removing an old staff (Ali) in updated Excel marks him inactive in schedule_staff
           without deleting him from project_staff or corrupting past attendance.
        """
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تست چرخه کادر اکسل {ts}", project_type="کلاس")
        sched_mon = attendance_manager.create_schedule(pid, "کلاس دوشنبه", "دوشنبه", "16:00")

        # Session 1
        sess_1 = attendance_manager.create_session(pid, "کلاس دوشنبه", session_date="2026-10-01", schedule_id=sched_mon)

        # Excel 1: contains Ali
        wb1 = openpyxl.Workbook()
        ws1 = wb1.active
        ws1.title = "کلاس دوشنبه"
        headers = ["ردیف", "واحد", "بخش", "نام و نام خانوادگی", "سمت", "عنوان کارت", "شماره تماس", "ساعت حضور", "پیگیری تاخیر", "توضیحات", "وضعیت کارت", "وضعیت حضور", "جنسیت", "فعال در چند بخش؟"]
        ws1.append(headers)
        ws1.append([1, "آموزش", "پذیرش", "علی کادر اول", "نیرو", "پذیرش", "09121110001", "16:00", "", "", "تحویل داده شد", "حاضر", "آقا", "خیر"])
        path1 = f"/tmp/test_roster_v1_{ts}.xlsx"
        wb1.save(path1)
        wb1.close()

        excel_manager.import_project_excel(pid, path1)

        # Check Ali in schedule_staff
        sched_staff = staff_manager.list_schedule_staff(sched_mon)
        self.assertEqual(len(sched_staff), 1)
        self.assertEqual(sched_staff[0]['name'], "علی کادر اول")

        # Session 2: Excel 2 updates roster: Ali is removed, Sara is added
        sess_2 = attendance_manager.create_session(pid, "کلاس دوشنبه ۲", session_date="2026-10-08", schedule_id=sched_mon)
        wb2 = openpyxl.Workbook()
        ws2 = wb2.active
        ws2.title = "کلاس دوشنبه ۲"
        ws2.append(headers)
        ws2.append([1, "آموزش", "پذیرش", "سارا کادر جدید", "نیرو", "پذیرش", "09121110002", "16:00", "", "", "تحویل داده شد", "حاضر", "خانم", "خیر"])
        path2 = f"/tmp/test_roster_v2_{ts}.xlsx"
        wb2.save(path2)
        wb2.close()

        excel_manager.import_project_excel(pid, path2)

        # Verify active roster in schedule_staff has Sara, and Ali is deactivated
        active_sched_staff = staff_manager.list_schedule_staff(sched_mon, active_only=True)
        active_names = [s['name'] for s in active_sched_staff]
        self.assertIn("سارا کادر جدید", active_names)
        self.assertNotIn("علی کادر اول", active_names, "Ali was removed from sheet and must be deactivated in schedule_staff")

        # Verify Ali is NOT deleted from project_staff
        all_proj_staff = staff_manager.list_staff(pid, active_only=False)
        all_names = [s['name'] for s in all_proj_staff]
        self.assertIn("علی کادر اول", all_names, "Ali must remain in project_staff!")

        # Verify Ali's attendance in Session 1 is intact
        att_s1 = attendance_manager.get_session_attendance(pid, sess_1)
        self.assertEqual(len(att_s1), 1)
        self.assertEqual(att_s1[0]['name'], "علی کادر اول", "Past session attendance for Ali must be 100% preserved!")

        for p in [path1, path2]:
            if os.path.exists(p): os.remove(p)

    def test_10_atomic_add_staff_member_with_schedule(self):
        """
        P1 Requirement:
        add_staff_member with schedule_id must be atomic.
        If schedule validation fails, no orphan staff member is created in project_staff.
        """
        ts = str(datetime.now().timestamp())
        pid1 = project_manager.create_project(f"پروژه اتمیک یک {ts}")
        pid2 = project_manager.create_project(f"پروژه اتمیک دو {ts}")

        sched_other = attendance_manager.create_schedule(pid2, "برنامه پروژه ۲", "شنبه", "10:00")

        # Attempt to add staff to Project 1 with schedule from Project 2 -> ValueError
        with self.assertRaises(ValueError):
            staff_manager.add_staff_member(pid1, "نیروی نامعتبر", "09128889900", "واحد", "بخش", schedule_id=sched_other)

        # Verify no orphan staff was created in Project 1
        st_list = staff_manager.list_staff(pid1)
        self.assertEqual(len(st_list), 0, "No orphan staff must exist after rollback!")

    def test_11_excel_import_two_phase_atomic_rollback(self):
        """
        P0 Requirement 1:
        Two-Phase Commit in Excel Import:
        If a database error occurs during import, the physical project_data.xlsx
        file MUST NOT be overwritten or modified (retains original content).
        """
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تست اتمیک اکسل {ts}")
        original_excel_path = excel_manager.get_project_excel_path(pid)

        # Put initial marker in original excel
        wb_orig = openpyxl.Workbook()
        ws_orig = wb_orig.active
        ws_orig.title = "شیت اصلی"
        ws_orig.append(["نشانگر اصلی قبل از شکست"])
        wb_orig.save(original_excel_path)
        wb_orig.close()

        # Create a malicious/corrupted update excel that causes DB failure
        wb_bad = openpyxl.Workbook()
        ws_bad = wb_bad.active
        ws_bad.title = "روز ۱"
        # Invalid data structure that will trigger constraint failure
        headers = ["ردیف", "واحد", "بخش", "نام و نام خانوادگی", "سمت", "عنوان کارت", "شماره تماس", "ساعت حضور", "پیگیری تاخیر", "توضیحات", "وضعیت کارت", "وضعیت حضور", "جنسیت", "فعال در چند بخش؟"]
        ws_bad.append(headers)
        ws_bad.append([1, "واحد", "بخش", "نام نیرو", "سمت", "کارت", "09120000000", "16:00", "", "", "", "", "آقا", "خیر"])
        bad_path = f"/tmp/bad_excel_{ts}.xlsx"
        wb_bad.save(bad_path)
        wb_bad.close()

        # Mock DB failure by locking or monkeypatching
        real_get_conn = db_instance.get_sqlite_connection
        def failing_conn():
            c = real_get_conn()
            # Force integrity failure on shortages
            c.execute("CREATE TRIGGER IF NOT EXISTS fail_trg BEFORE INSERT ON project_staff BEGIN SELECT RAISE(FAIL, 'MOCK_ERROR'); END;")
            return c

        db_instance.get_sqlite_connection = failing_conn
        try:
            with self.assertRaises(Exception):
                excel_manager.import_project_excel(pid, bad_path)

            # CRITICAL ASSERTION: The original Excel file MUST remain unchanged!
            wb_check = openpyxl.load_workbook(original_excel_path)
            self.assertIn("شیت اصلی", wb_check.sheetnames, "Original Excel file must NOT be overwritten when DB fails!")
            wb_check.close()
        finally:
            db_instance.get_sqlite_connection = real_get_conn
            # Clean trigger
            c_clean = db_instance.get_sqlite_connection()
            c_clean.execute("DROP TRIGGER IF EXISTS fail_trg")
            c_clean.close()
            if os.path.exists(bad_path): os.remove(bad_path)

    def test_12_worker_skips_inactive_schedule(self):
        """
        P1 Requirement:
        Worker MUST NOT select sessions belonging to a deactivated schedule (is_active == 0).
        """
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تست ورکر اسکجول غیرفعال {ts}")
        sched = attendance_manager.create_schedule(pid, "کلاس غیرفعال", "دوشنبه", "16:00")
        today_str = datetime.now().strftime("%Y-%m-%d")

        sess_id = attendance_manager.create_session(pid, "جلسه امروز", session_date=today_str, schedule_id=sched)

        # When schedule is active -> session is returned
        sessions_active = worker_manager.get_today_sessions(pid)
        self.assertEqual(len(sessions_active), 1)

        # Deactivate schedule
        attendance_manager.set_schedule_active(sched, is_active=False)

        # When schedule is inactive -> session MUST be skipped (empty list)
        sessions_inactive = worker_manager.get_today_sessions(pid)
        self.assertEqual(sessions_inactive, [], "Worker must NEVER process sessions of inactive schedules!")

    def test_13_pure_historical_snapshot_no_fallback(self):
        """
        P1 Requirement:
        Historical attendance reads exclusively from snapshot and never leaks future changes in project_staff.
        """
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تست سلامت اسنپ شات {ts}")
        st_id = staff_manager.add_staff_member(pid, "نام تاریخی اولیه", "09121112233", "واحد قدیم", "بخش قدیم", position="مسئول", gender="خانم")
        sess_id = attendance_manager.create_session(pid, "روز اول تاریخی")

        # Verify initial snapshot
        att1 = attendance_manager.get_session_attendance(pid, sess_id)
        self.assertEqual(att1[0]['name'], "نام تاریخی اولیه")
        self.assertEqual(att1[0]['unit'], "واحد قدیم")

        # Mutate current staff details in project_staff
        staff_manager.update_staff_field(st_id, "name", "نام تغییریافته جدید")
        staff_manager.update_staff_field(st_id, "unit", "واحد کاملا متفاوت")

        # Read historical attendance again
        att_frozen = attendance_manager.get_session_attendance(pid, sess_id)
        self.assertEqual(att_frozen[0]['name'], "نام تاریخی اولیه", "Historical name MUST remain the snapshot value!")
        self.assertEqual(att_frozen[0]['unit'], "واحد قدیم", "Historical unit MUST remain the snapshot value!")

    def test_14_schedule_staff_reactivation_on_rejoin(self):
        """
        P1 Requirement:
        Rejoining a schedule reactivates the same membership without creating duplicate rows.
        """
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تست ورود مجدد {ts}")
        sched = attendance_manager.create_schedule(pid, "کلاس بازگشت", "شنبه", "10:00")
        st = staff_manager.add_staff_member(pid, "نیروی بازگشتی", "09123334455", "بخش", "واحد", gender="آقا", schedule_id=sched)

        # Remove from schedule
        staff_manager.remove_staff_from_schedule(sched, st, end_session_id=5)
        active_staff = staff_manager.list_schedule_staff(sched, active_only=True)
        self.assertEqual(len(active_staff), 0)

        # Re-assign to schedule (Rejoin)
        staff_manager.assign_staff_to_schedule(sched, st, start_session_id=10)
        active_staff_rejoined = staff_manager.list_schedule_staff(sched, active_only=True)
        self.assertEqual(len(active_staff_rejoined), 1)
        self.assertEqual(active_staff_rejoined[0]['sched_start_session'], 10)
        self.assertIsNone(active_staff_rejoined[0]['sched_end_session'])

        # Verify only 1 row exists in schedule_staff table
        conn = db_instance.get_sqlite_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM schedule_staff WHERE schedule_id = ? AND staff_id = ?", (sched, st))
        count = c.fetchone()[0]
        conn.close()
        self.assertEqual(count, 1, "There must be exactly 1 row per (schedule_id, staff_id)!")

if __name__ == '__main__':
    unittest.main()
