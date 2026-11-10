import os
import sys
from datetime import datetime
from db_manager import db_instance
from attendance_manager import attendance_manager
from utils import (
    safe_markdown, normalize_persian, to_persian_digits,
    get_today_jalali_str, normalize_date_to_jalali, get_persian_weekday,
    parse_any_date_to_datetime
)


class ClassReportDataBuilder:
    """Extracts, validates, and organizes data for class text reports."""
    @staticmethod
    def build(project_id, session_id):
        from project_manager import project_manager

        proj = project_manager.get_project(project_id)
        sess = attendance_manager.get_session(session_id)
        # فراخوانی get_session_attendance اکنون به طور خودکار در صورت خالی بودن جلسه، کادر را در آن جلسه بارگذاری می‌کند
        all_att_raw = attendance_manager.get_session_attendance(project_id, session_id)
        org_chart = project_manager.get_project_org_chart(project_id)

        # فیلتر طلایی: حذف نیروهای «بدون شیفت» فقط از گزارش متنی
        all_att = [s for s in all_att_raw if "بدون شیفت" not in str(s.get('status', '')).strip()]

        proj_name = proj['name'] if proj else "کلاس"

        # محاسبه دقیق تاریخ شمسی جلسه (یا تاریخ شمسی امروز به عنوان پیش‌فرض)
        raw_sess_date = sess.get('session_date') if sess else ""
        if raw_sess_date and str(raw_sess_date).strip():
            sess_date = normalize_date_to_jalali(raw_sess_date)
        else:
            sess_date = get_today_jalali_str()

        # محاسبه دقیق روز هفته جلسه
        sess_day = (sess.get('day_of_week') or "").strip() if sess else ""
        if not sess_day and proj and proj.get('recurring_days'):
            sess_day = str(proj.get('recurring_days')).strip()
        if not sess_day:
            dt_obj = parse_any_date_to_datetime(sess_date)
            sess_day = get_persian_weekday(dt_obj)

        # 1. Identify Senior / Arshad staff
        arshad_staff = None
        for s in all_att:
            pos = str(s.get('position', '')).strip()
            sec = str(s.get('section', '')).strip()
            u = str(s.get('unit', '')).strip()
            if "ارشد" in pos or "ارشد" in sec or "ارشد" in u:
                arshad_staff = s
                break

        # 2. Group staff by unit, section, and hierarchy
        units_data = {}
        for s in all_att:
            if s == arshad_staff:
                continue

            u_raw = str(s.get('unit') or '').strip()
            if not u_raw or u_raw in ('None', '-'):
                u_raw = 'عمومی'

            sec_raw = str(s.get('section') or '').strip()

            if sec_raw in ['None', '', 'عمومی', 'اصلی', '-'] or sec_raw == u_raw:
                sec_raw = ''

            pos_raw = str(s.get('position') or '').strip()

            if u_raw not in units_data:
                units_data[u_raw] = {'heads': [], 'deputies': [], 'sections': {}, 'general_staff': []}

            if pos_raw in ["مسئول واحد", "مدیر", "مدیر واحد"]:
                units_data[u_raw]['heads'].append(s)
            elif pos_raw in ["جانشین", "معاون", "جانشین مسئول واحد", "جانشین واحد", "جانشین مسئول"]:
                units_data[u_raw]['deputies'].append(s)
            elif pos_raw in ["مسئول بخش", "سرپرست بخش", "سرپرست"]:
                if sec_raw:
                    if sec_raw not in units_data[u_raw]['sections']:
                        units_data[u_raw]['sections'][sec_raw] = {'heads': [], 'staff': []}
                    units_data[u_raw]['sections'][sec_raw]['heads'].append(s)
                else:
                    units_data[u_raw]['general_staff'].append(s)
            elif pos_raw == "مسئول":
                if not sec_raw:
                    units_data[u_raw]['heads'].append(s)
                else:
                    if sec_raw not in units_data[u_raw]['sections']:
                        units_data[u_raw]['sections'][sec_raw] = {'heads': [], 'staff': []}
                    units_data[u_raw]['sections'][sec_raw]['heads'].append(s)
            else:
                if sec_raw:
                    if sec_raw not in units_data[u_raw]['sections']:
                        units_data[u_raw]['sections'][sec_raw] = {'heads': [], 'staff': []}
                    units_data[u_raw]['sections'][sec_raw]['staff'].append(s)
                else:
                    units_data[u_raw]['general_staff'].append(s)

        ordered_units = list(org_chart.keys())
        for u in units_data.keys():
            if u not in ordered_units:
                ordered_units.append(u)

        return {
            'project_name': proj_name,
            'session_name': sess.get('name', '') if sess else '',
            'session_date': sess_date,
            'session_day': sess_day,
            'arshad': arshad_staff,
            'ordered_units': ordered_units,
            'org_chart': org_chart,
            'units_data': units_data
        }


class PersianClassReportRenderer:
    """Renders validated class report data into the required Persian business format with emojis and Shamsi date."""

    @staticmethod
    def render(report_data):
        proj_name = report_data['project_name']
        raw_date = normalize_date_to_jalali(report_data['session_date']) or get_today_jalali_str()
        sess_date = to_persian_digits(raw_date)
        sess_day = report_data.get('session_day', '')
        arshad = report_data['arshad']
        ordered_units = report_data['ordered_units']
        org_chart = report_data['org_chart']
        units_data = report_data['units_data']

        def format_staff(s):
            name = s.get('name', '')
            st = str(s.get('status', '')).strip()

            status_icon = "⚪️"
            if "حاضر" in st:
                status_icon = "✅"
            elif "غایب" in st:
                status_icon = "❌"
            elif "تاخیر" in st:
                status_icon = "⏱"

            return f"{status_icon} {name}"

        lines = [f"کلاس سطح: {proj_name}       روز: {sess_day}         تاریخ: {sess_date}\n"]

        if arshad:
            lines.append(f"*ارشد:* {format_staff(arshad)}")
            if arshad.get('phone'):
                phone_fa = to_persian_digits(arshad.get('phone', ''))
                lines.append(phone_fa)
        else:
            lines.append("*ارشد:* ")
            lines.append("۰۹...")
        lines.append("")

        dividers = ["🔹🔹🔹🔹🔹🔹🔹🔹🔹🔹", "🔸🔸🔸🔸🔸🔸🔸🔸🔸🔸"]
        div_idx = 0

        for u_name in ordered_units:
            u_data = units_data.get(u_name)
            if not u_data and u_name in org_chart:
                u_data = {'heads': [], 'deputies': [], 'sections': {}, 'general_staff': []}

            if not u_data:
                continue

            has_staff = bool(u_data.get('heads') or u_data.get('deputies') or u_data.get('general_staff'))
            if not has_staff:
                for sec_data in u_data.get('sections', {}).values():
                    if sec_data.get('heads') or sec_data.get('staff'):
                        has_staff = True
                        break

            if not has_staff:
                continue

            lines.append(dividers[div_idx])
            div_idx = 1 - div_idx
            lines.append("")

            display_unit_name = u_name if "واحد" in u_name else f"واحد {u_name}"
            head_str = ""
            if u_data['heads']:
                head_str = " ".join([format_staff(h) for h in u_data['heads']])

            lines.append(f"*{display_unit_name}:* {head_str}".strip())

            deputy_str = ""
            if u_data['deputies']:
                deputy_str = " ".join([format_staff(d) for d in u_data['deputies']])

            if deputy_str or u_data['deputies']:
                lines.append(f"جانشین مسئول واحد: {deputy_str}".strip())
            else:
                lines.append("جانشین مسئول واحد:")
            lines.append("")

            sections_in_chart = org_chart.get(u_name, [])
            all_unit_sections = list(sections_in_chart)
            for sec in u_data['sections'].keys():
                if sec not in all_unit_sections:
                    all_unit_sections.append(sec)

            valid_secs = [sec for sec in all_unit_sections if sec and sec not in ('-', 'None', 'عمومی', 'اصلی', u_name)]

            if valid_secs:
                for sec in valid_secs:
                    sec_data = u_data['sections'].get(sec, {'heads': [], 'staff': []})

                    sec_head_str = ""
                    if sec_data['heads']:
                        sec_head_str = " ".join([format_staff(sh) for sh in sec_data['heads']])

                    if sec_head_str:
                        lines.append(f"بخش {sec}:\nمسئول بخش: {sec_head_str}".strip())
                    else:
                        lines.append(f"بخش {sec}:".strip())

                    lines.append("نیروها:")
                    if sec_data['staff']:
                        for st in sec_data['staff']:
                            lines.append(f"  {format_staff(st)}")
                    lines.append("")

            if u_data['general_staff']:
                lines.append("نیرو:")
                for st in u_data['general_staff']:
                    lines.append(f"  {format_staff(st)}")
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
            if not unit or unit == "None":
                continue
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
                    if g == "آقا":
                        stats[unit_safe]['prs_m'] += 1
                    else:
                        stats[unit_safe]['prs_f'] += 1
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
        today = get_today_jalali_str()
        now_iso = datetime.now()
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO staff_logs (project_id, user_id, action_type, log_date, created_at)
            VALUES (%s, %s, %s, %s, %s)
            """, (project_id, user_id, action_type, today, now_iso))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()

    def get_staff_performance_report(self, project_id=None, date_str=None):
        target_date = normalize_date_to_jalali(date_str) if date_str else get_today_jalali_str()
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            query = """
            SELECT l.user_id, l.action_type, COUNT(*) as cnt, u.staff_name
            FROM staff_logs l
            LEFT JOIN users u ON l.user_id = u.user_id
            WHERE l.log_date = %s
            """
            params = [target_date]
            if project_id:
                query += " AND l.project_id = %s"
                params.append(project_id)
            query += " GROUP BY l.user_id, l.action_type, u.staff_name"
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def generate_class_text_report(self, project_id, session_id):
        report_data = ClassReportDataBuilder.build(project_id, session_id)
        return PersianClassReportRenderer.render(report_data)


report_manager = ReportManager()