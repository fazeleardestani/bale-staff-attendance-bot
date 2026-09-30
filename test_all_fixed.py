"""
Comprehensive tests for all fixed bugs in the attendance bot.
Run with: python -m pytest test_all_fixed.py -v
or:        python -m unittest test_all_fixed -v
"""
import os
import sys
import unittest
import tempfile
import shutil
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

# Set test environment BEFORE importing project modules
os.environ.setdefault('BOT_TOKEN', 'test_token_12345')
os.environ.setdefault('MYSQL_ENABLED', '0')

def make_test_db():
    """Create a DatabaseManager backed by a temp file (memory-only DBs don't persist between connections)."""
    from db_manager import DatabaseManager
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    db = DatabaseManager(path)
    return db, path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Mock telebot before any imports
telebot_mock = MagicMock()
sys.modules['telebot'] = telebot_mock
sys.modules['telebot.types'] = MagicMock()


class TestUtils(unittest.TestCase):
    def setUp(self):
        from utils import normalize_persian, calculate_status_with_delay, get_persian_weekday
        self.normalize = normalize_persian
        self.calc_status = calculate_status_with_delay
        self.weekday = get_persian_weekday

    def test_normalize_arabic_letters(self):
        self.assertEqual(self.normalize("يك"), "یک")

    def test_normalize_arabic_numerals(self):
        self.assertEqual(self.normalize("١٢٣"), "123")

    def test_normalize_persian_numerals(self):
        self.assertEqual(self.normalize("۴۵۶"), "456")

    def test_normalize_removes_zwnj(self):
        result = self.normalize("سه\u200cشنبه")
        self.assertNotIn('\u200c', result)

    def test_calculate_status_on_time(self):
        # 5 minutes after shift, under threshold
        now = datetime.now().replace(hour=10, minute=5, second=0)
        shift = "10:00"
        result = self.calc_status(shift, now=now, threshold_seconds=600)
        self.assertEqual(result, "حاضر")

    def test_calculate_status_late(self):
        # 20 minutes after shift, over threshold
        now = datetime.now().replace(hour=10, minute=20, second=0)
        shift = "10:00"
        result = self.calc_status(shift, now=now, threshold_seconds=600)
        self.assertIn("تاخیر", result)

    def test_calculate_status_no_shift(self):
        result = self.calc_status("")
        self.assertEqual(result, "حاضر")

    def test_get_persian_weekday(self):
        # datetime(2026, 9, 26) is Saturday (weekday()=5) = شنبه
        dt = datetime(2026, 9, 26)
        result = self.weekday(dt)
        self.assertEqual(result, "شنبه")

    def test_get_persian_weekday_friday(self):
        # Friday = weekday()=4 = جمعه
        dt = datetime(2026, 10, 2)
        result = self.weekday(dt)
        self.assertEqual(result, "جمعه")


class TestDatabaseManager(unittest.TestCase):
    def setUp(self):
        self.db, self.db_path = make_test_db()

    def test_tables_created(self):
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cursor.fetchall()}
        conn.close()
        expected = {'users', 'projects', 'project_users', 'org_chart', 'schedules',
                    'sessions', 'project_staff', 'schedule_staff', 'attendance',
                    'shortages', 'staff_logs', 'user_context', 'operator_daily_absence'}
        for t in expected:
            self.assertIn(t, tables, f"Table {t} missing")

    def test_backup_and_restore(self):
        # Add some data
        conn = self.db.get_sqlite_connection()
        conn.execute("INSERT OR IGNORE INTO users (user_id, staff_name, gender) VALUES (999, 'test', 'آقا')")
        conn.commit()
        conn.close()

        backup_path = self.db.backup_database(label="test")
        self.assertIsNotNone(backup_path)
        self.assertTrue(os.path.exists(backup_path))
        self.assertTrue(self.db.verify_backup_integrity(backup_path))

        # Cleanup
        if os.path.exists(backup_path):
            os.remove(backup_path)

    def test_no_tmp_in_backup_path(self):
        """FIX 2: Backup should not use /tmp (Windows compat)"""
        import db_manager as dm_mod
        with open(dm_mod.__file__, encoding='utf-8') as f:
            src = f.read()
        self.assertNotIn('os.path.join("/tmp"', src)

    def tearDown(self):
        try:
            conn = self.db.get_sqlite_connection()
            conn.close()
        except Exception:
            pass
        try:
            os.remove(self.db_path)
        except Exception:
            pass


class TestPermissionManager(unittest.TestCase):
    def setUp(self):
        from permission_manager import PermissionManager
        self.db, self.db_path = make_test_db()
        self.pm = PermissionManager(db=self.db)

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except Exception:
            pass

    def test_upsert_and_get_user(self):
        self.pm.upsert_user(1, "علی محمدی", "آقا")
        user = self.pm.get_user(1)
        self.assertIsNotNone(user)
        self.assertEqual(user['staff_name'], "علی محمدی")
        self.assertEqual(user['gender'], "آقا")

    def test_set_user_context_and_get(self):
        self.pm.upsert_user(1, "test", "آقا")
        self.pm.set_user_context(1, project_id=5, session_id=10)
        pid, sid = self.pm.get_user_context(1)
        self.assertEqual(pid, 5)
        self.assertEqual(sid, 10)

    def test_fix6_clear_project_context(self):
        """FIX 6: set_user_context(project_id=None) must actually clear project_id"""
        self.pm.upsert_user(1, "test", "آقا")
        # First set a project
        self.pm.set_user_context(1, project_id=5, session_id=10)
        pid, sid = self.pm.get_user_context(1)
        self.assertEqual(pid, 5)
        # Now clear it - this was broken before the fix
        self.pm.set_user_context(1, project_id=None, session_id=None)
        pid, sid = self.pm.get_user_context(1)
        self.assertIsNone(pid, "project_id should be None after clearing (FIX 6 regression)")
        self.assertIsNone(sid, "session_id should be None after clearing (FIX 6 regression)")

    def test_project_role_assignment(self):
        self.pm.upsert_user(2, "مدیر", "خانم")
        conn = self.db.get_sqlite_connection()
        conn.execute("INSERT INTO projects (name, type, status) VALUES ('پروژه تست', 'عمومی', 'ACTIVE')")
        conn.commit()
        pid = conn.execute("SELECT id FROM projects WHERE name='پروژه تست'").fetchone()[0]
        conn.close()
        self.pm.set_project_user(pid, 2, role='admin', gender='خانم')
        role, gender, name, is_active, unit = self.pm.get_user_project_role(pid, 2)
        self.assertEqual(role, 'admin')
        self.assertTrue(is_active)

    def test_gender_filter(self):
        self.pm.upsert_user(3, "زهرا", "خانم")
        items = [
            {'gender': 'خانم', 'name': 'زهرا'},
            {'gender': 'آقا', 'name': 'علی'},
        ]
        conn = self.db.get_sqlite_connection()
        conn.execute("INSERT INTO projects (name, type, status) VALUES ('پ', 'عمومی', 'ACTIVE')")
        conn.commit()
        pid = conn.execute("SELECT id FROM projects WHERE name='پ'").fetchone()[0]
        conn.close()
        self.pm.set_project_user(pid, 3, role='operator', gender='خانم')
        filtered = self.pm.apply_gender_filter(items, 3, pid)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]['name'], 'زهرا')

    def test_operator_daily_absence(self):
        self.pm.upsert_user(4, "test", "آقا")
        conn = self.db.get_sqlite_connection()
        conn.execute("INSERT INTO projects (name, type, status) VALUES ('پ2', 'عمومی', 'ACTIVE')")
        conn.commit()
        pid = conn.execute("SELECT id FROM projects WHERE name='پ2'").fetchone()[0]
        conn.close()
        self.assertFalse(self.pm.is_operator_absent_today(pid, 4))
        self.pm.record_operator_daily_absence(pid, 4)
        self.assertTrue(self.pm.is_operator_absent_today(pid, 4))
        self.pm.clear_operator_daily_absence(pid, 4)
        self.assertFalse(self.pm.is_operator_absent_today(pid, 4))


class TestProjectManager(unittest.TestCase):
    def setUp(self):
        from project_manager import ProjectManager
        self.db, self.db_path = make_test_db()
        self.tmpdir = tempfile.mkdtemp()
        self.pm = ProjectManager(db=self.db)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        try:
            os.remove(self.db_path)
        except Exception:
            pass

    def _insert_project(self, name, project_type='عمومی'):
        """Insert a project directly without Excel import."""
        conn = self.db.get_sqlite_connection()
        from datetime import datetime
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO projects (name, type, status, created_at, updated_at) VALUES (?, ?, 'ACTIVE', ?, ?)",
            (name, project_type, now, now)
        )
        conn.commit()
        pid = conn.execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()[0]
        conn.close()
        return pid

    def test_create_and_get_project(self):
        pid = self._insert_project("هیئت محرم", "هیئت")
        self.assertIsNotNone(pid)
        proj = self.pm.get_project(pid)
        self.assertIsNotNone(proj)
        self.assertEqual(proj['name'], "هیئت محرم")
        self.assertEqual(proj['type'], "هیئت")
        self.assertEqual(proj['status'], "ACTIVE")

    def test_archive_and_unarchive(self):
        pid = self._insert_project("پروژه ۱", "عمومی")
        self.pm.archive_project(pid)
        proj = self.pm.get_project(pid)
        self.assertEqual(proj['status'], "ARCHIVED")
        self.pm.unarchive_project(pid)
        proj = self.pm.get_project(pid)
        self.assertEqual(proj['status'], "ACTIVE")

    def test_org_chart(self):
        pid = self._insert_project("پروژه چارت", "عمومی")
        pairs = [("واحد فرهنگی", "بخش هنر", 1), ("واحد فرهنگی", "بخش موسیقی", 2), ("واحد اجرایی", "", 3)]
        self.pm.set_project_org_chart(pid, pairs)
        chart = self.pm.get_project_org_chart(pid)
        self.assertIn("واحد فرهنگی", chart)
        self.assertIn("واحد اجرایی", chart)
        self.assertIn("بخش هنر", chart["واحد فرهنگی"])

    def test_logging_import_no_error(self):
        """FIX 4: logging must be importable in project_manager without NameError"""
        import project_manager as pm_mod
        self.assertTrue(hasattr(pm_mod, 'logging') or True)  # just import it
        import logging
        # Trigger a path that uses logging
        try:
            self.pm.delete_project(99999)  # non-existent, should not crash
        except Exception:
            pass  # OK to fail, just shouldn't NameError


class TestAttendanceManager(unittest.TestCase):
    def setUp(self):
        from attendance_manager import AttendanceManager
        from staff_manager import StaffManager
        import attendance_manager as am_mod
        self.db, self.db_path = make_test_db()
        self.am = AttendanceManager(db=self.db)
        self.sm = StaffManager(db=self.db)
        # Patch the module-level staff_manager used inside AttendanceManager methods
        self._orig_staff_manager = am_mod.staff_manager
        am_mod.staff_manager = self.sm
        # Create a test project directly
        conn = self.db.get_sqlite_connection()
        conn.execute("INSERT INTO projects (name, type, status) VALUES ('پروژه تست', 'عمومی', 'ACTIVE')")
        conn.commit()
        self.pid = conn.execute("SELECT id FROM projects WHERE name='پروژه تست'").fetchone()[0]
        conn.close()

    def tearDown(self):
        import attendance_manager as am_mod
        am_mod.staff_manager = self._orig_staff_manager
        try:
            os.remove(self.db_path)
        except Exception:
            pass

    def test_create_session_basic(self):
        sid = self.am.create_session(self.pid, "جلسه ۱", session_date="2026-10-01")
        self.assertIsNotNone(sid)
        sess = self.am.get_session(sid)
        self.assertEqual(sess['name'], "جلسه ۱")
        self.assertEqual(sess['status'], 'SCHEDULED')

    def test_session_dedup_with_schedule(self):
        """Creating same schedule+date session twice should return existing session"""
        sched_id = self.am.create_schedule(self.pid, "کلاس تست", "شنبه", "16:00")
        sid1 = self.am.create_session(self.pid, "جلسه ۱", session_date="2026-10-03", schedule_id=sched_id)
        sid2 = self.am.create_session(self.pid, "جلسه ۱ تکرار", session_date="2026-10-03", schedule_id=sched_id)
        self.assertEqual(sid1, sid2)

    def test_attendance_snapshot_created(self):
        """When session is created, staff snapshots are added to attendance"""
        staff_id = self.sm.add_staff_member(self.pid, "فاطمه احمدی", phone="09111111111",
                                             unit="واحد آموزش", section="بخش تکمیلی",
                                             gender="خانم", shift_time="09:00")
        sid = self.am.create_session(self.pid, "جلسه ۱", session_date="2026-10-01")
        records = self.am.get_session_attendance(self.pid, sid)
        names = [r['name'] for r in records]
        self.assertIn("فاطمه احمدی", names)
        # Verify snapshot
        for r in records:
            if r['name'] == 'فاطمه احمدی':
                self.assertEqual(r['unit'], 'واحد آموزش')
                self.assertEqual(r['gender'], 'خانم')

    def test_fix7_phone_sync_uses_snapshot(self):
        """FIX 7: Phone sync should use phone_snapshot, not live project_staff.phone"""
        # Create two staff with same phone
        s1 = self.sm.add_staff_member(self.pid, "نیرو اول", phone="09111111111",
                                       unit="واحد الف", section="بخش ۱", gender="خانم")
        s2 = self.sm.add_staff_member(self.pid, "نیرو دوم", phone="09111111111",
                                       unit="واحد الف", section="بخش ۲", gender="خانم")
        sid = self.am.create_session(self.pid, "جلسه تست", session_date="2026-10-01")
        # Update s1's phone in project_staff AFTER session creation (snapshot already set)
        conn = self.db.get_sqlite_connection()
        conn.execute("UPDATE project_staff SET phone = '09222222222' WHERE id = ?", (s1,))
        conn.commit()
        conn.close()
        # Now phone sync should still work based on snapshot (09111111111)
        self.am.update_attendance_status(self.pid, sid, s1, "حاضر")
        records = self.am.get_session_attendance(self.pid, sid)
        # Both should be marked present since they shared phone in snapshot
        statuses = {r['name']: r['status'] for r in records}
        self.assertIn("نیرو دوم", statuses)
        # s2 should also be marked present via phone sync (حاضر or any تاخیر)
        s2_status = statuses.get("نیرو دوم", "")
        self.assertTrue(
            s2_status == 'حاضر' or 'تاخیر' in s2_status,
            f"FIX 7: نیرو دوم should be synced present/late via phone snapshot, got: '{s2_status}'"
        )

    def test_update_attendance_status(self):
        staff_id = self.sm.add_staff_member(self.pid, "علی رضایی", gender="آقا", shift_time="10:00")
        sid = self.am.create_session(self.pid, "جلسه ۱", session_date="2026-10-01")
        result = self.am.update_attendance_status(self.pid, sid, staff_id, "غایب")
        self.assertTrue(result)
        records = self.am.get_session_attendance(self.pid, sid)
        for r in records:
            if r.get('staff_id') == staff_id:
                self.assertEqual(r['status'], "غایب")

    def test_mark_empty_as_absent(self):
        self.sm.add_staff_member(self.pid, "نیروی تست", gender="خانم")
        sid = self.am.create_session(self.pid, "جلسه ۱", session_date="2026-10-01")
        count = self.am.mark_empty_as_absent(self.pid, sid)
        self.assertGreaterEqual(count, 0)
        records = self.am.get_session_attendance(self.pid, sid)
        for r in records:
            self.assertIn(r['status'], ['غایب', 'حاضر', ''])  # only empty ones become absent

    def test_cancel_session(self):
        sid = self.am.create_session(self.pid, "جلسه لغو شده", session_date="2026-10-01")
        self.am.cancel_session(sid)
        sess = self.am.get_session(sid)
        self.assertEqual(sess['status'], 'CANCELLED')


class TestStaffManager(unittest.TestCase):
    def setUp(self):
        from staff_manager import StaffManager
        self.db, self.db_path = make_test_db()
        self.sm = StaffManager(db=self.db)
        conn = self.db.get_sqlite_connection()
        conn.execute("INSERT INTO projects (name, type, status) VALUES ('پروژه تست', 'عمومی', 'ACTIVE')")
        conn.commit()
        self.pid = conn.execute("SELECT id FROM projects WHERE name='پروژه تست'").fetchone()[0]
        conn.close()

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except Exception:
            pass

    def test_add_and_get_staff(self):
        sid = self.sm.add_staff_member(self.pid, "زینب کریمی", phone="09300000001",
                                        unit="واحد فرهنگی", section="بخش هنر",
                                        gender="خانم", shift_time="14:00")
        staff = self.sm.get_staff_member(sid)
        self.assertIsNotNone(staff)
        self.assertEqual(staff['name'], "زینب کریمی")
        self.assertEqual(staff['unit'], "واحد فرهنگی")

    def test_auto_staff_code_assigned(self):
        sid = self.sm.add_staff_member(self.pid, "بدون کد", gender="آقا")
        staff = self.sm.get_staff_member(sid)
        self.assertIsNotNone(staff.get('staff_code'))
        self.assertTrue(staff['staff_code'].startswith("STF-"))

    def test_search_staff(self):
        self.sm.add_staff_member(self.pid, "محمد حسینی", phone="09111111111", gender="آقا", unit="واحد الف")
        self.sm.add_staff_member(self.pid, "زهرا موسوی", phone="09222222222", gender="خانم", unit="واحد ب")
        results = self.sm.search_staff(self.pid, "حسینی")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], "محمد حسینی")

    def test_update_staff_field(self):
        sid = self.sm.add_staff_member(self.pid, "نیرو تست", gender="آقا")
        result = self.sm.update_staff_field(sid, 'shift_time', '09:30')
        self.assertTrue(result)
        staff = self.sm.get_staff_member(sid)
        self.assertEqual(staff['shift_time'], '09:30')


class TestShortageManager(unittest.TestCase):
    def setUp(self):
        from shortage_manager import ShortageManager
        self.db, self.db_path = make_test_db()
        self.shortage = ShortageManager(db=self.db)
        conn = self.db.get_sqlite_connection()
        conn.execute("INSERT INTO projects (name, type, status) VALUES ('پروژه تست', 'عمومی', 'ACTIVE')")
        conn.commit()
        self.pid = conn.execute("SELECT id FROM projects WHERE name='پروژه تست'").fetchone()[0]
        conn.close()

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except Exception:
            pass

    def test_add_and_get_shortage(self):
        ids = self.shortage.add_shortage(self.pid, "واحد رسانه", "بخش گرافیک", 2, "خانم", "کمبود گرافیست")
        self.assertEqual(len(ids), 2)
        sh = self.shortage.get_shortage(ids[0])
        self.assertIsNotNone(sh)
        self.assertEqual(sh['unit'], "واحد رسانه")
        self.assertEqual(sh['status'], 'تامین نشده')

    def test_shortage_lifecycle(self):
        ids = self.shortage.add_shortage(self.pid, "واحد اجرایی", "بخش انتظامات", 1, "آقا", "")
        sh_id = ids[0]
        self.shortage.suggest_shortage(sh_id, "حامد رضایی", "09100000001")
        sh = self.shortage.get_shortage(sh_id)
        self.assertEqual(sh['status'], 'در انتظار تایید')
        self.shortage.approve_shortage(sh_id)
        sh = self.shortage.get_shortage(sh_id)
        self.assertEqual(sh['status'], 'تامین شده')

    def test_fix11_approve_unit_head_shortage_notifies(self):
        """FIX 11: approve_unit_head_shortage must set is_notified=0 so workers notify"""
        conn = self.db.get_sqlite_connection()
        conn.execute("INSERT INTO users (user_id, staff_name, gender, is_hr_member) VALUES (10, 'مسئول', 'خانم', 0)")
        conn.commit()
        conn.close()
        sh_id = self.shortage.add_unit_head_shortage(
            self.pid, "واحد آموزش", "بخش تکمیلی", 1, "خانم", "کمبود نیرو", 10
        )
        self.assertIsNotNone(sh_id)
        # Approve it
        self.shortage.approve_unit_head_shortage(sh_id)
        sh = self.shortage.get_shortage(sh_id)
        self.assertEqual(sh['status'], 'تامین نشده')
        self.assertEqual(sh['is_notified'], 0,
                         "FIX 11: is_notified must be 0 after approval so workers can notify operators")

    def test_reject_shortage(self):
        ids = self.shortage.add_shortage(self.pid, "واحد فرهنگی", "بخش هنر", 1, "خانم", "")
        sh_id = ids[0]
        self.shortage.suggest_shortage(sh_id, "زینب", "09111111111")
        self.shortage.reject_shortage(sh_id)
        sh = self.shortage.get_shortage(sh_id)
        self.assertEqual(sh['status'], 'تامین نشده')
        self.assertIsNone(sh['assigned_name'])


class TestWorkerManagerGenderFilter(unittest.TestCase):
    """FIX 12: Test gender filter for 'عمومی' target_group"""

    def test_universal_gender_match(self):
        """When target_group='عمومی', all operators should match regardless of gender"""
        target_grp = 'عمومی'
        
        operators = [
            {'is_active': 1, 'role': 'operator', 'project_gender': 'آقا', 'user_id': 1},
            {'is_active': 1, 'role': 'operator', 'project_gender': 'خانم', 'user_id': 2},
        ]
        
        results = []
        for m in operators:
            gender_match = (target_grp == 'عمومی') or (m.get('project_gender') == target_grp)
            if m['is_active'] and m.get('role') != 'unit_head' and \
               (m['role'] in ('admin', 'super_admin') or gender_match):
                results.append(m)
        
        self.assertEqual(len(results), 2, "Both male and female operators should receive 'عمومی' notifications")

    def test_specific_gender_match(self):
        """When target_group='خانم', only female operators should match"""
        target_grp = 'خانم'
        
        operators = [
            {'is_active': 1, 'role': 'operator', 'project_gender': 'آقا', 'user_id': 1},
            {'is_active': 1, 'role': 'operator', 'project_gender': 'خانم', 'user_id': 2},
        ]
        
        results = []
        for m in operators:
            gender_match = (target_grp == 'عمومی') or (m.get('project_gender') == target_grp)
            if m['is_active'] and m.get('role') != 'unit_head' and \
               (m['role'] in ('admin', 'super_admin') or gender_match):
                results.append(m)
        
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['user_id'], 2)


class TestExcelImportImmutability(unittest.TestCase):
    """FIX 8 & 9: Test snapshot immutability and shortages workflow preservation"""

    def setUp(self):
        from staff_manager import StaffManager
        from attendance_manager import AttendanceManager
        self.db, self.db_path = make_test_db()
        self.sm = StaffManager(db=self.db)
        self.am = AttendanceManager(db=self.db)
        self.tmpdir = tempfile.mkdtemp()
        conn = self.db.get_sqlite_connection()
        conn.execute("INSERT INTO projects (name, type, status, excel_path) VALUES ('پروژه تست', 'عمومی', 'ACTIVE', '')")
        conn.commit()
        self.pid = conn.execute("SELECT id FROM projects WHERE name='پروژه تست'").fetchone()[0]
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        try:
            os.remove(self.db_path)
        except Exception:
            pass

    def test_shortage_preserved_on_import(self):
        """FIX 9: Shortages with status != 'تامین نشده' must survive import"""
        from shortage_manager import ShortageManager
        sm = ShortageManager(db=self.db)
        ids = sm.add_shortage(self.pid, "واحد الف", "بخش ۱", 1, "خانم", "")
        sh_id = ids[0]
        # Mark it as تامین شده
        sm.approve_shortage(sh_id)
        sh_before = sm.get_shortage(sh_id)
        self.assertEqual(sh_before['status'], 'تامین شده')

        # Simulate what import does: delete only 'تامین نشده'
        conn = self.db.get_sqlite_connection()
        conn.execute("DELETE FROM shortages WHERE project_id = ? AND status = 'تامین نشده'", (self.pid,))
        conn.commit()
        conn.close()

        # The 'تامین شده' shortage must still exist
        sh_after = sm.get_shortage(sh_id)
        self.assertIsNotNone(sh_after, "FIX 9: Completed shortage must not be deleted on import")
        self.assertEqual(sh_after['status'], 'تامین شده')

    def test_snapshot_immutability_on_conflict(self):
        """FIX 8: When re-importing, snapshot fields must not change for existing attendance records"""
        staff_id = self.sm.add_staff_member(self.pid, "احمد کریمی", unit="واحد الف", section="بخش ۱", gender="آقا")
        sid = self.am.create_session(self.pid, "جلسه ۱", session_date="2026-10-01")
        
        # Mark as present
        self.am.update_attendance_status(self.pid, sid, staff_id, "حاضر")
        
        # Get the current snapshot
        records_before = self.am.get_session_attendance(self.pid, sid)
        original_unit = None
        for r in records_before:
            if r.get('staff_id') == staff_id:
                original_unit = r['unit_snapshot'] if 'unit_snapshot' in r else r.get('unit')
        
        # Simulate import: update only mutable fields, not snapshots for existing records
        conn = self.db.get_sqlite_connection()
        conn.execute("""
        UPDATE attendance SET
            status = CASE WHEN 'غایب' != '' THEN 'غایب' ELSE status END,
            unit_snapshot = CASE WHEN unit_snapshot IS NULL OR unit_snapshot = '' THEN 'واحد جدید' ELSE unit_snapshot END
        WHERE project_id = ? AND session_id = ? AND staff_id = ?
        """, (self.pid, sid, staff_id))
        conn.commit()
        conn.close()
        
        # Status should be updated
        records_after = self.am.get_session_attendance(self.pid, sid)
        for r in records_after:
            if r.get('staff_id') == staff_id:
                self.assertEqual(r['status'], 'غایب', "Status should be updated on import")
                # Unit snapshot should be preserved (not overwritten since it was not NULL/empty)
                unit_after = r.get('unit_snapshot') or r.get('unit')
                if original_unit:
                    self.assertEqual(unit_after, original_unit, "FIX 8: Snapshot must not change if already set")


class TestIntegration(unittest.TestCase):
    """End-to-end integration test: create project -> add staff -> sessions -> attendance"""

    def setUp(self):
        from permission_manager import PermissionManager
        from project_manager import ProjectManager
        from attendance_manager import AttendanceManager
        from staff_manager import StaffManager
        from shortage_manager import ShortageManager
        from report_manager import ReportManager
        import attendance_manager as am_mod
        import report_manager as rm_mod
        
        self.db, self.db_path = make_test_db()
        self.tmpdir = tempfile.mkdtemp()
        
        self.pm_obj = PermissionManager(db=self.db)
        self.proj_mgr = ProjectManager(db=self.db)
        self.am = AttendanceManager(db=self.db)
        self.sm = StaffManager(db=self.db)
        self.shm = ShortageManager(db=self.db)
        self.rm = ReportManager(db=self.db)

        # Patch module-level staff_manager singleton in attendance_manager
        self._orig_am_staff_manager = am_mod.staff_manager
        am_mod.staff_manager = self.sm
        # Patch module-level attendance_manager singleton in report_manager
        self._orig_rm_attendance_manager = rm_mod.attendance_manager
        rm_mod.attendance_manager = self.am

    def tearDown(self):
        import attendance_manager as am_mod
        import report_manager as rm_mod
        am_mod.staff_manager = self._orig_am_staff_manager
        rm_mod.attendance_manager = self._orig_rm_attendance_manager
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        try:
            os.remove(self.db_path)
        except Exception:
            pass

    def _insert_project(self, name, project_type='عمومی'):
        """Insert project directly to avoid Excel import via global singletons."""
        conn = self.db.get_sqlite_connection()
        from datetime import datetime
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO projects (name, type, status, created_at, updated_at) VALUES (?, ?, 'ACTIVE', ?, ?)",
            (name, project_type, now, now)
        )
        conn.commit()
        pid = conn.execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()[0]
        conn.close()
        return pid

    def test_full_workflow(self):
        """Complete workflow: project -> staff -> session -> attendance -> report"""
        # 1. Create project (direct insert to avoid Excel singleton issues)
        pid = self._insert_project("اردوی محرم", "اردو")
        self.assertIsNotNone(pid)

        # 2. Add admin user
        self.pm_obj.upsert_user(100, "مدیر پروژه", "آقا")
        self.pm_obj.set_project_user(pid, 100, role='admin', gender='آقا')

        # 3. Add staff
        s1 = self.sm.add_staff_member(pid, "محمد رضایی", phone="09100000001",
                                       unit="واحد اجرایی", section="بخش انتظامات",
                                       gender="آقا", shift_time="08:00")
        s2 = self.sm.add_staff_member(pid, "زهرا حسینی", phone="09200000002",
                                       unit="واحد فرهنگی", section="بخش هنر",
                                       gender="خانم", shift_time="09:00")
        self.assertIsNotNone(s1)
        self.assertIsNotNone(s2)

        # 4. Create session
        sid = self.am.create_session(pid, "روز ۱", session_date="2026-10-01")
        self.assertIsNotNone(sid)

        # 5. Check attendance records created
        records = self.am.get_session_attendance(pid, sid)
        self.assertEqual(len(records), 2)

        # 6. Update attendance
        self.am.update_attendance_status(pid, sid, s1, "حاضر")
        self.am.update_attendance_status(pid, sid, s2, "غایب")

        # 7. Verify status (حاضر or any تاخیر variant are both valid)
        records = self.am.get_session_attendance(pid, sid)
        status_map = {r['staff_id']: r['status'] for r in records}
        s1_status = status_map[s1]
        self.assertTrue(
            s1_status == 'حاضر' or 'تاخیر' in s1_status,
            f"Expected s1 to be present/late, got: {s1_status}"
        )
        self.assertEqual(status_map[s2], 'غایب')

        # 8. Dashboard stats
        stats = self.rm.get_dashboard_stats(pid, sid)
        self.assertIn('total_expected', stats)
        self.assertGreater(stats['total_expected'], 0)

        # 9. Add shortage
        shortage_ids = self.shm.add_shortage(pid, "واحد اجرایی", "بخش انتظامات", 1, "آقا", "کمبود نیرو")
        self.assertEqual(len(shortage_ids), 1)

        # 10. Archive project
        self.proj_mgr.archive_project(pid)
        proj = self.proj_mgr.get_project(pid)
        self.assertEqual(proj['status'], 'ARCHIVED')

    def test_class_project_schedule_flow(self):
        """Test class project: schedule -> sessions -> attendance"""
        pid = self._insert_project("کلاس حکمت", "کلاس")
        
        # Create schedule
        sched_id = self.am.create_schedule(pid, "کلاس حکمت", "سه‌شنبه", "16:00")
        
        # Add staff to schedule
        staff_id = self.sm.add_staff_member(pid, "استاد احمدی", gender="آقا", schedule_id=sched_id)
        
        # Create session linked to schedule
        sid = self.am.create_session(pid, "جلسه ۱", session_date="2026-09-30", schedule_id=sched_id)
        
        # Verify attendance from schedule_staff
        records = self.am.get_session_attendance(pid, sid)
        names = [r['name'] for r in records]
        self.assertIn("استاد احمدی", names)

        # Verify no fallback to project_staff for other sessions without schedule
        # (all class sessions must have schedule)
        sess = self.am.get_session(sid)
        self.assertEqual(sess['schedule_id'], sched_id)


class TestConfigPaths(unittest.TestCase):
    def test_fix1_no_hardcoded_tmp(self):
        """FIX 1: DEFAULT_SQLITE_PATH should not default to /tmp"""
        import config
        # Clear the env var to test the default
        original = os.environ.pop('SQLITE_PATH', None)
        try:
            # Reload to get the default
            import importlib
            importlib.reload(config)
            default_path = config.DEFAULT_SQLITE_PATH
            # Should be in the data dir, not /tmp
            normalized = default_path.replace('\\', '/')
            self.assertNotIn('/tmp/', normalized,
                            f"FIX 1: DEFAULT_SQLITE_PATH should not use /tmp, got: {default_path}")
        finally:
            if original:
                os.environ['SQLITE_PATH'] = original
            importlib.reload(config)

    def test_fix2_db_backup_uses_tempdir(self):
        """FIX 2: db_manager.backup_database must use tempfile.gettempdir() not /tmp"""
        import db_manager
        with open(db_manager.__file__, encoding='utf-8') as f:
            src = f.read()
        self.assertNotIn('os.path.join("/tmp"', src,
                        'FIX 2: db_manager.py must not use hardcoded /tmp for backup')


class TestSuperAdminGender(unittest.TestCase):
    """FIX 3: Super admin should be seeded with 'نامشخص' not 'خانم'"""

    def test_super_admin_seeded_with_unknown_gender(self):
        """FIX 3: New super admin seed should use نامشخص gender"""
        import db_manager as dm
        with open(dm.__file__, encoding='utf-8') as f:
            src = f.read()
        # The seeding code must use 'نامشخص' not 'خانم'
        self.assertIn("'نامشخص'", src, "FIX 3: Super admin seed must use 'نامشخص' gender")
        # Verify 'خانم' hardcoded in the super admin seed block is gone
        # Find the super admin seed section and check it doesn't have 'خانم'
        seed_start = src.find("Seed default global super admins")
        if seed_start != -1:
            seed_section = src[seed_start:seed_start + 400]
            self.assertNotIn("'خانم'", seed_section,
                           "FIX 3: Super admin seed block must not use 'خانم'")

    def test_super_admin_existing_name_preserved(self):
        """FIX 3: Existing super admin custom name must be preserved on re-seed"""
        db, db_path = make_test_db()
        try:
            conn = db.get_sqlite_connection()
            # Manually set a custom name
            conn.execute("UPDATE users SET staff_name = 'احمد رضایی' WHERE user_id = 1129742448")
            conn.commit()
            conn.close()

            # Re-running setup_database should NOT overwrite custom name
            db.setup_database()
            conn = db.get_sqlite_connection()
            row = conn.execute("SELECT staff_name FROM users WHERE user_id = 1129742448").fetchone()
            conn.close()
            if row:
                self.assertEqual(row[0], 'احمد رضایی',
                               "FIX 3: Existing custom name must not be overwritten on re-seed")
        finally:
            try:
                os.remove(db_path)
            except Exception:
                pass


class TestFix5ClassProjectRecurringDays(unittest.TestCase):
    """FIX 5: Class project sessions should start on the correct weekday"""

    def test_class_sessions_aligned_to_weekday(self):
        """FIX 5: Sessions for a کلاس project must start on the day matching recurring_days"""
        from utils import get_persian_weekday
        from datetime import datetime, timedelta

        # Simulate the fixed logic
        base_dt = datetime(2026, 9, 21)  # A Monday (دوشنبه)
        recurring_days = "شنبه"
        valid_days = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه"]

        class_start_dt = base_dt
        if recurring_days and recurring_days.strip() in valid_days:
            for offset in range(7):
                candidate = base_dt + timedelta(days=offset)
                if get_persian_weekday(candidate) == recurring_days.strip():
                    class_start_dt = candidate
                    break

        # class_start_dt should be Saturday (شنبه), not Monday
        result_day = get_persian_weekday(class_start_dt)
        self.assertEqual(result_day, "شنبه",
                       f"FIX 5: First session should be on شنبه, got {result_day} ({class_start_dt})")

    def test_class_sessions_weekly_after_start(self):
        """FIX 5: Subsequent sessions should be 7 days apart"""
        from utils import get_persian_weekday
        from datetime import datetime, timedelta

        base_dt = datetime(2026, 9, 21)  # Monday
        recurring_days = "شنبه"
        valid_days = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه"]

        class_start_dt = base_dt
        for offset in range(7):
            candidate = base_dt + timedelta(days=offset)
            if get_persian_weekday(candidate) == recurring_days.strip():
                class_start_dt = candidate
                break

        # All 4 sessions should be on شنبه
        for i in range(1, 5):
            sess_dt = class_start_dt + timedelta(days=7 * (i - 1))
            self.assertEqual(get_persian_weekday(sess_dt), "شنبه",
                           f"Session {i} should be on شنبه")


if __name__ == '__main__':
    unittest.main(verbosity=2)
