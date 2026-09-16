import os
import sys
import sqlite3
import shutil
import threading
import logging
from datetime import datetime
from config import (
    DEFAULT_SQLITE_PATH, MYSQL_ENABLED, MYSQL_HOST, MYSQL_USER, 
    MYSQL_PASS, MYSQL_DB, MYSQL_TIMEOUT, SUPER_ADMINS, LOGS_DIR, DB_BACKUPS_DIR
)

log_file = os.path.join(LOGS_DIR, "db_manager.log")
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

class DatabaseUnavailableError(Exception):
    """Raised when the primary database cannot be reached, locked, or unavailable."""
    pass

class DatabaseManager:
    CURRENT_SCHEMA_VERSION = 8

    def __init__(self, db_path=DEFAULT_SQLITE_PATH):
        self.db_path = db_path
        self.lock = threading.Lock()
        self._init_db_path()
        self.setup_database()

    def _init_db_path(self):
        """
        Initializes primary database path strictly without dangerous /tmp fallback.
        Raises DatabaseUnavailableError if the primary database is inaccessible or locked.
        """
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
            test_conn = sqlite3.connect(self.db_path, timeout=5.0)
            test_conn.execute("PRAGMA foreign_keys = ON")
            test_conn.execute("CREATE TABLE IF NOT EXISTS _lock_check (id INT)")
            test_conn.close()
        except (sqlite3.OperationalError, OSError) as e:
            logging.critical(f"Primary SQLite database is unavailable at {self.db_path}: {e}")
            raise DatabaseUnavailableError(f"Database file is inaccessible or locked: {e}")

    def get_sqlite_connection(self):
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            return conn
        except sqlite3.OperationalError as e:
            logging.error(f"Primary database connection error: {e}")
            raise DatabaseUnavailableError(f"Primary database unavailable: {e}")

    def get_mysql_connection(self):
        if not MYSQL_ENABLED:
            return None
        try:
            import pymysql
            import pymysql.cursors
            return pymysql.connect(
                host=MYSQL_HOST, user=MYSQL_USER, password=MYSQL_PASS,
                database=MYSQL_DB, charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=MYSQL_TIMEOUT, autocommit=True
            )
        except Exception as e:
            logging.error(f"MySQL connection error: {e}")
            return None

    def setup_database(self):
        with self.lock:
            conn = self.get_sqlite_connection()
            cursor = conn.cursor()
            try:
                # Schema version control table
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_meta (
                    version INTEGER PRIMARY KEY,
                    updated_at TEXT
                )
                """)

                # 1. Global users table (Identity & Profile)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    staff_name TEXT NOT NULL,
                    gender TEXT NOT NULL,
                    is_global_super_admin BOOLEAN DEFAULT 0,
                    is_active BOOLEAN DEFAULT 1,
                    is_hr_member BOOLEAN DEFAULT 1,
                    created_at TEXT
                )
                """)

                # 2. Projects table (Lifecycle: DRAFT, ACTIVE, COMPLETED, ARCHIVED)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    type TEXT DEFAULT 'عمومی',
                    description TEXT DEFAULT '',
                    status TEXT DEFAULT 'ACTIVE',
                    excel_path TEXT,
                    total_sessions INTEGER DEFAULT 0,
                    recurring_days TEXT DEFAULT '',
                    activation_time TEXT DEFAULT '',
                    start_date TEXT DEFAULT '',
                    end_date TEXT DEFAULT '',
                    has_prep_day BOOLEAN DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT
                )
                """)

                # 3. Project Membership & Roles (Project-scoped permissions)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS project_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    role TEXT DEFAULT 'user',
                    gender TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    assigned_unit TEXT,
                    created_at TEXT,
                    UNIQUE(project_id, user_id),
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
                """)

                # 4. Project Organizational Chart (Scoped to project with display order)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS org_chart (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    unit TEXT NOT NULL,
                    section TEXT NOT NULL,
                    display_order INTEGER DEFAULT 0,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
                """)

                # 5. Recurring Schedules for Classes
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    day_of_week TEXT,
                    time_str TEXT,
                    location TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
                """)

                # 6. Actual Calendar Sessions
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    schedule_id INTEGER,
                    name TEXT NOT NULL,
                    session_date TEXT,
                    time_str TEXT DEFAULT '',
                    day_of_week TEXT DEFAULT '',
                    status TEXT DEFAULT 'SCHEDULED',
                    created_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(schedule_id) REFERENCES schedules(id) ON DELETE SET NULL
                )
                """)

                # 7. Project Staff with staff_code and session lifecycle
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS project_staff (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    staff_code TEXT,
                    name TEXT NOT NULL,
                    phone TEXT,
                    unit TEXT,
                    section TEXT,
                    position TEXT DEFAULT 'نیرو',
                    card_title TEXT,
                    shift_time TEXT,
                    gender TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    notes TEXT,
                    is_multi_section TEXT DEFAULT 'خیر',
                    start_session_id INTEGER,
                    end_session_id INTEGER,
                    _excel_row INTEGER,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
                """)

                # 8. Schedule Staff (Intermediate table for class/schedule-level staff segregation)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS schedule_staff (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_id INTEGER NOT NULL,
                    staff_id INTEGER NOT NULL,
                    start_session_id INTEGER,
                    end_session_id INTEGER,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TEXT,
                    UNIQUE(schedule_id, staff_id),
                    FOREIGN KEY(schedule_id) REFERENCES schedules(id) ON DELETE CASCADE,
                    FOREIGN KEY(staff_id) REFERENCES project_staff(id) ON DELETE CASCADE
                )
                """)

                # 9. Attendance with immutable historical snapshots
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS attendance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    session_id INTEGER NOT NULL,
                    staff_id INTEGER NOT NULL,
                    status TEXT DEFAULT '',
                    card_status TEXT DEFAULT '',
                    late_tracking TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    staff_name_snapshot TEXT,
                    unit_snapshot TEXT,
                    section_snapshot TEXT,
                    position_snapshot TEXT,
                    gender_snapshot TEXT,
                    phone_snapshot TEXT,
                    card_title_snapshot TEXT,
                    shift_time_snapshot TEXT,
                    is_multi_section_snapshot TEXT,
                    staff_code_snapshot TEXT,
                    updated_at TEXT,
                    UNIQUE(project_id, session_id, staff_id),
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY(staff_id) REFERENCES project_staff(id) ON DELETE CASCADE
                )
                """)

                # 10. Shortages
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS shortages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    unit TEXT NOT NULL,
                    section TEXT NOT NULL,
                    count INTEGER DEFAULT 1,
                    target_group TEXT,
                    description TEXT,
                    status TEXT DEFAULT 'تامین نشده',
                    assigned_name TEXT,
                    phone TEXT,
                    is_notified INTEGER DEFAULT 0,
                    requested_by INTEGER,
                    _excel_row INTEGER,
                    created_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
                """)

                # 11. Staff Logs
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS staff_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER,
                    user_id INTEGER NOT NULL,
                    action_type TEXT NOT NULL,
                    log_date TEXT NOT NULL,
                    created_at TEXT
                )
                """)

                # 12. User Context
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_context (
                    user_id INTEGER PRIMARY KEY,
                    current_project_id INTEGER,
                    current_session_id INTEGER,
                    updated_at TEXT
                )
                """)

                # 13. Operator Daily Absence
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS operator_daily_absence (
                    project_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    absent_date TEXT NOT NULL,
                    created_at TEXT,
                    PRIMARY KEY(project_id, user_id, absent_date),
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
                """)

                # Execute explicit schema migrations
                self._apply_migrations(cursor)

                # High-performance indexes
                indexes = [
                    ("idx_staff_project_active", "project_staff (project_id, is_active)"),
                    ("idx_staff_project_phone", "project_staff (project_id, phone)"),
                    ("uidx_project_staff_code", "project_staff (project_id, staff_code) WHERE staff_code IS NOT NULL AND staff_code != ''"),
                    ("idx_schedule_staff_lookup", "schedule_staff (schedule_id, staff_id, is_active)"),
                    ("idx_attendance_lookup", "attendance (project_id, session_id, staff_id)"),
                    ("idx_sessions_project", "sessions (project_id)"),
                    ("idx_sessions_sched_date", "sessions (project_id, schedule_id, session_date)"),
                    ("uidx_sessions_sched_date", "sessions (project_id, schedule_id, session_date) WHERE schedule_id IS NOT NULL AND session_date != '' AND session_date IS NOT NULL"),
                    ("idx_shortages_project", "shortages (project_id, status)"),
                    ("idx_project_users_uid", "project_users (user_id)"),
                    ("idx_staff_logs_date", "staff_logs (log_date, project_id)"),
                    ("idx_org_chart_order", "org_chart (project_id, display_order)")
                ]
                for idx_name, idx_def in indexes:
                    try:
                        cursor.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_def}")
                    except sqlite3.OperationalError as op_err:
                        if "already exists" not in str(op_err):
                            logging.warning(f"Index creation warning {idx_name}: {op_err}")

                # Seed default global super admins
                now_iso = datetime.now().isoformat()
                for uid in SUPER_ADMINS:
                    cursor.execute("""
                    INSERT INTO users (user_id, staff_name, gender, is_global_super_admin, is_active, created_at)
                    VALUES (?, ?, 'خانم', 1, 1, ?)
                    ON CONFLICT(user_id) DO UPDATE SET is_global_super_admin=1, is_active=1
                    """, (uid, f"مدیر ارشد {uid}", now_iso))

                # Verify schema version update atomically
                cursor.execute("SELECT MAX(version) FROM schema_meta")
                row = cursor.fetchone()
                current_v = row[0] if (row and row[0] is not None) else 0
                if current_v < self.CURRENT_SCHEMA_VERSION:
                    if current_v < 8:
                        # Version-controlled one-time migration for historical snapshot backfill
                        cursor.execute("""
                        UPDATE attendance
                        SET 
                            staff_name_snapshot = COALESCE(NULLIF(staff_name_snapshot, ''), (SELECT name FROM project_staff WHERE id = attendance.staff_id)),
                            unit_snapshot = COALESCE(NULLIF(unit_snapshot, ''), (SELECT unit FROM project_staff WHERE id = attendance.staff_id)),
                            section_snapshot = COALESCE(NULLIF(section_snapshot, ''), (SELECT section FROM project_staff WHERE id = attendance.staff_id)),
                            position_snapshot = COALESCE(NULLIF(position_snapshot, ''), (SELECT position FROM project_staff WHERE id = attendance.staff_id)),
                            gender_snapshot = COALESCE(NULLIF(gender_snapshot, ''), (SELECT gender FROM project_staff WHERE id = attendance.staff_id)),
                            phone_snapshot = COALESCE(NULLIF(phone_snapshot, ''), (SELECT phone FROM project_staff WHERE id = attendance.staff_id)),
                            card_title_snapshot = COALESCE(NULLIF(card_title_snapshot, ''), (SELECT card_title FROM project_staff WHERE id = attendance.staff_id)),
                            shift_time_snapshot = COALESCE(NULLIF(shift_time_snapshot, ''), (SELECT shift_time FROM project_staff WHERE id = attendance.staff_id)),
                            is_multi_section_snapshot = COALESCE(NULLIF(is_multi_section_snapshot, ''), (SELECT is_multi_section FROM project_staff WHERE id = attendance.staff_id)),
                            staff_code_snapshot = COALESCE(NULLIF(staff_code_snapshot, ''), (SELECT staff_code FROM project_staff WHERE id = attendance.staff_id))
                        WHERE staff_name_snapshot IS NULL OR staff_name_snapshot = ''
                        """)
                    cursor.execute("""
                    INSERT OR REPLACE INTO schema_meta (version, updated_at)
                    VALUES (?, ?)
                    """, (self.CURRENT_SCHEMA_VERSION, now_iso))
                    logging.info(f"Database schema verified and upgraded to version {self.CURRENT_SCHEMA_VERSION}")

                conn.commit()
            except Exception as e:
                conn.rollback()
                logging.critical(f"Database setup or migration failed: {e}")
                raise
            finally:
                conn.close()

    def _apply_migrations(self, cursor):
        """Runs column migrations safely within the setup transaction."""
        all_col_migrations = [
            ("users", "staff_name", "TEXT DEFAULT ''"),
            ("users", "gender", "TEXT DEFAULT 'خانم'"),
            ("users", "is_global_super_admin", "BOOLEAN DEFAULT 0"),
            ("users", "is_active", "BOOLEAN DEFAULT 1"),
            ("users", "is_hr_member", "BOOLEAN DEFAULT 1"),
            ("users", "created_at", "TEXT"),
            ("projects", "type", "TEXT DEFAULT 'عمومی'"),
            ("projects", "description", "TEXT DEFAULT ''"),
            ("projects", "status", "TEXT DEFAULT 'ACTIVE'"),
            ("projects", "excel_path", "TEXT"),
            ("projects", "total_sessions", "INTEGER DEFAULT 0"),
            ("projects", "recurring_days", "TEXT DEFAULT ''"),
            ("projects", "activation_time", "TEXT DEFAULT ''"),
            ("projects", "start_date", "TEXT DEFAULT ''"),
            ("projects", "end_date", "TEXT DEFAULT ''"),
            ("projects", "has_prep_day", "BOOLEAN DEFAULT 0"),
            ("projects", "created_at", "TEXT"),
            ("projects", "updated_at", "TEXT"),
            ("project_users", "role", "TEXT DEFAULT 'user'"),
            ("project_users", "gender", "TEXT DEFAULT 'خانم'"),
            ("project_users", "is_active", "BOOLEAN DEFAULT 1"),
            ("project_users", "assigned_unit", "TEXT"),
            ("project_users", "created_at", "TEXT"),
            ("sessions", "schedule_id", "INTEGER"),
            ("sessions", "session_date", "TEXT DEFAULT ''"),
            ("sessions", "time_str", "TEXT DEFAULT ''"),
            ("sessions", "day_of_week", "TEXT DEFAULT ''"),
            ("sessions", "status", "TEXT DEFAULT 'SCHEDULED'"),
            ("sessions", "created_at", "TEXT"),
            ("project_staff", "phone", "TEXT DEFAULT ''"),
            ("project_staff", "unit", "TEXT DEFAULT ''"),
            ("project_staff", "section", "TEXT DEFAULT ''"),
            ("project_staff", "position", "TEXT DEFAULT 'نیرو'"),
            ("project_staff", "card_title", "TEXT DEFAULT ''"),
            ("project_staff", "shift_time", "TEXT DEFAULT ''"),
            ("project_staff", "gender", "TEXT DEFAULT ''"),
            ("project_staff", "is_active", "BOOLEAN DEFAULT 1"),
            ("project_staff", "notes", "TEXT DEFAULT ''"),
            ("project_staff", "is_multi_section", "TEXT DEFAULT 'خیر'"),
            ("project_staff", "_excel_row", "INTEGER"),
            ("shortages", "count", "INTEGER DEFAULT 1"),
            ("shortages", "target_group", "TEXT DEFAULT 'عمومی'"),
            ("shortages", "description", "TEXT DEFAULT ''"),
            ("shortages", "status", "TEXT DEFAULT 'تامین نشده'"),
            ("shortages", "assigned_name", "TEXT"),
            ("shortages", "phone", "TEXT"),
            ("shortages", "is_notified", "INTEGER DEFAULT 0"),
            ("shortages", "requested_by", "INTEGER"),
            ("shortages", "_excel_row", "INTEGER"),
            ("shortages", "created_at", "TEXT"),
            ("project_staff", "staff_code", "TEXT"),
            ("project_staff", "start_session_id", "INTEGER"),
            ("project_staff", "end_session_id", "INTEGER"),
            ("attendance", "staff_name_snapshot", "TEXT"),
            ("attendance", "unit_snapshot", "TEXT"),
            ("attendance", "section_snapshot", "TEXT"),
            ("attendance", "position_snapshot", "TEXT"),
            ("attendance", "gender_snapshot", "TEXT"),
            ("attendance", "phone_snapshot", "TEXT"),
            ("attendance", "card_title_snapshot", "TEXT"),
            ("attendance", "shift_time_snapshot", "TEXT"),
            ("attendance", "is_multi_section_snapshot", "TEXT"),
            ("attendance", "staff_code_snapshot", "TEXT"),
            ("org_chart", "display_order", "INTEGER DEFAULT 0")
        ]

        for tbl, col, col_def in all_col_migrations:
            try:
                cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {col_def}")
            except sqlite3.OperationalError as op_err:
                if "duplicate column name" not in str(op_err):
                    logging.warning(f"Migration notice for {tbl}.{col}: {op_err}")

    def verify_backup_integrity(self, backup_path):
        """Runs PRAGMA integrity_check on the backed-up database to confirm zero corruption."""
        if not os.path.exists(backup_path):
            return False
        try:
            chk_conn = sqlite3.connect(backup_path, timeout=10.0)
            chk_cur = chk_conn.cursor()
            chk_cur.execute("PRAGMA integrity_check")
            rows = chk_cur.fetchall()
            chk_conn.close()
            return len(rows) == 1 and rows[0][0] == "ok"
        except Exception as e:
            logging.error(f"Backup integrity check failed for {backup_path}: {e}")
            return False

    def backup_database(self, label="auto"):
        """Safe SQLite online backup utilizing sqlite3.Connection.backup API with integrity check & retention."""
        with self.lock:
            try:
                if not os.path.exists(self.db_path):
                    return None
                timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                category_dir = os.path.join(DB_BACKUPS_DIR, label)
                os.makedirs(category_dir, exist_ok=True)
                backup_name = f"bot_cache_{label}_{timestamp}.db"
                target_path = os.path.join(category_dir, backup_name)

                temp_target = os.path.join("/tmp", f"tmp_bkp_{timestamp}_{os.getpid()}.db")
                source_conn = self.get_sqlite_connection()
                dest_conn = sqlite3.connect(temp_target)
                with dest_conn:
                    source_conn.backup(dest_conn)
                dest_conn.close()
                source_conn.close()

                if not self.verify_backup_integrity(temp_target):
                    if os.path.exists(temp_target):
                        os.remove(temp_target)
                    logging.error(f"Integrity check failed on backup {temp_target}. Aborting backup.")
                    return None

                shutil.move(temp_target, target_path)

                # Retention policy: keep last 15 backups per category
                try:
                    files = sorted(
                        [os.path.join(category_dir, f) for f in os.listdir(category_dir) if f.endswith(".db")],
                        key=os.path.getmtime
                    )
                    if len(files) > 15:
                        for old_f in files[:-15]:
                            os.remove(old_f)
                except Exception as ret_err:
                    logging.warning(f"Database backup retention cleanup notice: {ret_err}")

                logging.info(f"Database safe verified online backup created: {target_path}")
                return target_path
            except Exception as e:
                logging.error(f"Backup failed: {e}")
                return None

    def restore_backup(self, backup_path, destination_path=None):
        """Restores database from a verified backup and runs an operational query test."""
        if not self.verify_backup_integrity(backup_path):
            raise ValueError(f"Cannot restore from corrupt or invalid backup: {backup_path}")
        dest = destination_path or self.db_path
        with self.lock:
            src_conn = sqlite3.connect(backup_path)
            dest_conn = sqlite3.connect(dest)
            with dest_conn:
                src_conn.backup(dest_conn)
            src_conn.close()

            # Verify operational status on restored database
            test_cur = dest_conn.cursor()
            test_cur.execute("SELECT COUNT(*) FROM projects")
            _ = test_cur.fetchone()
            dest_conn.close()

            logging.info(f"Successfully restored database to {dest} from {backup_path} with verified operational test.")
            return True

db_instance = DatabaseManager()
