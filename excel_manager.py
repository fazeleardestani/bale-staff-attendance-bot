import os
import sys
import logging
import tempfile
import shutil
import re
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

from config import PROJECT_FILES_DIR, EXCEL_BACKUPS_DIR, BASE_PATH
from db_manager import db_instance
from project_manager import project_manager
from staff_manager import staff_manager
from utils import normalize_persian, get_iran_now, normalize_date_to_jalali


def _clean_cell_str(val):
    if val is None:
        return ""
    s = str(val).strip()
    if s in ("None", "nan", "NaN", "NULL", "null"):
        return ""
    # تبدیل ی و ک عربی به فارسی استاندارد ولی حفظ فاصله‌های بین کلمات
    replacements = {
        'ي': 'ی',
        'ى': 'ی',
        'ك': 'ک',
        '\u200c': ' ',
        '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
        '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9',
        '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
        '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    return re.sub(r'\s+', ' ', s).strip()


def _norm_key(val):
    """کلید نرمال‌شده بدون فاصله و بدون تفاوت ی/ئ/آ/ا برای مقایسه عدم تکرار"""
    if not val:
        return ""
    return normalize_persian(str(val)).replace(" ", "")


class ExcelManager:
    def __init__(self, db=db_instance):
        self.db = db

    def get_project_excel_path(self, project_id):
        proj = project_manager.get_project(project_id)
        proj_dir = os.path.join(PROJECT_FILES_DIR, f"project_{project_id}")
        os.makedirs(proj_dir, exist_ok=True)
        default_target = os.path.join(proj_dir, "project_data.xlsx")

        if proj and proj.get('excel_path') and os.path.exists(proj['excel_path']):
            return proj['excel_path']

        default_template = os.path.join(BASE_PATH, "قالب اکسل پروژه.xlsx")
        if not os.path.exists(default_target) and os.path.exists(default_template):
            shutil.copy2(default_template, default_target)
            project_manager.update_project_excel_path(project_id, default_target)

        return default_target

    def backup_project_excel(self, project_id, label="auto"):
        excel_path = self.get_project_excel_path(project_id)
        if os.path.exists(excel_path):
            timestamp = get_iran_now().strftime("%Y-%m-%d_%H-%M-%S")
            category_dir = os.path.join(EXCEL_BACKUPS_DIR, label)
            os.makedirs(category_dir, exist_ok=True)
            backup_name = f"project_{project_id}_backup_{timestamp}.xlsx"
            target_path = os.path.join(category_dir, backup_name)
            shutil.copy2(excel_path, target_path)

            try:
                prefix = f"project_{project_id}_backup_"
                files = sorted(
                    [os.path.join(category_dir, f) for f in os.listdir(category_dir) if f.startswith(prefix) and f.endswith(".xlsx")],
                    key=os.path.getmtime
                )
                if len(files) > 15:
                    for old_f in files[:-15]:
                        try:
                            os.remove(old_f)
                        except OSError:
                            pass
            except Exception as ret_err:
                logging.warning(f"Excel backup retention cleanup notice: {ret_err}")

            return target_path
        return None

    def add_session_sheet(self, project_id, session_name):
        excel_path = self.get_project_excel_path(project_id)
        if not os.path.exists(excel_path):
            return False

        wb = openpyxl.load_workbook(excel_path, data_only=False)
        try:
            if session_name in wb.sheetnames:
                return True

            exclude_sheets = ['تامین نیرو', 'چارت', 'داشبورد']
            candidate_sheets = [s for s in wb.sheetnames if s not in exclude_sheets]

            if candidate_sheets:
                source_sheet_name = candidate_sheets[-1]
                source_ws = wb[source_sheet_name]
                new_ws = wb.copy_worksheet(source_ws)
                new_ws.title = session_name
                new_ws.sheet_view.rightToLeft = True

                for col_letter, col_dim in source_ws.column_dimensions.items():
                    new_ws.column_dimensions[col_letter].width = col_dim.width

                for row_idx, row_dim in source_ws.row_dimensions.items():
                    new_ws.row_dimensions[row_idx].height = row_dim.height

                header_map = {
                    _clean_cell_str(new_ws.cell(row=1, column=c).value): c
                    for c in range(1, 100) if new_ws.cell(row=1, column=c).value
                }
                clear_cols = ["وضعیت حضور", "وضعیت کارت", "پیگیری تاخیر", "توضیحات"]
                col_indices_to_clear = [header_map[col] for col in clear_cols if col in header_map]

                for r_idx in range(2, new_ws.max_row + 1):
                    for c_idx in col_indices_to_clear:
                        new_ws.cell(row=r_idx, column=c_idx).value = ""
            else:
                new_ws = wb.create_sheet(title=session_name)
                new_ws.sheet_view.rightToLeft = True

                headers = [
                    "ردیف", "واحد", "بخش", "نام و نام خانوادگی", "سمت",
                    "عنوان کارت", "شماره تماس", "ساعت حضور", "پیگیری تاخیر",
                    "توضیحات", "وضعیت کارت", "وضعیت حضور", "جنسیت", "فعال در چند بخش؟"
                ]
                new_ws.append(headers)

                header_font = Font(bold=True)
                header_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
                header_align = Alignment(horizontal="center", vertical="center")

                for col_idx, cell in enumerate(new_ws[1], start=1):
                    cell.font = header_font
                    cell.fill = header_fill
                    cell.alignment = header_align
                    col_letter = get_column_letter(col_idx)
                    new_ws.column_dimensions[col_letter].width = 18

            wb.save(excel_path)
            return True
        finally:
            wb.close()

    def deduplicate_project_staff_and_attendance(self, project_id, cursor):
        """
        پاکسازی هوشمند نیروهای تکراری که در آپلودهای قبلی در یک پروژه ساخته شده‌اند
        و ادغام سوابق حضور و غیاب آن‌ها در رکورد اصلی.
        """
        cursor.execute("""
        SELECT id, name, unit, section, phone, shift_time, gender, position, card_title
        FROM project_staff
        WHERE project_id = %s
        ORDER BY id ASC
        """, (project_id,))
        all_staff = cursor.fetchall()

        seen_keys = {}
        duplicates_map = {}  # dup_id -> primary_id

        for s in all_staff:
            # کلید یکتا بر اساس نام نرمال‌شده + واحد نرمال‌شده + بخش نرمال‌شده
            sec_clean = _clean_cell_str(s['section'])
            if sec_clean == '-':
                sec_clean = ''
            key_full = (_norm_key(s['name']), _norm_key(s['unit']), _norm_key(sec_clean))
            key_no_sec = (_norm_key(s['name']), _norm_key(s['unit']), '')

            primary_id = None
            if key_full in seen_keys:
                primary_id = seen_keys[key_full]
            elif sec_clean and key_no_sec in seen_keys:
                # اگر قبلاً همین فرد در همین واحد با بخش خالی ثبت شده بود، همان رکورد قبلی را با بخش جدید ارتقا بده
                primary_id = seen_keys[key_no_sec]
                seen_keys[key_full] = primary_id
                cursor.execute("""
                UPDATE project_staff
                SET section = %s,
                    card_title = COALESCE(NULLIF(%s, ''), card_title)
                WHERE id = %s
                """, (sec_clean, _clean_cell_str(s['card_title']) or sec_clean, primary_id))
            elif not sec_clean:
                # بررسی اینکه آیا این فرد قبلاً با بخش مشخص در همین واحد ثبت شده است
                for (kn, ku, ks), pid_existing in seen_keys.items():
                    if kn == _norm_key(s['name']) and ku == _norm_key(s['unit']) and ks != '':
                        primary_id = pid_existing
                        break

            if primary_id is not None and primary_id != s['id']:
                duplicates_map[s['id']] = primary_id
            else:
                seen_keys[key_full] = s['id']
                if not sec_clean:
                    seen_keys[key_no_sec] = s['id']

        for dup_id, prim_id in duplicates_map.items():
            # انتقال وضعیت حضور و غیاب از رکورد تکراری به رکورد اصلی (در صورتی که رکورد اصلی خالی باشد)
            cursor.execute("""
            SELECT session_id, status, card_status, late_tracking, description,
                   section_snapshot, phone_snapshot, shift_time_snapshot, gender_snapshot
            FROM attendance
            WHERE project_id = %s AND staff_id = %s
            """, (project_id, dup_id))
            dup_atts = cursor.fetchall()
            for da in dup_atts:
                cursor.execute("""
                UPDATE attendance
                SET status = CASE WHEN (status IS NULL OR status = '') AND %s != '' THEN %s ELSE status END,
                    card_status = CASE WHEN (card_status IS NULL OR card_status = '') AND %s != '' THEN %s ELSE card_status END,
                    late_tracking = CASE WHEN (late_tracking IS NULL OR late_tracking = '') AND %s != '' THEN %s ELSE late_tracking END,
                    description = CASE WHEN (description IS NULL OR description = '') AND %s != '' THEN %s ELSE description END,
                    section_snapshot = CASE WHEN (section_snapshot IS NULL OR section_snapshot IN ('', '-')) AND %s NOT IN ('', '-') THEN %s ELSE section_snapshot END
                WHERE project_id = %s AND session_id = %s AND staff_id = %s
                """, (
                    da['status'], da['status'],
                    da['card_status'], da['card_status'],
                    da['late_tracking'], da['late_tracking'],
                    da['description'], da['description'],
                    da['section_snapshot'] or '', da['section_snapshot'] or '',
                    project_id, da['session_id'], prim_id
                ))
            cursor.execute("DELETE FROM attendance WHERE project_id = %s AND staff_id = %s", (project_id, dup_id))
            cursor.execute("DELETE FROM schedule_staff WHERE staff_id = %s", (dup_id,))
            cursor.execute("DELETE FROM project_staff WHERE id = %s", (dup_id,))

    def import_project_excel(self, project_id, uploaded_file_path):
        if uploaded_file_path.lower().endswith('.xls') and not uploaded_file_path.lower().endswith('.xlsx'):
            raise ValueError("فرمت .xls قدیمی پشتیبانی نمی‌شود. لطفاً فایل اکسل را با فرمت مدرن .xlsx ارسال فرمایید.")

        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as staging_file:
            staging_path = staging_file.name

        try:
            shutil.copy2(uploaded_file_path, staging_path)
            wb = openpyxl.load_workbook(staging_path, data_only=True)
            if len(wb.sheetnames) == 0:
                wb.close()
                raise ValueError("فایل اکسل ارسالی فاقد هرگونه برگه (Sheet) می‌باشد.")
        except Exception as e:
            if os.path.exists(staging_path):
                os.remove(staging_path)
            raise ValueError(f"فایل اکسل نامعتبر یا غیرقابل خواندن است: {e}")

        exclude_sheets = ['تامین نیرو', 'چارت', 'داشبورد']
        valid_sheets = [s for s in wb.sheetnames if _clean_cell_str(s) not in exclude_sheets]

        db_backup_path = db_instance.backup_database(label="pre_update")
        if not db_backup_path or not db_instance.verify_backup_integrity(db_backup_path):
            wb.close()
            if os.path.exists(staging_path):
                os.remove(staging_path)
            raise RuntimeError("امکان ایجاد نسخه پشتیبان معتبر از پایگاه داده قبل از شروع عملیات وجود ندارد.")

        proj_excel_path = self.get_project_excel_path(project_id)
        excel_backup_path = None
        if os.path.exists(proj_excel_path):
            excel_backup_path = self.backup_project_excel(project_id, label="pre_update")

        conn = self.db.get_connection()
        now_iso = get_iran_now()

        try:
            cursor = conn.cursor()
            with conn:
                # ۱. ابتدا پاکسازی هرگونه رکورد تکراری قدیمی در این پروژه
                self.deduplicate_project_staff_and_attendance(project_id, cursor)

                if "چارت" in wb.sheetnames:
                    ws_chart = wb["چارت"]
                    pairs = []
                    for r in range(2, ws_chart.max_row + 1):
                        u = _clean_cell_str(ws_chart.cell(row=r, column=1).value)
                        s = _clean_cell_str(ws_chart.cell(row=r, column=2).value)
                        if u:
                            pairs.append((u, s, r - 1))
                    project_manager.set_project_org_chart(project_id, pairs, cursor=cursor)

                if "تامین نیرو" in wb.sheetnames:
                    ws_sh = wb["تامین نیرو"]
                    cursor.execute("DELETE FROM shortages WHERE project_id = %s AND status = 'تامین نشده'", (project_id,))
                    for r in range(2, ws_sh.max_row + 1):
                        u = _clean_cell_str(ws_sh.cell(row=r, column=2).value)
                        s = _clean_cell_str(ws_sh.cell(row=r, column=3).value)
                        st = _clean_cell_str(ws_sh.cell(row=r, column=4).value)
                        assigned_name = _clean_cell_str(ws_sh.cell(row=r, column=5).value)
                        phone = _clean_cell_str(ws_sh.cell(row=r, column=6).value)
                        desc = _clean_cell_str(ws_sh.cell(row=r, column=7).value)
                        target_grp = _clean_cell_str(ws_sh.cell(row=r, column=8).value)
                        if u:
                            cursor.execute("""
                            INSERT INTO shortages (project_id, unit, section, count, target_group, description, status, assigned_name, phone, _excel_row, is_notified, created_at)
                            VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s, %s, 0, %s)
                            """, (project_id, u, s, target_grp, desc, st or 'تامین نشده', assigned_name, phone, r, now_iso))

                for sheet_name in valid_sheets:
                    clean_sheet_name = _clean_cell_str(sheet_name)

                    sched_id = None
                    tag_match = re.search(r'\[(?:ID|SCHED)[-:]\s*(\d+)\]', clean_sheet_name, re.IGNORECASE)
                    if tag_match:
                        cand_id = int(tag_match.group(1))
                        cursor.execute("SELECT id FROM schedules WHERE id = %s AND project_id = %s AND is_active = TRUE", (cand_id, project_id))
                        row = cursor.fetchone()
                        if row:
                            sched_id = cand_id

                    if sched_id is None:
                        cursor.execute("SELECT id FROM schedules WHERE project_id = %s AND name = %s AND is_active = TRUE", (project_id, clean_sheet_name))
                        row = cursor.fetchone()
                        if row:
                            sched_id = row['id']

                    session_id = None
                    if sched_id is None:
                        cursor.execute("""
                        SELECT s.id, s.schedule_id 
                        FROM sessions s
                        LEFT JOIN schedules sc ON s.schedule_id = sc.id
                        WHERE s.project_id = %s AND s.name = %s AND s.status != 'CANCELLED'
                          AND (s.schedule_id IS NULL OR sc.is_active = TRUE)
                        """, (project_id, clean_sheet_name))
                        s_row = cursor.fetchone()
                        if s_row:
                            sched_id = s_row['schedule_id']
                            session_id = s_row['id']

                    cursor.execute("SELECT type FROM projects WHERE id = %s", (project_id,))
                    p_type_row = cursor.fetchone()
                    if p_type_row and p_type_row['type'] == 'کلاس' and sched_id is None:
                        cursor.execute("SELECT id FROM schedules WHERE project_id = %s AND is_active = TRUE ORDER BY id ASC LIMIT 1", (project_id,))
                        fallback_sc = cursor.fetchone()
                        if fallback_sc:
                            sched_id = fallback_sc['id']

                    if session_id is None:
                        if sched_id is not None:
                            cursor.execute("""
                            SELECT id, schedule_id FROM sessions
                            WHERE project_id = %s AND name = %s AND schedule_id = %s
                            """, (project_id, clean_sheet_name, sched_id))
                        else:
                            cursor.execute("""
                            SELECT id, schedule_id FROM sessions
                            WHERE project_id = %s AND name = %s
                            """, (project_id, clean_sheet_name))
                        sess_row = cursor.fetchone()
                        if sess_row:
                            session_id = sess_row['id']
                            if sched_id is None:
                                sched_id = sess_row.get('schedule_id')
                        else:
                            cursor.execute("""
                            INSERT INTO sessions (project_id, schedule_id, name, session_date, time_str, day_of_week, status, created_at)
                            VALUES (%s, %s, %s, '', '', '', 'SCHEDULED', %s)
                            RETURNING id;
                            """, (project_id, sched_id, clean_sheet_name, now_iso.isoformat()))
                            session_id = cursor.fetchone()['id']

                    ws = wb[sheet_name]
                    # نقشه هوشمند سرستون‌ها با نرمال‌سازی حروف فارسی/عربی و فاصله‌ها
                    header_map = {}
                    norm_header_map = {}
                    for c in range(1, 100):
                        val = ws.cell(row=1, column=c).value
                        if val is not None:
                            clean_h = _clean_cell_str(val)
                            if clean_h:
                                header_map[clean_h] = c
                                norm_header_map[_norm_key(clean_h)] = c

                    def get_col_idx(*possible_names):
                        for pname in possible_names:
                            if pname in header_map:
                                return header_map[pname]
                            nk = _norm_key(pname)
                            if nk in norm_header_map:
                                return norm_header_map[nk]
                        # جستجوی زیررشته‌ای برای ستون بخش یا نام
                        for pname in possible_names:
                            nk = _norm_key(pname)
                            for hk, col_i in norm_header_map.items():
                                if nk and nk in hk:
                                    return col_i
                        return None

                    col_name = get_col_idx("نام و نام خانوادگی", "نام و نام خانوادگي", "نام", "نام نیرو")
                    col_code = get_col_idx("کد پرسنلی", "کد نیرو", "شناسه نیرو")
                    col_unit = get_col_idx("واحد", "نام واحد")
                    col_sec = get_col_idx("بخش", "زیرواحد", "بخش / زیرواحد")
                    col_pos = get_col_idx("سمت", "مسئولیت", "نقش")
                    col_card_title = get_col_idx("عنوان کارت", "کارت")
                    col_phone = get_col_idx("شماره تماس", "موبایل", "تلفن", "شماره همراه")
                    col_shift = get_col_idx("ساعت حضور", "ساعت شیفت", "شیفت")
                    col_late = get_col_idx("پیگیری تاخیر", "پیگیری")
                    col_desc = get_col_idx("توضیحات")
                    col_card_st = get_col_idx("وضعیت کارت")
                    col_att_st = get_col_idx("وضعیت حضور", "حضور و غیاب")
                    col_gender = get_col_idx("جنسیت")
                    col_multi = get_col_idx("فعال در چند بخش؟", "فعال در چند بخش")

                    sheet_staff_ids = set()

                    # بارگذاری لیست فعلی کارکنان پروژه برای تطبیق هوشمند بدون کوئری اضافی
                    cursor.execute("SELECT id, staff_code, name, unit, section, phone FROM project_staff WHERE project_id = %s ORDER BY id ASC", (project_id,))
                    existing_project_staff = [dict(r) for r in cursor.fetchall()]

                    for r_idx in range(2, ws.max_row + 1):
                        def read_cell(c_idx):
                            if not c_idx:
                                return ""
                            return _clean_cell_str(ws.cell(row=r_idx, column=c_idx).value)

                        name = read_cell(col_name)
                        if not name:
                            continue

                        staff_code = read_cell(col_code)
                        unit = read_cell(col_unit)
                        section = read_cell(col_sec)

                        if section in ('None', 'nan', '-'):
                            section = ''

                        pos = read_cell(col_pos) or "نیرو"
                        card_title = read_cell(col_card_title) or section or unit
                        phone = read_cell(col_phone)
                        shift_time = read_cell(col_shift)
                        late_trk = read_cell(col_late)
                        desc = read_cell(col_desc)
                        card_st = read_cell(col_card_st)
                        att_st = read_cell(col_att_st)
                        gender = read_cell(col_gender)
                        is_multi = read_cell(col_multi) or "خیر"

                        # تطبیق هوشمند برای جلوگیری از ایجاد رکورد تکراری
                        matched_staff_id = None
                        norm_name = _norm_key(name)
                        norm_unit = _norm_key(unit)
                        norm_sec = _norm_key(section)

                        if staff_code:
                            for ex in existing_project_staff:
                                if ex.get('staff_code') and _norm_key(ex['staff_code']) == _norm_key(staff_code):
                                    matched_staff_id = ex['id']
                                    break

                        # ۱. تطبیق دقیق بر اساس (نام نرمال‌شده + واحد نرمال‌شده + بخش نرمال‌شده)
                        if not matched_staff_id:
                            for ex in existing_project_staff:
                                if (_norm_key(ex['name']) == norm_name and
                                        _norm_key(ex['unit']) == norm_unit and
                                        _norm_key(ex['section']) == norm_sec):
                                    matched_staff_id = ex['id']
                                    break

                        # ۲. اگر در همین واحد تنها یک نفر با همین نام (یا با بخش خالی از آپلودهای قبلی) وجود دارد، همان را آپدیت کن
                        if not matched_staff_id:
                            same_name_unit = [
                                ex for ex in existing_project_staff
                                if _norm_key(ex['name']) == norm_name and _norm_key(ex['unit']) == norm_unit
                            ]
                            if len(same_name_unit) == 1:
                                ex_cand = same_name_unit[0]
                                # اگر رکورد قبلی بخش نداشت یا در این شیت هنوز استفاده نشده، همان رکورد قبلی است
                                if not _norm_key(ex_cand.get('section')) or ex_cand['id'] not in sheet_staff_ids:
                                    matched_staff_id = ex_cand['id']

                        if matched_staff_id:
                            staff_id = matched_staff_id
                            cursor.execute("""
                            UPDATE project_staff SET
                                name = %s,
                                unit = %s,
                                section = %s,
                                phone = COALESCE(NULLIF(%s, ''), phone),
                                shift_time = COALESCE(NULLIF(%s, ''), shift_time),
                                gender = COALESCE(NULLIF(%s, ''), gender),
                                position = COALESCE(NULLIF(%s, ''), position),
                                card_title = COALESCE(NULLIF(%s, ''), card_title),
                                is_multi_section = COALESCE(NULLIF(%s, ''), is_multi_section),
                                is_active = TRUE,
                                _excel_row = %s
                            WHERE id = %s
                            """, (name, unit, section, phone, shift_time, gender, pos, card_title, is_multi, r_idx, staff_id))
                            # آپدیت کش محلی
                            for ex in existing_project_staff:
                                if ex['id'] == staff_id:
                                    ex['name'] = name
                                    ex['unit'] = unit
                                    ex['section'] = section
                                    break
                        else:
                            cursor.execute("""
                            INSERT INTO project_staff (project_id, staff_code, name, phone, unit, section, position, card_title, shift_time, gender, notes, is_multi_section, is_active, _excel_row)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)
                            RETURNING id;
                            """, (project_id, staff_code, name, phone, unit, section, pos, card_title, shift_time, gender, desc, is_multi, r_idx))
                            staff_id = cursor.fetchone()['id']
                            if not staff_code:
                                auto_code = f"STF-{staff_id:05d}"
                                cursor.execute("UPDATE project_staff SET staff_code = %s WHERE id = %s", (auto_code, staff_id))
                                staff_code = auto_code
                            existing_project_staff.append({
                                'id': staff_id, 'staff_code': staff_code,
                                'name': name, 'unit': unit, 'section': section, 'phone': phone
                            })

                        sheet_staff_ids.add(staff_id)

                        if sched_id is not None:
                            cursor.execute("""
                            INSERT INTO schedule_staff (schedule_id, staff_id, start_session_id, is_active, created_at)
                            VALUES (%s, %s, %s, TRUE, %s)
                            ON CONFLICT(schedule_id, staff_id) DO UPDATE SET
                                start_session_id = COALESCE(schedule_staff.start_session_id, EXCLUDED.start_session_id),
                                end_session_id = NULL,
                                is_active = TRUE
                            """, (sched_id, staff_id, session_id, now_iso))

                        # درج یا آپدیت کامل اطلاعات اسنپ‌شات (شامل بخش، سمت، عنوان کارت، ساعت حضور و ...)
                        cursor.execute("""
                        INSERT INTO attendance (
                            project_id, session_id, staff_id, status, card_status, late_tracking, description,
                            staff_name_snapshot, unit_snapshot, section_snapshot, position_snapshot, gender_snapshot,
                            phone_snapshot, card_title_snapshot, shift_time_snapshot, is_multi_section_snapshot, staff_code_snapshot,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT(project_id, session_id, staff_id) DO UPDATE SET
                            status = CASE WHEN EXCLUDED.status != '' THEN EXCLUDED.status ELSE attendance.status END,
                            card_status = CASE WHEN EXCLUDED.card_status != '' THEN EXCLUDED.card_status ELSE attendance.card_status END,
                            late_tracking = CASE WHEN EXCLUDED.late_tracking != '' THEN EXCLUDED.late_tracking ELSE attendance.late_tracking END,
                            description = CASE WHEN EXCLUDED.description != '' THEN EXCLUDED.description ELSE attendance.description END,
                            staff_name_snapshot = EXCLUDED.staff_name_snapshot,
                            unit_snapshot = EXCLUDED.unit_snapshot,
                            section_snapshot = EXCLUDED.section_snapshot,
                            position_snapshot = EXCLUDED.position_snapshot,
                            gender_snapshot = COALESCE(NULLIF(EXCLUDED.gender_snapshot, ''), attendance.gender_snapshot),
                            phone_snapshot = COALESCE(NULLIF(EXCLUDED.phone_snapshot, ''), attendance.phone_snapshot),
                            card_title_snapshot = COALESCE(NULLIF(EXCLUDED.card_title_snapshot, ''), attendance.card_title_snapshot),
                            shift_time_snapshot = COALESCE(NULLIF(EXCLUDED.shift_time_snapshot, ''), attendance.shift_time_snapshot),
                            is_multi_section_snapshot = COALESCE(NULLIF(EXCLUDED.is_multi_section_snapshot, ''), attendance.is_multi_section_snapshot),
                            staff_code_snapshot = COALESCE(NULLIF(EXCLUDED.staff_code_snapshot, ''), attendance.staff_code_snapshot),
                            updated_at = EXCLUDED.updated_at
                        """, (
                            project_id, session_id, staff_id, att_st, card_st, late_trk, desc,
                            name, unit, section, pos, gender,
                            phone, card_title, shift_time, is_multi, staff_code,
                            now_iso
                        ))

                    # حذف رکوردهای اضافی/تکراری در جلسه که در فایل اکسل آپلود شده وجود ندارند
                    if sheet_staff_ids:
                        placeholders = ','.join(['%s'] * len(sheet_staff_ids))
                        cursor.execute(f"""
                        DELETE FROM attendance
                        WHERE project_id = %s AND session_id = %s
                          AND staff_id NOT IN ({placeholders})
                          AND (status IS NULL OR status = '')
                        """, [project_id, session_id] + list(sheet_staff_ids))

                        if sched_id is not None:
                            cursor.execute(f"""
                            UPDATE schedule_staff 
                            SET is_active = FALSE, end_session_id = %s
                            WHERE schedule_id = %s AND is_active = TRUE AND staff_id NOT IN ({placeholders})
                            """, [session_id, sched_id] + list(sheet_staff_ids))

                # پاکسازی نهایی هرگونه نیروی تکراری احتمالی
                self.deduplicate_project_staff_and_attendance(project_id, cursor)

        except Exception as err:
            logging.error(f"Excel import DB transaction failed and rolled back: {err}")
            if os.path.exists(staging_path):
                os.remove(staging_path)
            raise err
        finally:
            conn.close()
            wb.close()

        try:
            shutil.copy2(staging_path, proj_excel_path)
            chk_wb = openpyxl.load_workbook(proj_excel_path, data_only=True)
            chk_wb.close()
            project_manager.update_project_excel_path(project_id, proj_excel_path)
        except Exception as fs_err:
            logging.critical(f"Failed to copy or verify staging Excel at destination: {fs_err}. Starting recovery.")
            if db_backup_path and os.path.exists(db_backup_path):
                db_instance.restore_backup(db_backup_path)
            if excel_backup_path and os.path.exists(excel_backup_path):
                shutil.copy2(excel_backup_path, proj_excel_path)
            elif os.path.exists(proj_excel_path):
                os.remove(proj_excel_path)
            raise RuntimeError(f"خطا در جایگزینی و اعتبارسنجی فایل اکسل. کلیه تغییرات دیتابیس و فایل به وضعیت قبل بازگردانی شدند: {fs_err}")
        finally:
            if os.path.exists(staging_path):
                os.remove(staging_path)

        return True

    def export_project_excel(self, project_id, output_path=None):
        from attendance_manager import attendance_manager

        excel_path = self.get_project_excel_path(project_id)
        if not os.path.exists(excel_path):
            return None
        out_path = output_path or excel_path
        wb = openpyxl.load_workbook(excel_path, data_only=False)

        try:
            sessions = attendance_manager.list_sessions(project_id, include_cancelled=False)
            for sess in sessions:
                sheet_name = sess['name']
                if sheet_name not in wb.sheetnames:
                    wb.close()
                    self.add_session_sheet(project_id, sheet_name)
                    wb = openpyxl.load_workbook(excel_path, data_only=False)
                    if sheet_name not in wb.sheetnames:
                        continue

                ws = wb[sheet_name]
                header_map = {_clean_cell_str(ws.cell(row=1, column=c).value): c for c in range(1, 100) if ws.cell(row=1, column=c).value}
                att_records = attendance_manager.get_session_attendance(project_id, sess['id'])

                row_lookup = {}
                name_col = header_map.get("نام و نام خانوادگی")
                unit_col = header_map.get("واحد")
                sec_col = header_map.get("بخش")
                if name_col:
                    for ri in range(2, ws.max_row + 1):
                        n_val = _norm_key(ws.cell(row=ri, column=name_col).value)
                        u_val = _norm_key(ws.cell(row=ri, column=unit_col).value) if unit_col else ""
                        s_val = _norm_key(ws.cell(row=ri, column=sec_col).value) if sec_col else ""
                        if n_val:
                            row_lookup[(n_val, u_val, s_val)] = ri
                            if (n_val, u_val, "") not in row_lookup:
                                row_lookup[(n_val, u_val, "")] = ri

                for rec in att_records:
                    rec_name = _norm_key(rec.get("name"))
                    rec_unit = _norm_key(rec.get("unit"))
                    raw_sec = str(rec.get("section") or "").strip()
                    if raw_sec == "-":
                        raw_sec = ""
                    rec_section = _norm_key(raw_sec)

                    r_idx = row_lookup.get((rec_name, rec_unit, rec_section)) or row_lookup.get((rec_name, rec_unit, ""))
                    if not r_idx:
                        r_idx = ws.max_row + 1
                        row_lookup[(rec_name, rec_unit, rec_section)] = r_idx

                    if "نام و نام خانوادگی" in header_map: ws.cell(row=r_idx, column=header_map["نام و نام خانوادگی"]).value = rec.get("name")
                    if "کد پرسنلی" in header_map: ws.cell(row=r_idx, column=header_map["کد پرسنلی"]).value = rec.get("staff_code")
                    if "واحد" in header_map: ws.cell(row=r_idx, column=header_map["واحد"]).value = rec.get("unit")
                    if "بخش" in header_map: ws.cell(row=r_idx, column=header_map["بخش"]).value = raw_sec
                    if "سمت" in header_map: ws.cell(row=r_idx, column=header_map["سمت"]).value = rec.get("position")
                    if "عنوان کارت" in header_map: ws.cell(row=r_idx, column=header_map["عنوان کارت"]).value = rec.get("card_title")
                    if "شماره تماس" in header_map: ws.cell(row=r_idx, column=header_map["شماره تماس"]).value = rec.get("phone")
                    if "ساعت حضور" in header_map: ws.cell(row=r_idx, column=header_map["ساعت حضور"]).value = rec.get("shift_time")
                    if "پیگیری تاخیر" in header_map: ws.cell(row=r_idx, column=header_map["پیگیری تاخیر"]).value = rec.get("late_tracking")
                    if "توضیحات" in header_map: ws.cell(row=r_idx, column=header_map["توضیحات"]).value = rec.get("description")
                    if "وضعیت کارت" in header_map: ws.cell(row=r_idx, column=header_map["وضعیت کارت"]).value = rec.get("card_status")
                    if "وضعیت حضور" in header_map: ws.cell(row=r_idx, column=header_map["وضعیت حضور"]).value = rec.get("status")
                    if "جنسیت" in header_map: ws.cell(row=r_idx, column=header_map["جنسیت"]).value = rec.get("gender")
                    if "فعال در چند بخش؟" in header_map: ws.cell(row=r_idx, column=header_map["فعال در چند بخش؟"]).value = rec.get("is_multi_section")

            if "تامین نیرو" in wb.sheetnames:
                ws_sh = wb["تامین نیرو"]
                conn = self.db.get_connection()
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT * FROM shortages WHERE project_id = %s ORDER BY id ASC", (project_id,))
                    shortages = cursor.fetchall()
                finally:
                    conn.close()
                for idx, sh in enumerate(shortages, start=2):
                    ws_sh.cell(row=idx, column=1).value = idx - 1
                    ws_sh.cell(row=idx, column=2).value = sh['unit']
                    ws_sh.cell(row=idx, column=3).value = sh['section']
                    ws_sh.cell(row=idx, column=4).value = sh['status']
                    ws_sh.cell(row=idx, column=5).value = sh['assigned_name']
                    ws_sh.cell(row=idx, column=6).value = sh['phone']
                    ws_sh.cell(row=idx, column=7).value = sh['description']
                    ws_sh.cell(row=idx, column=8).value = sh['target_group']

            wb.save(out_path)
            return out_path
        finally:
            wb.close()

    def cleanup_sample_sheets(self, project_id, active_session_names):
        excel_path = self.get_project_excel_path(project_id)
        if not os.path.exists(excel_path):
            return False

        try:
            wb = openpyxl.load_workbook(excel_path)
            sample_sheets = ['روز آماده سازی', 'روز 1', 'روز 2']
            for s in sample_sheets:
                if s in wb.sheetnames and s not in active_session_names:
                    if len(wb.sheetnames) > 1:
                        del wb[s]
            wb.save(excel_path)
            wb.close()
            return True
        except Exception:
            return False


excel_manager = ExcelManager()