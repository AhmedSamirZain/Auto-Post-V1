"""
i18n.py — نظام الترجمة (عربي/إنجليزي)
=====================================
يوفّر دالة t(key, lang) لترجمة نصوص الواجهة الأساسية.
النصوص غير المترجمة ترجع للعربية افتراضياً.
"""

TRANSLATIONS = {
    # ── القوائم الرئيسية (أزرار الكيبورد) ──
    "kb_home":      {"ar": "🏠 الرئيسية",   "en": "🏠 Home"},
    "kb_accounts":  {"ar": "👤 الحسابات",   "en": "👤 Accounts"},
    "kb_groups":    {"ar": "👥 المجموعات",  "en": "👥 Groups"},
    "kb_pages":     {"ar": "📄 الصفحات",    "en": "📄 Pages"},
    "kb_campaigns": {"ar": "🚀 الحملات",    "en": "🚀 Campaigns"},
    "kb_comments":  {"ar": "💬 التعليقات",  "en": "💬 Comments"},
    "kb_plan":      {"ar": "💎 خطتي",        "en": "💎 My Plan"},
    "kb_tools":     {"ar": "🧰 الأدوات",     "en": "🧰 Tools"},
    "kb_admin":     {"ar": "⚙️ لوحة التحكم", "en": "⚙️ Admin Panel"},
    "kb_back":      {"ar": "🔙 رجوع",        "en": "🔙 Back"},

    # ── عناوين الشاشات ──
    "main_menu":    {"ar": "القائمة الرئيسية", "en": "Main Menu"},
    "accounts_title": {"ar": "إدارة الحسابات", "en": "Account Management"},
    "groups_title": {"ar": "إدارة المجموعات",  "en": "Group Management"},
    "campaigns_title": {"ar": "الحملات",        "en": "Campaigns"},

    # ── رسائل عامة ──
    "choose_menu":  {"ar": "اختر من القائمة 👇", "en": "Choose from the menu 👇"},
    "welcome_back": {"ar": "أهلاً", "en": "Welcome"},
    "plan_label":   {"ar": "الخطة", "en": "Plan"},
    "accounts_count": {"ar": "الحسابات", "en": "Accounts"},
    "no_account":   {"ar": "⚠️ يجب ربط حساب أولاً!", "en": "⚠️ You must link an account first!"},
    "done":         {"ar": "✅ تم", "en": "✅ Done"},
    "cancelled":    {"ar": "❌ تم الإلغاء", "en": "❌ Cancelled"},
    "error":        {"ar": "❌ حدث خطأ", "en": "❌ An error occurred"},
    "maintenance":  {"ar": "🔧 البوت تحت الصيانة حالياً.\nبرجاء المحاولة لاحقاً 🙏",
                     "en": "🔧 The bot is under maintenance.\nPlease try again later 🙏"},

    # ── الأزرار الشائعة ──
    "btn_new_campaign": {"ar": "🆕 حملة جديدة", "en": "🆕 New Campaign"},
    "btn_upgrade":  {"ar": "💎 ترقية الخطة", "en": "💎 Upgrade Plan"},
    "btn_post_now": {"ar": "🚀 نشر الآن", "en": "🚀 Post Now"},
    "btn_schedule": {"ar": "⏰ جدولة لاحقاً", "en": "⏰ Schedule"},
    "btn_cancel":   {"ar": "❌ إلغاء", "en": "❌ Cancel"},
    "btn_confirm":  {"ar": "✅ تأكيد", "en": "✅ Confirm"},
}


def t(key: str, lang: str = "ar") -> str:
    """يترجم مفتاحاً للغة المطلوبة. يرجع العربية لو الترجمة غير موجودة."""
    entry = TRANSLATIONS.get(key)
    if not entry:
        return key
    return entry.get(lang) or entry.get("ar") or key
