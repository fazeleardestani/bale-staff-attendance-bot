import os
import sys
import importlib.abc
import importlib.util
import logging

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

import openpyxl
from copy import copy
import shutil
from datetime import datetime
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

        # If project has no excel file yet, copy base template by default
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

            # Retention policy: keep last 15 backups
            try:
                files = sorted(
                    [os.path.join(category_dir, f) for f in os.listdir(category_dir) if f.endswith(".xlsx")],
                    key=os.path.getmtime
                )
                if len(files) > 15:
                    for old_f in files[:-15]:
                        os.remove(old_f)
            except Exception:
                pass

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
            headers = ["ردیف", "واحد", "بخش", "نام و نام خانوادگی", "سمت", "عنوان کارت", "شماره تماس", "ساعت حضور", "پیگیری تاخیر", "توضیحات", "وضعیت کارت", "وضعیت حضور", "جنسیت", "فعال در چند بخش؟"]
            new_ws.append(headers)

        wb.save(excel_path)
        wb.close()
        return True

    def import_project_excel(self, project_id, uploaded_file_path):
        """
        Transactional, schedule-aware import of project Excel file.
        1. Validates extension (rejects .xls).
        2. Creates safety backups (database + excel).
        3. Enforces schedule roster invariant:
           If a session belongs to a schedule, attendance is STRICTLY constrained to schedule_staff.
           If schedule_staff is empty, the sheet initializes schedule_staff and future empty sessions sync from it.
           If schedule_staff already has assigned members, staff outside the roster are NOT added to attendance.
        4. Atomic transaction with full rollback on error.
        """
        if uploaded_file_path.lower().endswith('.xls') and not uploaded_file_path.lower().endswith('.xlsx'):
            raise ValueError("فرمت .xls قدیمی پشتیبانی نمی‌شود. لطفاً فایل اکسل را با فرمت مدرن .xlsx ارسال فرمایید.")

        from attendance_manager import attendance_manager

        # Create safety pre-update backups
        self.backup_project_excel(project_id, label="pre_update")
        db_instance.backup_database(label="pre_update")

        proj_excel_path = self.get_project_excel_path(project_id)
        if os.path.abspath(uploaded_file_path) != os.path.abspath(proj_excel_path):
            shutil.copy2(uploaded_file_path, proj_excel_path)
        project_manager.update_project_excel_path(project_id, proj_excel_path)

        wb = openpyxl.load_workbook(proj_excel_path, data_only=True)
        exclude_sheets = ['تامین نیرو', 'چارت', 'داشبورد']
        valid_sheets = [s for s in wb.sheetnames if s not in exclude_sheets]

        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        now_iso = datetime.now().isoformat()

        try:
            with conn:
                # 1. Organizational Chart with display order
                if "چارت" in wb.sheetnames:
                    ws_chart = wb["چارت"]
                    pairs = []
                    for r in range(2, ws_chart.max_row + 1):
                        u = str(ws_chart.cell(row=r, column=1).value or "").strip()
                        s = str(ws_chart.cell(row=r, column=2).value or "").strip()
                        if u and u not in ("None", ""):
                            pairs.append((u, s, r - 1))
                    project_manager.set_project_org_chart(project_id, pairs)

                # 2. Shortages
                if "تامین نیرو" in wb.sheetnames:
                    ws_sh = wb["تامین نیرو"]
                    cursor.execute("DELETE FROM shortages WHERE project_id = ?", (project_id,))
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
                            VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, 1, ?)
                            """, (project_id, u, s, target_grp, desc, st or 'تامین نشده', assigned_name, phone, r, now_iso))

                # 3. Sessions & Staff (Schedule-Aware)
                for sheet_name in valid_sheets:
                    session = attendance_manager.get_session_by_name(project_id, sheet_name)
                    if not session:
                        # Match schedule by name or substring if exists
                        cursor.execute("""
                        SELECT id FROM schedules 
                        WHERE project_id = ? AND (name = ? OR ? LIKE '%' || name || '%' OR name LIKE '%' || ? || '%') AND is_active = 1
                        """, (project_id, sheet_name, sheet_name, sheet_name))
                        matched_sched = cursor.fetchone()
                        sched_id = matched_sched['id'] if matched_sched else None
                        
                        session_id = attendance_manager.create_session(
                            project_id, sheet_name, schedule_id=sched_id, 
                            copy_from_prev_session=False, sync_excel_sheet=False
                        )
                    else:
                        session_id = session['id']
                        sched_id = session.get('schedule_id')

                    # Check existing roster in schedule_staff for this schedule
                    existing_schedule_staff_ids = set()
                    is_initial_schedule_setup = False
                    if sched_id is not None:
                        cursor.execute("SELECT staff_id FROM schedule_staff WHERE schedule_id = ? AND is_active = 1", (sched_id,))
                        existing_schedule_staff_ids = {r['staff_id'] for r in cursor.fetchall()}
                        if len(existing_schedule_staff_ids) == 0:
                            is_initial_schedule_setup = True

                    ws = wb[sheet_name]
                    header_map = {}
                    for c in range(1, 100):
                        val = ws.cell(row=1, column=c).value
                        if val:
                            header_map[str(val).strip()] = c

                    enrolled_in_sheet = []

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

                        # Stable identity: check staff_code first if available
                        staff_row = None
                        if staff_code:
                            cursor.execute("SELECT id FROM project_staff WHERE project_id = ? AND staff_code = ?", (project_id, staff_code))
                            staff_row = cursor.fetchone()

                        if not staff_row:
                            cursor.execute("""
                            SELECT id FROM project_staff WHERE project_id = ? AND name = ? AND unit = ? AND section = ?
                            """, (project_id, name, unit, section))
                            staff_row = cursor.fetchone()

                        if staff_row:
                            staff_id = staff_row['id']
                            cursor.execute("""
                            UPDATE project_staff SET
                                phone = COALESCE(NULLIF(?, ''), phone),
                                shift_time = COALESCE(NULLIF(?, ''), shift_time),
                                gender = COALESCE(NULLIF(?, ''), gender),
                                position = COALESCE(NULLIF(?, ''), position),
                                card_title = COALESCE(NULLIF(?, ''), card_title),
                                _excel_row = ?
                            WHERE id = ?
                            """, (phone, shift_time, gender, pos, card_title, r_idx, staff_id))
                        else:
                            cursor.execute("""
                            INSERT INTO project_staff (project_id, staff_code, name, phone, unit, section, position, card_title, shift_time, gender, notes, is_multi_section, _excel_row)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (project_id, staff_code, name, phone, unit, section, pos, card_title, shift_time, gender, desc, is_multi, r_idx))
                            staff_id = cursor.lastrowid
                            if not staff_code:
                                auto_code = f"STF-{staff_id:05d}"
                                cursor.execute("UPDATE project_staff SET staff_code = ? WHERE id = ?", (auto_code, staff_id))

                        # ROSTER INVARIANT VERIFICATION:
                        can_record_attendance = True
                        if sched_id is not None:
                            if is_initial_schedule_setup:
                                # Initial setup of this schedule via Excel sheet
                                cursor.execute("""
                                INSERT INTO schedule_staff (schedule_id, staff_id, is_active, created_at)
                                VALUES (?, ?, 1, ?)
                                ON CONFLICT(schedule_id, staff_id) DO UPDATE SET is_active = 1
                                """, (sched_id, staff_id, now_iso))
                                existing_schedule_staff_ids.add(staff_id)
                                can_record_attendance = True
                            else:
                                # Schedule ALREADY has an established roster:
                                # Staff must be an enrolled active member of this schedule!
                                if staff_id in existing_schedule_staff_ids:
                                    can_record_attendance = True
                                else:
                                    can_record_attendance = False
                                    logging.warning(f"Excel import: Staff '{name}' (ID {staff_id}) is not in schedule {sched_id} roster. Skipping attendance for session {session_id}.")

                        if can_record_attendance:
                            enrolled_in_sheet.append({
                                'id': staff_id, 'name': name, 'unit': unit, 
                                'section': section, 'position': pos, 'gender': gender
                            })
                            cursor.execute("""
                            INSERT INTO attendance (
                                project_id, session_id, staff_id, status, card_status, late_tracking, description,
                                staff_name_snapshot, unit_snapshot, section_snapshot, position_snapshot, gender_snapshot,
                                updated_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(project_id, session_id, staff_id) DO UPDATE SET
                                status = excluded.status,
                                card_status = excluded.card_status,
                                late_tracking = excluded.late_tracking,
                                description = excluded.description,
                                staff_name_snapshot = excluded.staff_name_snapshot,
                                unit_snapshot = excluded.unit_snapshot,
                                section_snapshot = excluded.section_snapshot,
                                position_snapshot = excluded.position_snapshot,
                                gender_snapshot = excluded.gender_snapshot,
                                updated_at = excluded.updated_at
                            """, (
                                project_id, session_id, staff_id, att_st, card_st, late_trk, desc,
                                name, unit, section, pos, gender,
                                now_iso
                            ))

                    # Auto-sync future empty sessions of the same schedule if this schedule was initialized
                    if sched_id is not None and is_initial_schedule_setup and enrolled_in_sheet:
                        cursor.execute("""
                        SELECT id FROM sessions 
                        WHERE project_id = ? AND schedule_id = ? AND id != ? AND status != 'CANCELLED'
                        """, (project_id, sched_id, session_id))
                        other_sessions = cursor.fetchall()
                        for osess in other_sessions:
                            osid = osess['id']
                            cursor.execute("SELECT COUNT(*) FROM attendance WHERE project_id = ? AND session_id = ?", (project_id, osid))
                            if cursor.fetchone()[0] == 0:
                                for s_item in enrolled_in_sheet:
                                    cursor.execute("""
                                    INSERT INTO attendance (
                                        project_id, session_id, staff_id, status, card_status,
                                        staff_name_snapshot, unit_snapshot, section_snapshot, position_snapshot, gender_snapshot,
                                        updated_at
                                    )
                                    VALUES (?, ?, ?, '', '', ?, ?, ?, ?, ?, ?)
                                    ON CONFLICT(project_id, session_id, staff_id) DO NOTHING
                                    """, (
                                        project_id, osid, s_item['id'],
                                        s_item['name'], s_item['unit'], s_item['section'], s_item['position'], s_item['gender'],
                                        now_iso
                                    ))

        except Exception as err:
            logging.error(f"Excel import failed and was rolled back: {err}")
            raise err
        finally:
            conn.close()
            wb.close()

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
            for rec in att_records:
                r_idx = rec.get('_excel_row')
                if not r_idx or r_idx > ws.max_row:
                    r_idx = ws.max_row + 1
                    rec['_excel_row'] = r_idx

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
            conn = self.db.get_sqlite_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM shortages WHERE project_id = ? ORDER BY id ASC", (project_id,))
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
