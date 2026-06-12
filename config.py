# ============================================================
#  config.py — Configuration / الإعدادات
#  All editable values in one place / كل القيم القابلة للتعديل
# ============================================================
#
#  Sections / الأقسام:
#    1. Bot & Admin  —  البوت والمشرف
#    2. Payment      —  معلومات الدفع
#    3. Links        —  روابط الدعم والقناة
#    4. Plan Limits  —  حدود الخطط (مجاني / Pro / Unlimited)
#    5. Anti-Ban     —  إعدادات الحماية من الحظر
#    6. Telegram     —  حدود التيليجرام
#
#  Usage / الاستخدام:
#    from config import BOT_TOKEN, ADMIN_ID, ...
#    All values read from .env file or environment variables
#    / كل القيم تقرأ من ملف .env أو متغيرات البيئة
# ============================================================

import os
from dotenv import load_dotenv

# قراءة من .env (لو موجود) — لا ينشئه تلقائيًا
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    load_dotenv(_env_path)

# لو على Streamlit Cloud: جرب st.secrets
try:
    import streamlit as st
    _st_secrets = st.secrets if hasattr(st, "secrets") else {}
except (ImportError, Exception):
    _st_secrets = {}

def _secret(key: str):
    v = os.getenv(key) or _st_secrets.get(key)
    if v and not isinstance(v, str):
        v = str(v)
    return v

# ── 1. Bot & Admin / البوت والمشرف ─────────────────────────
BOT_TOKEN    = _secret("BOT_TOKEN")
try:
    ADMIN_ID = int((_secret("ADMIN_ID") or "0").strip() or "0")
except (ValueError, AttributeError):
    ADMIN_ID = 0
BOT_NAME    = "Auto Post Bot"
BOT_VERSION = "2.0"

# ── معلومات الدفع ─────────────────────────────────────────
INSTAPAY_ADDRESS = _secret("INSTAPAY_ADDRESS")
VODAFONE_CASH    = _secret("VODAFONE_CASH")# محفظة فودافون كاش
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
