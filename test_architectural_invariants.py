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
        """P0: When schedule has 0 assigned staff, creating a session produces 0 attendance rows."""
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه بدون کادر برنامه {ts}", project_type="کلاس")
        
        st1 = staff_manager.add_staff_member(pid, "نیروی عمومی ۱", "09121111111", "آموزش", "پذیرش", gender="خانم")
        sched_empty = attendance_manager.create_schedule(pid, "کلاس بدون کادر", "دوشنبه", "16:00")

        sess_id = attendance_manager.create_session(pid, "جلسه اول خالی", schedule_id=sched_empty)
        att = attendance_manager.get_session_attendance(pid, sess_id)

        self.assertEqual(len(att), 0, "Session of empty schedule must have 0 attendance rows and NEVER fallback to project_staff!")

        admin_uid = 991100
        permission_manager.upsert_user(admin_uid, "مدیر پروژه", "خانم")
        permission_manager.set_project_user(pid, admin_uid, role="admin")
        ok, msg = attendance_manager.seed_session_roster_explicit(pid, sess_id, actor_user_id=admin_uid)
        att_after_seed = attendance_manager.get_session_attendance(pid, sess_id)
        self.assertEqual(len(att_after_seed), 0, "Explicit seed on empty schedule must NEVER fallback to project_staff!")

    def test_02_schedule_staff_segregation_and_lifecycle(self):
        """P0: Staff in Schedule Monday does not appear in Schedule Sunday session."""
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تفکیک کادر کلاس‌ها {ts}", project_type="کلاس")
        sched_mon = attendance_manager.create_schedule(pid, "دوشنبه سطح ۱", "دوشنبه", "16:00")
        sched_sun = attendance_manager.create_schedule(pid, "یکشنبه سطح ۳", "یکشنبه", "17:00")

        st_mon = staff_manager.add_staff_member(pid, "سارا دوشنبه‌تبار", "09121111111", "آموزش", "پذیرش", gender="خانم", schedule_id=sched_mon)
        st_sun = staff_manager.add_staff_member(pid, "مریم یکشنبه‌تبار", "09122222222", "آموزش", "پذیرش", gender="خانم", schedule_id=sched_sun)
        st_both = staff_manager.add_staff_member(pid, "زهرا هردوروز", "09123333333", "رسانه", "پخش", gender="خانم", schedule_id=sched_mon)
        staff_manager.assign_staff_to_schedule(sched_sun, st_both)

        sess_mon = attendance_manager.create_session(pid, "جلسه دوشنبه ۱", schedule_id=sched_mon)
        att_mon = attendance_manager.get_session_attendance(pid, sess_mon)
        staff_ids_mon = [s['staff_id'] for s in att_mon]

        self.assertIn(st_mon, staff_ids_mon)
        self.assertIn(st_both, staff_ids_mon)
        self.assertNotIn(st_sun, staff_ids_mon)

        sess_sun = attendance_manager.create_session(pid, "جلسه یکشنبه ۱", schedule_id=sched_sun)
        att_sun = attendance_manager.get_session_attendance(pid, sess_sun)
        staff_ids_sun = [s['staff_id'] for s in att_sun]

        self.assertIn(st_sun, staff_ids_sun)
        self.assertIn(st_both, staff_ids_sun)
        self.assertNotIn(st_mon, staff_ids_sun)

    def test_03_authorization_rejects_staff_not_in_session_roster(self):
        """P0: authorize_attendance_action verifies staff is enrolled in session roster."""
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه کنترل دسترسی جلسه {ts}", project_type="کلاس")
        sched_mon = attendance_manager.create_schedule(pid, "دوشنبه", "دوشنبه", "16:00")
        sched_tue = attendance_manager.create_schedule(pid, "سه‌شنبه", "سه‌شنبه", "16:00")

        st_mon = staff_manager.add_staff_member(pid, "علی دوشنبه", "09124444444", "آموزش", "پذیرش", gender="آقا", schedule_id=sched_mon)
        st_tue = staff_manager.add_staff_member(pid, "رضا سه‌شنبه", "09125555555", "آموزش", "پذیرش", gender="آقا", schedule_id=sched_tue)

        sess_mon = attendance_manager.create_session(pid, "جلسه دوشنبه ۱", schedule_id=sched_mon)

        op_uid = 992200
        permission_manager.upsert_user(op_uid, "اپراتور برادران", "آقا")
        permission_manager.set_project_user(pid, op_uid, role="operator", gender="آقا")

        allowed, _, _ = permission_manager.authorize_attendance_action(op_uid, pid, sess_mon, st_mon, "update_attendance")
        self.assertTrue(allowed)

        allowed, reason, _ = permission_manager.authorize_attendance_action(op_uid, pid, sess_mon, st_tue, "update_attendance")
        self.assertFalse(allowed)

    def test_04_strict_session_bounded_phone_sync(self):
        """P0: sync_same_phone strictly updates attendance of current session only."""
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تست سینک تلفن {ts}", project_type="کلاس")
        sched_mon = attendance_manager.create_schedule(pid, "دوشنبه", "دوشنبه", "16:00")
        sched_sun = attendance_manager.create_schedule(pid, "یکشنبه", "یکشنبه", "17:00")

        shared_phone = "09129990011"
        st_ali = staff_manager.add_staff_member(pid, "علی احمدی", shared_phone, "آموزش", "پذیرش", gender="آقا", schedule_id=sched_mon)
        st_reza = staff_manager.add_staff_member(pid, "رضا احمدی", shared_phone, "خدمات", "پذیرایی", gender="آقا", schedule_id=sched_sun)

        sess_mon = attendance_manager.create_session(pid, "جلسه دوشنبه ۱", schedule_id=sched_mon)
        sess_sun = attendance_manager.create_session(pid, "جلسه یکشنبه ۱", schedule_id=sched_sun)

        attendance_manager.update_attendance_status(pid, sess_mon, st_ali, "حاضر", sync_same_phone=True)

        rec_ali_mon = attendance_manager.get_staff_session_attendance(pid, sess_mon, st_ali)
        self.assertEqual(rec_ali_mon['status'], "حاضر")

        rec_reza_mon = attendance_manager.get_staff_session_attendance(pid, sess_mon, st_reza)
        self.assertIsNone(rec_reza_mon)

        rec_reza_sun = attendance_manager.get_staff_session_attendance(pid, sess_sun, st_reza)
        self.assertEqual(rec_reza_sun['status'], "")

    def test_05_schedule_project_ownership_enforcement(self):
        """P1: Cannot assign cross-project schedule."""
        ts = str(datetime.now().timestamp())
        pid1 = project_manager.create_project(f"پروژه اصلی {ts}")
        pid2 = project_manager.create_project(f"پروژه ثانویه {ts}")

        sched1 = attendance_manager.create_schedule(pid1, "برنامه پروژه ۱", "شنبه", "10:00")
        st2 = staff_manager.add_staff_member(pid2, "کادر پروژه ۲", "09127778899", "بخش", "واحد", gender="آقا")

        with self.assertRaises(ValueError) as ctx:
            attendance_manager.create_session(pid2, "جلسه نامعتبر", schedule_id=sched1)
        self.assertIn("متعلق به این پروژه نیست", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            staff_manager.assign_staff_to_schedule(sched1, st2)
        self.assertIn("یکسان نیستند", str(ctx.exception))

    def test_06_no_tmp_sqlite_fallback_on_unwritable_path(self):
        """P1: Unwritable path raises DatabaseUnavailableError and creates NO file in /tmp."""
        temp_parent = tempfile.mkdtemp()
        os.chmod(temp_parent, 0o555)
        unwritable_db = os.path.join(temp_parent, "test_locked_dir", "test_fail.db")
        expected_tmp_fallback = "/tmp/test_fail.db"

        if os.path.exists(expected_tmp_fallback):
            os.remove(expected_tmp_fallback)

        try:
            with self.assertRaises(DatabaseUnavailableError):
                DatabaseManager(db_path=unwritable_db)

            self.assertFalse(os.path.exists(expected_tmp_fallback), "Hidden /tmp database fallback must NOT be created!")
        finally:
            os.chmod(temp_parent, 0o777)
            shutil.rmtree(temp_parent)

    def test_07_create_session_atomicity(self):
        """P1: create_session must be atomic."""
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
        """P2: Backup integrity check and real operational query test on restored DB."""
        backup_path = db_instance.backup_database(label="operational_test")
        self.assertIsNotNone(backup_path)
        self.assertTrue(os.path.exists(backup_path))
        self.assertTrue(db_instance.verify_backup_integrity(backup_path))

        test_restore_path = "/tmp/test_restore_operational.db"
        if os.path.exists(test_restore_path):
            os.remove(test_restore_path)

        self.assertTrue(db_instance.restore_backup(backup_path, destination_path=test_restore_path))

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
        """P0: Excel sheet manages its schedule roster lifecycle."""
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تست چرخه کادر اکسل {ts}", project_type="کلاس")
        sched_mon = attendance_manager.create_schedule(pid, "کلاس دوشنبه", "دوشنبه", "16:00")

        sess_1 = attendance_manager.create_session(pid, "کلاس دوشنبه", session_date="2026-10-01", schedule_id=sched_mon)

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

        sched_staff = staff_manager.list_schedule_staff(sched_mon)
        self.assertEqual(len(sched_staff), 1)
        self.assertEqual(sched_staff[0]['name'], "علی کادر اول")

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

        active_sched_staff = staff_manager.list_schedule_staff(sched_mon, active_only=True)
        active_names = [s['name'] for s in active_sched_staff]
        self.assertIn("سارا کادر جدید", active_names)
        self.assertNotIn("علی کادر اول", active_names)

        all_proj_staff = staff_manager.list_staff(pid, active_only=False)
        all_names = [s['name'] for s in all_proj_staff]
        self.assertIn("علی کادر اول", all_names)

        att_s1 = attendance_manager.get_session_attendance(pid, sess_1)
        self.assertEqual(len(att_s1), 1)
        self.assertEqual(att_s1[0]['name'], "علی کادر اول")

        for p in [path1, path2]:
            if os.path.exists(p): os.remove(p)

    def test_10_atomic_add_staff_member_with_schedule(self):
        """P1: add_staff_member with schedule_id is atomic."""
        ts = str(datetime.now().timestamp())
        pid1 = project_manager.create_project(f"پروژه اتمیک یک {ts}")
        pid2 = project_manager.create_project(f"پروژه اتمیک دو {ts}")

        sched_other = attendance_manager.create_schedule(pid2, "برنامه پروژه ۲", "شنبه", "10:00")

        with self.assertRaises(ValueError):
            staff_manager.add_staff_member(pid1, "نیروی نامعتبر", "09128889900", "واحد", "بخش", schedule_id=sched_other)

        st_list = staff_manager.list_staff(pid1)
        self.assertEqual(len(st_list), 0)

    def test_11_excel_import_two_phase_atomic_rollback(self):
        """P0: If DB error occurs during import, physical project_data.xlsx is untouched."""
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تست اتمیک اکسل {ts}")
        original_excel_path = excel_manager.get_project_excel_path(pid)

        wb_orig = openpyxl.Workbook()
        ws_orig = wb_orig.active
        ws_orig.title = "شیت اصلی"
        ws_orig.append(["نشانگر اصلی قبل از شکست"])
        wb_orig.save(original_excel_path)
        wb_orig.close()

        wb_bad = openpyxl.Workbook()
        ws_bad = wb_bad.active
        ws_bad.title = "روز ۱"
        headers = ["ردیف", "واحد", "بخش", "نام و نام خانوادگی", "سمت", "عنوان کارت", "شماره تماس", "ساعت حضور", "پیگیری تاخیر", "توضیحات", "وضعیت کارت", "وضعیت حضور", "جنسیت", "فعال در چند بخش؟"]
        ws_bad.append(headers)
        ws_bad.append([1, "واحد", "بخش", "نام نیرو", "سمت", "کارت", "09120000000", "16:00", "", "", "", "", "آقا", "خیر"])
        bad_path = f"/tmp/bad_excel_{ts}.xlsx"
        wb_bad.save(bad_path)
        wb_bad.close()

        real_get_conn = db_instance.get_sqlite_connection
        def failing_conn():
            c = real_get_conn()
            c.execute("CREATE TRIGGER IF NOT EXISTS fail_trg BEFORE INSERT ON project_staff BEGIN SELECT RAISE(FAIL, 'MOCK_ERROR'); END;")
            return c

        db_instance.get_sqlite_connection = failing_conn
        try:
            with self.assertRaises(Exception):
                excel_manager.import_project_excel(pid, bad_path)

            wb_check = openpyxl.load_workbook(original_excel_path)
            self.assertIn("شیت اصلی", wb_check.sheetnames)
            wb_check.close()
        finally:
            db_instance.get_sqlite_connection = real_get_conn
            c_clean = db_instance.get_sqlite_connection()
            c_clean.execute("DROP TRIGGER IF EXISTS fail_trg")
            c_clean.close()
            if os.path.exists(bad_path): os.remove(bad_path)

    def test_12_worker_skips_inactive_schedule_and_archived_project(self):
        """P1: Worker skips inactive schedules and archived projects."""
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تست ورکر اسکجول غیرفعال {ts}")
        sched = attendance_manager.create_schedule(pid, "کلاس غیرفعال", "دوشنبه", "16:00")
        today_str = datetime.now().strftime("%Y-%m-%d")

        sess_id = attendance_manager.create_session(pid, "جلسه امروز", session_date=today_str, schedule_id=sched)

        sessions_active = worker_manager.get_today_sessions(pid)
        self.assertEqual(len(sessions_active), 1)

        # Deactivate schedule -> skipped
        attendance_manager.set_schedule_active(sched, is_active=False)
        sessions_inactive = worker_manager.get_today_sessions(pid)
        self.assertEqual(sessions_inactive, [])

        # Reactivate schedule, archive project -> skipped
        attendance_manager.set_schedule_active(sched, is_active=True)
        project_manager.archive_project(pid)
        sessions_archived = worker_manager.get_today_sessions(pid)
        self.assertEqual(sessions_archived, [])

    def test_13_pure_historical_snapshot_no_fallback(self):
        """P1: Historical attendance reads exclusively from snapshot and never leaks future changes."""
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تست سلامت اسنپ شات {ts}")
        st_id = staff_manager.add_staff_member(pid, "نام تاریخی اولیه", "09121112233", "واحد قدیم", "بخش قدیم", position="مسئول", gender="خانم")
        sess_id = attendance_manager.create_session(pid, "روز اول تاریخی")

        att1 = attendance_manager.get_session_attendance(pid, sess_id)
        self.assertEqual(att1[0]['name'], "نام تاریخی اولیه")
        self.assertEqual(att1[0]['unit'], "واحد قدیم")

        staff_manager.update_staff_field(st_id, "name", "نام تغییریافته جدید")
        staff_manager.update_staff_field(st_id, "unit", "واحد کاملا متفاوت")

        att_frozen = attendance_manager.get_session_attendance(pid, sess_id)
        self.assertEqual(att_frozen[0]['name'], "نام تاریخی اولیه")
        self.assertEqual(att_frozen[0]['unit'], "واحد قدیم")

    def test_14_schedule_staff_reactivation_on_rejoin(self):
        """P1: Rejoining a schedule clears end_session_id and reactivates membership."""
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تست ورود مجدد {ts}")
        sched = attendance_manager.create_schedule(pid, "کلاس بازگشت", "شنبه", "10:00")
        st = staff_manager.add_staff_member(pid, "نیروی بازگشتی", "09123334455", "بخش", "واحد", gender="آقا", schedule_id=sched)

        staff_manager.remove_staff_from_schedule(sched, st, end_session_id=5)
        active_staff = staff_manager.list_schedule_staff(sched, active_only=True)
        self.assertEqual(len(active_staff), 0)

        staff_manager.assign_staff_to_schedule(sched, st, start_session_id=10)
        active_staff_rejoined = staff_manager.list_schedule_staff(sched, active_only=True)
        self.assertEqual(len(active_staff_rejoined), 1)
        self.assertEqual(active_staff_rejoined[0]['sched_start_session'], 10)
        self.assertIsNone(active_staff_rejoined[0]['sched_end_session'])

    def test_15_update_session_field_forbidden_when_attendance_exists(self):
        """
        P0 (New):
        Changing schedule_id for a session that already has attendance records
        is STRICTLY FORBIDDEN and must raise ValueError.
        """
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تست عدم تغییر برنامه {ts}", project_type="کلاس")
        sched_a = attendance_manager.create_schedule(pid, "برنامه آ", "دوشنبه", "16:00")
        sched_b = attendance_manager.create_schedule(pid, "برنامه ب", "سه‌شنبه", "17:00")

        st = staff_manager.add_staff_member(pid, "نیروی برنامه آ", "09121113344", "واحد", "بخش", gender="آقا", schedule_id=sched_a)
        sess = attendance_manager.create_session(pid, "جلسه دوشنبه", schedule_id=sched_a)

        # Mark attendance
        attendance_manager.update_attendance_status(pid, sess, st, "حاضر")

        # Attempt to change schedule_id of session to sched_b -> ValueError
        with self.assertRaises(ValueError) as ctx:
            attendance_manager.update_session_field(sess, "schedule_id", sched_b)
        self.assertIn("دارای سابقه حضور و غیاب است غیرمجاز می‌باشد", str(ctx.exception))

    def test_16_update_session_field_prevents_date_collision(self):
        """
        P0 (New):
        Updating session_date or schedule_id must prevent date collisions on (project, schedule, date).
        """
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تست تداخل تقویم {ts}", project_type="کلاس")
        sched = attendance_manager.create_schedule(pid, "برنامه هفتگی", "دوشنبه", "16:00")

        sess1 = attendance_manager.create_session(pid, "جلسه اول", session_date="2026-10-01", schedule_id=sched)
        sess2 = attendance_manager.create_session(pid, "جلسه دوم", session_date="2026-10-08", schedule_id=sched)

        # Attempt to update sess2 date to 2026-10-01 (collides with sess1) -> ValueError
        with self.assertRaises(ValueError) as ctx:
            attendance_manager.update_session_field(sess2, "session_date", "2026-10-01")
        self.assertIn("جلسه دیگری از قبل وجود دارد", str(ctx.exception))

    def test_17_no_fuzzy_matching_excel_schedule(self):
        """
        P0 (New):
        Excel Import must NOT use fuzzy substring matching between similar schedule names.
        Schedules 'سطح ۳ دوشنبه' and 'سطح ۳ سه‌شنبه' must never be confused.
        """
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تست تطبیق دقیق {ts}", project_type="کلاس")
        sched_mon = attendance_manager.create_schedule(pid, "سطح ۳ دوشنبه", "دوشنبه", "16:00")
        sched_tue = attendance_manager.create_schedule(pid, "سطح ۳ سه‌شنبه", "سه‌شنبه", "16:00")

        st_mon = staff_manager.add_staff_member(pid, "نیروی دوشنبه", "09121110001", "واحد", "بخش", gender="آقا", schedule_id=sched_mon)
        st_tue = staff_manager.add_staff_member(pid, "نیروی سه‌شنبه", "09121110002", "واحد", "بخش", gender="آقا", schedule_id=sched_tue)

        # Create Excel with exact sheet name "سطح ۳ دوشنبه"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "سطح ۳ دوشنبه"
        headers = ["ردیف", "واحد", "بخش", "نام و نام خانوادگی", "سمت", "عنوان کارت", "شماره تماس", "ساعت حضور", "پیگیری تاخیر", "توضیحات", "وضعیت کارت", "وضعیت حضور", "جنسیت", "فعال در چند بخش؟"]
        ws.append(headers)
        ws.append([1, "واحد", "بخش", "نیروی دوشنبه", "نیرو", "کارت", "09121110001", "16:00", "", "", "", "حاضر", "آقا", "خیر"])
        path = f"/tmp/test_exact_sched_{ts}.xlsx"
        wb.save(path)
        wb.close()

        try:
            excel_manager.import_project_excel(pid, path)

            # Session created must be strictly tied to sched_mon, NOT sched_tue!
            sess = attendance_manager.get_session_by_name(pid, "سطح ۳ دوشنبه", schedule_id=sched_mon)
            self.assertIsNotNone(sess)
            self.assertEqual(sess['schedule_id'], sched_mon)

            # Sched Tue roster must be completely untouched
            tue_staff = staff_manager.list_schedule_staff(sched_tue)
            self.assertEqual(len(tue_staff), 1)
            self.assertEqual(tue_staff[0]['name'], "نیروی سه‌شنبه")
        finally:
            if os.path.exists(path): os.remove(path)

    def test_18_excel_sheet_removal_only_deactivates_that_schedule(self):
        """
        P0 (New):
        Removing a staff member from Sheet Monday ONLY deactivates their membership in Schedule Monday.
        Their membership in Schedule Tuesday and their record in project_staff remain 100% active.
        """
        ts = str(datetime.now().timestamp())
        pid = project_manager.create_project(f"پروژه تست حذف انتخابی {ts}", project_type="کلاس")
        sched_mon = attendance_manager.create_schedule(pid, "کلاس دوشنبه", "دوشنبه", "16:00")
        sched_tue = attendance_manager.create_schedule(pid, "کلاس سه‌شنبه", "سه‌شنبه", "16:00")

        # Staff Ali is member of BOTH Monday and Tuesday schedules
        st_ali = staff_manager.add_staff_member(pid, "علی دوکلاسه", "09121118899", "واحد", "بخش", gender="آقا", schedule_id=sched_mon)
        staff_manager.assign_staff_to_schedule(sched_tue, st_ali)

        # Other staff in Monday
        st_other = staff_manager.add_staff_member(pid, "همکار دوشنبه", "09121119900", "واحد", "بخش", gender="آقا", schedule_id=sched_mon)

        # Verify initial active membership in both
        self.assertEqual(len(staff_manager.list_schedule_staff(sched_mon, active_only=True)), 2)
        self.assertEqual(len(staff_manager.list_schedule_staff(sched_tue, active_only=True)), 1)

        # Import Sheet Monday containing ONLY 'همکار دوشنبه' (Ali is omitted / removed)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "کلاس دوشنبه"
        headers = ["ردیف", "واحد", "بخش", "نام و نام خانوادگی", "سمت", "عنوان کارت", "شماره تماس", "ساعت حضور", "پیگیری تاخیر", "توضیحات", "وضعیت کارت", "وضعیت حضور", "جنسیت", "فعال در چند بخش؟"]
        ws.append(headers)
        ws.append([1, "واحد", "بخش", "همکار دوشنبه", "نیرو", "کارت", "09121119900", "16:00", "", "", "", "حاضر", "آقا", "خیر"])
        path = f"/tmp/test_omit_ali_{ts}.xlsx"
        wb.save(path)
        wb.close()

        try:
            excel_manager.import_project_excel(pid, path)

            # 1. Ali MUST be deactivated in Schedule Monday
            mon_active = staff_manager.list_schedule_staff(sched_mon, active_only=True)
            mon_names = [s['name'] for s in mon_active]
            self.assertNotIn("علی دوکلاسه", mon_names, "Ali must be inactive in Schedule Monday!")

            # 2. Ali MUST remain ACTIVE in Schedule Tuesday!
            tue_active = staff_manager.list_schedule_staff(sched_tue, active_only=True)
            tue_names = [s['name'] for s in tue_active]
            self.assertIn("علی دوکلاسه", tue_names, "Ali must remain ACTIVE in Schedule Tuesday!")

            # 3. Ali MUST remain in project_staff as active!
            proj_ali = staff_manager.get_staff_member(st_ali)
            self.assertEqual(proj_ali['is_active'], 1, "Ali must remain active in project_staff!")
        finally:
            if os.path.exists(path): os.remove(path)

if __name__ == '__main__':
    unittest.main()
