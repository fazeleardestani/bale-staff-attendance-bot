import os
import sys
from datetime import datetime
from db_manager import db_instance
from attendance_manager import attendance_manager
from utils import safe_markdown, normalize_persian

class ClassReportDataBuilder:
    """Extracts, validates, and organizes data for class text reports."""
    @staticmethod
    def build(project_id, session_id):
        from project_manager import project_manager

        proj = project_manager.get_project(project_id)
        sess = attendance_manager.get_session(session_id)
        all_att = attendance_manager.get_session_attendance(project_id, session_id)
        org_chart = project_manager.get_project_org_chart(project_id)

        proj_name = proj['name'] if proj else "کلاس"
        sess_date = sess.get('session_date') or datetime.now().strftime("%Y/%m/%d") if sess else ""

        # 1. Identify Senior / Arshad staff
        arshad_staff = None
        for s in all_att:
            pos = str(s.get('position', '')).strip()
            sec = str(s.get('section', '')).strip()
            u = str(s.get('unit', '')).strip()
            if "ارشد" in pos or "ارشد" in sec or "ارشد" in u:
                arshad_staff = s
                break

        # 2. Group staff by unit and section
        units_data = {}
        for s in all_att:
            if s == arshad_staff:
                continue
            u_raw = (s.get('unit') or 'سایر').strip()
            sec_raw = (s.get('section') or 'عمومی').strip()
            pos_raw = (s.get('position') or 'نیرو').strip()

            if u_raw not in units_data:
                units_data[u_raw] = {'head': [], 'deputy': [], 'sections': {}, 'all_staff': []}

            if "مسئول واحد" in pos_raw or "مسئول" in pos_raw or "سرپرست" in pos_raw:
                units_data[u_raw]['head'].append(s)
            elif "جانشین" in pos_raw or "معاون" in pos_raw:
                units_data[u_raw]['deputy'].append(s)
            else:
                if sec_raw not in units_data[u_raw]['sections']:
                    units_data[u_raw]['sections'][sec_raw] = []
                units_data[u_raw]['sections'][sec_raw].append(s)
            units_data[u_raw]['all_staff'].append(s)

        # 3. Determine units order based on org_chart display_order
        ordered_units = list(org_chart.keys())
        for u in units_data.keys():
            if u not in ordered_units:
                ordered_units.append(u)

        return {
            'project_name': proj_name,
            'session_date': sess_date,
            'arshad': arshad_staff,
            'ordered_units': ordered_units,
            'org_chart': org_chart,
            'units_data': units_data
        }

class PersianClassReportRenderer:
    """Renders validated class report data into the required Persian business format."""
    @staticmethod
    def render(report_data):
        proj_name = report_data['project_name']
        sess_date = report_data['session_date']
        arshad = report_data['arshad']
        ordered_units = report_data['ordered_units']
        org_chart = report_data['org_chart']
        units_data = report_data['units_data']

        def format_staff(s):
            name = s.get('name', '')
            st = (s.get('status') or '').strip()
            g_prefix = "آقا" if s.get('gender') == 'آقا' else "خانم"
            status_part = f" ({st})" if st else ""
            return f"{g_prefix} {name}{status_part}"

        def format_staff_list(staff_list):
            if not staff_list:
                return ""
            return ", ".join([format_staff(s) for s in staff_list])

        lines = [f"{proj_name}", f"تاریخ: {sess_date}", ""]

        if arshad:
            g_prefix = "آقا" if arshad.get('gender') == 'آقا' else "خانم"
            st_part = f" ({arshad['status']})" if arshad.get('status') else ""
            lines.append(f"*ارشد:* {g_prefix} {arshad['name']}{st_part}")
            lines.append(f"{arshad.get('phone', '')}")
        else:
            lines.append("*ارشد:* خانم ")
            lines.append("۰۹...")
        lines.append("")

        for u_name in ordered_units:
            u_data = units_data.get(u_name)
            head_str = format_staff_list(u_data['head']) if u_data and u_data['head'] else "خانم "
            deputy_str = format_staff_list(u_data['deputy']) if u_data and u_data['deputy'] else ""

            lines.append(f"*واحد {u_name}:* {head_str}")
            if deputy_str:
                lines.append(f"جانشین مسئول واحد: {deputy_str}")
            lines.append("")

            sections_in_chart = org_chart.get(u_name, [])
            all_unit_sections = list(sections_in_chart)
            if u_data:
                for sec in u_data['sections'].keys():
                    if sec not in all_unit_sections:
                        all_unit_sections.append(sec)

            valid_secs = [sec for sec in all_unit_sections if sec and sec not in ('-', 'None', 'عمومی')]

            if valid_secs:
                for sec in valid_secs:
                    sec_staff = u_data['sections'].get(sec, []) if u_data else []
                    lines.append(f"بخش {sec}:")
                    lines.append(f"نیروها: {format_staff_list(sec_staff)}")
                    lines.append("")
            else:
                unit_general_staff = []
                if u_data:
                    for s_list in u_data['sections'].values():
                        unit_general_staff.extend(s_list)
                if unit_general_staff:
                    lines.append(f"نیروها: {format_staff_list(unit_general_staff)}")
                    lines.append("")

        return "\n".join(lines).strip()

class ReportManager:
    def __init__(self, db=db_instance):
        self.db = db

    def get_dashboard_stats(self, project_id, session_id):
        all_att = attendance_manager.get_session_attendance(project_id, session_id)
        stats = {}
        total_exp = 0
        total_prs = 0

        for u in all_att:
            unit = u.get("unit", "نامشخص")
            if not unit or unit == "None": continue
            unit_safe = safe_markdown(unit)
            if unit_safe not in stats:
                stats[unit_safe] = {'exp': 0, 'prs_m': 0, 'prs_f': 0, 'abs': 0, 'cg': 0, 'cr': 0}

            st = str(u.get("status", "")).strip()
            cd = str(u.get("card_status", "")).strip()
            g = str(u.get("gender", "")).strip()

            if "بدون شیفت" not in st:
                stats[unit_safe]['exp'] += 1
                total_exp += 1
                if "حاضر" in st or "تاخیر" in st:
                    if g == "آقا": stats[unit_safe]['prs_m'] += 1
                    else: stats[unit_safe]['prs_f'] += 1
                    total_prs += 1
                elif "غایب" in st or not st:
                    stats[unit_safe]['abs'] += 1

            if "تحویل داده شد" in cd:
                stats[unit_safe]['cg'] += 1
            elif "پس گرفته شد" in cd or "تحویل گرفته شد" in cd:
                stats[unit_safe]['cr'] += 1

        overall_pct = int((total_prs / total_exp * 100)) if total_exp > 0 else 0
        return {
            'unit_stats': stats,
            'total_expected': total_exp,
            'total_present': total_prs,
            'overall_percentage': overall_pct
        }

    def log_staff_action(self, project_id, user_id, action_type):
        today = datetime.now().strftime("%Y-%m-%d")
        now_iso = datetime.now().isoformat()
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
            INSERT INTO staff_logs (project_id, user_id, action_type, log_date, created_at)
            VALUES (?, ?, ?, ?, ?)
            """, (project_id, user_id, action_type, today, now_iso))
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    def get_staff_performance_report(self, project_id=None, date_str=None):
        target_date = date_str or datetime.now().strftime("%Y-%m-%d")
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        query = """
        SELECT l.user_id, l.action_type, COUNT(*) as cnt, u.staff_name
        FROM staff_logs l
        LEFT JOIN users u ON l.user_id = u.user_id
        WHERE l.log_date = ?
        """
        params = [target_date]
        if project_id:
            query += " AND l.project_id = ?"
            params.append(project_id)
        query += " GROUP BY l.user_id, l.action_type, u.staff_name"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def generate_class_text_report(self, project_id, session_id):
        """Pipeline: Report Data Builder -> Validated Report Data -> Persian Class Report Renderer."""
        report_data = ClassReportDataBuilder.build(project_id, session_id)
        return PersianClassReportRenderer.render(report_data)

report_manager = ReportManager()
