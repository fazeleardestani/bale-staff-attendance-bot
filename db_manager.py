import os
import logging
from datetime import datetime
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor
from config import PG_HOST, PG_USER, PG_PASS, PG_DB, PG_PORT, SUPER_ADMINS, LOGS_DIR

log_file = os.path.join(LOGS_DIR, "db_manager.log")
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

class DatabaseUnavailableError(Exception):
    pass

class PostgresConnectionWrapper:
    """پوششی برای شبیه‌سازی رفتار SQLite و بازگرداندن اتصال به Pool به جای بستن آن"""
    def __init__(self, conn, pool):
        self.conn = conn
        self.pool = pool

    def cursor(self):
        return self.conn.cursor(cursor_factory=RealDictCursor)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        if self.conn and self.pool:
            self.pool.putconn(self.conn)
            self.conn = None

class DatabaseManager:
    CURRENT_SCHEMA_VERSION = 9

    def __init__(self):
        self.pool = None
        self._init_pool()
        self.setup_database()

    def _init_pool(self):
        try:
            self.pool = SimpleConnectionPool(
                minconn=1,
                maxconn=20,
                host=PG_HOST,
                database=PG_DB,
                user=PG_USER,
                password=PG_PASS,
                port=PG_PORT
            )
        except Exception as e:
            logging.critical(f"Failed to connect to PostgreSQL: {e}")
            raise DatabaseUnavailableError(f"PostgreSQL is inaccessible: {e}")

    def get_connection(self):
        try:
            conn = self.pool.getconn()
            conn.autocommit = False # مدیریت دستی تراکنش‌ها
            return PostgresConnectionWrapper(conn, self.pool)
        except Exception as e:
            logging.error(f"Failed to get connection from pool: {e}")
            raise DatabaseUnavailableError(f"Connection Pool Error: {e}")

    def setup_database(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            # جداول کاملا بر اساس سینتکس PostgreSQL بازنویسی شده‌اند (استفاده از SERIAL و BOOLEAN)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_meta (
                version INTEGER PRIMARY KEY,
                updated_at TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                staff_name TEXT NOT NULL,
                gender TEXT NOT NULL,
                is_global_super_admin BOOLEAN DEFAULT FALSE,
                is_active BOOLEAN DEFAULT TRUE,
                is_hr_member BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS projects (
                id SERIAL PRIMARY KEY,
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
                has_prep_day BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS project_users (
                id SERIAL PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                role TEXT DEFAULT 'user',
                gender TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                assigned_unit TEXT,
                created_at TIMESTAMP,
                UNIQUE(project_id, user_id)
            );
            
            CREATE TABLE IF NOT EXISTS org_chart (
                id SERIAL PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                unit TEXT NOT NULL,
                section TEXT NOT NULL,
                display_order INTEGER DEFAULT 0
            );
            
            CREATE TABLE IF NOT EXISTS schedules (
                id SERIAL PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                day_of_week TEXT,
                time_str TEXT,
                location TEXT,
                is_active BOOLEAN DEFAULT TRUE
            );
            
            CREATE TABLE IF NOT EXISTS sessions (
                id SERIAL PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                schedule_id INTEGER REFERENCES schedules(id) ON DELETE SET NULL,
                name TEXT NOT NULL,
                session_date TEXT,
                time_str TEXT DEFAULT '',
                day_of_week TEXT DEFAULT '',
                status TEXT DEFAULT 'SCHEDULED',
                created_at TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS project_staff (
                id SERIAL PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                staff_code TEXT,
                name TEXT NOT NULL,
                phone TEXT,
                unit TEXT,
                section TEXT,
                position TEXT DEFAULT 'نیرو',
                card_title TEXT,
                shift_time TEXT,
                gender TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                notes TEXT,
                is_multi_section TEXT DEFAULT 'خیر',
                start_session_id INTEGER,
                end_session_id INTEGER,
                _excel_row INTEGER
            );
            
            CREATE TABLE IF NOT EXISTS schedule_staff (
                id SERIAL PRIMARY KEY,
                schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
                staff_id INTEGER NOT NULL REFERENCES project_staff(id) ON DELETE CASCADE,
                start_session_id INTEGER,
                end_session_id INTEGER,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP,
                UNIQUE(schedule_id, staff_id)
            );
            
            CREATE TABLE IF NOT EXISTS attendance (
                id SERIAL PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                staff_id INTEGER NOT NULL REFERENCES project_staff(id) ON DELETE CASCADE,
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
                updated_at TIMESTAMP,
                UNIQUE(project_id, session_id, staff_id)
            );
            
            CREATE TABLE IF NOT EXISTS shortages (
                id SERIAL PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                unit TEXT NOT NULL,
                section TEXT NOT NULL,
                count INTEGER DEFAULT 1,
                target_group TEXT,
                description TEXT,
                status TEXT DEFAULT 'تامین نشده',
                assigned_name TEXT,
                phone TEXT,
                is_notified INTEGER DEFAULT 0,
                requested_by BIGINT,
                _excel_row INTEGER,
                created_at TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS staff_logs (
                id SERIAL PRIMARY KEY,
                project_id INTEGER,
                user_id BIGINT NOT NULL,
                action_type TEXT NOT NULL,
                log_date TEXT NOT NULL,
                created_at TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS user_context (
                user_id BIGINT PRIMARY KEY,
                current_project_id INTEGER,
                current_session_id INTEGER,
                updated_at TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS operator_daily_absence (
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                absent_date TEXT NOT NULL,
                created_at TIMESTAMP,
                PRIMARY KEY(project_id, user_id, absent_date)
            );
            """)

            # ساخت ایندکس‌های PostgreSQL (استفاده از IF NOT EXISTS)
            indexes = [
                ("idx_staff_project_active", "project_staff (project_id, is_active)"),
                ("idx_staff_project_phone", "project_staff (project_id, phone)"),
                ("idx_schedule_staff_lookup", "schedule_staff (schedule_id, staff_id, is_active)"),
                ("idx_attendance_lookup", "attendance (project_id, session_id, staff_id)"),
                ("idx_sessions_project", "sessions (project_id)"),
                ("idx_shortages_project", "shortages (project_id, status)"),
                ("idx_project_users_uid", "project_users (user_id)"),
                ("idx_staff_logs_date", "staff_logs (log_date, project_id)")
            ]
            for idx_name, idx_def in indexes:
                cursor.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_def}")

            # ثبت ادمین‌های سراسری پیش‌فرض
            now_iso = datetime.now()
            for uid in SUPER_ADMINS:
                cursor.execute("""
                INSERT INTO users (user_id, staff_name, gender, is_global_super_admin, is_active, created_at)
                VALUES (%s, %s, 'نامشخص', TRUE, TRUE, %s)
                ON CONFLICT(user_id) DO UPDATE SET
                    is_global_super_admin = TRUE,
                    is_active = TRUE
                """, (uid, f"مدیر ارشد {uid}", now_iso))

            conn.commit()
        except Exception as e:
            conn.rollback()
            logging.critical(f"Database setup failed: {e}")
            raise
        finally:
            conn.close()

db_instance = DatabaseManager()