"""
settings.py — جسر الإعدادات الديناميكية
========================================
يقرأ القيم القابلة للتعديل من قاعدة البيانات (لوحة الأدمن)،
ويرجع لقيمة config.py الافتراضية لو مفيش قيمة محفوظة.

استخدمه بدل config مباشرة للقيم اللي الأدمن بيعدّلها:
    await S.get("pro_price", config.SUBSCRIPTION_PACKAGES)
"""
import database as db
import config

# القيم القابلة للتعديل من لوحة الأدمن + افتراضياتها من config
# المفتاح في DB -> (وصف عربي, القيمة الافتراضية)
EDITABLE = {
    "bot_name":        ("اسم البوت", config.BOT_NAME),
    "bot_tagline":     ("وصف البوت", config.BOT_TAGLINE),
    "support_username":("يوزر الدعم", config.SUPPORT_USERNAME),
    "channel_username":("يوزر القناة", config.CHANNEL_USERNAME),
    "payment_name":    ("اسم محفظة الدفع", config.PAYMENT_NAME),
    "vodafone_cash":   ("رقم فودافون كاش", config.VODAFONE_CASH or ""),
    "instapay":        ("عنوان إنستاباي", config.INSTAPAY_ADDRESS or ""),
    "currency":        ("العملة", config.CURRENCY),
    # حدود الخطط
    "free_accounts":   ("مجاني: عدد الحسابات", config.PLAN_LIMITS["free"]["max_accounts"]),
    "free_groups":     ("مجاني: عدد المجموعات", config.PLAN_LIMITS["free"]["max_groups"]),
    "free_campaigns":  ("مجاني: حملات/يوم", config.PLAN_LIMITS["free"]["max_campaigns"]),
    "pro_accounts":    ("Pro: عدد الحسابات", config.PLAN_LIMITS["pro"]["max_accounts"]),
    "pro_groups":      ("Pro: عدد المجموعات", config.PLAN_LIMITS["pro"]["max_groups"]),
    "pro_campaigns":   ("Pro: حملات/يوم", config.PLAN_LIMITS["pro"]["max_campaigns"]),
    "unl_accounts":    ("Unlimited: عدد الحسابات", config.PLAN_LIMITS["unlimited"]["max_accounts"]),
    "unl_groups":      ("Unlimited: عدد المجموعات", config.PLAN_LIMITS["unlimited"]["max_groups"]),
    "unl_campaigns":   ("Unlimited: حملات/يوم", config.PLAN_LIMITS["unlimited"]["max_campaigns"]),
    # النقاط
    "points_referral": ("نقاط دعوة صديق", config.POINTS["referral"]),
    "points_account":  ("نقاط ربط حساب", config.POINTS["add_account"]),
    "points_campaign": ("نقاط إتمام حملة", config.POINTS["complete_camp"]),
    # التجربة المجانية
    "trial_plan":      ("خطة التجربة", config.TRIAL_PLAN),
    "trial_days":      ("أيام التجربة", config.TRIAL_DAYS),
    # الحماية
    "default_delay":   ("الفاصل الافتراضي (ث)", config.DEFAULT_DELAY_SECONDS),
    "min_between_camp":("أقل فاصل بين الحملات (ث)", config.MIN_SECONDS_BETWEEN_CAMPAIGNS),
    "max_concurrent":  ("أقصى حملات متزامنة", config.MAX_CONCURRENT_CAMPAIGNS),
    # التشغيل
    "maintenance_mode":("وضع الصيانة (1=مغلق)", 0),
    "welcome_bonus":   ("نقاط ترحيب للمستخدم الجديد", 0),
}


async def get(key: str):
    """يرجّع قيمة الإعداد من DB أو الافتراضي من config."""
    if key not in EDITABLE:
        return await db.get_setting(key)
    _, default = EDITABLE[key]
    return await db.get_setting(key, default)


async def set(key: str, value):
    await db.set_setting(key, value)


async def plan_limits() -> dict:
    """يبني PLAN_LIMITS ديناميكياً من الإعدادات المحفوظة."""
    base = {k: dict(v) for k, v in config.PLAN_LIMITS.items()}
    base["free"]["max_accounts"]  = int(await get("free_accounts"))
    base["free"]["max_groups"]    = int(await get("free_groups"))
    base["free"]["max_campaigns"] = int(await get("free_campaigns"))
    base["pro"]["max_accounts"]   = int(await get("pro_accounts"))
    base["pro"]["max_groups"]     = int(await get("pro_groups"))
    base["pro"]["max_campaigns"]  = int(await get("pro_campaigns"))
    base["unlimited"]["max_accounts"]  = int(await get("unl_accounts"))
    base["unlimited"]["max_groups"]    = int(await get("unl_groups"))
    base["unlimited"]["max_campaigns"] = int(await get("unl_campaigns"))
    return base


async def is_maintenance() -> bool:
    return bool(int(await get("maintenance_mode") or 0))
