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

try:
    import pymysql
    import pymysql.cursors
    PYMYSQL_AVAILABLE = True
except ImportError:
    PYMYSQL_AVAILABLE = False
    logging.warning("pymysql is not installed. Remote MySQL sync will be skipped.")

class DatabaseManager:
    def __init__(self, db_path=DEFAULT_SQLITE_PATH):
        self.db_path = db_path
        self.active_db_path = db_path
        self.lock = threading.Lock()
        self.setup_database()

    def get_sqlite_connection(self):
        try:
            conn = sqlite3.connect(self.active_db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("CREATE TABLE IF NOT EXISTS _lock_check (id INT)")
            return conn
        except sqlite3.OperationalError as e:
            if "disk I/O error" in str(e) or "locking" in str(e):
                fallback_path = os.path.join("/tmp", os.path.basename(self.db_path))
                logging.warning(f"File locking failed on {self.active_db_path}. Falling back to {fallback_path}")
                self.active_db_path = fallback_path
                conn = sqlite3.connect(self.active_db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("CREATE TABLE IF NOT EXISTS _lock_check (id INT)")
                return conn
            raise

    def get_mysql_connection(self):
        if not MYSQL_ENABLED or not PYMYSQL_AVAILABLE:
            return None
        try:
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

            # 1. Global users table (Identity & Profile)
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                staff_name TEXT NOT NULL,
                gender TEXT NOT NULL,
                is_global_super_admin BOOLEAN DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                is_hr_member BOOLEAN DEFAULT 1,
                created_at TEXT
            )
            ''')

            # 2. Projects table (Lifecycle: DRAFT, ACTIVE, COMPLETED, ARCHIVED)
            cursor.execute('''
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
            ''')

            # Migrations for existing projects table
            for col, col_type in [
                ("total_sessions", "INTEGER DEFAULT 0"),
                ("recurring_days", "TEXT DEFAULT ''"),
                ("activation_time", "TEXT DEFAULT ''"),
                ("start_date", "TEXT DEFAULT ''"),
                ("end_date", "TEXT DEFAULT ''"),
                ("has_prep_day", "BOOLEAN DEFAULT 0")
            ]:
                try:
                    cursor.execute(f"ALTER TABLE projects ADD COLUMN {col} {col_type}")
                except Exception:
                    pass

            # 3. Project Membership & Roles (Project-scoped permissions)
            cursor.execute('''
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
            ''')

            # 4. Project Organizational Chart (Scoped to project)
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS org_chart (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                unit TEXT NOT NULL,
                section TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
            ''')

            # 5. Recurring Schedules for Classes
            cursor.execute('''
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
            ''')

            # 6. Actual Calendar Sessions
            cursor.execute('''
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
            ''')

            for col, col_type in [
                ("time_str", "TEXT DEFAULT ''"),
                ("day_of_week", "TEXT DEFAULT ''")
            ]:
                try:
                    cursor.execute(f"ALTER TABLE sessions ADD COLUMN {col} {col_type}")
                except Exception:
                    pass

            # 7. Project Staff
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS project_staff (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
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
                _excel_row INTEGER,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
            ''')

            # 8. Attendance
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                session_id INTEGER NOT NULL,
                staff_id INTEGER NOT NULL,
                status TEXT DEFAULT '',
                card_status TEXT DEFAULT '',
                late_tracking TEXT DEFAULT '',
                description TEXT DEFAULT '',
                updated_at TEXT,
                UNIQUE(project_id, session_id, staff_id),
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                FOREIGN KEY(staff_id) REFERENCES project_staff(id) ON DELETE CASCADE
            )
            ''')

            # 9. Shortages
            cursor.execute('''
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
            ''')

            # 10. Staff Logs
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS staff_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER,
                user_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                log_date TEXT NOT NULL,
                created_at TEXT
            )
            ''')

            # 11. User Context
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_context (
                user_id INTEGER PRIMARY KEY,
                current_project_id INTEGER,
                current_session_id INTEGER,
                updated_at TEXT
            )
            ''')

            # Comprehensive Schema Migrations for backward and forward compatibility
            migrations = [
                # users
                ("users", "staff_name", "TEXT DEFAULT ''"),
                ("users", "gender", "TEXT DEFAULT 'خانم'"),
                ("users", "is_global_super_admin", "BOOLEAN DEFAULT 0"),
                ("users", "is_active", "BOOLEAN DEFAULT 1"),
                ("users", "is_hr_member", "BOOLEAN DEFAULT 1"),
                ("users", "created_at", "TEXT"),
                # projects
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
                # project_users
                ("project_users", "role", "TEXT DEFAULT 'user'"),
                ("project_users", "gender", "TEXT DEFAULT 'خانم'"),
                ("project_users", "is_active", "BOOLEAN DEFAULT 1"),
                ("project_users", "assigned_unit", "TEXT"),
                ("project_users", "created_at", "TEXT"),
                # schedules
                ("schedules", "day_of_week", "TEXT DEFAULT ''"),
                ("schedules", "time_str", "TEXT DEFAULT ''"),
                ("schedules", "location", "TEXT DEFAULT ''"),
                ("schedules", "is_active", "BOOLEAN DEFAULT 1"),
                # sessions
                ("sessions", "schedule_id", "INTEGER"),
                ("sessions", "session_date", "TEXT DEFAULT ''"),
                ("sessions", "time_str", "TEXT DEFAULT ''"),
                ("sessions", "day_of_week", "TEXT DEFAULT ''"),
                ("sessions", "status", "TEXT DEFAULT 'SCHEDULED'"),
                ("sessions", "created_at", "TEXT"),
                # project_staff
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
                # attendance
                ("attendance", "status", "TEXT DEFAULT ''"),
                ("attendance", "card_status", "TEXT DEFAULT ''"),
                ("attendance", "late_tracking", "TEXT DEFAULT ''"),
                ("attendance", "description", "TEXT DEFAULT ''"),
                ("attendance", "updated_at", "TEXT"),
                # shortages
                ("shortages", "count", "INTEGER DEFAULT 1"),
                ("shortages", "target_group", "TEXT DEFAULT 'عمومی'"),
                ("shortages", "description", "TEXT DEFAULT ''"),
                ("shortages", "status", "TEXT DEFAULT 'تامین نشده'"),
                ("shortages", "assigned_name", "TEXT"),
                ("shortages", "phone", "TEXT"),
                ("shortages", "is_notified", "INTEGER DEFAULT 0"),
                ("shortages", "requested_by", "INTEGER"),
                ("shortages", "_excel_row", "INTEGER"),
                ("shortages", "created_at", "TEXT")
            ]
            for tbl, col, col_def in migrations:
                try:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {col_def}")
                except Exception:
                    pass

            # High-performance indexes
            indexes = [
                ("idx_staff_project_active", "project_staff (project_id, is_active)"),
                ("idx_staff_project_phone", "project_staff (project_id, phone)"),
                ("idx_attendance_lookup", "attendance (project_id, session_id, staff_id)"),
                ("idx_sessions_project", "sessions (project_id)"),
                ("idx_shortages_project", "shortages (project_id, status)"),
                ("idx_project_users_uid", "project_users (user_id)"),
                ("idx_staff_logs_date", "staff_logs (log_date, project_id)")
            ]
            for idx_name, idx_def in indexes:
                try:
                    cursor.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_def}")
                except Exception:
                    pass

            # Seed default global super admins
            now_iso = datetime.now().isoformat()
            for uid in SUPER_ADMINS:
                cursor.execute('''
                INSERT INTO users (user_id, staff_name, gender, is_global_super_admin, is_active, created_at)
                VALUES (?, ?, 'خانم', 1, 1, ?)
                ON CONFLICT(user_id) DO UPDATE SET is_global_super_admin=1, is_active=1
                ''', (uid, f"مدیر ارشد {uid}", now_iso))

            conn.commit()
            conn.close()

    def backup_database(self, label="auto"):
        with self.lock:
            try:
                if not os.path.exists(self.active_db_path):
                    return None
                timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                backup_name = f"bot_cache_{label}_{timestamp}.db"
                target_path = os.path.join(DB_BACKUPS_DIR, backup_name)
                shutil.copy2(self.active_db_path, target_path)
                logging.info(f"Database backup created: {target_path}")
                return target_path
            except Exception as e:
                logging.error(f"Backup failed: {e}")
                return None

db_instance = DatabaseManager()
