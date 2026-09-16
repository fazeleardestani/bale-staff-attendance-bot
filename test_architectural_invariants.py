import sys
import os
import sqlite3
import unittest
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
        P1 Requirement 11:
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
        DatabaseManager must NOT silently fall back to /tmp. If path is invalid or locked,
        it must raise DatabaseUnavailableError.
        """
        invalid_path = "/non_existent_folder_xyz_123/database.db"
        with self.assertRaises(DatabaseUnavailableError):
            DatabaseManager(db_path=invalid_path)

    def test_07_create_session_atomicity(self):
        """
        P1 Requirement 8:
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

if __name__ == '__main__':
    unittest.main()
