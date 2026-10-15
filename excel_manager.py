import os
import sys
import importlib.abc
import importlib.util
import logging
import tempfile
import shutil
import re
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from copy import copy
from datetime import datetime

_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

class _DirectFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        name = fullname.split('.')[-1]
        for p in (path or sys.path):
            fpath = os.path.join(p or os.getcwd(), name + '.py')
            if os.path.isfile(fpath):
                return importlib.util.spec_from_file_location(fullname, fpath)
        return None

if not any(isinstance(f, _DirectFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _DirectFinder())

from config import PROJECT_FILES_DIR, EXCEL_BACKUPS_DIR, BASE_PATH
from db_manager import db_instance
from project_manager import project_manager
from staff_manager import staff_manager

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
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
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
        if session_name in wb.sheetnames:
            wb.close()
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
                str(new_ws.cell(row=1, column=c).value).strip(): c
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
            
            headers = ["ردیف", "واحد", "بخش", "نام و نام خانوادگی", "سمت", "عنوان کارت", "شماره تماس", "ساعت حضور", "پیگیری تاخیر", "توضیحات", "وضعیت کارت", "وضعیت حضور", "جنسیت", "فعال در چند بخش؟"]
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
        wb.close()
        return True

    def import_project_excel(self, project_id, uploaded_file_path):
        if uploaded_file_path.lower().endswith('.xls') and not uploaded_file_path.lower().endswith('.xlsx'):
            raise ValueError("فرمت .xls قدیمی پشتیبانی نمی‌شود. لطفاً فایل اکسل را با فرمت مدرن .xlsx ارسال فرمایید.")

        from attendance_manager import attendance_manager

        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as staging_file:
            staging_path = staging_file.name

        try:
            shutil.copy2(uploaded_file_path, staging_path)
            wb = openpyxl.load_workbook(staging_path, data_only=True)
            if len(wb.sheetnames) == 0:
                raise ValueError("فایل اکسل ارسالی فاقد هرگونه برگه (Sheet) می‌باشد.")
        except Exception as e:
            if os.path.exists(staging_path):
                os.remove(staging_path)
            raise ValueError(f"فایل اکسل نامعتبر یا غیرقابل خواندن است: {e}")

        exclude_sheets = ['تامین نیرو', 'چارت', 'داشبورد']
        valid_sheets = [s for s in wb.sheetnames if s not in exclude_sheets]

        db_backup_path = db_instance.backup_database(label="pre_update")
        if not db_backup_path or not db_instance.verify_backup_integrity(db_backup_path):
            if os.path.exists(staging_path):
                os.remove(staging_path)
            raise RuntimeError("امکان ایجاد نسخه پشتیبان معتبر از پایگاه داده قبل از شروع عملیات وجود ندارد.")

        proj_excel_path = self.get_project_excel_path(project_id)
        excel_backup_path = None
        if os.path.exists(proj_excel_path):
            excel_backup_path = self.backup_project_excel(project_id, label="pre_update")

        conn = self.db.get_connection()
        cursor = conn.cursor()
        now_iso = datetime.now()

        try:
            with conn:
                if "چارت" in wb.sheetnames:
                    ws_chart = wb["چارت"]
                    pairs = []
                    for r in range(2, ws_chart.max_row + 1):
                        u = str(ws_chart.cell(row=r, column=1).value or "").strip()
                        s = str(ws_chart.cell(row=r, column=2).value or "").strip()
                        if u and u not in ("None", ""):
                            pairs.append((u, s, r - 1))
                    project_manager.set_project_org_chart(project_id, pairs)

                if "تامین نیرو" in wb.sheetnames:
                    ws_sh = wb["تامین نیرو"]
                    cursor.execute("DELETE FROM shortages WHERE project_id = %s AND status = 'تامین نشده'", (project_id,))
                    for r in range(2, ws_sh.max_row + 1):
                        u = str(ws_sh.cell(row=r, column=2).value or "").strip()
                        s = str(ws_sh.cell(row=r, column=3).value or "").strip()
                        st = str(ws_sh.cell(row=r, column=4).value or "").strip()
                        assigned_name = str(ws_sh.cell(row=r, column=5).value or "").strip()
                        phone = str(ws_sh.cell(row=r, column=6).value or "").strip()
                        desc = str(ws_sh.cell(row=r, column=7).value or "").strip()
                        target_grp = str(ws_sh.cell(row=r, column=8).value or "").strip()
                        if u and u not in ("None", ""):
                            cursor.execute("""
                            INSERT INTO shortages (project_id, unit, section, count, target_group, description, status, assigned_name, phone, _excel_row, is_notified, created_at)
                            VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s, %s, 0, %s)
                            """, (project_id, u, s, target_grp, desc, st or 'تامین نشده', assigned_name, phone, r, now_iso))

                for sheet_name in valid_sheets:
                    clean_sheet_name = sheet_name.strip()

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

                    session = None
                    if sched_id is None:
                        cursor.execute("""
                        SELECT s.id, s.schedule_id 
                        FROM sessions s
                        JOIN schedules sc ON s.schedule_id = sc.id
                        WHERE s.project_id = %s AND s.name = %s AND s.status != 'CANCELLED' AND sc.is_active = TRUE
                        """, (project_id, clean_sheet_name))
                        s_row = cursor.fetchone()
                        if s_row:
                            sched_id = s_row['schedule_id']
                            session_id = s_row['id']
                            session = {'id': session_id, 'schedule_id': sched_id}

                    cursor.execute("SELECT type FROM projects WHERE id = %s", (project_id,))
                    p_type_row = cursor.fetchone()
                    if p_type_row and p_type_row['type'] == 'کلاس' and sched_id is None:
                        raise ValueError(f"شیت «{sheet_name}» به هیچ برنامه (Schedule) فعال این پروژه متصل نیست.")

                    if not session:
                        session = attendance_manager.get_session_by_name(project_id, clean_sheet_name, schedule_id=sched_id)
                    if not session:
                        session_id = attendance_manager.create_session(
                            project_id, clean_sheet_name, schedule_id=sched_id, 
                            copy_from_prev_session=False, sync_excel_sheet=False
                        )
                    else:
                        session_id = session['id']
                        if sched_id is None:
                            sched_id = session.get('schedule_id')

                    ws = wb[sheet_name]
                    header_map = {}
                    for c in range(1, 100):
                        val = ws.cell(row=1, column=c).value
                        if val:
                            header_map[str(val).strip()] = c

                    sheet_staff_ids = set()

                    for r_idx in range(2, ws.max_row + 1):
                        def get_val(col_name):
                            c = header_map.get(col_name)
                            if not c: return ""
                            v = ws.cell(row=r_idx, column=c).value
                            return str(v or "").strip() if v is not None and str(v).strip() != "None" else ""

                        name = get_val("نام و نام خانوادگی")
                        if not name: continue
                        
                        staff_code = get_val("کد پرسنلی") or get_val("کد نیرو") or get_val("شناسه نیرو")
                        unit = get_val("واحد")
                        section = get_val("بخش")
                        
                        # --- قانون دائمی جلوگیری از تولید هویت‌های تکراری ---
                        if section in ['None', '', 'عمومی', 'اصلی', '-'] or section == unit:
                            section = ''
                        # ----------------------------------------------------
                        
                        pos = get_val("سمت") or "نیرو"
                        card_title = get_val("عنوان کارت") or section
                        phone = get_val("شماره تماس")
                        shift_time = get_val("ساعت حضور")
                        late_trk = get_val("پیگیری تاخیر")
                        desc = get_val("توضیحات")
                        card_st = get_val("وضعیت کارت")
                        att_st = get_val("وضعیت حضور")
                        gender = get_val("جنسیت")
                        is_multi = get_val("فعال در چند بخش؟") or "خیر"

                        staff_row = None
                        if staff_code:
                            cursor.execute("SELECT id FROM project_staff WHERE project_id = %s AND staff_code = %s", (project_id, staff_code))
                            staff_row = cursor.fetchone()

                        if not staff_row:
                            cursor.execute("""
                            SELECT id FROM project_staff WHERE project_id = %s AND name = %s AND unit = %s AND section = %s
                            """, (project_id, name, unit, section))
                            staff_row = cursor.fetchone()

                        if staff_row:
                            staff_id = staff_row['id']
                            cursor.execute("""
                            UPDATE project_staff SET
                                phone = COALESCE(NULLIF(%s, ''), phone),
                                shift_time = COALESCE(NULLIF(%s, ''), shift_time),
                                gender = COALESCE(NULLIF(%s, ''), gender),
                                position = COALESCE(NULLIF(%s, ''), position),
                                card_title = COALESCE(NULLIF(%s, ''), card_title),
                                _excel_row = %s
                            WHERE id = %s
                            """, (phone, shift_time, gender, pos, card_title, r_idx, staff_id))
                        else:
                            cursor.execute("""
                            INSERT INTO project_staff (project_id, staff_code, name, phone, unit, section, position, card_title, shift_time, gender, notes, is_multi_section, _excel_row)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING id;
                            """, (project_id, staff_code, name, phone, unit, section, pos, card_title, shift_time, gender, desc, is_multi, r_idx))
                            staff_id = cursor.fetchone()['id']
                            if not staff_code:
                                auto_code = f"STF-{staff_id:05d}"
                                cursor.execute("UPDATE project_staff SET staff_code = %s WHERE id = %s", (auto_code, staff_id))

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
                            staff_name_snapshot = CASE WHEN attendance.staff_name_snapshot IS NULL OR attendance.staff_name_snapshot = '' THEN EXCLUDED.staff_name_snapshot ELSE attendance.staff_name_snapshot END,
                            unit_snapshot = CASE WHEN attendance.unit_snapshot IS NULL OR attendance.unit_snapshot = '' THEN EXCLUDED.unit_snapshot ELSE attendance.unit_snapshot END,
                            section_snapshot = CASE WHEN attendance.section_snapshot IS NULL OR attendance.section_snapshot = '' THEN EXCLUDED.section_snapshot ELSE attendance.section_snapshot END,
                            position_snapshot = CASE WHEN attendance.position_snapshot IS NULL OR attendance.position_snapshot = '' THEN EXCLUDED.position_snapshot ELSE attendance.position_snapshot END,
                            gender_snapshot = CASE WHEN attendance.gender_snapshot IS NULL OR attendance.gender_snapshot = '' THEN EXCLUDED.gender_snapshot ELSE attendance.gender_snapshot END,
                            phone_snapshot = CASE WHEN attendance.phone_snapshot IS NULL OR attendance.phone_snapshot = '' THEN EXCLUDED.phone_snapshot ELSE attendance.phone_snapshot END,
                            card_title_snapshot = CASE WHEN attendance.card_title_snapshot IS NULL OR attendance.card_title_snapshot = '' THEN EXCLUDED.card_title_snapshot ELSE attendance.card_title_snapshot END,
                            shift_time_snapshot = CASE WHEN attendance.shift_time_snapshot IS NULL OR attendance.shift_time_snapshot = '' THEN EXCLUDED.shift_time_snapshot ELSE attendance.shift_time_snapshot END,
                            is_multi_section_snapshot = CASE WHEN attendance.is_multi_section_snapshot IS NULL OR attendance.is_multi_section_snapshot = '' THEN EXCLUDED.is_multi_section_snapshot ELSE attendance.is_multi_section_snapshot END,
                            staff_code_snapshot = CASE WHEN attendance.staff_code_snapshot IS NULL OR attendance.staff_code_snapshot = '' THEN EXCLUDED.staff_code_snapshot ELSE attendance.staff_code_snapshot END,
                            updated_at = EXCLUDED.updated_at
                        """, (
                            project_id, session_id, staff_id, att_st, card_st, late_trk, desc,
                            name, unit, section, pos, gender,
                            phone, card_title, shift_time, is_multi, staff_code,
                            now_iso
                        ))

                    if sched_id is not None and sheet_staff_ids:
                        placeholders = ','.join(['%s'] * len(sheet_staff_ids))
                        cursor.execute(f"""
                        UPDATE schedule_staff 
                        SET is_active = FALSE, end_session_id = %s
                        WHERE schedule_id = %s AND is_active = TRUE AND staff_id NOT IN ({placeholders})
                        """, [session_id, sched_id] + list(sheet_staff_ids))

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

        sessions = attendance_manager.list_sessions(project_id, include_cancelled=False)
        for sess in sessions:
            sheet_name = sess['name']
            if sheet_name not in wb.sheetnames:
                self.add_session_sheet(project_id, sheet_name)
                wb.close()
                wb = openpyxl.load_workbook(excel_path, data_only=False)
                if sheet_name not in wb.sheetnames:
                    continue

            ws = wb[sheet_name]
            header_map = {str(ws.cell(row=1, column=c).value).strip(): c for c in range(1, 100) if ws.cell(row=1, column=c).value}
            att_records = attendance_manager.get_session_attendance(project_id, sess['id'])

            row_lookup = {}
            name_col = header_map.get("نام و نام خانوادگی")
            unit_col = header_map.get("واحد")
            sec_col = header_map.get("بخش")
            if name_col:
                for ri in range(2, ws.max_row + 1):
                    n_val = str(ws.cell(row=ri, column=name_col).value or "").strip()
                    u_val = str(ws.cell(row=ri, column=unit_col).value or "").strip() if unit_col else ""
                    s_val = str(ws.cell(row=ri, column=sec_col).value or "").strip() if sec_col else ""
                    if n_val:
                        row_lookup[(n_val, u_val, s_val)] = ri

            for rec in att_records:
                rec_name = str(rec.get("name") or "").strip()
                rec_unit = str(rec.get("unit") or "").strip()
                rec_section = str(rec.get("section") or "").strip()
                r_idx = row_lookup.get((rec_name, rec_unit, rec_section))
                if not r_idx:
                    r_idx = ws.max_row + 1
                    row_lookup[(rec_name, rec_unit, rec_section)] = r_idx

                if "نام و نام خانوادگی" in header_map: ws.cell(row=r_idx, column=header_map["نام و نام خانوادگی"]).value = rec.get("name")
                if "کد پرسنلی" in header_map: ws.cell(row=r_idx, column=header_map["کد پرسنلی"]).value = rec.get("staff_code")
                if "واحد" in header_map: ws.cell(row=r_idx, column=header_map["واحد"]).value = rec.get("unit")
                if "بخش" in header_map: ws.cell(row=r_idx, column=header_map["بخش"]).value = rec.get("section")
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
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM shortages WHERE project_id = %s ORDER BY id ASC", (project_id,))
            shortages = cursor.fetchall()
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
        wb.close()
        return out_path

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