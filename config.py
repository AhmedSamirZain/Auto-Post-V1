# ============================================================
#  config.py — كل القيم القابلة للتعديل في مكان واحد
# ============================================================
import os

# ── معلومات البوت والأدمن ──────────────────────────────────
BOT_TOKEN   = os.getenv("BOT_TOKEN", "")
ADMIN_ID    = int(os.getenv("ADMIN_ID", "941670953"))
BOT_NAME    = "Auto Post Bot"
BOT_VERSION = "2.0"

# ── معلومات الدفع ─────────────────────────────────────────
INSTAPAY_NUMBER   = "01XXXXXXXXX"      # رقم إنستاباي
VODAFONE_CASH     = "01XXXXXXXXX"      # محفظة فودافون كاش
PAYMENT_NAME      = "محمد ..."         # اسم صاحب المحفظة

# ── روابط ─────────────────────────────────────────────────
SUPPORT_USERNAME  = "@AutoPostSupport"
CHANNEL_USERNAME  = "@AutoPostChannel"

# ── حدود الخطط ────────────────────────────────────────────
PLAN_LIMITS = {
    "free": {
        "label":        "🆓 مجاني",
        "max_accounts": 1,
        "max_groups":   30,
        "max_campaigns": 2,
        "duration_days": 0,
        "price":        "مجاني",
    },
    "pro": {
        "label":        "⭐ Pro",
        "max_accounts": 3,
        "max_groups":   300,
        "max_campaigns": 20,
        "duration_days": 30,
        "price":        "99 جنيه / شهر",
    },
    "unlimited": {
        "label":        "👑 Unlimited",
        "max_accounts": 10,
        "max_groups":   999999,
        "max_campaigns": 999,
        "duration_days": 30,
        "price":        "199 جنيه / شهر",
    },
}

# ── إعدادات الحماية من الحظر ──────────────────────────────
DEFAULT_DELAY_SECONDS = 60        # الفاصل الافتراضي بين المنشورات
DEFAULT_ANTIBAN_LEVEL = "medium"  # low / medium / high

# ── حدود Telegram ─────────────────────────────────────────
MAX_MESSAGE_LENGTH = 4096
MAX_INLINE_BUTTONS  = 100
