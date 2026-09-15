import os
import sys
from datetime import datetime
from db_manager import db_instance
from attendance_manager import attendance_manager
from utils import safe_markdown

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
            cursor.execute('''
            INSERT INTO staff_logs (project_id, user_id, action_type, log_date, created_at)
            VALUES (?, ?, ?, ?, ?)
            ''', (project_id, user_id, action_type, today, now_iso))
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    def get_staff_performance_report(self, project_id=None, date_str=None):
        target_date = date_str or datetime.now().strftime("%Y-%m-%d")
        conn = self.db.get_sqlite_connection()
        cursor = conn.cursor()
        query = '''
        SELECT l.user_id, l.action_type, COUNT(*) as cnt, u.staff_name
        FROM staff_logs l
        LEFT JOIN users u ON l.user_id = u.user_id
        WHERE l.log_date = ?
        '''
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
        from project_manager import project_manager
        from attendance_manager import attendance_manager
        from utils import normalize_persian

        proj = project_manager.get_project(project_id)
        sess = attendance_manager.get_session(session_id)
        all_att = attendance_manager.get_session_attendance(project_id, session_id)

        proj_name = proj['name'] if proj else "کلاس"
        sess_date = sess.get('session_date') or datetime.now().strftime("%Y/%m/%d") if sess else ""

        def format_staff(s):
            name = s['name']
            st = (s.get('status') or '').strip()
            g_prefix = "آقا" if s.get('gender') == 'آقا' else "خانم"
            status_part = f" ({st})" if st else ""
            return f"{g_prefix} {name}{status_part}"

        def format_staff_list(staff_list):
            if not staff_list:
                return ""
            return ", ".join([format_staff(s) for s in staff_list])

        arshad_staff = None
        for s in all_att:
            pos = str(s.get('position', '')).strip()
            sec = str(s.get('section', '')).strip()
            u = str(s.get('unit', '')).strip()
            if "ارشد" in pos or "ارشد" in sec or "ارشد" in u:
                arshad_staff = s
                break

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

        def get_unit_bucket(keyword):
            kw_norm = normalize_persian(keyword)
            for u_name, data in units_data.items():
                if kw_norm in normalize_persian(u_name):
                    return data
            return None

        def get_section_staff(unit_bucket, sec_keyword):
            if not unit_bucket: return []
            sec_kw_norm = normalize_persian(sec_keyword)
            matched = []
            for sec_name, staff_list in unit_bucket['sections'].items():
                if sec_kw_norm in normalize_persian(sec_name):
                    matched.extend(staff_list)
            return matched

        def get_head_str(unit_bucket, default_prefix="خانم"):
            if unit_bucket and unit_bucket['head']:
                return format_staff_list(unit_bucket['head'])
            return f"{default_prefix} "

        def get_deputy_str(unit_bucket):
            if unit_bucket and unit_bucket['deputy']:
                return format_staff_list(unit_bucket['deputy'])
            return ""

        lines = []
        lines.append(f"{proj_name}")
        lines.append(f"تاریخ: {sess_date}")
        lines.append("")
        
        if arshad_staff:
            g_prefix = "آقا" if arshad_staff.get('gender') == 'آقا' else "خانم"
            st_part = f" ({arshad_staff['status']})" if arshad_staff.get('status') else ""
            lines.append(f"*ارشد:* {g_prefix} {arshad_staff['name']}{st_part}")
            lines.append(f"{arshad_staff.get('phone', '')}")
        else:
            lines.append("*ارشد:* خانم ")
            lines.append("۰۹...")
        lines.append("")

        u_amoozesh = get_unit_bucket("آموزش")
        lines.append(f"* واحد امور آموزش:* {get_head_str(u_amoozesh)}")
        lines.append(f"جانشین مسئول واحد: {get_deputy_str(u_amoozesh)}")
        lines.append("")
        paziresh = get_section_staff(u_amoozesh, "پذیرش")
        lines.append("بخش پذیرش:")
        lines.append(f"نیروها: {format_staff_list(paziresh)}")
        lines.append("")
        barkhat = get_section_staff(u_amoozesh, "برخط")
        lines.append("بخش برخط:")
        lines.append(f"نیروها: {format_staff_list(barkhat)}")
        lines.append("")

        u_mohit = get_unit_bucket("محیط")
        lines.append(f"*واحد محیط:* {get_head_str(u_mohit)}")
        lines.append(f"جانشین مسئول واحد: {get_deputy_str(u_mohit)}")
        mohit_staff = []
        if u_mohit:
            for s_list in u_mohit['sections'].values(): mohit_staff.extend(s_list)
        lines.append(f"نیروها: {format_staff_list(mohit_staff)}")
        lines.append("")

        u_entezamat = get_unit_bucket("انتظامات")
        lines.append(f"*واحد انتظامات:* {get_head_str(u_entezamat)}")
        lines.append(f"جانشین مسئول واحد: {get_deputy_str(u_entezamat)}")
        entezamat_staff = []
        if u_entezamat:
            for s_list in u_entezamat['sections'].values(): entezamat_staff.extend(s_list)
        lines.append(f"نیروها: {format_staff_list(entezamat_staff)}")
        lines.append("")

        u_tadarokat = get_unit_bucket("تدارکات")
        lines.append(f"*واحد تدارکات:* {get_head_str(u_tadarokat)}")
        lines.append(f"جانشین مسئول واحد: {get_deputy_str(u_tadarokat)}")
        lines.append("")
        amadehsazi = get_section_staff(u_tadarokat, "آماده سازی") or get_section_staff(u_tadarokat, "آمادهسازی")
        lines.append("بخش آمادهسازی:")
        lines.append(f"نیروها: {format_staff_list(amadehsazi)}")
        lines.append("")
        pazirayi = [s for s in get_section_staff(u_tadarokat, "پذیرایی") if "استاد" not in s.get('section', '')]
        lines.append("بخش پذیرایی:")
        lines.append(f"نیروها: {format_staff_list(pazirayi)}")
        lines.append("")
        pazirayi_ostad = get_section_staff(u_tadarokat, "پذیرایی استاد") or get_section_staff(u_tadarokat, "استاد")
        lines.append(f"بخش پذیرایی استاد: {format_staff_list(pazirayi_ostad)}")
        lines.append("")

        u_resane = get_unit_bucket("رسانه")
        lines.append(f"*واحد رسانه:* {get_head_str(u_resane)}")
        lines.append(f"جانشین مسئول واحد: {get_deputy_str(u_resane)}")
        lines.append("")
        samiobasari = get_section_staff(u_resane, "سمعی") or get_section_staff(u_resane, "بصری")
        lines.append("بخش سمعی و بصری:")
        lines.append(f"نیروها: {format_staff_list(samiobasari)}")
        lines.append("")
        pakhsh = get_section_staff(u_resane, "پخش")
        lines.append(f"بخش پخش رسانه: {format_staff_list(pakhsh)}")
        lines.append("")

        u_ravabet = get_unit_bucket("روابط عمومی")
        lines.append(f"*واحد روابط عمومی:* {get_head_str(u_ravabet)}")
        lines.append(f"جانشین مسئول واحد: {get_deputy_str(u_ravabet)}")
        lines.append("")
        ertebatat = get_section_staff(u_ravabet, "ارتباطات")
        lines.append(f"بخش ارتباطات:")
        lines.append(f"نیرو: {format_staff_list(ertebatat)}")
        lines.append("")
        kheyriyeh = get_section_staff(u_ravabet, "خیریه")
        lines.append(f"بخش خیریه: {format_staff_list(kheyriyeh)}")
        lines.append("")

        u_anbar = get_unit_bucket("انبار")
        lines.append(f"*واحد انبار:* {get_head_str(u_anbar)}")
        anbar_staff = []
        if u_anbar:
            for s_list in u_anbar['sections'].values(): anbar_staff.extend(s_list)
        lines.append(f"نیرو: {format_staff_list(anbar_staff)}")
        lines.append("")

        u_kader = get_unit_bucket("امورکادر") or get_unit_bucket("امور کادر") or get_unit_bucket("کادر")
        lines.append(f"*واحد امورکادر:* {get_head_str(u_kader)}")
        lines.append(f"جانشین مسئول واحد: {get_deputy_str(u_kader)}")
        lines.append("")
        kader_staff = []
        if u_kader:
            for s_list in u_kader['sections'].values(): kader_staff.extend(s_list)
        lines.append(f"نیرو: {format_staff_list(kader_staff)}")

        return "\n".join(lines)


report_manager = ReportManager()

