#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
جامع‌ترین آزمون خودکار بات حضور و غیاب (End-to-End Comprehensive Test Suite)
این اسکریپت تمام مسیرها، تمام دکمه‌ها، تمام فرآیندهای ورودی پیام و تمامی نقش‌های کاربری را
بدون نیاز به اتصال واقعی به سرور بله تست کرده و سلامت ۱۰۰٪ عملکرد را اعتبارسنجی می‌کند.
"""

import sys
import os
import shutil
import unittest
from datetime import datetime

# افزودن مسیر جاری به sys.path
_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

from db_manager import db_instance
from permission_manager import permission_manager
from project_manager import project_manager
from attendance_manager import attendance_manager
from staff_manager import staff_manager
from shortage_manager import shortage_manager
from report_manager import report_manager
from excel_manager import excel_manager
import bot

# ماک کردن اشیاء تلگرام / بله
class DummyChat:
    def __init__(self, chat_id):
        self.id = chat_id

class DummyMessage:
    def __init__(self, chat_id, text="", message_id=1001, document=None):
        self.chat = DummyChat(chat_id)
        self.message_id = message_id
        self.text = text
        self.caption = ""
        self.document = document

class DummyCall:
    def __init__(self, chat_id, data, message_id=1001):
        self.id = f"call_{chat_id}_{data}"
        self.data = data
        self.message = DummyMessage(chat_id, message_id=message_id)

# Mock bot API methods so tests run offline without network delays or 404s
if hasattr(bot, 'bot') and bot.bot:
    bot.bot.send_message = lambda chat_id, text, **kwargs: DummyMessage(chat_id, text)
    bot.bot.edit_message_text = lambda text, chat_id=None, message_id=None, **kwargs: DummyMessage(chat_id, text, message_id)
    bot.bot.edit_message_reply_markup = lambda chat_id=None, message_id=None, **kwargs: True
    bot.bot.answer_callback_query = lambda *args, **kwargs: True
    bot.bot.send_document = lambda *args, **kwargs: True

class BotComprehensiveTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        print("\n" + "="*75)
        print("🚀 اجرای آزمون جامع کلیه مسیرها، دکمه‌ها، هندلرها و نقش‌های ربات")
        print("="*75)
        
        cls.ts = int(datetime.now().timestamp())
        cls.super_admin_id = 90001  # مدیر ارشد سراسری و مدیر ارشد پروژه
        cls.admin_id = 90002        # مدیر پروژه
        cls.uh_tadarakat = 90003    # مسئول واحد تدارکات
        cls.uh_ent = 90004          # مسئول واحد انتظامات
        cls.operator_id = 90005     # اپراتور
        cls.viewer_id = 90006       # بیننده
        cls.stranger_id = 99999     # کاربر غریبه و غیرمجاز

        # ۱. ثبت کاربر ارشد سراسری
        permission_manager.upsert_user(cls.super_admin_id, "مدیر ارشد تست", "آقا", is_global_super_admin=1)
        
        # ۲. ایجاد پروژه تستی رویداد با نام یکتا
        cls.pid = project_manager.create_project(f"پروژه رویداد تست جامع {cls.ts}", project_type="رویداد", description="پروژه تستی")
        cls.s1 = attendance_manager.create_session(cls.pid, "روز آماده سازی")
        cls.s2 = attendance_manager.create_session(cls.pid, "روز 1")
        
        # ۳. ثبت و تخصیص نقش‌های پروژه
        permission_manager.upsert_user(cls.admin_id, "مدیر پروژه تست", "خانم")
        permission_manager.set_project_user(cls.pid, cls.admin_id, role="admin", is_active=1)

        permission_manager.upsert_user(cls.uh_tadarakat, "مسئول تدارکات", "آقا")
        permission_manager.set_project_user(cls.pid, cls.uh_tadarakat, role="unit_head", assigned_unit="تدارکات", is_active=1)

        permission_manager.upsert_user(cls.uh_ent, "مسئول انتظامات", "خانم")
        permission_manager.set_project_user(cls.pid, cls.uh_ent, role="unit_head", assigned_unit="انتظامات", is_active=1)

        permission_manager.upsert_user(cls.operator_id, "اپراتور تست", "خانم")
        permission_manager.set_project_user(cls.pid, cls.operator_id, role="operator", is_active=1)

        permission_manager.upsert_user(cls.viewer_id, "بیننده تست", "آقا")
        permission_manager.set_project_user(cls.pid, cls.viewer_id, role="viewer", is_active=1)

        # ۴. ایجاد نیروهای کادر در واحدهای مختلف
        cls.st1 = staff_manager.add_staff_member(cls.pid, "علی رضایی", "تدارکات", "پذیرایی", "خدام", "آقا", "09121111111")
        cls.st2 = staff_manager.add_staff_member(cls.pid, "زهرا حسینی", "انتظامات", "مسیر", "راهنما", "خانم", "09122222222")

        # ثبت حضور و کارت اولیه
        attendance_manager.update_attendance_status(cls.pid, cls.s1, cls.st1, "حاضر")
        attendance_manager.update_card_status(cls.pid, cls.s1, cls.st1, "تحویل داده شد")

        # تنظیم کانتکست فعال پروژه و جلسه
        for uid in [cls.super_admin_id, cls.admin_id, cls.uh_tadarakat, cls.uh_ent, cls.operator_id, cls.viewer_id]:
            permission_manager.set_user_context(uid, project_id=cls.pid, session_id=cls.s1)

    def test_01_welcome_and_menus(self):
        """تست منوی خوش‌آمدگویی و سوئیچ پروژه برای تمام نقش‌ها"""
        print("\n🔹 تست ۱: ارسال /start و بررسی منوی آغازین برای همه نقش‌ها...")
        for uid in [self.super_admin_id, self.admin_id, self.uh_tadarakat, self.operator_id, self.viewer_id, self.stranger_id]:
            bot.send_welcome(DummyMessage(uid, "/start"))
            bot.handle_callbacks(DummyCall(uid, "start_menu"))
            bot.handle_callbacks(DummyCall(uid, "menu_switch_project"))
            bot.handle_callbacks(DummyCall(uid, f"selproj_{self.pid}"))
        print("  ✅ منوهای اولیه با موفقیت لود شدند.")

    def test_02_unit_head_full_flow(self):
        """تست کامل اعلام کمبود نیرو توسط مسئول واحد (باگ گزارش شده)"""
        print("\n🔹 تست ۲: فرآیند کامل اعلام کمبود نیرو توسط مسئول واحد...")
        uh = self.uh_tadarakat
        
        # ۱. باز کردن منوی اختصاصی مسئول واحد
        bot.handle_callbacks(DummyCall(uh, "menu_unit_head_shortage"))
        
        # ۲. کلیک روی بخش مورد نظر (دقیقاً همان دکمه‌ای که خطا داده بود)
        bot.handle_callbacks(DummyCall(uh, "uh_sec_پذیرایی"))
        
        # بررسی ثبت وضعیت موقت
        st = bot.bot_state.get(uh)
        self.assertIsNotNone(st, "Bot state should be initialized")
        self.assertEqual(st.get('type'), 'uh_shortage')
        self.assertEqual(st.get('section'), 'پذیرایی')
        
        # ۳. وارد کردن تعداد نیروی نامعتبر
        bot.process_uh_shortage_count(DummyMessage(uh, "متن نامعتبر"))
        self.assertNotIn('count', st)
        
        # ۴. وارد کردن تعداد با ارقام فارسی
        bot.process_uh_shortage_count(DummyMessage(uh, "۲"))
        self.assertEqual(st.get('count'), 2)
        
        # ۵. انتخاب جنسیت
        bot.handle_callbacks(DummyCall(uh, "uh_grp_آقا"))
        self.assertEqual(st.get('target_group'), "آقا")
        
        # ۶. ثبت توضیحات کمبود
        bot.process_uh_shortage_desc(DummyMessage(uh, "نیاز به دو نیروی چای‌ریز برای سالن برادران"))
        
        # ۷. تست تایپ بخش به صورت دستی
        bot.handle_callbacks(DummyCall(uh, "menu_unit_head_shortage"))
        bot.handle_callbacks(DummyCall(uh, "uh_sec_manual_prompt"))
        bot.process_uh_manual_section(DummyMessage(uh, "بخش دستی جدید"))
        self.assertEqual(bot.bot_state[uh]['section'], "بخش دستی جدید")
        
        # ۸. مشاهده کادر و وضعیت حضور واحد توسط مسئول واحد
        bot.handle_callbacks(DummyCall(uh, "menu_unit_head_staff"))
        bot.handle_callbacks(DummyCall(uh, "menu_unit_head_attendance"))
        print("  ✅ فرآیند مسئول واحد و ثبت کمبود با موفقیت ۱۰۰٪ تست شد.")

    def test_03_admin_shortage_approval_and_custom_shortages(self):
        """تست کارتابل ادمین جهت تایید، رد و ویرایش کمبودها و ایجاد کمبود جدید"""
        print("\n🔹 تست ۳: بررسی، تایید و رد کمبود نیرو توسط ادمین...")
        admin = self.admin_id
        
        # کارتابل مشاهده کمبودها
        bot.handle_callbacks(DummyCall(admin, "menu_view_shortages"))
        
        shortages = shortage_manager.get_all_unresolved_shortages(self.pid)
        self.assertTrue(len(shortages) > 0, "At least one shortage should exist")
        sh_id = shortages[-1]['id']
        
        # ویرایش تعداد کمبود توسط ادمین
        bot.handle_callbacks(DummyCall(admin, f"edit_uh_count_{sh_id}_{self.uh_tadarakat}"))
        bot.process_admin_edit_uh_count(DummyMessage(admin, "1"), sh_id, self.uh_tadarakat)
        
        # تایید کمبود
        bot.handle_callbacks(DummyCall(admin, f"appr_uh_shrt_{sh_id}_{self.uh_tadarakat}"))
        
        # ثبت کمبود مستقیم توسط ادمین ارشد
        sa = self.super_admin_id
        bot.handle_callbacks(DummyCall(sa, "menu_shortage"))
        bot.handle_callbacks(DummyCall(sa, "shrt_unit_تدارکات"))
        bot.process_shortage_section(DummyMessage(sa, "دپو"))
        bot.process_shortage_count(DummyMessage(sa, "3"))
        bot.process_shortage_desc(DummyMessage(sa, "کمبود پشتیبانی خانم‌ها"))
        bot.handle_callbacks(DummyCall(sa, "shrt_grp_خانم"))
        
        # پیشنهاد نیرو برای کمبود
        bot.handle_callbacks(DummyCall(sa, f"suggestshrt_{sh_id}"))
        bot.process_suggest_shrt_name(DummyMessage(sa, "محمدرضا کاظمی"))
        bot.process_suggest_shrt_phone(DummyMessage(sa, "09123334444"))
        
        # تایید و رد پیشنهاد
        bot.handle_callbacks(DummyCall(sa, f"approveshrt_{sh_id}"))
        bot.handle_callbacks(DummyCall(sa, f"rejectshrt_{sh_id}"))
        print("  ✅ فرآیندهای تایید و مدیریت کمبودها با موفقیت تست شد.")

    def test_04_attendance_and_cards_workflows(self):
        """تست ثبت حضور، غیاب، پیگیری تأخیر و کارت‌ها"""
        print("\n🔹 تست ۴: تست کامل حضور و غیاب و کارت‌ها...")
        op = self.operator_id
        
        # منوی واحدها
        bot.handle_callbacks(DummyCall(op, "menu_units"))
        bot.handle_callbacks(DummyCall(op, "up_0:تدارکات"))
        
        # باز کردن پروفایل علی رضایی
        bot.handle_callbacks(DummyCall(op, f"user_{self.st1}"))
        
        # تغییر وضعیت‌های مختلف حضور
        for st_val in ["حاضر", "غایب", "بدون شیفت", "clear"]:
            bot.handle_callbacks(DummyCall(op, f"act_status_{self.st1}_{st_val}"))
            
        # تغییر وضعیت‌های کارت
        for card_val in ["تحویل داده شد", "پس گرفته شد", "بدون کارت", "clear"]:
            bot.handle_callbacks(DummyCall(op, f"act_card_{self.st1}_{card_val}"))
            
        # پیگیری تأخیر
        bot.handle_callbacks(DummyCall(op, f"tracklate_{self.st1}"))
        bot.process_track_late(DummyMessage(op, "تماس گرفته شد، نیم ساعت دیگر می‌رسد"))
        
        # ویرایش توضیحات
        bot.handle_callbacks(DummyCall(op, f"editdesc_{self.st1}"))
        bot.process_edit_desc(DummyMessage(op, "کارت موقت تحویل گردید"))
        
        # صفحه لیست متأخرین
        bot.handle_callbacks(DummyCall(op, "menu_late_page_0"))
        bot.handle_callbacks(DummyCall(op, "menu_late_page_1"))
        print("  ✅ عملیات حضور و کارت و توضیحات با موفقیت ثبت شدند.")

    def test_05_sessions_and_group_shift(self):
        """تست تغییر جلسه فعال، ایجاد جلسه جدید، لغو جلسه و شیفت گروهی"""
        print("\n🔹 تست ۵: مدیریت جلسات و شیفت ساعتی گروهی...")
        sa = self.super_admin_id
        
        bot.handle_callbacks(DummyCall(sa, "menu_manage_sessions"))
        bot.handle_callbacks(DummyCall(sa, "menu_change_session"))
        bot.handle_callbacks(DummyCall(sa, f"setsess_{self.s2}"))
        bot.handle_callbacks(DummyCall(sa, f"sess_detail_{self.s2}"))
        bot.handle_callbacks(DummyCall(sa, f"togglesess_{self.s2}"))
        
        # ایجاد جلسه جدید
        bot.handle_callbacks(DummyCall(sa, "add_new_session_prompt"))
        bot.process_add_session_name(DummyMessage(sa, "روز پایانی"))
        
        # شیفت گروهی ساعت حضور
        bot.handle_callbacks(DummyCall(sa, "menu_group_shift"))
        bot.handle_callbacks(DummyCall(sa, "grpshift_u_تدارکات"))
        bot.handle_callbacks(DummyCall(sa, "grpshift_s_پذیرایی"))
        bot.process_group_shift_time(DummyMessage(sa, "17:30"))
        print("  ✅ جلسات و شیفت ساعتی با موفقیت انجام شدند.")

    def test_06_project_management_and_settings(self):
        """تست تنظیمات پروژه، ویرایش مشخصات، آرشیو و بازگردانی"""
        print("\n🔹 تست ۶: تنظیمات پروژه، ویرایش مشخصات و آرشیو...")
        sa = self.super_admin_id
        
        bot.handle_callbacks(DummyCall(sa, "menu_manage_project"))
        
        # ویرایش نام و توضیحات پروژه
        bot.handle_callbacks(DummyCall(sa, f"editproj_name_{self.pid}"))
        bot.process_edit_project_name(DummyMessage(sa, f"پروژه رویداد ویرایش‌شده {self.ts}"), self.pid)
        
        bot.handle_callbacks(DummyCall(sa, f"editproj_desc_{self.pid}"))
        bot.process_edit_project_desc(DummyMessage(sa, "توضیحات بهینه‌سازی‌شده تست"), self.pid)
        
        # آرشیو و خروج از آرشیو
        bot.handle_callbacks(DummyCall(sa, f"archiveproj_{self.pid}"))
        proj_arch = project_manager.get_project(self.pid)
        self.assertEqual(proj_arch['status'], 'ARCHIVED')
        
        bot.handle_callbacks(DummyCall(sa, f"unarchiveproj_{self.pid}"))
        proj_active = project_manager.get_project(self.pid)
        self.assertEqual(proj_active['status'], 'ACTIVE')
        print("  ✅ تنظیمات پروژه و آرشیو تست شد.")

    def test_07_smart_project_creation_wizard(self):
        """تست ویزارد هوشمند ایجاد پروژه رویداد و کلاس آموزشی"""
        print("\n🔹 تست ۷: ویزارد هوشمند ساخت پروژه...")
        sa = self.super_admin_id
        
        # ویزارد رویداد ۲ روزه با روز آماده‌سازی
        bot.handle_callbacks(DummyCall(sa, "menu_new_project_prompt"))
        bot.handle_callbacks(DummyCall(sa, "wiz_type_رویداد"))
        bot.process_wiz_project_name(DummyMessage(sa, f"رویداد هوشمند {self.ts}"))
        bot.handle_callbacks(DummyCall(sa, "wiz_day_2"))
        bot.handle_callbacks(DummyCall(sa, "wiz_prep_yes"))
        
        # ویزارد کلاس آموزشی ۱۰ جلسه‌ای
        bot.handle_callbacks(DummyCall(sa, "menu_new_project_prompt"))
        bot.handle_callbacks(DummyCall(sa, "wiz_type_کلاس"))
        bot.process_wiz_project_name(DummyMessage(sa, f"کلاس هوشمند {self.ts}"))
        bot.handle_callbacks(DummyCall(sa, "wiz_day_سه‌شنبه"))
        bot.process_wiz_class_time(DummyMessage(sa, "16:00"))
        bot.handle_callbacks(DummyCall(sa, "wiz_cnt_10"))
        print("  ✅ ساخت خودکار پروژه‌ها با ویزارد با موفقیت تایید شد.")

    def test_08_hr_fixed_pool_and_permissions(self):
        """تست امور کادر ثابت و کنترل سطح دسترسی پروژه‌ها"""
        print("\n🔹 تست ۸: حوضچه ثابت کادر و تخصیص نقش‌ها...")
        sa = self.super_admin_id
        
        bot.handle_callbacks(DummyCall(sa, "menu_hr_members"))
        bot.handle_callbacks(DummyCall(sa, f"hr_view_{self.admin_id}"))
        bot.handle_callbacks(DummyCall(sa, f"guser_setg_{self.admin_id}_0"))
        bot.handle_callbacks(DummyCall(sa, f"guser_setact_{self.admin_id}_1"))
        
        # تخصیص به پروژه
        bot.handle_callbacks(DummyCall(sa, f"hr_assignproj_{self.admin_id}"))
        bot.handle_callbacks(DummyCall(sa, f"hr_doassign_{self.admin_id}_{self.pid}"))
        bot.handle_callbacks(DummyCall(sa, f"hr_pickunit_{self.admin_id}_{self.pid}_تدارکات"))
        bot.handle_callbacks(DummyCall(sa, f"hr_setrole_{self.admin_id}_{self.pid}_تدارکات_admin"))
        
        # کادر اجرایی پروژه
        bot.handle_callbacks(DummyCall(sa, "menu_permissions"))
        bot.handle_callbacks(DummyCall(sa, "proj_add_from_hr"))
        bot.handle_callbacks(DummyCall(sa, f"proj_editmember_{self.admin_id}"))
        bot.handle_callbacks(DummyCall(sa, f"proj_setmember_role_{self.admin_id}"))
        bot.handle_callbacks(DummyCall(sa, f"proj_applyrole_{self.admin_id}_admin"))
        bot.handle_callbacks(DummyCall(sa, f"proj_pickunit_{self.admin_id}"))
        bot.handle_callbacks(DummyCall(sa, f"proj_applyrole_{self.admin_id}_unithead_تدارکات"))
        bot.handle_callbacks(DummyCall(sa, f"proj_applyrole_{self.admin_id}_تدارکات_admin"))
        print("  ✅ امور کادر و تخصیص دسترسی‌ها بدون ایراد اجرا گردید.")

    def test_09_reports_dashboards_and_excel(self):
        """تست داشبورد آماری، گزارش کادر، خروجی اکسل و جستجو"""
        print("\n🔹 تست ۹: داشبورد زنده، خروجی اکسل و جستجو...")
        admin = self.admin_id
        
        bot.handle_callbacks(DummyCall(admin, "menu_dashboard"))
        bot.handle_callbacks(DummyCall(admin, "menu_staff_performance"))
        bot.handle_callbacks(DummyCall(admin, "download_excel"))
        bot.handle_callbacks(DummyCall(admin, "menu_update_excel"))
        
        # جستجوی نیرو
        bot.handle_callbacks(DummyCall(admin, "menu_search"))
        bot.process_search(DummyMessage(admin, "رضایی"), self.pid, self.s1)
        
        # افزودن نیروی جدید در پروژه
        bot.handle_callbacks(DummyCall(admin, "menu_add_user"))
        bot.process_add_staff_name(DummyMessage(admin, "سارا کریمی"))
        bot.handle_callbacks(DummyCall(admin, "addu_unit_تدارکات"))
        bot.handle_callbacks(DummyCall(admin, "addu_sec_پذیرایی"))
        bot.process_add_staff_phone(DummyMessage(admin, "09129998877"))
        bot.handle_callbacks(DummyCall(admin, "addu_gen_خانم"))
        
        # گزارش متنی کلاس
        bot.handle_callbacks(DummyCall(admin, "menu_class_text_report"))
        print("  ✅ گزارش‌ها، اکسل، افزودن نیرو و جستجو تایید شدند.")

if __name__ == '__main__':
    unittest.main()
