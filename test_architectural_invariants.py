import sys
import os
import sqlite3
import unittest
from datetime import datetime

sys.path.insert(0, '/working_dir/c_482d3e8b87a19f32')

from db_manager import db_instance
from permission_manager import permission_manager
from project_manager import project_manager
from staff_manager import staff_manager
from attendance_manager import attendance_manager
from shortage_manager import shortage_manager
from report_manager import report_manager
from excel_manager import excel_manager
from worker_manager import worker_manager

class TestArchitecturalInvariants(unittest.TestCase):

    def test_01_schedule_staff_segregation(self):
        """Tests that staff allocated to specific schedules do not leak across schedules."""
        pid = project_manager.create_project("پروژه آزمون تفکیک کادر کلاس‌ها " + str(datetime.now().timestamp()), project_type="کلاس")
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

        # Removing Staff 3 from Monday should keep her in Sunday
        staff_manager.remove_staff_from_schedule(sched_mon, st_both)
        sess_mon_2 = attendance_manager.create_session(pid, "جلسه دوشنبه ۲", schedule_id=sched_mon)
        att_mon_2 = attendance_manager.get_session_attendance(pid, sess_mon_2)
        staff_ids_mon_2 = [s['staff_id'] for s in att_mon_2]
        self.assertNotIn(st_both, staff_ids_mon_2, "Staff 3 removed from Monday must not appear in Monday 2!")

        sess_sun_2 = attendance_manager.create_session(pid, "جلسه یکشنبه ۲", schedule_id=sched_sun)
        att_sun_2 = attendance_manager.get_session_attendance(pid, sess_sun_2)
        staff_ids_sun_2 = [s['staff_id'] for s in att_sun_2]
        self.assertIn(st_both, staff_ids_sun_2, "Staff 3 must remain active in Sunday schedule!")

    def test_02_historical_session_integrity_no_autoseed(self):
        """Tests that old sessions are NEVER mutated or reseeded when reading."""
        pid = project_manager.create_project("پروژه آزمون سلامت تاریخچه " + str(datetime.now().timestamp()), project_type="رویداد")
        st1 = staff_manager.add_staff_member(pid, "حسین قدیمی", "09124444444", "تدارکات", "پذیرایی", gender="آقا")
        sess1 = attendance_manager.create_session(pid, "روز اول")

        # Snapshot verified
        att1 = attendance_manager.get_session_attendance(pid, sess1)
        self.assertEqual(len(att1), 1)
        self.assertEqual(att1[0]['name'], "حسین قدیمی")
        self.assertEqual(att1[0]['unit'], "تدارکات")

        # Add new staff today in project
        st2 = staff_manager.add_staff_member(pid, "محمد جدید", "09125555555", "رسانه", "صوت", gender="آقا")
        # Change st1 unit in current project_staff
        staff_manager.update_staff_field(st1, "unit", "مدیریت")

        # Re-read historical sess1 attendance
        att1_again = attendance_manager.get_session_attendance(pid, sess1)
        self.assertEqual(len(att1_again), 1, "Historical session roster must NOT grow with newly added staff!")
        self.assertEqual(att1_again[0]['name'], "حسین قدیمی")
        self.assertEqual(att1_again[0]['unit'], "تدارکات", "Historical unit snapshot must remain 'تدارکات' despite current update!")

    def test_03_worker_no_dangerous_guess(self):
        """Tests that get_today_sessions returns [] when no session is scheduled for today."""
        pid = project_manager.create_project("پروژه آزمون عدم حدس ورکر " + str(datetime.now().timestamp()), project_type="رویداد")
        # Create a historical session dated in past
        sess_past = attendance_manager.create_session(pid, "جلسه سال قبل", session_date="2020-01-01")

        today_sessions = worker_manager.get_today_sessions(pid)
        self.assertEqual(today_sessions, [], "Worker must NOT fall back to past sessions!")

    def test_04_central_authorization_and_gender(self):
        """Tests authorize_attendance_action prevents cross-gender and cross-project tampering."""
        pid1 = project_manager.create_project("پروژه مجاز ۱ " + str(datetime.now().timestamp()))
        pid2 = project_manager.create_project("پروژه نامجاز ۲ " + str(datetime.now().timestamp()))

        sess1 = attendance_manager.create_session(pid1, "روز ۱")
        st_female = staff_manager.add_staff_member(pid1, "فاطمه رضایی", "09126666666", "آموزش", "پذیرش", gender="خانم")
        st_male = staff_manager.add_staff_member(pid1, "علی کریمی", "09127777777", "آموزش", "پذیرش", gender="آقا")

        # Female operator 77111
        permission_manager.upsert_user(77111, "اپراتور خواهران", "خانم")
        permission_manager.set_project_user(pid1, 77111, role="operator", gender="خانم")

        # 1. Female operator modifying female staff -> Allowed
        allowed, _, _ = permission_manager.authorize_attendance_action(77111, pid1, sess1, st_female, "update_attendance")
        self.assertTrue(allowed)

        # 2. Female operator modifying male staff -> Denied
        allowed, reason, _ = permission_manager.authorize_attendance_action(77111, pid1, sess1, st_male, "update_attendance")
        self.assertFalse(allowed)
        self.assertIn("عدم تطابق جنسیتی", reason)

        # 3. Female operator attempting access to pid2 -> Denied
        allowed, reason, _ = permission_manager.authorize_attendance_action(77111, pid2, sess1, st_female, "update_attendance")
        self.assertFalse(allowed)

        # 4. In manager level, call is blocked
        res = attendance_manager.update_attendance_status(pid1, sess1, st_male, "حاضر", actor_user_id=77111)
        self.assertFalse(res, "Manager must reject unauthorized cross-gender write")

    def test_05_bounded_phone_synchronization(self):
        """Tests that sync_same_phone is strictly bounded to the same project and session."""
        pid = project_manager.create_project("پروژه آزمون همگام‌سازی شماره تلفن " + str(datetime.now().timestamp()))
        sess1 = attendance_manager.create_session(pid, "روز ۱")
        sess2 = attendance_manager.create_session(pid, "روز ۲")

        # Staff in section A with phone P1
        st_a = staff_manager.add_staff_member(pid, "رضا احمدی ۱", "09128888888", "تدارکات", "پذیرایی", gender="آقا")
        # Same staff in section B with phone P1
        st_b = staff_manager.add_staff_member(pid, "رضا احمدی ۲", "09128888888", "تدارکات", "نظافت", gender="آقا")

        # Update in sess1
        attendance_manager.update_attendance_status(pid, sess1, st_a, "حاضر", sync_same_phone=True)

        rec_a_s1 = attendance_manager.get_staff_session_attendance(pid, sess1, st_a)
        rec_b_s1 = attendance_manager.get_staff_session_attendance(pid, sess1, st_b)
        self.assertEqual(rec_a_s1['status'], "حاضر")
        self.assertEqual(rec_b_s1['status'], "حاضر", "Same phone in same session must sync")

        # Ensure sess2 is completely untouched!
        rec_a_s2 = attendance_manager.get_staff_session_attendance(pid, sess2, st_a)
        self.assertTrue(rec_a_s2 is None or rec_a_s2.get("status") == "", "Phone sync must NEVER leak to other sessions!")

    def test_06_backup_integrity_and_restore(self):
        """Tests SQLite online backup integrity and restore verification."""
        backup_path = db_instance.backup_database(label="invariant_test")
        self.assertIsNotNone(backup_path)
        self.assertTrue(os.path.exists(backup_path))
        self.assertTrue(db_instance.verify_backup_integrity(backup_path))

        # Test restoring onto temporary separate database
        test_restore_path = "/tmp/test_restore_dest.db"
        if os.path.exists(test_restore_path):
            os.remove(test_restore_path)
        self.assertTrue(db_instance.restore_backup(backup_path, destination_path=test_restore_path))
        self.assertTrue(db_instance.verify_backup_integrity(test_restore_path))
        if os.path.exists(test_restore_path):
            os.remove(test_restore_path)

    def test_07_excel_xls_rejection(self):
        """Tests that legacy .xls files are rejected with clear user guidance."""
        pid = project_manager.create_project("پروژه تست اکسل " + str(datetime.now().timestamp()))
        with self.assertRaises(ValueError) as ctx:
            excel_manager.import_project_excel(pid, "/tmp/legacy_file.xls")
        self.assertIn(".xls", str(ctx.exception))

if __name__ == '__main__':
    unittest.main()
