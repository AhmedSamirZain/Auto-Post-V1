"""
Auto Post Bot v2.0 — Handlers
UI مطابق لبوت VoltCast GroupFB
"""
import os
import json
import re
import random
import secrets
import asyncio
import logging
from datetime import datetime, timedelta
import pytz

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters,
)
from telegram.constants import ParseMode

import database as db
import settings as S
import i18n
from config import (
    ADMIN_ID, PLAN_LIMITS, PAYMENT_NAME, INSTAPAY_ADDRESS, VODAFONE_CASH,
    SUPPORT_USERNAME, CHANNEL_USERNAME, BOT_NAME, BOT_TAGLINE, CURRENCY,
    SUBSCRIPTION_PACKAGES, POINTS, POINTS_REDEEM, TRIAL_PLAN, TRIAL_DAYS,
    DELAY_OPTIONS, DEFAULT_DELAY_SECONDS, DEFAULT_ANTIBAN_LEVEL, TIMEZONE,
    MIN_SECONDS_BETWEEN_CAMPAIGNS, MAX_CONCURRENT_CAMPAIGNS,
    QUICK_TEMPLATES,
)

# تتبّع الحملات النشطة ووقت آخر حملة لكل مستخدم (لمنع الإفراط)
import time as _time_mod
_last_campaign_time = {}
_active_campaigns = {}

logger = logging.getLogger(__name__)


def _escape_md(text: str) -> str:
    """Strip Markdown v1 special chars from user-generated content (v1 has no escape)."""
    if not text:
        return ""
    return str(text).replace("_", " ").replace("*", "").replace("`", "'").replace("[", "(").replace("]", ")")


def _strip_md(text: str) -> str:
    """إزالة كل رموز الماركداون لإرسال النص كنص عادي عند فشل الـ parse."""
    if not text:
        return ""
    return str(text).replace("*", "").replace("`", "").replace("_", " ")


def _clip(text: str, limit: int = 4000) -> str:
    """يقصّ النص لو تجاوز حد تليجرام (4096) لتجنّب أخطاء الإرسال."""
    if text and len(text) > limit:
        return text[:limit - 20] + "\n… (تم الاختصار)"
    return text


def _acc_name(acc: dict) -> str:
    """اسم الحساب بشكل آمن — يمنع KeyError 'name' نهائياً."""
    if not acc:
        return "حساب"
    name = acc.get("account_name") or acc.get("name") or ""
    name = str(name).strip()
    return name if name else "حساب"


# ── فحص حدود الخطة (Plan Limits Enforcement) ──
async def _notify_user(context, uid: int, text: str):
    """يرسل إشعاراً للمستخدم فقط لو الإشعارات مفعّلة عنده."""
    try:
        user = await db.get_user(uid)
        if user and not user.get("notifications", 1):
            return  # الإشعارات مُطفأة
        await context.bot.send_message(uid, text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        try:
            await context.bot.send_message(uid, _strip_md(text))
        except Exception:
            pass


async def _get_limits(uid: int) -> dict:
    """يرجّع حدود خطة المستخدم الحالية (ديناميكي من لوحة الأدمن)."""
    user = await db.get_user(uid)
    plan = user.get("plan", "free") if user else "free"
    limits = await S.plan_limits()
    return limits.get(plan, limits["free"])


async def _check_account_limit(uid: int) -> tuple:
    """يتأكد إن المستخدم لسه يقدر يضيف حساب. يرجّع (مسموح, رسالة)."""
    limits = await _get_limits(uid)
    current = len(await db.get_accounts(uid))
    if current >= limits["max_accounts"]:
        return False, (
            f"🔒 *وصلت للحد الأقصى من الحسابات*\n\n"
            f"خطتك تسمح بـ *{limits['max_accounts']}* حساب فقط.\n"
            f"رقّ خطتك لربط حسابات أكثر."
        )
    return True, ""


async def _check_groups_limit(uid: int, adding: int = 0) -> tuple:
    """يتأكد إن عدد المجموعات لسه ضمن الحد. يرجّع (مسموح, رسالة, المتبقي)."""
    limits = await _get_limits(uid)
    current = len(await db.get_groups(uid))
    remaining = limits["max_groups"] - current
    if remaining <= 0:
        return False, (
            f"🔒 *وصلت للحد الأقصى من المجموعات*\n\n"
            f"خطتك تسمح بـ *{limits['max_groups']}* مجموعة.\n"
            f"رقّ خطتك لإضافة المزيد."
        ), 0
    return True, "", remaining


async def _check_campaign_limit(uid: int) -> tuple:
    """يتأكد إن حملات اليوم لسه ضمن الحد. يرجّع (مسموح, رسالة)."""
    limits = await _get_limits(uid)
    camps = await db.get_campaigns(uid)
    today = datetime.now().strftime("%Y-%m-%d")
    today_count = sum(1 for c in camps if str(c.get("created_at", ""))[:10] == today)
    if today_count >= limits["max_campaigns"]:
        return False, (
            f"🔒 *وصلت للحد الأقصى من الحملات اليوم*\n\n"
            f"خطتك تسمح بـ *{limits['max_campaigns']}* حملة يومياً.\n"
            f"رقّ خطتك أو انتظر للغد."
        )
    return True, ""

# ── States ────────────────────────────────────────────────────────────────────
CAIRO_TZ = pytz.timezone(TIMEZONE)

(
    S_MAIN,
    S_ACCOUNTS, S_ACC_NAME, S_ACC_COOKIES, S_ACC_PROXY,
    S_GROUPS, S_GRP_SEARCH,
    S_PAGES, S_PAGE_POST, S_PAGE_STORY_IMG, S_PAGE_STORY_LINK,
    S_CAMPAIGNS, S_CAMP_CAPTION, S_CAMP_MEDIA, S_CAMP_TARGETS, S_CAMP_SCHEDULE,
    S_COMMENTS, S_CMT_URL, S_CMT_TEXT,
    S_MY_PLAN, S_ACTIVATE_CODE,
    S_TOOLS, S_SETTINGS,
    S_TEMPLATES, S_TPL_TITLE, S_TPL_CONTENT,
    S_ADMIN, S_ADMIN_BROADCAST, S_ADMIN_UID, S_ADMIN_PLAN, S_ADMIN_DAYS,
    S_ADMIN_PROMO,
    S_PAGE_BOT, S_PAGE_BOT_URL, S_PAGE_BOT_TPL, S_PAGE_BOT_KW,
    S_PAGE_BOT_RCMT, S_PAGE_BOT_RDM,
    S_SUB_SCREENSHOT,
    S_ADMIN_SETTING_VALUE,
    S_ADMIN_SEARCH, S_ADMIN_BROADCAST_PLAN,
    S_CAMP_AB_A, S_CAMP_AB_B,
    S_STORY_PAGES,
) = range(45)


def _cairo_now() -> datetime:
    return datetime.now(CAIRO_TZ)


def _parse_cairo_time(text: str):
    text = text.strip()
    m = re.match(r'^(\d{1,2}):(\d{2})$', text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        now = _cairo_now()
        dt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)
        return dt
    m2 = re.match(r'^(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})$', text)
    if m2:
        try:
            naive = datetime.strptime(f"{m2.group(1)} {m2.group(2)}:{m2.group(3)}", "%Y-%m-%d %H:%M")
            return CAIRO_TZ.localize(naive)
        except Exception:
            pass
    return None


def _format_cairo_dt(dt: datetime) -> str:
    MONTHS = ["يناير","فبراير","مارس","أبريل","مايو","يونيو",
               "يوليو","أغسطس","سبتمبر","أكتوبر","نوفمبر","ديسمبر"]
    DAYS   = ["الاثنين","الثلاثاء","الأربعاء","الخميس","الجمعة","السبت","الأحد"]
    day_name = DAYS[dt.weekday()]
    hour = dt.hour
    period = "صباحاً" if hour < 12 else "مساءً"
    h12 = hour % 12 or 12
    return f"{day_name} {dt.day} {MONTHS[dt.month-1]} الساعة {h12}:{dt.minute:02d} {period}"

# ── Keyboards ─────────────────────────────────────────────────────────────────

def _kb(rows, **kw):
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True, **kw)

MAIN_KB = _kb([
    ["🏠 الرئيسية"],
    ["👤 الحسابات",   "👥 المجموعات"],
    ["📄 الصفحات",   "🚀 الحملات"],
    ["💬 التعليقات", "💎 خطتي"],
    ["🧰 الأدوات"],
])

PAGES_KB = _kb([
    ["🖼 نشر",       "📚 ستوري"],
    ["🤖 بوت"],
    ["🔙 رجوع",      "🏠 الرئيسية"],
])

TOOLS_KB = _kb([
    ["⚙️ إعدادات البوت", "🌐 تغيير اللغة"],
    ["📦 مركز القوالب",  "🗂 سجل الحملات"],
    ["🔔 مركز التنبيهات","📋 سجل النشاط"],
    ["📊 مؤشرات الثقة",  "⭐ تقييم البوت"],
    ["🏠 الرئيسية"],
])

ADMIN_KB = _kb([
    ["🏠 الرئيسية"],
    ["👤 الحسابات",   "👥 المجموعات"],
    ["📄 الصفحات",   "🚀 الحملات"],
    ["💬 التعليقات", "💎 خطتي"],
    ["🧰 الأدوات"],
    ["⚙️ لوحة التحكم"],
])

# ── Inline helpers ─────────────────────────────────────────────────────────────

def ik(*rows):
    return InlineKeyboardMarkup(list(rows))

def btn(text, cb=None, url=None):
    if url:
        return InlineKeyboardButton(text, url=url)
    return InlineKeyboardButton(text, callback_data=cb)

def back_btn(cb="main"):
    return [btn("🔙 رجوع", cb), btn("🏠 الرئيسية", "main")]

async def _answer(update: Update, text="", alert=False):
    if update.callback_query:
        try:
            await update.callback_query.answer(text, show_alert=alert)
        except Exception:
            pass

async def _edit(update: Update, text: str, markup=None):
    """تعديل الرسالة مع fallback تلقائي: لو فشل Markdown يبعت نص عادي بدل ما يقع."""
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                _clip(text), parse_mode=ParseMode.MARKDOWN, reply_markup=markup
            )
            return
        except Exception as e:
            es = str(e).lower()
            # لو المشكلة إن الرسالة مش متغيرة، نتجاهلها
            if "not modified" in es:
                return
            # لو خطأ parsing، نحاول نبعت نص عادي
            if "parse" in es or "entity" in es or "entities" in es:
                try:
                    await update.callback_query.edit_message_text(
                        _strip_md(text), reply_markup=markup
                    )
                    return
                except Exception:
                    pass
            # أي خطأ تاني: نحاول نبعت رسالة جديدة
            try:
                await update.callback_query.message.reply_text(
                    _strip_md(text), reply_markup=markup
                )
            except Exception:
                pass
    else:
        try:
            await update.message.reply_text(
                _clip(text), parse_mode=ParseMode.MARKDOWN, reply_markup=markup
            )
        except Exception:
            try:
                await update.message.reply_text(_strip_md(text), reply_markup=markup)
            except Exception:
                pass

async def _send(update: Update, text: str, reply_kb=None, inline_kb=None, parse_mode=ParseMode.MARKDOWN):
    """إرسال رسالة مع fallback تلقائي عند فشل Markdown."""
    msg = update.message or update.callback_query.message
    markup = reply_kb or inline_kb
    try:
        await msg.reply_text(
            _clip(text),
            parse_mode=parse_mode if parse_mode else None,
            reply_markup=markup,
        )
    except Exception as e:
        es = str(e).lower()
        if "parse" in es or "entity" in es or "entities" in es:
            try:
                await msg.reply_text(_strip_md(text), reply_markup=markup)
                return
            except Exception:
                pass
        # محاولة أخيرة بدون أي تنسيق
        try:
            await msg.reply_text(_strip_md(text), reply_markup=markup)
        except Exception:
            pass


# ── Text dispatcher map ───────────────────────────────────────────────────────

MAIN_NAV = {
    "👤 الحسابات":   "accounts",
    "👥 المجموعات":  "groups",
    "📄 الصفحات":   "pages",
    "🚀 الحملات":    "campaigns",
    "💬 التعليقات": "comments",
    "💎 خطتي":      "my_plan",
    "🧰 الأدوات":   "tools",
    "🏠 الرئيسية":  "main",
    "🔙 رجوع":      "main",
    # Tools sub-nav
    "⚙️ إعدادات البوت": "settings",
    "🌐 تغيير اللغة":   "set_lang",
    "📦 مركز القوالب":  "templates",
    "🗂 سجل الحملات":   "camp_log",
    "🔔 مركز التنبيهات":"notifications",
    "📋 سجل النشاط":    "activity_log",
    "📊 مؤشرات الثقة":  "trust",
    "⭐ تقييم البوت":   "rate",
    # Pages sub-nav
    "🖼 نشر":   "page_post",
    "📚 ستوري": "page_story",
    "🤖 بوت":   "page_bot",
    # Admin
    "⚙️ لوحة التحكم": "admin_panel",
    # English keyboard keys
    "🏠 Home":      "main",
    "👤 Accounts":  "accounts",
    "👥 Groups":    "groups",
    "📄 Pages":     "pages",
    "🚀 Campaigns": "campaigns",
    "💬 Comments":  "comments",
    "💎 My Plan":   "my_plan",
    "🧰 Tools":     "tools",
    "⚙️ Admin Panel": "admin_panel",
}

# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # وضع الصيانة — يمنع غير الأدمن
    if user.id != ADMIN_ID and await S.is_maintenance():
        await _send(update,
            "🔧 *البوت تحت الصيانة حالياً*\n\n"
            "نقوم بتحديثات لتحسين الخدمة.\nبرجاء المحاولة لاحقاً 🙏")
        return S_MAIN
    args = context.args or []
    ref = None
    if args:
        try:
            ref = int(args[0])
            if ref == user.id:
                ref = None
        except ValueError:
            pass

    existing = await db.get_user(user.id)
    if not existing:
        await db.create_user(user.id, user.username, user.full_name, ref)
        if ref:
            u = await db.get_user(ref)
            if u:
                await db.update_user(ref, points=(u.get("points", 0) + int(await S.get("points_referral"))))
        await _send(update,
            f"🎉 *أهلاً بك يا {user.first_name}!*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"مرحباً في *{BOT_NAME}* ⚡\n"
            f"{BOT_TAGLINE}\n\n"
            f"✨ *ابدأ في 3 خطوات:*\n"
            f"  1️⃣ اربط حساب فيسبوك  👤\n"
            f"  2️⃣ اسحب مجموعاتك  👥\n"
            f"  3️⃣ أنشئ حملتك الأولى  🚀\n\n"
            f"🎁 خطتك الحالية: *مجاني* — جرّب وارتقِ وقت ما تحب.\n\n"
            f"اضغط الزر أدناه للبدء 👇",
            reply_kb=_get_main_kb(user.id)
        )
        await _send(update,
            "👇 *جاهز تبدأ؟*",
            inline_kb=ik(
                [btn("👤 اربط حسابك الآن", "accounts")],
                [btn("📖 شرح سريع", "onboard_help"), btn("💎 الباقات", "plan_upgrade")],
            )
        )
        return S_MAIN

    await _send_main_menu(update, context)
    return S_MAIN


MAIN_KB_EN = _kb([
    ["🏠 Home"],
    ["👤 Accounts",  "👥 Groups"],
    ["📄 Pages",     "🚀 Campaigns"],
    ["💬 Comments",  "💎 My Plan"],
    ["🧰 Tools"],
])

ADMIN_KB_EN = _kb([
    ["🏠 Home"],
    ["👤 Accounts",  "👥 Groups"],
    ["📄 Pages",     "🚀 Campaigns"],
    ["💬 Comments",  "💎 My Plan"],
    ["🧰 Tools"],
    ["⚙️ Admin Panel"],
])


def _get_main_kb(uid: int, lang: str = "ar") -> ReplyKeyboardMarkup:
    if lang == "en":
        return ADMIN_KB_EN if uid == ADMIN_ID else MAIN_KB_EN
    return ADMIN_KB if uid == ADMIN_ID else MAIN_KB


async def _send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = await db.get_user(uid)
    # تنبيه المستخدم لو اشتراكه انتهى توّاً
    if user and user.get("_just_expired"):
        try:
            await _send(update, "⏰ *انتهى اشتراكك* وتم تحويلك للخطة المجانية.\nرقّ خطتك للاستمرار 💎")
        except Exception:
            pass
    lang = (user.get("language") or "ar") if user else "ar"
    plan = _plan_label(user.get("plan", "free")) if user else "🆓 مجاني"
    limits = await _get_limits(uid)
    accounts = await db.get_accounts(uid)
    exp = _fmt_date(user.get("plan_expires")) if user else "—"
    now_cairo = _cairo_now().strftime("%H:%M")
    if lang == "en":
        text = (
            f"🏠 *Main Menu*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"👋 Welcome *{update.effective_user.first_name}*\n"
            f"Plan: {plan}"
            + (f" ┃ Expires: {exp}" if user and user.get("plan_expires") else "")
            + f"\n🔗 Accounts: {len(accounts)}/{limits['max_accounts']}\n"
            f"🕐 Cairo time: {now_cairo}\n\n"
            f"Choose from the menu 👇"
        )
    else:
        text = (
            f"🏠 *القائمة الرئيسية*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"👋 أهلاً *{update.effective_user.first_name}*\n"
            f"الخطة: {plan}"
            + (f" ┃ تنتهي: {exp}" if user and user.get("plan_expires") else "")
            + f"\n🔗 الحسابات: {len(accounts)}/{limits['max_accounts']}\n"
            f"🕐 توقيت القاهرة: {now_cairo}\n\n"
            f"اختر من القائمة 👇"
        )
    await _send(update, text, reply_kb=_get_main_kb(uid, lang))


async def cb_onboard_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شرح سريع للمستخدم الجديد."""
    await _answer(update)
    await _edit(update,
        "📖 *دليل الاستخدام السريع*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "👤 *الحسابات:* اربط حساب فيسبوك عن طريق الكوكيز "
        "(من إضافة Cookie-Editor في المتصفح).\n\n"
        "👥 *المجموعات:* اسحب مجموعاتك تلقائياً أو ابحث/ارفع قائمة.\n\n"
        "🚀 *الحملات:* اختر ميديا + كابشن + مجموعات → انشر الآن أو جدول لوقت لاحق.\n\n"
        "💬 *التعليقات:* علّق، ردّ، أو اعمل منشن للمتفاعلين تلقائياً.\n\n"
        "📄 *الصفحات:* انشر على صفحاتك + ستوري + بوت رد تلقائي.\n\n"
        "🧰 *الأدوات:* قوالب، إعدادات حماية، سجل نشاط، وأكثر.\n\n"
        "🛡️ *نصيحة:* خلي الفاصل الزمني 60 ثانية أو أكثر لحماية حسابك من الحظر.",
        ik([btn("👤 ابدأ بربط حساب", "accounts")], back_btn("main"))
    )
    return S_MAIN


async def cb_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زر الرئيسية = نفس وظيفة /start (يعيد ضبط كل شيء)."""
    await _answer(update)
    context.user_data.clear()
    await _send_main_menu(update, context)
    return S_MAIN


# ── Nav text router ───────────────────────────────────────────────────────────

async def nav_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    key = MAIN_NAV.get(txt)
    if key == "main":
        context.user_data.clear()

    handlers_map = {
        "main":          cmd_start,
        "accounts":      _show_accounts,
        "groups":        _show_groups,
        "pages":         _show_pages,
        "campaigns":     _show_campaigns,
        "comments":      _show_comments,
        "my_plan":       _show_my_plan,
        "tools":         _show_tools,
        "settings":      _show_settings,
        "set_lang":      _show_set_lang,
        "templates":     _show_templates,
        "camp_log":      _show_camp_log,
        "notifications": _show_notifications,
        "activity_log":  _show_activity_log,
        "trust":         _show_trust,
        "rate":          _show_rate,
        "page_post":     _show_page_post,
        "page_story":    _show_page_story,
        "page_bot":      _show_page_bot,
        "admin_panel":   _show_admin_from_kb,
    }
    fn = handlers_map.get(key)
    if fn:
        result = await fn(update, context)
        if result is not None:
            return result

    state_map = {
        "accounts":      S_ACCOUNTS,
        "groups":        S_GROUPS,
        "pages":         S_PAGES,
        "campaigns":     S_CAMPAIGNS,
        "comments":      S_COMMENTS,
        "my_plan":       S_MY_PLAN,
        "tools":         S_TOOLS,
        "settings":      S_SETTINGS,
        "templates":     S_TEMPLATES,
        "page_post":     S_PAGE_POST,
        "page_story":    S_PAGE_STORY_IMG,
        "page_bot":      S_PAGE_BOT,
        "admin_panel":   S_ADMIN,
    }
    return state_map.get(key, S_MAIN)


# ══════════════════════════════════════════════════════════════
#  ACCOUNTS
# ══════════════════════════════════════════════════════════════

async def _show_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    accounts = await db.get_accounts(uid)
    user = await db.get_user(uid)
    limits = PLAN_LIMITS.get(user.get("plan", "free"), PLAN_LIMITS["free"])

    text = (
        "👤 *إدارة الحسابات*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"الحسابات: *{len(accounts)}/{limits['max_accounts']}*\n\n"
    )

    rows = []
    for acc in accounts:
        rows.append([
            btn("🗑", f"acc_del_{acc['id']}"),
            btn("🔍", f"acc_check_{acc['id']}"),
            btn("❗", f"acc_report_{acc['id']}"),
            btn(f"✅ {_acc_name(acc)[:18]}", f"acc_detail_{acc['id']}"),
        ])

    if not accounts:
        text += "لم تقم بربط أي حساب بعد.\n\n🔴 ربط حساب واحد على الأقل مطلوب للبدء."

    rows.append([
        btn("🔍 فحص جميع الحسابات", "acc_check_all"),
        btn("➕ ربط حساب جديد",     "acc_add"),
    ])
    if accounts:
        rows.append([btn("🔐 نسخة احتياطية للحسابات", "acc_backup")])

    await _send(update, text, inline_kb=ik(*rows))
    return S_ACCOUNTS


async def cb_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    return await _show_accounts(update, context)


async def cb_acc_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    allowed, msg = await _check_account_limit(update.effective_user.id)
    if not allowed:
        await _edit(update, msg, ik([btn("💎 ترقية الخطة", "plan_upgrade")], back_btn("accounts")))
        return S_ACCOUNTS
    await _edit(update,
        "🍪 *ربط حساب فيسبوك جديد*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "✅ *صيغة نصية مقبولة:*\n"
        "`c_user=123456; xs=abc; datr=xyz`\n\n"
        "✅ *أو JSON* من إضافة Cookie-Editor\n\n"
        "⚠️ يجب أن تحتوي على `c_user` و `xs`\n\n"
        "سيتم جلب اسم الحساب تلقائياً من فيسبوك 🤖",
        ik(back_btn("accounts"))
    )
    return S_ACC_COOKIES


async def acc_got_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["acc_name"] = update.message.text.strip()
    await _send(update,
        "🍪 *أرسل كوكيز الحساب*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "✅ *صيغة نصية مقبولة:*\n"
        "`c_user=123456; xs=abc; datr=xyz`\n\n"
        "✅ *أو JSON* من إضافة Cookie-Editor\n\n"
        "⚠️ يجب أن تحتوي على `c_user` و `xs`",
        inline_kb=ik([btn("❌ إلغاء", "accounts")])
    )
    return S_ACC_COOKIES


async def acc_got_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils import validate_cookies, cookies_to_json, parse_cookie_string
    raw = update.message.text.strip()
    if not validate_cookies(raw):
        await _send(update,
            "❌ *صيغة الكوكيز غير صحيحة!*\n\n"
            "الصيغة الصحيحة:\n`c_user=123; xs=abc; datr=xyz`\n\nحاول مرة أخرى:"
        )
        return S_ACC_COOKIES

    context.user_data["acc_cookies"] = cookies_to_json(raw)
    parsed = parse_cookie_string(raw)
    has_cuser = any(c.get("name") == "c_user" for c in parsed)

    if not has_cuser:
        await _send(update,
            "⚠️ *تحذير:* لم يُعثر على `c_user`.\n"
            "قد لا تعمل بعض الميزات. هل تستمر؟",
            inline_kb=ik(
                [btn("✅ استمر على أي حال", "acc_continue"),
                 btn("❌ إعادة الإدخال",    "acc_retry")],
            )
        )
        return S_ACC_PROXY

    await _send(update,
        "🌐 *بروكسي؟ (اختياري)*\n\n"
        "الصيغة: `http://user:pass@host:port`\n"
        "أو اضغط تخطي:",
        inline_kb=ik([btn("⏭ تخطي", "acc_skip_proxy")])
    )
    return S_ACC_PROXY


async def cb_acc_continue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    await _edit(update,
        "🌐 *بروكسي؟ (اختياري)*\n\nأو اضغط تخطي:",
        ik([btn("⏭ تخطي", "acc_skip_proxy")])
    )
    return S_ACC_PROXY


async def cb_acc_skip_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    context.user_data["acc_proxy"] = None
    return await _save_account(update, context)


async def acc_got_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    val = update.message.text.strip()
    # حالة تغيير بروكسي حساب موجود
    proxy_acc_id = context.user_data.pop("proxy_acc_id", None)
    if proxy_acc_id is not None:
        new_proxy_val = None if val in ("حذف", "delete", "-") else val
        try:
            await db.update_account_proxy(proxy_acc_id, update.effective_user.id, new_proxy_val)
            await _send(update,
                "✅ *تم تحديث البروكسي.*" if new_proxy_val else "✅ *تم حذف البروكسي.*",
                inline_kb=ik(back_btn("accounts")))
        except Exception as e:
            await _send(update, f"❌ تعذّر التحديث: {_escape_md(str(e))}",
                inline_kb=ik(back_btn("accounts")))
        return S_ACCOUNTS
    # حالة ربط حساب جديد
    context.user_data["acc_proxy"] = val
    return await _save_account(update, context)


async def _save_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    cookies = context.user_data.get("acc_cookies", "[]")
    proxy   = context.user_data.get("acc_proxy")
    c_user  = _extract_cuser(cookies)

    await _send(update, "⏳ *جاري جلب اسم الحساب من فيسبوك...*")

    from fb_automator import get_account_name
    name = await get_account_name(cookies)

    acc_id = await db.add_account(uid, name, cookies, proxy)
    await db.log_activity(uid, "add_account", f"أضاف حساب: {name}")
    # منح نقاط ربط الحساب
    try:
        _u = await db.get_user(uid)
        await db.update_user(uid, points=(_u.get("points", 0) + int(await S.get("points_account"))))
    except Exception:
        pass

    await _send(update,
        f"✅ *تم ربط الحساب بنجاح!*\n\n"
        f"👤 الاسم: *{name}*\n"
        f"🆔 المعرف: `{c_user}`\n"
        f"🌐 البروكسي: {proxy or 'بدون'}\n\n"
        f"الآن اسحب مجموعاتك وصفحاتك 👇",
        inline_kb=ik(
            [btn("🗂 سحب المجموعات", f"acc_fetch_grp_{acc_id}"),
             btn("📄 سحب الصفحات",   f"acc_fetch_pg_{acc_id}")],
            [btn("👤 إدارة الحسابات", "accounts")],
        )
    )
    return S_ACCOUNTS


def _extract_cuser(cookies_json: str) -> str:
    try:
        lst = json.loads(cookies_json)
        for c in lst:
            if c.get("name") == "c_user":
                return c.get("value", "—")
    except Exception:
        pass
    return "—"


async def cb_acc_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    acc_id = int(update.callback_query.data.split("_")[-1])
    uid = update.effective_user.id
    accs = await db.get_accounts(uid)
    acc  = next((a for a in accs if a["id"] == acc_id), None)
    if not acc:
        return S_ACCOUNTS

    c_user = _extract_cuser(acc.get("cookies", "[]"))
    groups = await db.get_groups(uid, acc_id)
    pages  = await db.get_pages(uid)
    user   = await db.get_user(uid)
    limits = PLAN_LIMITS.get(user.get("plan","free"), PLAN_LIMITS["free"])

    await _edit(update,
        f"👤 *{_escape_md(_acc_name(acc))}*\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 المعرف: `{c_user}`\n"
        f"📊 الخطة: {_plan_label(user.get('plan','free'))} "
        f"({len(groups)}/{limits['max_groups']})\n"
        f"🌐 بروكسي: {_escape_md(acc.get('proxy') or 'بدون')}\n"
        f"📅 أُضيف: {_fmt_date(acc.get('added_at',''))}",
        ik(
            [btn("🗂 سحب المجموعات",   f"acc_fetch_grp_{acc_id}"),
             btn("📄 سحب الصفحات",     f"acc_fetch_pg_{acc_id}")],
            [btn("✏️ تغيير البروكسي",  f"acc_proxy_{acc_id}"),
             btn("🗑 حذف الحساب",      f"acc_del_{acc_id}")],
            back_btn("accounts"),
        )
    )
    return S_ACCOUNTS


async def cb_acc_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يطلب التأكيد قبل الحذف."""
    await _answer(update)
    acc_id = int(update.callback_query.data.split("_")[-1])
    uid = update.effective_user.id
    accs = await db.get_accounts(uid)
    acc = next((a for a in accs if a["id"] == acc_id), None)
    name = _acc_name(acc) if acc else "الحساب"
    await _edit(update,
        f"⚠️ *تأكيد الحذف*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"هل أنت متأكد من حذف الحساب:\n*{_escape_md(name)}*؟\n\n"
        f"سيتم حذف الحساب ومجموعاته المرتبطة. لا يمكن التراجع!",
        ik([btn("⚠️ نعم، احذف", f"acc_delok_{acc_id}"),
            btn("❌ إلغاء", "accounts")])
    )
    return S_ACCOUNTS


async def cb_acc_del_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التنفيذ الفعلي للحذف بعد التأكيد."""
    await _answer(update)
    acc_id = int(update.callback_query.data.split("_")[-1])
    uid = update.effective_user.id
    await db.delete_account(acc_id, uid)
    await _edit(update, "🗑 *تم حذف الحساب بنجاح.*", ik(back_btn("accounts")))
    return S_ACCOUNTS


async def cb_acc_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    acc_id = int(update.callback_query.data.split("_")[-1])
    uid = update.effective_user.id
    accs = await db.get_accounts(uid)
    acc  = next((a for a in accs if a["id"] == acc_id), None)
    if not acc:
        return S_ACCOUNTS
    await _edit(update, "⏳ *جاري فحص الحساب...*")
    from fb_automator import FBAutomator
    automator = FBAutomator(acc_id, acc["cookies"], acc.get("proxy"))
    ok = await automator.check_login()
    if ok:
        await _edit(update, f"✅ *الحساب نشط*: {_escape_md(_acc_name(acc))}", ik(back_btn("accounts")))
    else:
        await _edit(update, f"❌ *الحساب غير نشط أو الكوكيز منتهية*\n{_escape_md(_acc_name(acc))}", ik(back_btn("accounts")))
    return S_ACCOUNTS


async def cb_acc_check_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    accs = await db.get_accounts(uid)
    await _edit(update, f"⏳ *جاري فحص {len(accs)} حساب...*")
    from fb_automator import FBAutomator
    active = failed = 0
    report = "🔍 *تقرير فحص الحسابات*\n━━━━━━━━━━━━━━━━━━\n\n"
    for acc in accs:
        automator = FBAutomator(acc["id"], acc["cookies"], acc.get("proxy"))
        ok = await automator.check_login()
        if ok:
            active += 1
            report += f"✅ {_escape_md(_acc_name(acc))}\n"
        else:
            failed += 1
            report += f"❌ {_escape_md(_acc_name(acc))}\n"
    report += f"\n✅ النشطة: {active} | ❌ الفاشلة: {failed}"
    await _edit(update, report, ik(back_btn("accounts")))
    return S_ACCOUNTS


async def cb_acc_fetch_grp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    acc_id = int(update.callback_query.data.split("_")[-1])
    uid = update.effective_user.id
    accs = await db.get_accounts(uid)
    acc  = next((a for a in accs if a["id"] == acc_id), None)
    if not acc:
        return S_ACCOUNTS
    await _edit(update, "⏳ *جاري سحب المجموعات من فيسبوك...*\nقد يستغرق دقيقة.")
    from fb_automator import FBAutomator
    automator = FBAutomator(acc_id, acc["cookies"], acc.get("proxy"))
    groups = await automator.fetch_groups()
    _ok, _msg, remaining = await _check_groups_limit(uid)
    saved = 0
    for g in groups:
        if saved >= remaining:
            break
        await db.add_group(uid, acc_id, g["group_id"], g["group_name"], g.get("group_url",""), g.get("members_count",0))
        saved += 1
    await db.log_activity(uid, "fetch_groups", f"سحب {saved} مجموعة")
    if saved:
        extra = (f"\n\n⚠️ تم حفظ {saved} فقط (حد خطتك). رقّ خطتك للمزيد."
                 if len(groups) > saved else "")
        await _edit(update, f"✅ *تم سحب {saved} مجموعة!*{extra}", ik(back_btn("accounts")))
    else:
        await _edit(update, "⚠️ لم يُعثر على مجموعات (أو وصلت لحد خطتك).\nتحقق من الكوكيز.", ik(back_btn("accounts")))
    return S_ACCOUNTS


async def cb_acc_fetch_pg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    acc_id = int(update.callback_query.data.split("_")[-1])
    uid = update.effective_user.id
    accs = await db.get_accounts(uid)
    acc  = next((a for a in accs if a["id"] == acc_id), None)
    if not acc:
        return S_ACCOUNTS
    await _edit(update, "⏳ *جاري سحب الصفحات...*")
    from fb_automator import FBAutomator
    automator = FBAutomator(acc_id, acc["cookies"], acc.get("proxy"))
    pages = await automator.fetch_pages()
    for pg in pages:
        await db.add_page(uid, acc_id, pg["page_id"], pg["page_name"], pg.get("access_token",""))
    if pages:
        await _edit(update, f"✅ *تم سحب {len(pages)} صفحة!*", ik(back_btn("accounts")))
    else:
        await _edit(update, "⚠️ لم يُعثر على صفحات.", ik(back_btn("accounts")))
    return S_ACCOUNTS


# ══════════════════════════════════════════════════════════════
#  GROUPS
# ══════════════════════════════════════════════════════════════

async def _show_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    groups = await db.get_groups(uid)
    count  = len(groups)
    view_row = [btn(f"📋 عرض المجموعات المحفوظة ({count})", "grp_view")] if count else []
    rows = []
    if view_row:
        rows.append(view_row)
    rows += [
        [btn("🗂 سحب جروباتي من فيسبوك", "grp_mine")],
        [btn("🔎 استخراج جروبات شخص آخر", "grp_other")],
        [btn("🔍 بحث عن جروبات",           "grp_search")],
        [btn("📋 القوائم المحفوظة",        "grp_lists")],
        [btn("🗑 حذف جميع الجروبات",       "grp_delete_all")],
    ]
    if count:
        rows.append([btn("📤 تصدير المجموعات", "grp_export")])
    await _send(update,
        f"👥 *إدارة المجموعات*\n━━━━━━━━━━━━━━━━━━\n\nالمجموعات المحفوظة: *{count}*",
        inline_kb=ik(*rows)
    )
    return S_GROUPS


async def cb_grp_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    groups = await db.get_groups(uid)
    if not groups:
        await _edit(update, "لا توجد مجموعات محفوظة.", ik(back_btn("groups")))
        return S_GROUPS
    lines = [f"📋 المجموعات المحفوظة ({len(groups)})", "━" * 18, ""]
    for g in groups[:30]:
        lines.append(f"• {g.get('group_name','—')}")
    if len(groups) > 30:
        lines.append(f"... و {len(groups)-30} مجموعة أخرى")
    await _send(update, "\n".join(lines), inline_kb=ik(back_btn("groups")), parse_mode=None)
    return S_GROUPS


async def cb_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    return await _show_groups(update, context)


async def cb_grp_mine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    accs = await db.get_accounts(uid)
    if not accs:
        await _edit(update, "⚠️ يجب ربط حساب أولاً!", ik([btn("👤 الحسابات","accounts")]))
        return S_GROUPS
    rows = [[btn(f"✅ {_acc_name(a)}", f"grp_fetch_{a['id']}")] for a in accs]
    rows.append(back_btn("groups"))
    await _edit(update, "👤 *اختر الحساب لسحب مجموعاته:*", ik(*rows))
    return S_GROUPS


async def cb_grp_fetch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    acc_id = int(update.callback_query.data.split("_")[-1])
    uid = update.effective_user.id
    accs = await db.get_accounts(uid)
    acc  = next((a for a in accs if a["id"] == acc_id), None)
    if not acc:
        return S_GROUPS
    await _edit(update, "⏳ *جاري سحب المجموعات...*")
    from fb_automator import FBAutomator
    grps = await FBAutomator(acc_id, acc["cookies"], acc.get("proxy")).fetch_groups()
    _ok, _msg, remaining = await _check_groups_limit(uid)
    saved = 0
    for g in grps:
        if saved >= remaining:
            break
        await db.add_group(uid, acc_id, g["group_id"], g["group_name"], g.get("group_url",""))
        saved += 1
    await db.log_activity(uid, "fetch_groups", f"سحب {saved} مجموعة")
    extra = (f"\n\n⚠️ تم حفظ {saved} فقط (حد خطتك)." if grps and len(grps) > saved else "")
    await _edit(update,
        f"✅ *تم سحب {saved} مجموعة!*{extra}" if saved else "⚠️ لم يُعثر على مجموعات (أو وصلت لحد خطتك).",
        ik(back_btn("groups"))
    )
    return S_GROUPS


async def cb_grp_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    join = "join" in update.callback_query.data
    context.user_data["grp_join"] = join
    await _edit(update,
        f"🔍 *{'بحث + انضمام' if join else 'بحث عن جروبات'}*\n\nأرسل كلمة البحث:",
        ik(back_btn("groups"))
    )
    return S_GRP_SEARCH


async def grp_got_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.message.text.strip()
    uid = update.effective_user.id
    mode = context.user_data.pop("grp_mode", None)

    # ── رفع قائمة مجموعات يدوياً ──
    if mode == "upload":
        import re as _re
        added = 0
        for line in q.splitlines():
            line = line.strip()
            if not line:
                continue
            m = _re.search(r'/groups/(\d{6,20})', line) or _re.search(r'^(\d{6,20})$', line)
            gid = m.group(1) if m else None
            if gid:
                await db.add_group(uid, 0, gid, f"مجموعة {gid}", f"https://facebook.com/groups/{gid}")
                added += 1
        await _send(update,
            f"✅ *تم إضافة {added} مجموعة من القائمة!*" if added
            else "⚠️ لم أتعرّف على أي رابط/ID صحيح.\nأرسل روابط مثل: facebook.com/groups/123456",
            inline_kb=ik(back_btn("groups"))
        )
        return S_GROUPS

    # ── استخراج جروبات شخص آخر ──
    if mode == "other":
        accs = await db.get_accounts(uid)
        if not accs:
            await _send(update, "⚠️ يجب ربط حساب أولاً!", inline_kb=ik([btn("👤 الحسابات","accounts")]))
            return S_GROUPS
        await _send(update, "⏳ *جاري الاستخراج...*\nقد يستغرق دقيقة.")
        from fb_automator import FBAutomator
        acc = accs[0]
        auto = FBAutomator(acc["id"], acc.get("cookies",""), acc.get("proxy"))
        try:
            grps = await auto.fetch_groups_of_profile(q)
        except Exception as e:
            grps = []
            logger.error(f"fetch_groups_of_profile error: {e}")
        for g in grps:
            await db.add_group(uid, acc["id"], g["group_id"], g["group_name"], g.get("group_url",""))
        await _send(update,
            f"✅ *تم استخراج {len(grps)} مجموعة!*" if grps
            else "⚠️ لم أتمكن من استخراج مجموعات من هذا الرابط (قد تكون مخفية).",
            inline_kb=ik(back_btn("groups"))
        )
        return S_GROUPS

    # ── بحث عادي ──
    join = context.user_data.get("grp_join", False)
    accs = await db.get_accounts(uid)
    if not accs:
        await _send(update, "⚠️ يجب ربط حساب أولاً!", inline_kb=ik([btn("👤 الحسابات","accounts")]))
        return S_GROUPS
    await _send(update, f"🔍 *جاري البحث عن:* {q}")
    from fb_automator import FBAutomator
    acc = accs[0]
    auto = FBAutomator(acc["id"], acc.get("cookies",""), acc.get("proxy"))
    try:
        results = await auto.search_groups(q)
    except Exception as e:
        results = []
        logger.error(f"search_groups error: {e}")
    if not results:
        await _send(update,
            "⚠️ لم يتم العثور على نتائج، أو أن فيسبوك يحجب البحث حالياً.",
            inline_kb=ik(back_btn("groups"))
        )
        return S_GROUPS
    saved = 0
    joined = 0
    for g in results:
        await db.add_group(uid, acc["id"], g["group_id"], g["group_name"], g.get("group_url",""))
        saved += 1
        # الانضمام التلقائي لو مطلوب
        if join:
            try:
                jr = await auto.join_group(g["group_id"])
                if jr.get("success"):
                    joined += 1
                await asyncio.sleep(random.uniform(3, 7))  # فاصل أمان بين كل انضمام
            except Exception as e:
                logger.error(f"join_group error: {e}")
    extra = f"\n🔗 تم الانضمام لـ {joined} مجموعة" if join else ""
    await _send(update,
        f"✅ *تم العثور على {saved} مجموعة وحفظها!*{extra}",
        inline_kb=ik(back_btn("groups"))
    )
    return S_GROUPS


async def cb_grp_delete_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    await _edit(update,
        "⚠️ *هل أنت متأكد؟*\nسيتم حذف جميع المجموعات المحفوظة!",
        ik([btn("⚠️ نعم، احذف الكل", "grp_confirm_del"), btn("❌ إلغاء","groups")])
    )
    return S_GROUPS


async def cb_grp_confirm_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    await db.delete_groups(update.effective_user.id)
    await _edit(update, "🗑 *تم حذف جميع المجموعات.*", ik(back_btn("groups")))
    return S_GROUPS


async def cb_grp_lists(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    lists = await db.get_group_lists(uid)
    text = "📋 *القوائم المحفوظة*\n━━━━━━━━━━━━━━━━━━\n\n"
    if lists:
        for lst in lists:
            ids = json.loads(lst.get("group_ids","[]"))
            text += f"• *{lst['list_name']}* — {len(ids)} مجموعة\n"
    else:
        text += "لا توجد قوائم محفوظة."
    await _edit(update, text, ik(back_btn("groups")))
    return S_GROUPS


async def cb_grp_check_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    user = await db.get_user(uid)
    if user.get("plan","free") == "free":
        await _edit(update,
            "🔴 *فحص النشر المباشر*\n\n⚠️ هذه ميزة Pro/Unlimited.",
            ik([btn("💎 ترقية الخطة","plan_upgrade")], back_btn("groups"))
        )
    else:
        await _edit(update, "⏳ *جاري فحص صلاحيات النشر...*", ik())
        accs = await db.get_accounts(uid)
        if not accs:
            await _edit(update, "⚠️ يجب ربط حساب أولاً!", ik([btn("👤 الحسابات","accounts")]))
            return S_GROUPS
        from fb_automator import FBAutomator
        ok_count = 0
        for a in accs:
            try:
                if await FBAutomator(a["id"], a.get("cookies",""), a.get("proxy")).check_login():
                    ok_count += 1
            except Exception:
                pass
        grps = await db.get_groups(uid)
        await _edit(update,
            f"📊 *نتيجة فحص النشر*\n━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ حسابات جاهزة للنشر: *{ok_count}/{len(accs)}*\n"
            f"👥 مجموعات محفوظة: *{len(grps)}*\n\n"
            + ("✅ كل شيء جاهز للنشر!" if ok_count and grps
               else "⚠️ تحقق من الكوكيز أو اسحب مجموعات أولاً."),
            ik(back_btn("groups"))
        )
    return S_GROUPS


async def cb_grp_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    user = await db.get_user(uid)
    plan = user.get("plan", "free") if user else "free"
    action = update.callback_query.data
    labels = {
        "grp_other":   "استكشاف مجموعات أخرى",
        "grp_members": "جلب قائمة الأعضاء",
        "grp_upload":  "رفع قائمة مجموعات",
    }
    label = labels.get(action, action)
    if plan == "free":
        await _edit(update,
            f"🔒 *{label}*\n\nهذه الميزة متاحة لمشتركي Pro وما فوق.",
            ik([btn("💎 ترقية الخطة","plan_upgrade")], back_btn("groups"))
        )
        return S_GROUPS

    if action == "grp_other":
        context.user_data["grp_mode"] = "other"
        await _edit(update,
            "🔎 *استخراج جروبات شخص آخر*\n━━━━━━━━━━━━━━━━━━\n\n"
            "أرسل رابط بروفايل الشخص أو الـ ID الرقمي الخاص به\n"
            "وسأحاول استخراج المجموعات العامة الظاهرة لديه:",
            ik(back_btn("groups"))
        )
        return S_GRP_SEARCH

    if action == "grp_upload":
        context.user_data["grp_mode"] = "upload"
        await _edit(update,
            "📤 *رفع قائمة مجموعات*\n━━━━━━━━━━━━━━━━━━\n\n"
            "أرسل روابط أو IDs المجموعات (كل واحدة في سطر):\n"
            "مثال:\n`https://facebook.com/groups/123456`\n`789012`",
            ik(back_btn("groups"))
        )
        return S_GRP_SEARCH

    # grp_members وغيرها
    await _edit(update,
        f"ℹ️ *{label}*\n\nاستخدم زر «سحب جروباتي» أو «استخراج جروبات شخص آخر» للحصول على المجموعات.",
        ik(back_btn("groups"))
    )
    return S_GROUPS


# ══════════════════════════════════════════════════════════════
#  PAGES  (sub-keyboard: نشر / ستوري / بوت)
# ══════════════════════════════════════════════════════════════

async def cb_pages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    return await _show_pages(update, context)


async def _show_pages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    pages = await db.get_pages(uid)
    text = (
        "📄 *الصفحات*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "كل أدوات الصفحات في مكان واحد:\n"
        "نشر، ستوري، وبوت الصفحات.\n\n"
        f"الصفحات المربوطة: *{len(pages)}*"
    )
    await _send(update, text, reply_kb=PAGES_KB)
    return S_PAGES


async def _show_page_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    pages = await db.get_pages(uid)
    if not pages:
        await _send(update,
            "📄 *نشر الصفحات*\n\n⚠️ لا توجد صفحات مربوطة.\nاسحب صفحاتك أولاً من قسم الحسابات.",
            inline_kb=ik([btn("👤 الحسابات","accounts")])
        )
        return S_PAGES

    await _send(update,
        "🖼 *نشر الصفحات*\n━━━━━━━━━━━━━━━━━━\n\nاختر طريقة النشر على صفحاتك.",
        inline_kb=ik(
            [btn("🚀 نشر الآن",     "ppost_now")],
            [btn("⏰ جدولة",        "ppost_schedule")],
            [btn("🎵 من تيك توك",  "ppost_tiktok")],
            [btn("❌ إلغاء",        "pages")],
        )
    )
    return S_PAGE_POST


async def cb_ppost_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    pages = await db.get_pages(uid)
    rows = []
    selected = context.user_data.get("pg_selected", [])
    for pg in pages:
        check = "✅" if pg["id"] in selected else "⬜"
        rows.append([btn(f"{check} {pg['page_name'][:30]}", f"pg_sel_{pg['id']}")])

    rows.append([
        btn("تحديد الكل",    "pg_sel_all"),
        btn("مسح",           "pg_sel_none"),
    ])
    rows.append([
        btn("💾 حفظ كمفضلة",     "pg_save_fav"),
        btn("⭐ استخدام مفضلة",  "pg_use_fav"),
    ])
    rows.append([btn("📁 إدارة المفضلة", "pg_manage_fav")])
    rows.append([btn("✅ تم", "pg_confirm_sel"), btn("❌ إلغاء", "pages")])

    await _edit(update,
        f"اختر الصفحات الأساسية للدفعة:\n*(محدد: {len(selected)}/{len(pages)})*",
        ik(*rows)
    )
    return S_PAGE_POST


async def cb_pg_sel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    pg_id = int(update.callback_query.data.split("_")[-1])
    sel = context.user_data.setdefault("pg_selected", [])
    if pg_id in sel:
        sel.remove(pg_id)
    else:
        sel.append(pg_id)
    return await cb_ppost_now(update, context)


async def cb_pg_sel_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    pages = await db.get_pages(uid)
    context.user_data["pg_selected"] = [p["id"] for p in pages]
    return await cb_ppost_now(update, context)


async def cb_pg_sel_none(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    context.user_data["pg_selected"] = []
    return await cb_ppost_now(update, context)


async def cb_pg_confirm_sel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    selected = context.user_data.get("pg_selected", [])
    if not selected:
        await _answer(update, "اختر صفحة واحدة على الأقل!", True)
        return S_PAGE_POST
    await _edit(update,
        "اختر طريقة التوزيع:\n\n"
        "• *توزيع:* يقسم المحتوى على الصفحات المختارة.\n"
        "• *تكرار:* ينشر كل محتوى على كل الصفحات.",
        ik(
            [btn("📊 توزيع",  "pg_dist_spread")],
            [btn("🔁 تكرار",  "pg_dist_repeat")],
            [btn("❌ إلغاء",  "pages")],
        )
    )
    return S_PAGE_POST


async def cb_pg_dist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    mode = "توزيع" if "spread" in update.callback_query.data else "تكرار"
    context.user_data["pg_dist"] = mode
    await _edit(update,
        f"🖼 *أرسل صورة أو فيديو بدون كابشن.*\n\n"
        f"بعد كل ملف سأطلب الكابشن الخاص به في رسالة نصية منفصلة\n"
        f"حتى لا يظهر حد كابشن تيليجرام.\n\n"
        f"⚙️ الوضع: *{mode}*",
        ik([btn("❌ إلغاء","pages")])
    )
    return S_CAMP_MEDIA


async def _show_page_story(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    accs = await db.get_accounts(uid)
    if not accs:
        await _send(update, "⚠️ يجب ربط حساب أولاً!", inline_kb=ik([btn("👤 الحسابات","accounts")]))
        return S_PAGES
    pages = await db.get_pages(uid)
    if not pages:
        await _send(update,
            "📚 *ستوري الصفحات*\n\n⚠️ لا توجد صفحات مربوطة.\nاسحب صفحاتك أولاً من قسم الحسابات.",
            inline_kb=ik([btn("👤 الحسابات","accounts")]))
        return S_PAGES
    # نبدأ اختيار الصفحات (زي الفيديو)
    context.user_data["story_pages"] = []
    return await _render_story_pages(update, context)


async def _render_story_pages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    pages = await db.get_pages(uid)
    sel = context.user_data.get("story_pages", [])
    rows = []
    for pg in pages[:50]:
        check = "✅" if pg["id"] in sel else "⬜"
        name = pg.get("page_name", pg.get("name", "صفحة"))
        rows.append([btn(f"{check} {name[:30]}", f"stpg_{pg['id']}")])
    rows.append([btn("✅ تحديد الكل", "stpg_all"), btn("🔘 مسح", "stpg_none")])
    rows.append([btn(f"▶️ متابعة ({len(sel)} صفحة)", "stpg_done")])
    rows.append([btn("❌ إلغاء", "pages")])
    text = (
        f"📚 *ستوري الصفحات — اختيار الصفحات*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"اختر الصفحات التي تريد نشر الستوري عليها:\n"
        f"(إجمالي: {len(pages)} — محدد: *{len(sel)}*)"
    )
    if update.callback_query:
        await _edit(update, text, ik(*rows))
    else:
        await _send(update, text, inline_kb=ik(*rows))
    return S_STORY_PAGES


async def cb_story_pg_sel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    pg_id = int(update.callback_query.data.split("_")[-1])
    sel = context.user_data.setdefault("story_pages", [])
    if pg_id in sel:
        sel.remove(pg_id)
    else:
        sel.append(pg_id)
    return await _render_story_pages(update, context)


async def cb_story_pg_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    pages = await db.get_pages(update.effective_user.id)
    context.user_data["story_pages"] = [p["id"] for p in pages]
    return await _render_story_pages(update, context)


async def cb_story_pg_none(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    context.user_data["story_pages"] = []
    return await _render_story_pages(update, context)


async def cb_story_pg_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    sel = context.user_data.get("story_pages", [])
    if not sel:
        await _answer(update, "اختر صفحة واحدة على الأقل!", True)
        return S_STORY_PAGES
    await _edit(update,
        f"📚 *تم اختيار {len(sel)} صفحة*\n━━━━━━━━━━━━━━━━━━\n\n"
        "الآن أرسل محتوى الستوري:\n"
        "🖼 صورة • 🎥 فيديو • ✍️ نص",
        ik([btn("❌ إلغاء","pages")])
    )
    return S_PAGE_STORY_IMG


async def story_got_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = update.effective_user.id
    context.user_data["story_type"] = "image"
    if msg.photo:
        f = await msg.photo[-1].get_file()
        path = f"/tmp/story_{uid}.jpg"
        await f.download_to_drive(path)
        context.user_data["story_img"] = path
        context.user_data["story_type"] = "image"
    elif msg.video:
        f = await msg.video.get_file()
        path = f"/tmp/story_{uid}.mp4"
        await f.download_to_drive(path)
        context.user_data["story_img"] = path
        context.user_data["story_type"] = "video"
    elif msg.document:
        f = await msg.document.get_file()
        mime = (msg.document.mime_type or "").lower()
        ext = "mp4" if "video" in mime else "jpg"
        path = f"/tmp/story_{uid}.{ext}"
        await f.download_to_drive(path)
        context.user_data["story_img"] = path
        context.user_data["story_type"] = "video" if "video" in mime else "image"
    elif msg.text:
        # ستوري نصي
        context.user_data["story_img"] = None
        context.user_data["story_text"] = msg.text.strip()
        context.user_data["story_type"] = "text"
    else:
        await _send(update, "⚠️ أرسل صورة أو فيديو أو نص.")
        return S_PAGE_STORY_IMG

    type_label = {"image": "🖼 صورة", "video": "🎥 فيديو", "text": "✍️ نص"}.get(
        context.user_data["story_type"], "محتوى")
    await _send(update,
        f"✅ *تم حفظ {type_label} الستوري.*\n\n"
        "أرسل الرابط المرفق للستوري (مفيد للأفلييت/المنتجات)\nأو اضغط تخطي:",
        inline_kb=ik([btn("⏭ تخطي الرابط","story_skip_link"), btn("❌ إلغاء","pages")])
    )
    return S_PAGE_STORY_LINK


async def cb_story_skip_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    context.user_data["story_link"] = None
    return await _ask_story_time(update, context)


async def story_got_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["story_link"] = update.message.text.strip()
    return await _ask_story_time(update, context)


async def _ask_story_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send(update,
        "⏰ *متى تريد نشر الستوري؟*\n\nاكتب الموعد بطريقتك الطبيعية.\nمثال: `غداً 10 الصبح`",
        inline_kb=ik(
            [btn("🚀 نشر الآن","story_now"), btn("❌ إلغاء","pages")]
        )
    )
    return S_PAGE_STORY_LINK


async def _do_post_story(update, context):
    """ينشر الستوري على كل الصفحات المختارة (زي الفيديو) مع عدّاد حي."""
    uid = update.effective_user.id
    img = context.user_data.get("story_img")
    link = context.user_data.get("story_link")
    stype = context.user_data.get("story_type", "image")
    sel = context.user_data.get("story_pages", [])
    accs = await db.get_accounts(uid)
    if not accs:
        await _edit(update, "⚠️ يجب ربط حساب أولاً!", ik([btn("👤 الحسابات","accounts")]))
        return S_PAGES

    from fb_automator import FBAutomator
    acc = accs[0]
    auto = FBAutomator(acc["id"], acc.get("cookies",""), acc.get("proxy"))

    # 🔍 فحص الحساب قبل النشر (زي الفيديو)
    try:
        ok = await auto.check_login()
    except Exception:
        ok = True  # نكمّل بدل ما نوقف لو الفحص فشل
    if not ok:
        await _edit(update,
            "❌ *الحساب غير نشط أو الكوكيز منتهية.*\nحدّث الكوكيز وحاول مجدداً.",
            ik([btn("👤 الحسابات","accounts")]))
        return S_PAGES

    pages = await db.get_pages(uid)
    target_pages = [p for p in pages if p["id"] in sel] if sel else pages[:1]
    if not target_pages:
        await _edit(update, "⚠️ لم يتم اختيار صفحات.", ik(back_btn("pages")))
        return S_PAGES

    total = len(target_pages)
    done = failed = 0
    # رسالة عدّاد حي
    pm = None
    try:
        m = update.message or update.callback_query.message
        pm = await m.reply_text(f"📤 جاري النشر...\n0/{total} صفحة")
    except Exception:
        pass

    for i, pg in enumerate(target_pages):
        try:
            res = await auto.post_story(pg.get("page_id",""), img, link)
        except Exception as e:
            res = {"success": False, "error": str(e)}
        if res.get("success"):
            done += 1
        else:
            failed += 1
        # تحديث العدّاد الحي
        if pm:
            try:
                await pm.edit_text(
                    f"📤 النشر جارٍ...\n"
                    f"{_progress_bar(i+1, total)}\n"
                    f"✅ {done} | ❌ {failed} | {i+1}/{total}")
            except Exception:
                pass
        await asyncio.sleep(random.uniform(1.5, 3.5))

    await db.log_activity(uid, "نشر ستوري", f"على {done} صفحة", "success" if done else "error")
    # تنظيف الملف المؤقت
    try:
        if img and os.path.exists(img) and img.startswith("/tmp/"):
            os.remove(img)
    except Exception:
        pass
    type_label = {"image":"🖼 صورة","video":"🎥 فيديو","text":"✍️ نص"}.get(stype,"محتوى")
    await _send(update,
        f"✅ *تم نشر الستوري!*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"📎 المحتوى: {type_label}\n"
        f"✅ نجح على: *{done}* صفحة\n"
        f"❌ فشل على: *{failed}* صفحة",
        inline_kb=ik(back_btn("pages"))
    )
    for k in ("story_img","story_link","story_pages","story_type","story_text"):
        context.user_data.pop(k, None)
    return S_PAGES


async def cb_story_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    await _edit(update, "⏳ *جاري نشر الستوري الآن...*", ik())
    return await _do_post_story(update, context)


async def story_got_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dt = _parse_cairo_time(update.message.text.strip())
    if not dt:
        await _send(update, "❌ الصيغة غير صحيحة. أرسل الوقت مثلاً: `14:30`")
        return S_PAGE_STORY_LINK
    readable = _format_cairo_dt(dt)
    context.user_data["story_time"] = dt.strftime("%H:%M")
    await _send(update,
        f"🕐 *فهمت الموعد هكذا:*\n\n"
        f"📅 {readable}\n"
        f"(توقيت القاهرة)\n\n"
        f"هل تريد اعتماد هذا الموعد؟",
        inline_kb=ik(
            [btn("✅ تأكيد الموعد",  "story_confirm")],
            [btn("✏️ تعديل الموعد", "story_edit")],
            [btn("❌ إلغاء",         "pages")],
        )
    )
    return S_PAGE_STORY_LINK


async def cb_story_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    await _edit(update, "⏳ *جاري نشر الستوري...*", ik())
    return await _do_post_story(update, context)


async def cb_story_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إعادة طلب موعد الستوري."""
    await _answer(update)
    return await _ask_story_time(update, context)


async def _show_page_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    bots = await db.get_page_bots(uid)
    text = "🤖 *بوت الصفحات*\n━━━━━━━━━━━━━━━━━━\n\n"
    if bots:
        text += f"البوتات النشطة: *{len(bots)}*\n\n"
        for b in bots[:5]:
            text += f"• {b['page_name']} — {b['template_name']}\n"
    else:
        text += "لا يوجد بوت مفعّل حتى الآن.\n\n"
    text += "\nيمكنك ربط بوت بصفحة فيسبوك للرد التلقائي على كلمات مفتاحية."
    rows = [[btn("➕ إضافة بوت جديد", "pagebot_new")]]
    for b in bots[:5]:
        rows.append([btn(f"🗑 حذف: {b['page_name']}", f"pagebot_del_{b['id']}")])
    rows.append(back_btn("pages"))
    await _send(update, text, inline_kb=ik(*rows))
    return S_PAGE_BOT


# ══════════════════════════════════════════════════════════════
#  CAMPAIGNS  (المجموعات)
# ══════════════════════════════════════════════════════════════

async def _show_campaigns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rows = [[btn("🆕 حملة جديدة", "camp_new")]]
    # لو فيه مسودّة محفوظة نعرض زر استئنافها
    draft = await db.get_draft(uid)
    if draft:
        rows.append([btn("📝 استئناف المسودّة", "camp_resume_draft"),
                     btn("🗑 حذف المسودّة", "camp_del_draft")])
    rows += [
        [btn("⏰ الحملات المجدولة", "camp_scheduled"),
         btn("📊 السجل",            "camp_log_cb")],
        [btn("📈 إحصائيات الحملات",  "camp_stats"),
         btn("📅 تقويم النشر",        "camp_calendar")],
    ]
    await _send(update,
        "🚀 *الحملات*\n━━━━━━━━━━━━━━━━━━\n\nاختر نوع العملية:",
        inline_kb=ik(*rows)
    )
    return S_CAMPAIGNS


async def cb_camp_resume_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استئناف مسودّة محفوظة."""
    await _answer(update)
    uid = update.effective_user.id
    draft = await db.get_draft(uid)
    if not draft:
        await _edit(update, "لا توجد مسودّة محفوظة.", ik(back_btn("campaigns")))
        return S_CAMPAIGNS
    context.user_data["camp"] = dict(draft)
    cap = draft.get("caption", "") or "(بدون نص)"
    media = {"video": "🎥 فيديو", "photo": "🖼 صورة"}.get(draft.get("media_type"), "📄 بدون ميديا")
    await _edit(update,
        f"📝 *استئناف المسودّة*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"📎 المحتوى: {media}\n"
        f"📝 النص: {_escape_md(cap[:200])}\n\n"
        f"اختر وجهة النشر للمتابعة:",
        ik(
            [btn("👥 نشر في مجموعات", "camp_tgt_groups")],
            [btn("📄 نشر في صفحات",   "camp_tgt_pages")],
            back_btn("campaigns"),
        )
    )
    return S_CAMP_TARGETS


async def cb_camp_del_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف المسودّة المحفوظة."""
    await _answer(update)
    await db.delete_draft(update.effective_user.id)
    await _answer(update, "🗑 تم حذف المسودّة")
    return await _show_campaigns_cb(update, context)


async def _show_campaigns_cb(update, context):
    await _edit(update,
        "🚀 *الحملات*\n━━━━━━━━━━━━━━━━━━\n\nاختر نوع العملية:",
        ik([btn("🆕 حملة جديدة", "camp_new")],
           [btn("📊 السجل", "camp_log_cb"), btn("📅 تقويم النشر", "camp_calendar")])
    )
    return S_CAMPAIGNS


async def cb_camp_save_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حفظ الحملة الحالية كمسودّة."""
    await _answer(update)
    uid = update.effective_user.id
    camp = context.user_data.get("camp", {})
    # نحفظ فقط الحقول القابلة للتخزين (مش ملفات كبيرة)
    draft = {
        "caption": camp.get("caption", ""),
        "media_path": camp.get("media_path"),
        "media_type": camp.get("media_type"),
        "target_type": camp.get("target_type", "groups"),
    }
    await db.save_draft(uid, draft)
    await _edit(update,
        "💾 *تم حفظ المسودّة!*\n\nتقدر تكمّلها لاحقاً من قائمة الحملات.",
        ik(back_btn("campaigns"))
    )
    return S_CAMPAIGNS


async def cb_campaigns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    return await _show_campaigns(update, context)


async def cb_camp_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    if not await db.get_accounts(uid):
        await _edit(update, "❌ يجب ربط حساب أولاً!", ik([btn("👤 الحسابات","accounts")]))
        return S_CAMPAIGNS
    allowed, msg = await _check_campaign_limit(uid)
    if not allowed:
        await _edit(update, msg, ik([btn("💎 ترقية الخطة", "plan_upgrade")], back_btn("campaigns")))
        return S_CAMPAIGNS
    context.user_data["camp"] = {}
    await _edit(update,
        "📎 *حملة جديدة — الخطوة 1/4 (الميديا)*\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل *صورة أو فيديو* بدون كابشن.\n"
        "أو أرسل رابط تيك توك / يوتيوب / ريلز.\n\n"
        "⚠️ أرسل الميديا أولاً بدون أي نص، سأطلب الكابشن في الخطوة التالية.",
        ik([btn("⏭ بدون ميديا","camp_skip_media")], back_btn("campaigns"))
    )
    return S_CAMP_MEDIA


async def cb_camp_skip_cap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    context.user_data.setdefault("camp", {})["caption"] = ""
    if context.user_data.get("pg_selected"):
        return await _launch_page_post(update, context)
    return await _ask_camp_targets(update, context)


async def camp_got_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("camp", {})["caption"] = update.message.text.strip()
    if context.user_data.get("pg_selected"):
        return await _launch_page_post(update, context)
    return await _ask_camp_targets(update, context)


async def _launch_page_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ينشر المحتوى على الصفحات المختارة فعلياً."""
    uid = update.effective_user.id
    camp = context.user_data.get("camp", {})
    caption = camp.get("caption", "")
    media_path = camp.get("media_path")
    sel = context.user_data.get("pg_selected", [])
    accs = await db.get_accounts(uid)
    pages = await db.get_pages(uid)
    if not accs:
        await _send(update, "⚠️ يجب ربط حساب أولاً!", inline_kb=ik([btn("👤 الحسابات","accounts")]))
        return S_PAGES
    target_pages = [p for p in pages if p["id"] in sel]
    if not target_pages:
        await _send(update, "⚠️ لم يتم اختيار صفحات.", inline_kb=ik(back_btn("pages")))
        return S_PAGES
    await _send(update, f"🚀 *جاري النشر على {len(target_pages)} صفحة...*\nسيتم إشعارك عند الانتهاء.")
    acc = accs[0]
    from fb_automator import FBAutomator
    auto = FBAutomator(acc["id"], acc.get("cookies",""), acc.get("proxy"))
    done = failed = 0
    for pg in target_pages:
        try:
            r = await auto.post_to_page(pg.get("page_id",""), caption, media_path)
        except Exception as e:
            r = {"success": False, "error": str(e)}
        if r.get("success"):
            done += 1
        else:
            failed += 1
        await asyncio.sleep(random.uniform(2, 5))
    # تنظيف الميديا
    try:
        if media_path and os.path.exists(media_path) and media_path.startswith("/tmp/"):
            os.remove(media_path)
    except Exception:
        pass
    await db.log_activity(uid, "نشر صفحات", f"✅{done} ❌{failed}", "success" if done else "error")
    context.user_data.pop("pg_selected", None)
    context.user_data.pop("pg_dist", None)
    context.user_data["camp"] = {}
    await _send(update,
        f"✅ *انتهى النشر على الصفحات!*\n\n✅ نجح: {done}\n❌ فشل: {failed}",
        inline_kb=ik(back_btn("pages"))
    )
    return S_PAGES


async def _ask_camp_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send(update,
        "📎 *الخطوة 1/4 — الميديا*\n\nأرسل فيديو/صورة أو رابط (تيك توك/يوتيوب/ريلز).\nأو اضغط تخطي.",
        inline_kb=ik([btn("⏭ بدون ميديا","camp_skip_media")])
    )
    return S_CAMP_MEDIA


async def cb_camp_skip_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    context.user_data["camp"]["media_path"] = None
    context.user_data["camp"]["media_type"] = None
    return await _ask_camp_caption(update, context)


async def camp_got_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    path = None
    mtype = None
    if msg.video:
        f = await msg.video.get_file()
        path = f"/tmp/vid_{update.effective_user.id}.mp4"
        await f.download_to_drive(path)
        mtype = "video"
    elif msg.photo:
        f = await msg.photo[-1].get_file()
        path = f"/tmp/img_{update.effective_user.id}.jpg"
        await f.download_to_drive(path)
        mtype = "photo"
    elif msg.text:
        await _send(update, "⏳ *جاري تحميل الفيديو...*")
        from fb_automator import download_video
        path = await download_video(msg.text.strip())
        mtype = "video" if path else None
        if not path:
            await _send(update, "❌ فشل التحميل. سيُتابَع بدون ميديا.")
    else:
        await _send(update, "⚠️ نوع غير مدعوم. أرسل فيديو أو صورة.")
        return S_CAMP_MEDIA

    context.user_data["camp"]["media_path"] = path
    context.user_data["camp"]["media_type"] = mtype
    if path:
        await _send(update, "✅ *تم استلام الميديا!*\n\nالآن أرسل الكابشن.")
    return await _ask_camp_caption(update, context)


async def _ask_camp_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send(update,
        "📝 *الخطوة 2/4 — الكابشن*\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل *نص المنشور* الآن،\n"
        "أو اختر *قالباً جاهزاً* وعدّله.",
        inline_kb=ik(
            [btn("📦 قوالب جاهزة", "camp_quick_tpl")],
            [btn("🎯 اختبار A/B (نسختين)", "camp_ab")],
            [btn("⏭ بدون نص", "camp_skip_cap")],
        )
    )
    return S_CAMP_CAPTION


async def cb_camp_ab(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء اختبار A/B — طلب النسخة الأولى."""
    await _answer(update)
    await _edit(update,
        "🎯 *اختبار A/B*\n━━━━━━━━━━━━━━━━━━\n\n"
        "هتكتب نسختين من النص، والبوت ينشرهم بالتناوب على المجموعات،\n"
        "وفي الآخر يقولك أي نسخة وصلت أكتر.\n\n"
        "✍️ أرسل *النسخة الأولى (أ):*",
        ik(back_btn("campaigns"))
    )
    return S_CAMP_AB_A


async def camp_got_ab_a(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام النسخة أ."""
    context.user_data.setdefault("camp", {})["ab_a"] = update.message.text.strip()
    await _send(update,
        "🅰️ *تم حفظ النسخة الأولى.*\n\n"
        "✍️ الآن أرسل *النسخة الثانية (ب):*",
        inline_kb=ik(back_btn("campaigns"))
    )
    return S_CAMP_AB_B


async def camp_got_ab_b(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام النسخة ب — ثم المتابعة لاختيار الوجهة."""
    camp = context.user_data.setdefault("camp", {})
    camp["ab_b"] = update.message.text.strip()
    camp["ab_variants"] = [camp.get("ab_a", ""), camp["ab_b"]]
    camp["caption"] = camp.get("ab_a", "")  # افتراضي للعرض
    await _send(update,
        "🅱️ *تم حفظ النسختين!*\n━━━━━━━━━━━━━━━━━━\n\n"
        "🅰️ " + _escape_md(camp.get("ab_a","")[:80]) + "\n\n"
        "🅱️ " + _escape_md(camp.get("ab_b","")[:80]) + "\n\n"
        "الآن اختر وجهة النشر:",
        inline_kb=ik(
            [btn("👥 نشر في مجموعات", "camp_tgt_groups")],
            [btn("📄 نشر في صفحات",   "camp_tgt_pages")],
            back_btn("campaigns"),
        )
    )
    return S_CAMP_TARGETS


async def cb_camp_quick_tpl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض القوالب الجاهزة لاختيار واحد."""
    await _answer(update)
    rows = [[btn(t["title"], f"qtpl_{i}")] for i, t in enumerate(QUICK_TEMPLATES)]
    rows.append([btn("✍️ سأكتب بنفسي", "camp_skip_cap")])
    await _edit(update,
        "📦 *القوالب الجاهزة*\n━━━━━━━━━━━━━━━━━━\n\n"
        "اختر قالباً وسيُستخدم كنص المنشور (تقدر تعدّله بإرسال نص جديد):",
        ik(*rows)
    )
    return S_CAMP_CAPTION


async def cb_quick_tpl_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تطبيق قالب جاهز كنص الحملة."""
    await _answer(update)
    try:
        idx = int(update.callback_query.data.replace("qtpl_", ""))
        tpl = QUICK_TEMPLATES[idx]
    except (ValueError, IndexError):
        await _answer(update, "قالب غير صالح", True)
        return S_CAMP_CAPTION
    context.user_data.setdefault("camp", {})["caption"] = tpl["content"]
    await _edit(update,
        f"✅ *تم اختيار قالب: {tpl['title']}*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"{_escape_md(tpl['content'])}\n\n"
        "تقدر تعدّله بإرسال نص جديد، أو تابع بالقالب كما هو 👇",
        ik([btn("✅ متابعة بهذا القالب", "camp_use_tpl")],
           [btn("📦 قالب آخر", "camp_quick_tpl")])
    )
    return S_CAMP_CAPTION


async def cb_camp_use_tpl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """متابعة الحملة بالقالب المختار."""
    await _answer(update)
    if context.user_data.get("pg_selected"):
        return await _launch_page_post(update, context)
    return await _ask_camp_targets(update, context)


async def _ask_camp_targets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send(update,
        "📍 *الخطوة 3/4 — وجهة النشر*",
        inline_kb=ik(
            [btn("👥 نشر في مجموعات", "camp_tgt_groups")],
            [btn("📄 نشر في صفحات",   "camp_tgt_pages")],
        )
    )
    return S_CAMP_TARGETS


async def cb_camp_tgt_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    groups = await db.get_groups(uid)
    context.user_data["camp"]["target_type"] = "groups"
    if not groups:
        await _edit(update, "⚠️ لا توجد مجموعات! اسحبها من قسم المجموعات أولاً.",
            ik([btn("👥 المجموعات","groups")]))
        return S_CAMP_TARGETS
    context.user_data["camp"]["all_targets"] = groups
    context.user_data["camp"]["sel_targets"] = []
    return await _render_target_sel(update, context)


async def _render_target_sel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    targets = context.user_data["camp"]["all_targets"]
    sel     = context.user_data["camp"]["sel_targets"]
    rows = []
    for t in targets[:30]:
        check = "✅" if t["id"] in sel else "⬜"
        rows.append([btn(f"{check} {t.get('group_name','')[:35]}", f"tsel_{t['id']}")])
    rows.append([btn("✅ تحديد الكل","tsel_all"), btn("🔘 إلغاء الكل","tsel_none")])
    rows.append([btn(f"▶️ التالي ({len(sel)} محدد)","camp_confirm_tgt")])
    rows.append(back_btn("campaigns"))
    text = f"👥 *اختر المجموعات:*\n(إجمالي: {len(targets)} — محدد: {len(sel)})"
    try:
        await update.callback_query.edit_message_text(text, reply_markup=ik(*rows), parse_mode=ParseMode.MARKDOWN)
    except Exception:
        msg = update.message or update.callback_query.message
        await msg.reply_text(text, reply_markup=ik(*rows), parse_mode=ParseMode.MARKDOWN)
    return S_CAMP_TARGETS


async def cb_tsel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    tid = int(update.callback_query.data.split("_")[-1])
    sel = context.user_data["camp"]["sel_targets"]
    if tid in sel:
        sel.remove(tid)
    else:
        sel.append(tid)
    return await _render_target_sel(update, context)


async def cb_tsel_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    context.user_data["camp"]["sel_targets"] = [t["id"] for t in context.user_data["camp"]["all_targets"]]
    return await _render_target_sel(update, context)


async def cb_tsel_none(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    context.user_data["camp"]["sel_targets"] = []
    return await _render_target_sel(update, context)


async def cb_camp_confirm_tgt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    camp = context.user_data["camp"]
    sel = camp.get("sel_targets", [])
    if not sel:
        await _answer(update, "اختر هدفاً واحداً على الأقل!", True)
        return S_CAMP_TARGETS
    # 👁️ معاينة المنشور قبل النشر
    caption = camp.get("caption", "") or "(بدون نص)"
    media = camp.get("media_type")
    media_label = {"video": "🎥 فيديو", "photo": "🖼 صورة"}.get(media, "📄 بدون ميديا")
    preview = caption if len(caption) <= 300 else caption[:300] + "…"
    await _edit(update,
        f"👁️ *معاينة المنشور*\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📎 المحتوى: {media_label}\n"
        f"🎯 الوجهة: *{len(sel)}* مجموعة\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📝 *النص:*\n{_escape_md(preview)}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"اختر موعد النشر:",
        ik(
            [btn("🚀 نشر الآن",   "camp_now")],
            [btn("⏰ جدولة لاحقاً", "camp_sched_prompt")],
            [btn("🔁 جدولة متكررة", "camp_recurring")],
            [btn("✏️ تعديل النص", "camp_edit_cap"),
             btn("💾 حفظ كمسودّة", "camp_save_draft")],
            back_btn("campaigns"),
        )
    )
    return S_CAMP_SCHEDULE


async def cb_camp_recurring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اختيار تكرار الحملة."""
    await _answer(update)
    await _edit(update,
        "🔁 *جدولة متكررة*\n━━━━━━━━━━━━━━━━━━\n\n"
        "كل قد إيه تتكرر الحملة؟",
        ik(
            [btn("📅 كل يوم", "rec_daily")],
            [btn("📆 كل أسبوع", "rec_weekly")],
            back_btn("campaigns"),
        )
    )
    return S_CAMP_SCHEDULE


async def cb_camp_set_recurring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حفظ نوع التكرار ثم طلب وقت البدء."""
    await _answer(update)
    rec = update.callback_query.data.replace("rec_", "")
    context.user_data["camp"]["recurring"] = rec
    label = "يومياً" if rec == "daily" else "أسبوعياً"
    now_c = _cairo_now()
    await _edit(update,
        f"🔁 *تكرار {label}*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"أرسل وقت النشر (سيتكرر {label} في نفس الوقت):\n\n"
        f"الوقت الآن بالقاهرة: *{now_c.strftime('%H:%M')}*\nمثال: `14:30`",
        ik(back_btn("campaigns"))
    )
    return S_CAMP_SCHEDULE


async def cb_camp_edit_cap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تعديل نص المنشور من المعاينة."""
    await _answer(update)
    await _edit(update,
        "✏️ *أرسل النص الجديد للمنشور:*",
        ik(back_btn("campaigns"))
    )
    return S_CAMP_CAPTION


async def cb_camp_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    return await _launch_campaign(update, context)


async def cb_camp_sched_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    now_c = _cairo_now()
    await _edit(update,
        f"⏰ *أرسل وقت النشر (توقيت القاهرة):*\n\n"
        f"الوقت الحالي بالقاهرة: *{now_c.strftime('%H:%M')}*\n\n"
        f"مثال: `14:30` أو `2025-06-01 14:30`",
        ik(back_btn("campaigns"))
    )
    return S_CAMP_SCHEDULE


async def camp_got_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_str = update.message.text.strip()
    dt = _parse_cairo_time(time_str)
    if not dt:
        await _send(update,
            "❌ *صيغة غير مفهومة.*\n\n"
            "استخدم مثلاً: `14:30` أو `2025-06-01 14:30`"
        )
        return S_CAMP_SCHEDULE

    readable = _format_cairo_dt(dt)
    context.user_data["camp"]["schedule"] = dt.strftime("%Y-%m-%d %H:%M")
    context.user_data["camp"]["schedule_dt"] = dt

    await _send(update,
        f"🕐 *فهمت الموعد هكذا:*\n\n"
        f"📅 {readable}\n"
        f"(توقيت القاهرة)\n\n"
        f"هل تريد تأكيد هذا الموعد؟",
        inline_kb=ik(
            [btn("✅ تأكيد الموعد",  "camp_sched_confirm")],
            [btn("✏️ تعديل الموعد", "camp_sched_prompt")],
            [btn("❌ إلغاء",         "campaigns")],
        )
    )
    return S_CAMP_SCHEDULE


async def _launch_campaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    camp = context.user_data.get("camp", {})
    accs = await db.get_accounts(uid)
    if not accs:
        await _send(update, "❌ لا يوجد حساب مربوط!")
        return S_CAMPAIGNS
    acc = accs[0]
    sel = camp.get("sel_targets", [])
    camp_id = await db.add_campaign(
        uid, acc["id"],
        title=f"حملة {datetime.now().strftime('%m-%d %H:%M')}",
        content=camp.get("caption",""),
        targets=sel,
        target_type=camp.get("target_type","groups"),
        media_path=camp.get("media_path"),
        media_type=camp.get("media_type"),
        schedule_time=camp.get("schedule"),
        recurring=camp.get("recurring", ""),
    )
    sched = camp.get("schedule")
    if sched:
        await _send(update,
            f"✅ *تم جدولة الحملة #{camp_id}*\n\nالنشر في: *{sched}*\nالمجموعات: {len(sel)}",
            inline_kb=ik([btn("🗂 السجل","camp_log_cb")])
        )
    else:
        # منع الإفراط: فاصل زمني + حد الحملات المتزامنة
        now_ts = _time_mod.time()
        last = _last_campaign_time.get(uid, 0)
        if now_ts - last < MIN_SECONDS_BETWEEN_CAMPAIGNS:
            wait = int(MIN_SECONDS_BETWEEN_CAMPAIGNS - (now_ts - last))
            await db.update_campaign_status(camp_id, "failed")
            await _send(update, f"⏳ انتظر *{wait} ثانية* قبل بدء حملة جديدة.")
            return S_CAMPAIGNS
        if _active_campaigns.get(uid, 0) >= MAX_CONCURRENT_CAMPAIGNS:
            await db.update_campaign_status(camp_id, "failed")
            await _send(update,
                f"⚠️ لديك *{MAX_CONCURRENT_CAMPAIGNS}* حملات شغّالة بالفعل.\n"
                "انتظر انتهاءها قبل بدء حملة جديدة.")
            return S_CAMPAIGNS

        user_obj = await db.get_user(uid)
        delay = user_obj.get("time_delay", 60)
        anti = user_obj.get("anti_ban_level","medium")
        _last_campaign_time[uid] = now_ts
        _active_campaigns[uid] = _active_campaigns.get(uid, 0) + 1
        # نرسل رسالة شريط التقدّم ونحتفظ بها للتحديث
        progress_msg = None
        try:
            _m = update.message or update.callback_query.message
            progress_msg = await _m.reply_text(
                f"🚀 بدأت الحملة #{camp_id}!\n\n"
                f"⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ 0%\n"
                f"0/{len(sel)} مجموعة\n"
                f"الفاصل: {delay}ث"
            )
        except Exception:
            pass
        asyncio.create_task(_run_campaign_bg(context, uid, camp_id, acc, sel, camp.get("caption",""), camp.get("media_path"), delay, anti, progress_msg, camp.get("ab_variants")))
    context.user_data["camp"] = {}
    return S_CAMPAIGNS


def _progress_bar(done, total):
    """يبني شريط تقدّم مرئي."""
    if total <= 0:
        return "⬜" * 10 + " 0%"
    pct = int((done / total) * 100)
    filled = pct // 10
    return "🟩" * filled + "⬜" * (10 - filled) + f" {pct}%"


async def _run_campaign_bg(context, uid, camp_id, acc, group_ids, caption, media_path, delay, anti, progress_msg=None, ab_variants=None):
    """تشغيل الحملة في الخلفية مع:
    - تدوير الحسابات تلقائياً (Account Rotation)
    - نشر موزّع: كل حساب ينشر في مجموعة مختلفة
    - كشف الحساب المحظور وإشعار صاحبه + الأدمن
    - اختبار A/B: التناوب بين نسختين من النص
    """
    # نسخ A/B: قائمة [نص أ, نص ب]
    ab_stats = {"A": 0, "B": 0}
    await db.update_campaign_status(camp_id, "running")
    from fb_automator import FBAutomator

    # كل حسابات المستخدم لتدويرها
    all_accounts = await db.get_accounts(uid)
    if not all_accounts:
        all_accounts = [acc]

    done = failed = 0
    banned_accounts = []
    all_groups = await db.get_groups(uid)
    targets = [g for g in all_groups if g["id"] in group_ids]

    acc_count = len(all_accounts)
    for idx, g in enumerate(targets):
        # تدوير الحسابات: كل مجموعة تأخذ الحساب التالي بالدور
        current = all_accounts[idx % acc_count]
        if current["id"] in [b["id"] for b in banned_accounts]:
            # تخطّي الحسابات المحظورة، جرّب التالي
            healthy = [a for a in all_accounts if a["id"] not in [b["id"] for b in banned_accounts]]
            if not healthy:
                break
            current = healthy[idx % len(healthy)]

        auto = FBAutomator(current["id"], current.get("cookies", ""), current.get("proxy"))
        # اختيار النص: A/B بالتناوب أو النص العادي
        if ab_variants and len(ab_variants) == 2:
            variant = "A" if idx % 2 == 0 else "B"
            post_caption = ab_variants[0] if variant == "A" else ab_variants[1]
        else:
            variant = None
            post_caption = caption
        try:
            r = await auto.post_to_group(
                g["group_id"], post_caption, media_path,
                delay_range=(max(delay - 20, 10), delay + 40),
                anti_ban_level=anti,
            )
        except Exception as e:
            r = {"success": False, "error": str(e)}

        if r.get("success"):
            done += 1
            if variant:
                ab_stats[variant] += 1
        else:
            failed += 1
            err = str(r.get("error", "")).lower()
            # كشف الحظر / انتهاء الكوكيز
            if any(k in err for k in ("login", "checkpoint", "blocked", "banned", "حظر", "تسجيل")):
                if current not in banned_accounts:
                    banned_accounts.append(current)
        await db.update_campaign_status(camp_id, "running", done)
        # تحديث شريط التقدّم الحي
        if progress_msg:
            try:
                processed = done + failed
                await progress_msg.edit_text(
                    f"🚀 الحملة #{camp_id} جارية...\n\n"
                    f"{_progress_bar(processed, len(targets))}\n"
                    f"✅ {done} | ❌ {failed} | 📊 {processed}/{len(targets)}"
                )
            except Exception:
                pass

    status = "done" if done else "failed"
    await db.update_campaign_status(camp_id, status, done)
    # تنظيف ملف الميديا المؤقت بعد انتهاء الحملة
    try:
        if media_path and os.path.exists(media_path) and media_path.startswith("/tmp/"):
            os.remove(media_path)
    except Exception:
        pass
    await db.log_activity(uid, "campaign_done", f"حملة #{camp_id}: ✅{done} ❌{failed}")
    # منح نقاط إتمام الحملة
    if done:
        try:
            _u = await db.get_user(uid)
            await db.update_user(uid, points=(_u.get("points", 0) + int(await S.get("points_campaign"))))
        except Exception:
            pass

    # إشعار صاحب الحملة (يحترم إعداد الإشعارات)
    msg = f"✅ *انتهت الحملة #{camp_id}!*\n\n✅ نجح: {done}\n❌ فشل: {failed}"
    if ab_variants and (ab_stats["A"] or ab_stats["B"]):
        winner = "النسخة أ 🅰️" if ab_stats["A"] >= ab_stats["B"] else "النسخة ب 🅱️"
        msg += (f"\n\n🎯 *نتيجة اختبار A/B:*\n"
                f"🅰️ نُشرت: {ab_stats['A']}\n🅱️ نُشرت: {ab_stats['B']}\n"
                f"🏆 الأكثر وصولاً: {winner}")
    if banned_accounts:
        names = "، ".join(_acc_name(b) for b in banned_accounts)
        msg += f"\n\n🚫 *حسابات يبدو أنها محظورة/منتهية:*\n{names}"
    await _notify_user(context, uid, msg)

    # إشعار الأدمن بالحسابات المحظورة فوراً
    if banned_accounts:
        try:
            names = "، ".join(_acc_name(b) for b in banned_accounts)
            await context.bot.send_message(
                ADMIN_ID,
                f"🚫 تنبيه حظر: المستخدم {uid} لديه حسابات محظورة بعد حملة #{camp_id}:\n{names}",
            )
        except Exception:
            pass

    # تحرير عدّاد الحملات النشطة
    try:
        _active_campaigns[uid] = max(0, _active_campaigns.get(uid, 1) - 1)
    except Exception:
        pass


async def _show_camp_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    camps = await db.get_campaigns(uid)
    text = "🗂 *سجل الحملات*\n━━━━━━━━━━━━━━━━━━\n\n"
    if not camps:
        text += "لا توجد حملات."
    for c in camps[:10]:
        status = _status_label(c.get("status","pending"))
        text += (
            f"#{c['id']} *{c.get('title','حملة')}*\n"
            f"{status} | {c.get('posts_done',0)}/{c.get('posts_total',0)}\n"
            f"📅 {_fmt_date(c.get('created_at',''))}\n\n"
        )
    await _send(update, text, inline_kb=ik([btn("🔄 تحديث","camp_log_cb")]))
    return S_CAMPAIGNS


async def cb_camp_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    return await _show_camp_log(update, context)


# ══════════════════════════════════════════════════════════════
#  COMMENTS
# ══════════════════════════════════════════════════════════════

async def _show_comments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send(update,
        "💬 *إدارة التعليقات*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "من هنا يمكنك تشغيل أدوات التعليقات بشكل واضح:\n\n"
        "1) إضافة تعليق على أحدث منشورات جروب أو صفحة.\n"
        "2) الرد على التعليقات الحالية أو مراقبة الجديدة والرد عليها.\n"
        "3) منشن المتفاعلين على منشور جروب من الأشخاص الأعلى تفاعلاً.\n\n"
        "اختر ما الذي تريد تشغيله الآن:",
        inline_kb=ik(
            [btn("🖊 إضافة تعليق على منشورات", "cmt_add")],
            [btn("💬 الرد على التعليقات",      "cmt_reply")],
            [btn("😊 منشن المتفاعلين",          "cmt_mention")],
            [btn("🤖 شات بوت الصفحات",          "cmt_chatbot")],
            [btn("❌ إلغاء",                    "main")],
        )
    )
    return S_COMMENTS


async def cb_comments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    return await _show_comments(update, context)


async def cb_cmt_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    action = update.callback_query.data
    if action == "cmt_add":
        return await cb_cmt_add_start(update, context)
    if action == "cmt_reply":
        return await cb_cmt_reply_start(update, context)
    if action == "cmt_mention":
        return await cb_cmt_mention_start(update, context)
    if action == "cmt_chatbot":
        # توجيه لتدفّق بوت الصفحات الفعلي (موجود ويعمل)
        return await cb_pagebot_new(update, context)
    user = await db.get_user(uid)
    plan = user.get("plan","free") if user else "free"
    label = {"cmt_chatbot": "شات بوت الصفحات"}.get(action, action)
    await _edit(update,
        f"🔒 *{label}*\n\nهذه الميزة متاحة لمشتركي Pro وما فوق.",
        ik([btn("💎 ترقية الخطة","plan_upgrade")], back_btn("comments"))
    )
    return S_COMMENTS


async def cb_cmt_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    accs = await db.get_accounts(uid)
    if not accs:
        await _edit(update, "❌ يجب ربط حساب فيسبوك أولاً.", ik([btn("👤 الحسابات","accounts")]))
        return S_COMMENTS
    context.user_data["cmt_reply"] = {}
    rows = [[btn(f"👤 {a.get('account_name','حساب')}", f"cmtr_acc_{a['id']}")] for a in accs[:8]]
    rows.append(back_btn("comments"))
    await _edit(update,
        "💬 *الرد على التعليقات — الخطوة 1/3*\n━━━━━━━━━━━━━━━━━━\n\nاختر الحساب:",
        ik(*rows)
    )
    return S_CMT_URL


async def cb_cmt_mention_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    accs = await db.get_accounts(uid)
    if not accs:
        await _edit(update, "❌ يجب ربط حساب فيسبوك أولاً.", ik([btn("👤 الحسابات","accounts")]))
        return S_COMMENTS
    context.user_data["cmt_mention"] = {}
    rows = [[btn(f"👤 {a.get('account_name','حساب')}", f"cmtm_acc_{a['id']}")] for a in accs[:8]]
    rows.append(back_btn("comments"))
    await _edit(update,
        "😊 *منشن المتفاعلين — الخطوة 1/3*\n━━━━━━━━━━━━━━━━━━\n\nاختر الحساب:",
        ik(*rows)
    )
    return S_CMT_URL


async def _show_admin_from_kb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await _send(update, "❌ ليس لديك صلاحية.")
        return S_MAIN
    return await cmd_admin(update, context)


async def cb_camp_sched_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    return await _launch_campaign(update, context)


async def cb_pagebot_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    pages = await db.get_pages(uid)
    if not pages:
        await _edit(update,
            "❌ يجب جلب الصفحات أولاً من قسم الحسابات.",
            ik(back_btn("page_bot"))
        )
        return S_PAGE_BOT
    rows = [[btn(f"📄 {p.get('page_name', p.get('name', 'صفحة'))}", f"pbot_pg_{p['id']}")] for p in pages[:8]]
    rows.append(back_btn("page_bot"))
    context.user_data["pagebot"] = {}
    await _edit(update,
        "🤖 *إضافة بوت — الخطوة 1/5*\n━━━━━━━━━━━━━━━━━━\n\nاختر الصفحة:",
        ik(*rows)
    )
    return S_PAGE_BOT


async def cb_pbot_pg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    pg_id = update.callback_query.data.replace("pbot_pg_", "")
    pages = await db.get_pages(uid)
    pg = next((p for p in pages if str(p["id"]) == pg_id), None)
    if not pg:
        return S_PAGE_BOT
    context.user_data["pagebot"]["page_id"]   = str(pg_id)
    context.user_data["pagebot"]["page_name"] = pg.get("page_name", pg.get("name", "صفحة"))
    await _edit(update,
        "🤖 *الخطوة 2/5 — رابط منشور فيسبوك*\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل رابط المنشور الذي سيردّ عليه البوت:\n"
        "مثال: `https://www.facebook.com/photo?fbid=…`",
        ik(back_btn("page_bot"))
    )
    return S_PAGE_BOT_URL


async def pagebot_got_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if "facebook.com" not in url:
        await _send(update, "❌ يجب أن يكون رابط فيسبوك صحيح.")
        return S_PAGE_BOT_URL
    context.user_data["pagebot"]["post_url"] = url
    uid = update.effective_user.id
    tpls = await db.get_templates(uid, "reply")
    rows = [[btn(f"📦 {t['title']}", f"pbot_tpl_{t['id']}")] for t in tpls[:8]]
    rows.append([btn("⏭ بدون قالب", "pbot_tpl_none")])
    rows.append(back_btn("page_bot"))
    await _send(update,
        "🤖 *الخطوة 3/5 — القالب*\n━━━━━━━━━━━━━━━━━━\n\nاختر قالب الرد:",
        inline_kb=ik(*rows)
    )
    return S_PAGE_BOT_TPL


async def cb_pbot_tpl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    data = update.callback_query.data
    tpl_name = "بدون قالب" if data == "pbot_tpl_none" else data.replace("pbot_tpl_", "tpl_")
    context.user_data["pagebot"]["template_name"] = tpl_name
    await _edit(update,
        "🤖 *الخطوة 4/5 — الكلمات المفتاحية*\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل الكلمات المفتاحية التي ستُشغّل البوت.\n"
        "كل كلمة في سطر جديد:",
        ik(back_btn("page_bot"))
    )
    return S_PAGE_BOT_KW


async def pagebot_got_kw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kws = [k.strip() for k in update.message.text.splitlines() if k.strip()]
    context.user_data["pagebot"]["keywords"] = kws
    await _send(update,
        "🤖 *الخطوة 5أ/5 — نص الرد على التعليق*\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل نص الرد الذي سيُضاف كتعليق:",
        inline_kb=ik([btn("⏭ تخطي", "pbot_rcmt_skip")], back_btn("page_bot"))
    )
    return S_PAGE_BOT_RCMT


async def pagebot_got_rcmt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pagebot"]["reply_comment"] = update.message.text.strip()
    await _send(update,
        "🤖 *الخطوة 5ب/5 — نص الرسالة الخاصة (DM)*\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل نص الرسالة الخاصة التي ستُرسل للمستخدم:",
        inline_kb=ik([btn("⏭ تخطي", "pbot_rdm_skip")], back_btn("page_bot"))
    )
    return S_PAGE_BOT_RDM


async def cb_pbot_rcmt_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    context.user_data["pagebot"]["reply_comment"] = ""
    await _edit(update,
        "🤖 *الخطوة 5ب/5 — نص الرسالة الخاصة (DM)*\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل نص الرسالة الخاصة التي ستُرسل للمستخدم:",
        ik([btn("⏭ تخطي", "pbot_rdm_skip")], back_btn("page_bot"))
    )
    return S_PAGE_BOT_RDM


async def cb_pbot_rdm_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    context.user_data["pagebot"]["reply_dm"] = ""
    return await _save_pagebot(update, context)


async def pagebot_got_rdm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pagebot"]["reply_dm"] = update.message.text.strip()
    return await _save_pagebot(update, context)


async def _save_pagebot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    pb = context.user_data.get("pagebot", {})
    accs = await db.get_accounts(uid)
    acc_id = accs[0]["id"] if accs else 0
    await db.add_page_bot(
        user_id=uid,
        account_id=acc_id,
        page_id=pb.get("page_id", ""),
        page_name=pb.get("page_name", ""),
        post_url=pb.get("post_url", ""),
        template_name=pb.get("template_name", ""),
        keywords=pb.get("keywords", []),
        reply_comment=pb.get("reply_comment", ""),
        reply_dm=pb.get("reply_dm", ""),
    )
    context.user_data.pop("pagebot", None)
    await _send(update,
        "✅ *تم حفظ البوت بنجاح!*\n\n"
        f"📄 الصفحة: {pb.get('page_name','')}\n"
        f"🔑 الكلمات: {', '.join(pb.get('keywords', []))}\n\n"
        "البوت الآن نشط ويراقب التعليقات.",
        inline_kb=ik(back_btn("page_bot"))
    )
    return S_PAGE_BOT


async def cb_pagebot_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    bot_id = int(update.callback_query.data.split("_")[-1])
    await db.delete_page_bot(bot_id, uid)
    await _answer(update, "تم حذف البوت ✅")
    return await _show_page_bot(update, context)


async def cb_cmt_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    accs = await db.get_accounts(uid)
    if not accs:
        await _edit(update,
            "❌ يجب ربط حساب فيسبوك أولاً.",
            ik([btn("👤 الحسابات","accounts")])
        )
        return S_COMMENTS
    context.user_data["cmt"] = {}
    rows = [[btn(f"👤 {a.get('account_name', a.get('name', 'حساب'))}", f"cmt_acc_{a['id']}")] for a in accs[:8]]
    rows.append(back_btn("comments"))
    await _edit(update,
        "💬 *إضافة تعليق — الخطوة 1/3*\n━━━━━━━━━━━━━━━━━━\n\nاختر الحساب:",
        ik(*rows)
    )
    return S_CMT_URL


async def cb_cmt_acc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    data = update.callback_query.data
    acc_id = int(data.split("_")[-1])
    # تحديد نوع العملية من البادئة (cmt_ / cmtr_ / cmtm_)
    if data.startswith("cmtr_"):
        context.user_data["cmt_mode"] = "reply"
    elif data.startswith("cmtm_"):
        context.user_data["cmt_mode"] = "mention"
    else:
        context.user_data["cmt_mode"] = "add"
    # نستخدم دائماً المفتاح الموحد "cmt" لتجنب KeyError
    cmt = context.user_data.setdefault("cmt", {})
    cmt["account_id"] = acc_id

    mode = context.user_data["cmt_mode"]
    if mode == "reply":
        prompt = (
            "💬 *الخطوة 2/3 — رابط المنشور*\n━━━━━━━━━━━━━━━━━━\n\n"
            "أرسل رابط المنشور الذي تريد الرد على تعليقاته:\n"
            "مثال: `https://www.facebook.com/groups/12345/posts/99999`"
        )
    elif mode == "mention":
        prompt = (
            "😊 *الخطوة 2/3 — رابط المنشور*\n━━━━━━━━━━━━━━━━━━\n\n"
            "أرسل رابط منشور الجروب لعمل منشن للمتفاعلين:\n"
            "مثال: `https://www.facebook.com/groups/12345/posts/99999`"
        )
    else:
        prompt = (
            "💬 *الخطوة 2/3 — رابط المنشور أو الجروب*\n━━━━━━━━━━━━━━━━━━\n\n"
            "أرسل رابط المنشور أو الجروب الذي تريد التعليق فيه:\n"
            "مثال: `https://www.facebook.com/groups/12345/posts/99999`"
        )
    await _edit(update, prompt, ik(back_btn("comments")))
    return S_CMT_URL


async def cmt_got_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if "facebook.com" not in url:
        await _send(update, "❌ يجب أن يكون رابطاً من فيسبوك.")
        return S_CMT_URL
    context.user_data["cmt"]["url"] = url
    await _send(update,
        "💬 *الخطوة 3/3 — نص التعليق*\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل نص التعليق الذي تريد إضافته:",
        inline_kb=ik(back_btn("comments"))
    )
    return S_CMT_TEXT


async def cmt_got_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if not txt:
        await _send(update, "❌ يجب إدخال نص الرسالة.")
        return S_CMT_TEXT
    cmt = context.user_data.setdefault("cmt", {})
    cmt["text"] = txt
    mode = context.user_data.get("cmt_mode", "add")
    titles = {
        "add":     ("✅ *تأكيد التعليق*", "💬 التعليق:", "إضافة التعليق"),
        "reply":   ("✅ *تأكيد الرد*", "↩️ الرد:", "بدء الرد على التعليقات"),
        "mention": ("✅ *تأكيد المنشن*", "😊 نص المنشن:", "بدء المنشن"),
    }
    title, lbl, action = titles.get(mode, titles["add"])
    await _send(update,
        f"{title}\n━━━━━━━━━━━━━━━━━━\n\n"
        f"📎 الرابط: `{cmt.get('url','')[:60]}`\n"
        f"{lbl} {txt}\n\n"
        f"هل تريد المتابعة الآن؟",
        inline_kb=ik(
            [btn(f"✅ {action}", "cmt_confirm")],
            [btn("❌ إلغاء",      "comments")],
        )
    )
    return S_CMT_TEXT


async def cb_cmt_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    cmt = context.user_data.get("cmt", {})
    mode = context.user_data.get("cmt_mode", "add")
    acc_id = cmt.get("account_id")
    url   = cmt.get("url", "")
    text  = cmt.get("text", "")
    accs = await db.get_accounts(uid)
    acc = next((a for a in accs if a["id"] == acc_id), None)
    if not acc:
        await _edit(update, "❌ الحساب غير موجود.", ik(back_btn("comments")))
        return S_COMMENTS

    progress = {
        "add":     "⏳ *جارٍ إضافة التعليق…*",
        "reply":   "⏳ *جارٍ الرد على التعليقات…*",
        "mention": "⏳ *جارٍ عمل المنشن للمتفاعلين…*",
    }.get(mode, "⏳ *جارٍ التنفيذ…*")
    await _edit(update, progress, ik())

    from fb_automator import FBAutomator
    auto = FBAutomator(acc["id"], acc.get("cookies", ""), acc.get("proxy"))

    try:
        if mode == "reply":
            ok, info = await auto.reply_to_comments(url, text)
            log_action, ok_msg = "رد على تعليقات", f"✅ *تم الرد على {info} تعليق!*"
        elif mode == "mention":
            ok, info = await auto.mention_reactors(url, text)
            log_action, ok_msg = "منشن المتفاعلين", f"✅ *تم عمل منشن لـ {info} متفاعل!*"
        else:
            ok, err = await auto.post_comment(url, text)
            info = err
            log_action, ok_msg = "تعليق", "✅ *تم إضافة التعليق بنجاح!*"
    except Exception as e:
        ok, info = False, str(e)
        log_action, ok_msg = mode, ""

    await db.log_activity(uid, log_action, f"URL: {url[:60]}", "success" if ok else "error")
    if ok:
        await _edit(update, ok_msg, ik(back_btn("comments")))
    else:
        await _edit(update, f"❌ *فشلت العملية:*\n{_escape_md(str(info))}", ik(back_btn("comments")))
    context.user_data.pop("cmt", None)
    context.user_data.pop("cmt_mode", None)
    return S_COMMENTS


# ══════════════════════════════════════════════════════════════
#  MY PLAN
# ══════════════════════════════════════════════════════════════

async def _show_my_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = await db.get_user(uid)
    plan = user.get("plan","free")
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    accs = await db.get_accounts(uid)
    grps = await db.get_groups(uid)
    camps = await db.get_campaigns(uid)
    exp = _fmt_date(user.get("plan_expires","")) or "—"
    text = (
        "💎 *العضوية والنقاط*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"راجع اشتراكك، استخدامك، والترقية من هنا."
    )
    await _send(update, text, inline_kb=ik(
        [btn(f"🎁 تجربة {_plan_label(TRIAL_PLAN).split()[-1]} {TRIAL_DAYS} يوم", "plan_trial")],
        [btn("📊 استخدام الخطة",   "plan_usage"),
         btn("💎 تفاصيل الاشتراك", "plan_details")],
        [btn("⬆️ ترقية الخطة",     "plan_upgrade"),
         btn("🎁 ادعُ واربح",      "plan_referral")],
        [btn("🎁 استبدال النقاط",  "plan_redeem"),
         btn("💰 شرح نظام النقاط","plan_points")],
        [btn("🔑 تفعيل كود",       "activate_code_btn")],
        [btn("🧰 الأدوات","tools_cb"), btn("🏠 الرئيسية","main")],
    ))
    return S_MY_PLAN


async def cb_my_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    return await _show_my_plan(update, context)


async def cb_plan_usage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    user = await db.get_user(uid)
    plan = user.get("plan","free")
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    accs  = await db.get_accounts(uid)
    grps  = await db.get_groups(uid)
    camps = await db.get_campaigns(uid)
    today_camps = [c for c in camps if c.get("created_at","")[:10] == datetime.now().strftime("%Y-%m-%d")]
    exp = _fmt_date(user.get("plan_expires","")) or "—"
    await _edit(update,
        f"📊 *استخدام الخطة*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"الخطة: *{_plan_label(plan)}*\n"
        f"تنتهي: *{exp}*\n\n"
        f"🔗 الحسابات: {len(accs)}/{limits['max_accounts']}\n"
        f"👥 المجموعات: {len(grps)}/{limits['max_groups']}\n"
        f"🚀 حملات اليوم: {len(today_camps)}/{limits['max_campaigns']}\n"
        f"🪙 النقاط: {user.get('points',0)}",
        ik(back_btn("my_plan"))
    )
    return S_MY_PLAN


async def cb_plan_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    user = await db.get_user(uid)
    plan = user.get("plan","free")
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    exp = _fmt_date(user.get("plan_expires","")) or "دائم"
    await _edit(update,
        f"💎 *تفاصيل الاشتراك*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"الخطة: *{_plan_label(plan)}*\n"
        f"السعر: *{limits['price']}*\n"
        f"تنتهي: *{exp}*\n\n"
        f"الحدود:\n"
        f"• حسابات: {limits['max_accounts']}\n"
        f"• مجموعات: {limits['max_groups']}\n"
        f"• حملات/يوم: {limits['max_campaigns']}\n",
        ik(back_btn("my_plan"))
    )
    return S_MY_PLAN


async def cb_plan_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    user = await db.get_user(uid)
    if user.get("plan","free") != "free":
        await _answer(update, "أنت بالفعل مشترك!", True)
        return S_MY_PLAN
    await _edit(update,
        f"🎁 *تجربة {_plan_label(TRIAL_PLAN)} — {TRIAL_DAYS} يوم مجاناً*\n━━━━━━━━━━━━━━━━━━\n\n"
        "للحصول على التجربة:\n\n"
        "1. تواصل مع الدعم\n"
        "2. سيرسل لك كود التفعيل\n\n"
        f"الدعم: {SUPPORT_USERNAME}",
        ik([btn("📞 تواصل مع الدعم", url=f"https://t.me/{SUPPORT_USERNAME.strip('@')}")],
           back_btn("my_plan"))
    )
    return S_MY_PLAN


async def cb_plan_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    # وصف الخطط يتولّد من PLAN_LIMITS
    pro = PLAN_LIMITS.get("pro", {})
    unl = PLAN_LIMITS.get("unlimited", {})
    text = (
        "🛒 *اختر الخطة المناسبة لك*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"⭐ *Pro — {pro.get('price','')}*\n"
        f"• {pro.get('max_accounts','')} حسابات فيسبوك\n"
        f"• {pro.get('max_groups','')} مجموعة\n"
        f"• {pro.get('max_campaigns','')} حملة\n"
        "• جدولة + قوالب + صفحات\n\n"
        f"👑 *Unlimited — {unl.get('price','')}*\n"
        f"• {unl.get('max_accounts','')} حسابات فيسبوك\n"
        "• غير محدود مجموعات\n"
        "• غير محدود حملات\n"
        "• كل المميزات + أولوية دعم\n\n"
        "اختر الباقة التي تريد الاشتراك فيها:"
    )
    # الأزرار تتولّد من الباقات (الديناميكية من لوحة الأدمن)
    pkgs = await _get_packages()
    currency = await S.get("currency")
    rows = []
    for pkg in pkgs:
        short = "unl" if pkg["plan"] == "unlimited" else pkg["plan"]
        cb = f"sub_{short}_{pkg['days']}_{pkg['price']}"
        rows.append([btn(f"{pkg['label']} ({pkg['price']} {currency})", cb)])
    rows.append([btn("🔑 تفعيل كود ترقية", "activate_code_btn")])
    rows.append(back_btn("my_plan"))
    await _edit(update, text, ik(*rows))
    return S_MY_PLAN


async def cb_sub_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User picked a plan+duration — show payment info and ask for screenshot."""
    await _answer(update)
    data = update.callback_query.data  # e.g. sub_pro_30_99
    parts = data.split("_")
    # format: sub_{plan}_{days}_{amount}
    plan = parts[1]                        # pro / unl
    days = int(parts[2])
    amount = parts[3]                      # e.g. 99
    plan_key = "unlimited" if plan == "unl" else "pro"
    plan_label_str = _plan_label(plan_key)

    context.user_data["sub"] = {
        "plan": plan_key,
        "days": days,
        "amount": f"{amount} {CURRENCY}",
    }

    text = (
        f"💳 *إتمام الاشتراك — {plan_label_str}*\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 الخطة: *{plan_label_str}*\n"
        f"📅 المدة: *{days} يوم*\n"
        f"💰 المبلغ: *{amount} {CURRENCY}*\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💳 *طرق الدفع:*\n\n"
        f"📱 *فودافون كاش:*\n"
        f"`{VODAFONE_CASH}`\n\n"
        f"🏦 *إنستاباي:*\n"
        f"`{INSTAPAY_ADDRESS}`\n\n"
        f"👤 الاسم: *{PAYMENT_NAME}*\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ *بعد التحويل:*\n"
        f"أرسل صورة (سكرين شوت) لإثبات التحويل هنا مباشرة 👇"
    )
    await _edit(update, text, ik([btn("❌ إلغاء", "plan_upgrade")]))
    return S_SUB_SCREENSHOT


async def sub_got_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User sent the payment screenshot — save it and notify admin."""
    msg = update.message
    uid = update.effective_user.id
    sub = context.user_data.get("sub", {})

    if not sub:
        await _send(update, "❌ انتهت الجلسة. ابدأ من جديد.", reply_kb=_get_main_kb(uid))
        return S_MAIN

    # Get file_id from photo or document
    file_id = None
    if msg.photo:
        file_id = msg.photo[-1].file_id
    elif msg.document and msg.document.mime_type and "image" in msg.document.mime_type:
        file_id = msg.document.file_id

    if not file_id:
        await _send(update,
            "⚠️ أرسل *صورة* (سكرين شوت) لإثبات التحويل.\nلا نقبل ملفات أخرى.",
            inline_kb=ik([btn("❌ إلغاء", "plan_upgrade")])
        )
        return S_SUB_SCREENSHOT

    plan     = sub["plan"]
    days     = sub["days"]
    amount   = sub["amount"]
    user_obj = await db.get_user(uid)
    full_name = user_obj.get("full_name", "") if user_obj else ""
    username  = user_obj.get("username", "") if user_obj else ""

    # Save request to DB
    req_id = await db.create_subscription_request(uid, plan, days, amount, file_id)

    # Confirm to user
    await _send(update,
        f"✅ *تم استلام طلبك بنجاح!*\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 رقم الطلب: *#{req_id}*\n"
        f"📦 الخطة: *{_plan_label(plan)}*\n"
        f"💰 المبلغ: *{amount}*\n\n"
        f"⏳ سيتم مراجعة طلبك خلال 24 ساعة.\n"
        f"سيصلك إشعار فور التفعيل 🎉",
        reply_kb=_get_main_kb(uid)
    )

    # Notify admin
    admin_text = (
        f"💳 *طلب اشتراك جديد #{req_id}*\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 الاسم: *{full_name}*\n"
        f"🆔 ID: `{uid}`\n"
        f"📱 يوزر: @{username or 'بدون'}\n\n"
        f"📦 الباقة: *{_plan_label(plan)}*\n"
        f"📅 المدة: *{days} يوم*\n"
        f"💰 المبلغ: *{amount}*\n"
    )
    admin_kb = ik(
        [btn(f"✅ تفعيل", f"sub_approve_{req_id}"),
         btn(f"❌ رفض",   f"sub_reject_{req_id}")],
    )
    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=file_id,
            caption=admin_text,
            reply_markup=admin_kb,
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"Could not notify admin about sub request #{req_id}: {e}")

    context.user_data.pop("sub", None)
    return S_MAIN


async def cb_sub_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin approves subscription request."""
    await _answer(update)
    if update.effective_user.id != ADMIN_ID:
        return

    req_id = int(update.callback_query.data.replace("sub_approve_", ""))
    req = await db.get_subscription_request(req_id)
    if not req:
        await _answer(update, "❌ الطلب غير موجود!", True)
        return

    if req["status"] != "pending":
        await _answer(update, f"الطلب تمت معالجته مسبقاً ({req['status']})", True)
        return

    # Activate plan for user
    await db.assign_user_plan(req["user_id"], req["plan"], req["duration_days"])
    await db.update_subscription_request(req_id, "approved")

    # Edit admin message to show approved
    try:
        caption = update.callback_query.message.caption or ""
        await update.callback_query.edit_message_caption(
            caption=caption + f"\n\n✅ *تم التفعيل بواسطة الأدمن*",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass

    # Notify user
    try:
        await context.bot.send_message(
            chat_id=req["user_id"],
            text=(
                f"🎉 *تم تفعيل اشتراكك!*\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"📦 الخطة: *{_plan_label(req['plan'])}*\n"
                f"📅 المدة: *{req['duration_days']} يوم*\n\n"
                f"استمتع بجميع مميزات الخطة الآن 🚀"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"Could not notify user {req['user_id']} of approval: {e}")

    await _answer(update, f"✅ تم تفعيل خطة {_plan_label(req['plan'])} للمستخدم {req['user_id']}", True)


async def cb_sub_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin rejects subscription request."""
    await _answer(update)
    if update.effective_user.id != ADMIN_ID:
        return

    req_id = int(update.callback_query.data.replace("sub_reject_", ""))
    req = await db.get_subscription_request(req_id)
    if not req:
        await _answer(update, "❌ الطلب غير موجود!", True)
        return

    if req["status"] != "pending":
        await _answer(update, f"الطلب تمت معالجته مسبقاً ({req['status']})", True)
        return

    await db.update_subscription_request(req_id, "rejected")

    # Edit admin message
    try:
        caption = update.callback_query.message.caption or ""
        await update.callback_query.edit_message_caption(
            caption=caption + f"\n\n❌ *تم الرفض بواسطة الأدمن*",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass

    # Notify user
    try:
        await context.bot.send_message(
            chat_id=req["user_id"],
            text=(
                f"❌ *تم رفض طلب اشتراكك*\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"للاستفسار تواصل مع الدعم: {SUPPORT_USERNAME}"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass

    await _answer(update, f"❌ تم رفض الطلب #{req_id}", True)


async def cb_plan_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    try:
        me = await update.get_bot().get_me()
        link = f"https://t.me/{me.username}?start={uid}"
    except Exception:
        link = f"رابطك الخاص (ID: {uid})"
    user = await db.get_user(uid)
    ref = await db.get_referral_stats(uid)
    ref_pts = await S.get("points_referral")
    earned = ref["total"] * int(ref_pts)
    await _edit(update,
        f"🎁 *ادعُ واربح*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"🔗 *رابط الإحالة الخاص بك:*\n`{link}`\n\n"
        f"📊 *إحصائياتك:*\n"
        f"👥 عدد من دعوتهم: *{ref['total']}*\n"
        f"💎 منهم اشتركوا: *{ref['converted']}*\n"
        f"🪙 النقاط المكتسبة: *{earned}*\n"
        f"🪙 رصيدك الحالي: *{user.get('points',0)}*\n\n"
        f"💡 *كل صديق يسجّل = {ref_pts} نقطة*\n"
        f"شارك رابطك في الجروبات والقنوات!",
        ik(
            [btn("🏆 لوحة المتصدرين", "ref_leaderboard")],
            [btn("📤 مشاركة الرابط", url=f"https://t.me/share/url?url={link}&text=جرّب أقوى بوت نشر تلقائي على فيسبوك!")],
            back_btn("my_plan"),
        )
    )
    return S_MY_PLAN


async def cb_ref_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة متصدري الإحالات."""
    await _answer(update)
    uid = update.effective_user.id
    top = await db.get_top_referrers(10)
    text = "🏆 *لوحة متصدري الإحالات*\n━━━━━━━━━━━━━━━━━━\n\n"
    if not top:
        text += "لا توجد إحالات بعد. كن أول المتصدرين! 🚀"
    else:
        medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
        for i, r in enumerate(top):
            u = await db.get_user(r["referrer_id"])
            name = (u.get("full_name") or "مستخدم") if u else "مستخدم"
            me_mark = " 👈 أنت" if r["referrer_id"] == uid else ""
            text += f"{medals[i]} {_escape_md(name[:20])} — *{r['c']}* دعوة{me_mark}\n"
    await _edit(update, text, ik(back_btn("my_plan")))
    return S_MY_PLAN


async def cb_activate_code_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    await _edit(update,
        "🔑 *تفعيل كود الترقية*\n\nأرسل الكود:",
        ik(back_btn("my_plan"))
    )
    return S_ACTIVATE_CODE


async def activate_got_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    code = update.message.text.strip().upper()
    promo = await db.use_promo_code(code, uid)
    if not promo:
        await _send(update, "❌ *الكود غير صالح أو منتهي الصلاحية.*")
        return S_MY_PLAN
    await db.assign_user_plan(uid, promo["plan"], promo["duration_days"])
    await _send(update,
        f"✅ *تم تفعيل الكود!*\n\n"
        f"الخطة: *{_plan_label(promo['plan'])}*\n"
        f"المدة: *{promo['duration_days']}* يوم 🎉",
        reply_kb=MAIN_KB
    )
    return S_MAIN


async def cb_plan_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    user = await db.get_user(uid)
    pts = user.get("points", 0)
    lvl_name, lvl_emoji, next_pts = _user_level(pts)
    next_line = (f"⬆️ المستوى التالي بعد *{next_pts - pts}* نقطة"
                 if next_pts else "🏆 وصلت لأعلى مستوى!")
    await _edit(update,
        f"💰 *نظام النقاط والمستويات*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"🏅 مستواك: *{lvl_name}*\n"
        f"🪙 نقاطك: *{pts}*\n"
        f"{next_line}\n\n"
        f"📈 *طرق الربح:*\n"
        f"• دعوة صديق: {await S.get('points_referral')} نقطة\n"
        f"• ربط حساب فيسبوك: {await S.get('points_account')} نقاط\n"
        f"• إتمام حملة: {await S.get('points_campaign')} نقاط\n\n"
        f"🎁 *المستويات:*\n"
        f"🌱 مبتدئ • 🥉 برونزي (50) • 🥈 فضي (200)\n"
        f"🥇 ذهبي (500) • 💎 ماسي (1000)\n\n"
        f"💸 *طرق الصرف:*\n"
        + "".join(f"• {r['cost']} نقطة = {r['label']}\n" for r in POINTS_REDEEM),
        ik([btn("🎁 استبدال النقاط","plan_redeem")], back_btn("my_plan"))
    )
    return S_MY_PLAN


async def cb_plan_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    user = await db.get_user(uid)
    pts = user.get("points", 0)
    data = update.callback_query.data

    # تنفيذ الاستبدال الفعلي (redeem_<cost>)
    if data.startswith("redeem_"):
        try:
            cost = int(data.replace("redeem_", ""))
        except ValueError:
            cost = 0
        option = next((r for r in POINTS_REDEEM if r["cost"] == cost), None)
        if not option:
            await _answer(update, "خيار غير صالح!", True)
            return S_MY_PLAN
        if pts < cost:
            await _answer(update, "نقاطك غير كافية!", True)
            return S_MY_PLAN
        days = option["days"]
        await db.update_user(uid, points=pts - cost)
        if hasattr(db, "extend_user_plan"):
            await db.extend_user_plan(uid, days)
        else:
            await db.assign_user_plan(uid, "pro", days)
        await _edit(update,
            f"🎉 *تم الاستبدال بنجاح!*\n━━━━━━━━━━━━━━━━━━\n\n"
            f"حصلت على *{option['label']}* 🎁\n"
            f"النقاط المتبقية: *{pts - cost} 🪙*",
            ik(back_btn("my_plan"))
        )
        return S_MY_PLAN

    # عرض قائمة الاستبدال — تتولّد من POINTS_REDEEM
    min_cost = min(r["cost"] for r in POINTS_REDEEM) if POINTS_REDEEM else 0
    rows = []
    for r in POINTS_REDEEM:
        if pts >= r["cost"]:
            rows.append([btn(f"{r['label']} ({r['cost']} نقطة)", f"redeem_{r['cost']}")])
    rows.append(back_btn("my_plan"))
    await _edit(update,
        f"🎁 *استبدال النقاط*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"نقاطك: *{pts} 🪙*\n\n"
        f"{('⚠️ نقاطك غير كافية. (الحد الأدنى: ' + str(min_cost) + ' نقطة)') if pts < min_cost else 'اختر ما تريد استبداله:'}",
        ik(*rows)
    )
    return S_MY_PLAN


# ══════════════════════════════════════════════════════════════
#  TOOLS  (sub-keyboard)
# ══════════════════════════════════════════════════════════════

async def _show_tools(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send(update,
        "🧰 *الإعدادات العامة*\n━━━━━━━━━━━━━━━━━━\n\n"
        "القوالب، السجل، الإعدادات، وأدوات المتابعة في مكان واحد.",
        reply_kb=TOOLS_KB
    )
    return S_TOOLS


async def cb_tools(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    return await _show_tools(update, context)


async def _show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = await db.get_user(uid)
    delay = user.get("time_delay",60)
    level = {"low":"🟢 منخفض","medium":"🟡 متوسط","high":"🔴 عالي"}.get(user.get("anti_ban_level","medium"),"متوسط")
    notif = "🔔 ON" if user.get("notifications",1) else "🔕 OFF"
    await _send(update,
        f"⚙️ *إعدادات البوت*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"⏱ الفاصل الزمني: *{delay}ث*\n"
        f"🛡 مستوى الحماية: *{level}*\n"
        f"🔔 الإشعارات: *{notif}*\n",
        inline_kb=ik(
            [btn(f"⏱ الفاصل الزمني ({delay}ث)", "set_delay")],
            [btn(f"🛡 مستوى الحماية: {level}",    "set_antiban")],
            [btn(f"🔔 الإشعارات: {notif}",         "set_notif")],
            [btn("🌐 تغيير اللغة",                  "set_lang_cb")],
        )
    )
    return S_SETTINGS


async def cb_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    return await _show_settings(update, context)


async def cb_set_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    # تتولّد أزرار الفاصل من DELAY_OPTIONS في config (3 في كل صف)
    opt_btns = [btn(f"{d}ث", f"delay_{d}") for d in DELAY_OPTIONS]
    rows = [opt_btns[i:i+3] for i in range(0, len(opt_btns), 3)]
    rows.append(back_btn("settings_cb"))
    await _edit(update, "⏱ *اختر الفاصل الزمني:*", ik(*rows))
    return S_SETTINGS


async def cb_delay_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    val = int(update.callback_query.data.replace("delay_",""))
    await db.update_user(update.effective_user.id, time_delay=val)
    await _answer(update, f"تم: {val}ث ✅")
    return await _show_settings(update, context)


async def cb_set_antiban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    await _edit(update, "🛡 *مستوى الحماية:*", ik(
        [btn("🟢 منخفض","ab_low"), btn("🟡 متوسط","ab_medium"), btn("🔴 عالي","ab_high")],
        back_btn("settings_cb"),
    ))
    return S_SETTINGS


async def cb_ab_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    val = update.callback_query.data.replace("ab_","")
    await db.update_user(update.effective_user.id, anti_ban_level=val)
    await _answer(update, "✅ تم")
    return await _show_settings(update, context)


async def cb_set_notif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    user = await db.get_user(uid)
    new = 0 if user.get("notifications",1) else 1
    await db.update_user(uid, notifications=new)
    await _answer(update, f"الإشعارات: {'ON ✅' if new else 'OFF 🔕'}")
    return await _show_settings(update, context)


async def _show_set_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send(update, "🌐 *اختر اللغة:*",
        inline_kb=ik([btn("🇸🇦 العربية","lang_ar"), btn("🇬🇧 English","lang_en")]))
    return S_SETTINGS


async def cb_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    lang = update.callback_query.data.replace("lang_", "")
    await db.update_user(update.effective_user.id, language=lang)
    msg = "✅ تم ضبط اللغة: العربية" if lang == "ar" else "✅ Language set to English"
    await _answer(update, msg)
    await _send_main_menu(update, context)
    return S_MAIN


async def _ulang(uid: int) -> str:
    """لغة المستخدم (ar افتراضي)."""
    try:
        user = await db.get_user(uid)
        return (user.get("language") or "ar") if user else "ar"
    except Exception:
        return "ar"


async def _show_templates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send(update,
        "📦 *مركز القوالب*\n━━━━━━━━━━━━━━━━━━\n\nاختر نوع القوالب:",
        inline_kb=ik(
            [btn("📝 قوالب المنشورات", "tpl_post")],
            [btn("↩️ قوالب الردود",    "tpl_reply")],
            [btn("🧠 رد ذكي",          "tpl_smart")],
            [btn("🤖 شات بوت",         "tpl_chatbot")],
        )
    )
    return S_TEMPLATES


async def cb_templates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    return await _show_templates(update, context)


async def cb_tpl_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    ttype = update.callback_query.data.replace("tpl_","")
    context.user_data["tpl_type"] = ttype
    uid = update.effective_user.id
    tpls = await db.get_templates(uid, template_type=ttype)
    labels = {"post":"📝 المنشورات","reply":"↩️ الردود","smart":"🧠 رد ذكي","chatbot":"🤖 شات بوت"}
    text = f"{labels.get(ttype,ttype)}\n━━━━━━━━━━━━━━━━━━\n\n"
    rows = []
    for t in tpls[:10]:
        text += f"• *{t['title']}*\n"
        rows.append([btn(f"📋 {t['title'][:25]}", f"tpl_use_{t['id']}"), btn("🗑",f"tpl_del_{t['id']}")])
    if not tpls:
        text += "لا توجد قوالب."
    rows.append([btn("➕ إنشاء قالب", "tpl_add")])
    rows.append(back_btn("templates_cb"))
    await _edit(update, text, ik(*rows))
    return S_TEMPLATES


async def cb_tpl_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    await _edit(update, "📝 أرسل *عنوان* القالب:", ik(back_btn("templates_cb")))
    return S_TPL_TITLE


async def tpl_got_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tpl_title"] = update.message.text.strip()
    await _send(update, "📄 أرسل *محتوى* القالب:")
    return S_TPL_CONTENT


async def tpl_got_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    title = context.user_data.get("tpl_title","قالب")
    content = update.message.text.strip()
    ttype = context.user_data.get("tpl_type","post")
    await db.add_template(uid, title, content, ttype)
    await _send(update, f"✅ *تم حفظ القالب: {title}*",
        inline_kb=ik([btn("📦 مركز القوالب","templates_cb")]))
    return S_TEMPLATES


async def cb_tpl_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    tid = int(update.callback_query.data.split("_")[-1])
    await db.delete_template(tid, update.effective_user.id)
    await _answer(update, "تم حذف القالب ✅")
    ttype = context.user_data.get("tpl_type","post")
    update.callback_query.data = f"tpl_{ttype}"
    return await cb_tpl_type(update, context)


async def _show_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = await db.get_user(uid)
    accs = await db.get_accounts(uid)
    grps = await db.get_groups(uid)
    logs = await db.get_activity_log(uid, limit=5)
    text = "🔔 *مركز التنبيهات*\n━━━━━━━━━━━━━━━━━━\n\n"
    alerts = []
    if not accs:
        alerts.append("🔴 لا يوجد حساب فيسبوك مربوط")
    if not grps:
        alerts.append("🟡 لا توجد مجموعات محفوظة — اسحب مجموعاتك")
    # تنبيه قرب انتهاء الاشتراك
    if user and user.get("plan_expires") and user.get("plan", "free") != "free":
        try:
            exp = datetime.strptime(user["plan_expires"], "%Y-%m-%d %H:%M")
            days_left = (exp - datetime.utcnow()).days
            if days_left <= 3:
                alerts.append(f"🟠 اشتراكك ينتهي خلال *{max(days_left,0)} يوم* — جدّد الآن")
        except Exception:
            pass
    # تنبيه أخطاء حديثة
    err_logs = [l for l in logs if l.get("status") != "success"]
    if err_logs:
        alerts.append(f"🔴 آخر العمليات بها *{len(err_logs)}* أخطاء — راجع سجل النشاط")

    if not alerts:
        text += "✅ لا توجد تنبيهات. كل شيء يعمل بشكل جيد!"
    else:
        text += "\n".join(f"• {a}" for a in alerts)
    text += "\n\n📋 *آخر النشاطات:*\n"
    if logs:
        for l in logs[:3]:
            e = "✅" if l.get("status") == "success" else "❌"
            text += f"{e} {_escape_md(str(l.get('action','')))}\n"
    else:
        text += "لا يوجد نشاط بعد."
    await _send(update, text, inline_kb=ik(
        [btn("📋 سجل النشاط", "activity_log_cb")],
        back_btn("tools_cb")
    ))
    return S_TOOLS


async def _show_activity_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    logs = await db.get_activity_log(uid, limit=15)
    lines = ["📋 سجل النشاط", "━" * 18, ""]
    for log in logs:
        e = "✅" if log.get("status") == "success" else "❌"
        lines.append(f"{e} {log['action']} — {_fmt_date(log.get('created_at',''))}")
    if not logs:
        lines.append("لا يوجد نشاط مسجل.")
    await _send(update, "\n".join(lines), inline_kb=ik(back_btn("tools_cb")), parse_mode=None)
    return S_TOOLS


async def _show_trust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = await db.get_user(uid)
    accs = await db.get_accounts(uid)
    grps = await db.get_groups(uid)
    delay = user.get("time_delay", 60) if user else 60
    anti = user.get("anti_ban_level", "medium") if user else "medium"

    # حساب النقاط فعلياً من عوامل متعددة
    score = 0
    factors = []
    if accs:
        score += 30; factors.append("✅ حساب مربوط (+30)")
    else:
        factors.append("❌ لا يوجد حساب مربوط")
    if delay >= 60:
        score += 25; factors.append(f"✅ فاصل زمني آمن: {delay}ث (+25)")
    elif delay >= 30:
        score += 15; factors.append(f"⚠️ فاصل زمني متوسط: {delay}ث (+15)")
    else:
        factors.append(f"❌ فاصل زمني قصير: {delay}ث (خطر حظر)")
    if anti == "high":
        score += 25; factors.append("✅ حماية عالية (+25)")
    elif anti == "medium":
        score += 15; factors.append("⚠️ حماية متوسطة (+15)")
    else:
        score += 5; factors.append("⚠️ حماية منخفضة (+5)")
    if grps:
        score += 20; factors.append(f"✅ {len(grps)} مجموعة محفوظة (+20)")
    else:
        factors.append("❌ لا توجد مجموعات محفوظة")

    score = min(score, 100)
    bar = "🟩" * (score // 10) + "⬜" * (10 - score // 10)
    rating = "ممتاز 🏆" if score >= 80 else "جيد 👍" if score >= 50 else "ضعيف ⚠️"
    await _send(update,
        f"📊 *مؤشرات الثقة*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"النقاط: *{score}/100* ({rating})\n{bar}\n\n"
        + "\n".join(factors),
        inline_kb=ik(back_btn("tools_cb"))
    )
    return S_TOOLS


async def _show_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send(update,
        "⭐ *تقييم البوت*\n\nشكراً! رأيك يساعدنا 🙏",
        inline_kb=ik(
            [btn("⭐","r1"), btn("⭐⭐","r2"), btn("⭐⭐⭐","r3"),
             btn("⭐⭐⭐⭐","r4"), btn("⭐⭐⭐⭐⭐","r5")],
        )
    )
    return S_TOOLS


async def cb_activity_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    return await _show_activity_log(update, context)


# ── 5.2 إحصائيات الحملات ──
async def cb_camp_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    s = await db.get_campaign_stats(uid)
    bar_done = "🟩" * (s["success_rate"] // 10) + "⬜" * (10 - s["success_rate"] // 10)
    await _edit(update,
        f"📊 *إحصائيات الحملات*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 إجمالي الحملات: *{s['total']}*\n"
        f"✅ مكتملة: *{s['done']}*\n"
        f"❌ فاشلة: *{s['failed']}*\n"
        f"🚀 جارية الآن: *{s['running']}*\n"
        f"📤 إجمالي المنشورات: *{s['total_posts']}*\n\n"
        f"نسبة النجاح: *{s['success_rate']}%*\n{bar_done}",
        ik(back_btn("campaigns"))
    )
    return S_CAMPAIGNS


# ── Feature 4: تقويم النشر المرئي ──
async def cb_camp_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعرض الحملات المجدولة مجمّعة حسب اليوم (تقويم نصي)."""
    await _answer(update)
    uid = update.effective_user.id
    camps = await db.get_campaigns(uid)
    scheduled = [c for c in camps if c.get("schedule_time") and c.get("status") == "pending"]
    if not scheduled:
        await _edit(update,
            "📅 *تقويم النشر*\n━━━━━━━━━━━━━━━━━━\n\n"
            "لا توجد حملات مجدولة حالياً.\n\nأنشئ حملة واختر «جدولة لاحقاً».",
            ik([btn("🆕 حملة جديدة", "camp_new")], back_btn("campaigns"))
        )
        return S_CAMPAIGNS
    # تجميع حسب التاريخ
    from collections import defaultdict
    by_day = defaultdict(list)
    for c in scheduled:
        day = str(c["schedule_time"])[:10]
        time = str(c["schedule_time"])[11:16]
        rec = c.get("recurring") or ""
        rec_label = " 🔁يومي" if rec == "daily" else " 🔁أسبوعي" if rec == "weekly" else ""
        by_day[day].append((time, c, rec_label))
    text = "📅 *تقويم النشر*\n━━━━━━━━━━━━━━━━━━\n\n"
    for day in sorted(by_day.keys()):
        items = sorted(by_day[day])
        text += f"🗓 *{day}* ({len(items)} حملة)\n"
        for time, c, rec_label in items:
            tgt = c.get("posts_total", 0)
            text += f"   • {time} — {tgt} وجهة{rec_label}\n"
        text += "\n"
    text += f"📊 الإجمالي: *{len(scheduled)}* حملة مجدولة"
    await _edit(update, text, ik(back_btn("campaigns")))
    return S_CAMPAIGNS


# ── 5.1 النسخ الاحتياطي للكوكيز (تصدير) ──
async def cb_acc_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    accs = await db.export_accounts(uid)
    if not accs:
        await _edit(update, "⚠️ لا توجد حسابات للنسخ الاحتياطي.", ik(back_btn("accounts")))
        return S_ACCOUNTS
    # نجهّز ملف JSON ونرسله كمستند
    import json as _json, io
    data = [{"account_name": a.get("account_name",""), "cookies": a.get("cookies",""),
             "proxy": a.get("proxy")} for a in accs]
    buf = io.BytesIO(_json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    buf.name = f"accounts_backup_{uid}.json"
    try:
        msg = update.callback_query.message
        await msg.reply_document(
            document=buf,
            filename=f"accounts_backup_{uid}.json",
            caption="🔐 *نسخة احتياطية لحساباتك*\n⚠️ احتفظ بالملف في مكان آمن — يحتوي على كوكيز حساباتك!",
            parse_mode=ParseMode.MARKDOWN,
        )
        await _answer(update, "✅ تم إرسال النسخة الاحتياطية")
    except Exception as e:
        await _edit(update, f"❌ تعذّر إنشاء النسخة: {_escape_md(str(e))}", ik(back_btn("accounts")))
    return S_ACCOUNTS


# ── 5.6 تصدير قائمة المجموعات ──
async def cb_grp_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    grps = await db.get_groups(uid)
    if not grps:
        await _edit(update, "⚠️ لا توجد مجموعات للتصدير.", ik(back_btn("groups")))
        return S_GROUPS
    import io
    lines = [f"{g.get('group_name','')} | {g.get('group_url','')}" for g in grps]
    buf = io.BytesIO("\n".join(lines).encode("utf-8"))
    buf.name = f"groups_{uid}.txt"
    try:
        msg = update.callback_query.message
        await msg.reply_document(
            document=buf, filename=f"groups_{uid}.txt",
            caption=f"📤 *تصدير {len(grps)} مجموعة*", parse_mode=ParseMode.MARKDOWN,
        )
        await _answer(update, "✅ تم التصدير")
    except Exception as e:
        await _edit(update, f"❌ تعذّر التصدير: {_escape_md(str(e))}", ik(back_btn("groups")))
    return S_GROUPS


async def cb_rate_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    try:
        n = int(update.callback_query.data.replace("r", ""))
    except ValueError:
        n = 5
    await _edit(update, f"✅ شكراً على تقييمك {'⭐'*n}! 🙏", ik(back_btn("tools_cb")))
    return S_TOOLS


# ── معالجات الأزرار التي كانت معطّلة (dead buttons) ──

async def cb_acc_retry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إعادة إدخال الكوكيز."""
    await _answer(update)
    return await cb_acc_add(update, context)


async def cb_acc_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الإبلاغ عن مشكلة في حساب (زر ❗)."""
    await _answer(update)
    acc_id = int(update.callback_query.data.split("_")[-1])
    uid = update.effective_user.id
    accs = await db.get_accounts(uid)
    acc = next((a for a in accs if a["id"] == acc_id), None)
    if not acc:
        return S_ACCOUNTS
    await _edit(update,
        f"❗ *حالة الحساب: {_escape_md(_acc_name(acc))}*\n━━━━━━━━━━━━━━━━━━\n\n"
        "إذا كان الحساب يعطي أخطاء متكررة:\n"
        "• جرّب فحص الحساب 🔍\n"
        "• حدّث الكوكيز (احذف وأعد الربط)\n"
        "• تأكد أن الحساب غير محظور",
        ik([btn("🔍 فحص الحساب", f"acc_check_{acc_id}")],
           [btn("🗑 حذف الحساب", f"acc_del_{acc_id}")],
           back_btn("accounts"))
    )
    return S_ACCOUNTS


async def cb_acc_change_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تغيير بروكسي حساب."""
    await _answer(update)
    acc_id = int(update.callback_query.data.split("_")[-1])
    context.user_data["proxy_acc_id"] = acc_id
    await _edit(update,
        "🌐 *تغيير البروكسي*\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل البروكسي الجديد بالصيغة:\n`http://user:pass@host:port`\n\n"
        "أو أرسل `حذف` لإزالة البروكسي.",
        ik(back_btn("accounts"))
    )
    return S_ACC_PROXY


async def cb_camp_tgt_pages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """النشر في صفحات بدل المجموعات."""
    await _answer(update)
    uid = update.effective_user.id
    pages = await db.get_pages(uid)
    context.user_data.setdefault("camp", {})["target_type"] = "pages"
    if not pages:
        await _edit(update, "⚠️ لا توجد صفحات مربوطة! اسحبها من قسم الحسابات أولاً.",
            ik([btn("👤 الحسابات", "accounts")]))
        return S_CAMP_TARGETS
    context.user_data["camp"]["all_targets"] = [
        {"id": p["id"], "group_name": p.get("page_name", p.get("name", "صفحة"))} for p in pages
    ]
    context.user_data["camp"]["sel_targets"] = []
    return await _render_target_sel(update, context)


async def cb_ppost_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """جدولة نشر الصفحات."""
    await _answer(update)
    await _edit(update,
        "⏰ *جدولة نشر الصفحات*\n━━━━━━━━━━━━━━━━━━\n\n"
        "اختر الصفحات أولاً ثم سيُطلب منك الموعد بعد إرسال المحتوى.",
        ik([btn("🚀 اختيار الصفحات", "ppost_now")], back_btn("pages"))
    )
    return S_PAGE_POST


async def cb_ppost_tiktok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نشر من تيك توك على الصفحات."""
    await _answer(update)
    context.user_data.setdefault("camp", {})["from_tiktok"] = True
    await _edit(update,
        "🎵 *النشر من تيك توك*\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل رابط فيديو تيك توك وسأقوم بتحميله ثم تجهيزه للنشر على صفحاتك:",
        ik(back_btn("pages"))
    )
    return S_CAMP_MEDIA


async def cb_pg_save_fav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حفظ الصفحات المختارة كمفضلة."""
    await _answer(update)
    uid = update.effective_user.id
    sel = context.user_data.get("pg_selected", [])
    if not sel:
        await _answer(update, "اختر صفحات أولاً!", True)
        return S_PAGE_POST
    await db.save_group_list(uid, "صفحات مفضلة", sel)
    await _answer(update, f"✅ تم حفظ {len(sel)} صفحة كمفضلة", True)
    return await cb_ppost_now(update, context)


async def cb_pg_use_fav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استخدام صفحات مفضلة محفوظة."""
    await _answer(update)
    uid = update.effective_user.id
    lists = await db.get_group_lists(uid)
    fav = next((l for l in lists if l.get("list_name") == "صفحات مفضلة"), None)
    if not fav:
        await _answer(update, "لا توجد مفضلة محفوظة بعد", True)
        return S_PAGE_POST
    import json as _json
    context.user_data["pg_selected"] = _json.loads(fav.get("group_ids", "[]"))
    await _answer(update, "✅ تم تحميل المفضلة")
    return await cb_ppost_now(update, context)


async def cb_pg_manage_fav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إدارة المفضلة."""
    await _answer(update)
    uid = update.effective_user.id
    lists = await db.get_group_lists(uid)
    text = "📁 *إدارة المفضلة*\n━━━━━━━━━━━━━━━━━━\n\n"
    if lists:
        for l in lists:
            import json as _json
            n = len(_json.loads(l.get("group_ids", "[]")))
            text += f"• {l.get('list_name','قائمة')} — {n} عنصر\n"
    else:
        text += "لا توجد قوائم مفضلة محفوظة."
    await _edit(update, text, ik(back_btn("pages")))
    return S_PAGE_POST


async def cb_set_lang_from_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فتح اختيار اللغة من الإعدادات."""
    await _answer(update)
    await _edit(update, "🌐 *اختر اللغة:*",
        ik([btn("🇸🇦 العربية","lang_ar"), btn("🇬🇧 English","lang_en")], back_btn("settings_cb")))
    return S_SETTINGS


async def cb_tpl_use(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض/استخدام قالب محفوظ."""
    await _answer(update)
    tid = int(update.callback_query.data.split("_")[-1])
    uid = update.effective_user.id
    ttype = context.user_data.get("tpl_type", "post")
    tpls = await db.get_templates(uid, template_type=ttype)
    tpl = next((t for t in tpls if t["id"] == tid), None)
    if not tpl:
        await _answer(update, "القالب غير موجود", True)
        return S_TEMPLATES
    await _edit(update,
        f"📋 *{_escape_md(tpl.get('title','قالب'))}*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"{_escape_md(tpl.get('content',''))}",
        ik([btn("🗑 حذف القالب", f"tpl_del_{tid}")], back_btn("templates_cb"))
    )
    return S_TEMPLATES


# ══════════════════════════════════════════════════════════════
#  ADMIN
# ══════════════════════════════════════════════════════════════

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ غير مصرح.")
        return S_MAIN
    return await _show_admin(update, context)


async def _show_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = await db.get_stats()
    pending_subs = await db.get_pending_subscription_requests()
    pending_count = len(pending_subs)
    pending_badge = f" 🔴 ({pending_count})" if pending_count else ""
    text = (
        "🔐 *لوحة الأدمن — Auto Post Bot*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 المستخدمون: *{stats['users']}*\n"
        f"💎 المشتركون: *{stats['pro_users']}*\n"
        f"🔗 الحسابات: *{stats['accounts']}*\n"
        f"🚀 الحملات: *{stats['campaigns']}*\n"
    )
    maint = await S.is_maintenance()
    maint_badge = " 🔧" if maint else ""
    kb = ik(
        [btn(f"💳 طلبات الاشتراك{pending_badge}", "adm_sub_requests")],
        [btn("👥 المستخدمون",          "adm_users"),
         btn("🔍 بحث مستخدم",         "adm_search")],
        [btn("📊 إحصائيات",           "adm_stats"),
         btn("📈 إحصائيات متقدمة",     "adm_stats_pro")],
        [btn("⚙️ إعدادات البوت",       "adm_settings")],
        [btn("💰 الأسعار والباقات",    "adm_pricing"),
         btn("🎁 النقاط والمكافآت",    "adm_points")],
        [btn("🔒 حدود الخطط",          "adm_limits")],
        [btn("✏️ تعيين خطة لمستخدم", "adm_assign"),
         btn("🔑 كودات الترقية",       "adm_promos")],
        [btn("📣 رسالة للجميع", "adm_broadcast"),
         btn("📨 رسالة موجّهة", "adm_broadcast_target")],
        [btn("🗂️ تصدير قاعدة البيانات", "adm_export_db")],
        [btn(f"🔧 وضع الصيانة{maint_badge}", "adm_maintenance")],
    )
    msg_fn = update.message.reply_text if update.message else update.callback_query.message.reply_text
    await msg_fn(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return S_ADMIN


async def cb_adm_sub_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin views pending subscription requests."""
    await _answer(update)
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN

    reqs = await db.get_pending_subscription_requests()
    if not reqs:
        await _edit(update,
            "💳 *طلبات الاشتراك*\n━━━━━━━━━━━━━━━━━━\n\n✅ لا توجد طلبات معلقة حالياً.",
            ik(back_btn("adm_menu"))
        )
        return S_ADMIN

    text = f"💳 *طلبات الاشتراك المعلقة ({len(reqs)})*\n━━━━━━━━━━━━━━━━━━\n\n"
    rows = []
    for r in reqs[:10]:
        text += (
            f"• *#{r['id']}* — ID: `{r['user_id']}`\n"
            f"  الخطة: {_plan_label(r['plan'])} | {r['duration_days']} يوم | {r['amount']}\n"
            f"  📅 {r['created_at'][:16]}\n\n"
        )
        rows.append([
            btn(f"✅ تفعيل #{r['id']}", f"sub_approve_{r['id']}"),
            btn(f"❌ رفض #{r['id']}",   f"sub_reject_{r['id']}"),
        ])
    rows.append(back_btn("adm_menu"))
    await _edit(update, text, ik(*rows))
    return S_ADMIN


async def cb_adm_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN
    return await _show_admin(update, context)


async def cb_adm_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    users = await db.get_all_users()
    text = "👥 *المستخدمون*\n━━━━━━━━━━━━━━━━━━\n\n"
    for u in users[:20]:
        text += f"• `{u['user_id']}` {u.get('full_name','—')} — {_plan_label(u.get('plan','free'))}\n"
    if len(users) > 20:
        text += f"\n... و {len(users)-20} آخرون"
    await _edit(update, text, ik(back_btn("adm_menu")))
    return S_ADMIN


async def cb_adm_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    stats = await db.get_stats()
    users = await db.get_all_users()
    pc = {}
    for u in users:
        p = u.get("plan","free")
        pc[p] = pc.get(p,0) + 1
    text = (
        "📊 *إحصائيات تفصيلية*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 المستخدمون: *{stats['users']}*\n"
        f"🔗 الحسابات: *{stats['accounts']}*\n"
        f"🚀 الحملات: *{stats['campaigns']}*\n\n"
        "📊 *الخطط:*\n"
    )
    for pn, cnt in pc.items():
        text += f"• {_plan_label(pn)}: {cnt}\n"
    await _edit(update, text, ik(back_btn("adm_menu")))
    return S_ADMIN


async def cb_adm_assign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    await _edit(update,
        "✏️ *تعيين خطة لمستخدم*\n\nأرسل *User ID* المستخدم:",
        ik(back_btn("adm_menu"))
    )
    return S_ADMIN_UID


async def adm_got_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
    except ValueError:
        await _send(update, "❌ أرسل رقماً صحيحاً.")
        return S_ADMIN_UID
    target = await db.get_user(uid)
    context.user_data["adm_uid"] = uid
    if not target:
        await _send(update,
            f"⚠️ المستخدم `{uid}` غير موجود.\nهل تريد إنشاءه؟",
            inline_kb=ik(
                [btn("✅ إنشاء وتعيين", f"adm_force_{uid}"),
                 btn("❌ إلغاء",        "adm_menu")],
            )
        )
        return S_ADMIN
    rows = [[btn(f"{p['label']}", f"adm_plan_{pkey}")] for pkey, p in PLAN_LIMITS.items()]
    rows.append(back_btn("adm_menu"))
    await _send(update,
        f"👤 *{target.get('full_name',uid)}*\nالخطة: {_plan_label(target.get('plan','free'))}\n\nاختر الخطة الجديدة:",
        inline_kb=ik(*rows)
    )
    return S_ADMIN_PLAN


async def cb_adm_force(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = int(update.callback_query.data.replace("adm_force_",""))
    await db.create_user(uid, "", f"User_{uid}")
    context.user_data["adm_uid"] = uid
    rows = [[btn(f"{p['label']}", f"adm_plan_{pn}")] for pn, p in PLAN_LIMITS.items()]
    rows.append(back_btn("adm_menu"))
    await _edit(update, f"✅ تم إنشاء `{uid}`\n\nاختر الخطة:", ik(*rows))
    return S_ADMIN_PLAN


async def cb_adm_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    plan = update.callback_query.data.replace("adm_plan_","")
    context.user_data["adm_plan"] = plan
    await _edit(update,
        f"📅 خطة: *{_plan_label(plan)}*\n\nكم عدد الأيام؟",
        ik(
            [btn("7","adm_days_7"), btn("30","adm_days_30"), btn("90","adm_days_90"), btn("365","adm_days_365")],
            [btn("♾ دائم (9999)","adm_days_9999")],
            back_btn("adm_menu"),
        )
    )
    return S_ADMIN_DAYS


async def cb_adm_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    days = int(update.callback_query.data.replace("adm_days_",""))
    uid  = context.user_data.get("adm_uid")
    plan = context.user_data.get("adm_plan","free")
    if not uid:
        await _edit(update, "❌ انتهت الجلسة.", ik(back_btn("adm_menu")))
        return S_ADMIN
    await db.assign_user_plan(uid, plan, days)
    await _edit(update,
        f"✅ *تم التعيين!*\n\n`{uid}` → *{_plan_label(plan)}* / {days} يوم",
        ik(back_btn("adm_menu"))
    )
    try:
        await update.get_bot().send_message(uid,
            f"🎉 *تم ترقية خطتك!*\n\nالخطة: *{_plan_label(plan)}*\nالمدة: *{days}* يوم",
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass
    return S_ADMIN


async def cb_adm_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    await _edit(update,
        "📣 *إرسال رسالة للجميع*\n\nأرسل نص الرسالة:",
        ik(back_btn("adm_menu"))
    )
    return S_ADMIN_BROADCAST


async def adm_got_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN
    msg_text = update.message.text.strip()
    users = await db.get_all_users()
    sent = failed = 0
    for u in users:
        try:
            await context.bot.send_message(u["user_id"],
                f"📣 *رسالة من الإدارة:*\n\n{msg_text}",
                parse_mode=ParseMode.MARKDOWN)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await _send(update,
        f"✅ *تم الإرسال!*\n\nنجح: {sent} | فشل: {failed}",
        inline_kb=ik([btn("🔐 لوحة الأدمن","adm_menu")])
    )
    return S_ADMIN


async def cb_adm_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    text = "💎 *الخطط المتاحة*\n━━━━━━━━━━━━━━━━━━\n\n"
    for pn, p in PLAN_LIMITS.items():
        text += (
            f"*{p['label']}*\n"
            f"السعر: {p['price']} | حسابات: {p['max_accounts']} | مجموعات: {p['max_groups']}\n\n"
        )
    text += "لتعديل الخطط، عدّل ملف `config.py`"
    await _edit(update, text, ik(back_btn("adm_menu")))
    return S_ADMIN


async def cb_adm_promos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    await _edit(update,
        "🔑 *كودات الترقية*\n━━━━━━━━━━━━━━━━━━\n\nاختر عملية:",
        ik(
            [btn("➕ إنشاء كود جديد","adm_new_promo")],
            back_btn("adm_menu"),
        )
    )
    return S_ADMIN


async def cb_adm_new_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    rows = [[btn(p["label"], f"promo_p_{pn}")] for pn, p in PLAN_LIMITS.items() if pn != "free"]
    rows.append(back_btn("adm_menu"))
    await _edit(update, "🔑 اختر خطة الكود:", ik(*rows))
    context.user_data["promo"] = {}
    return S_ADMIN_PROMO


async def cb_promo_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    plan = update.callback_query.data.replace("promo_p_","")
    context.user_data["promo"]["plan"] = plan
    await _edit(update, "📅 كم عدد الأيام؟", ik(
        [btn("7","pd_7"), btn("30","pd_30"), btn("90","pd_90"), btn("365","pd_365")],
    ))
    return S_ADMIN_PROMO


async def cb_promo_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    days = int(update.callback_query.data.replace("pd_",""))
    plan = context.user_data.get("promo",{}).get("plan","pro")
    code = secrets.token_hex(4).upper()
    ok = await db.create_promo_code(code, plan, days)
    if ok:
        await _edit(update,
            f"✅ *الكود:* `{code}`\n"
            f"الخطة: *{_plan_label(plan)}*\n"
            f"المدة: *{days}* يوم",
            ik(back_btn("adm_menu"))
        )
    else:
        await _edit(update, "❌ فشل إنشاء الكود.", ik(back_btn("adm_menu")))
    return S_ADMIN


# ══════════════════════════════════════════════════════════════
#  لوحة التحكم الشاملة للأدمن (تعديل كل شيء بدون كود)
# ══════════════════════════════════════════════════════════════

# مجموعات الإعدادات القابلة للتعديل من الواجهة
_SETTINGS_GROUPS = {
    "adm_settings": ("⚙️ إعدادات عامة",
        ["bot_name", "bot_tagline", "support_username", "channel_username",
         "payment_name", "vodafone_cash", "instapay", "currency",
         "default_delay", "min_between_camp", "max_concurrent", "welcome_bonus"]),
    "adm_pricing": ("💰 الأسعار والباقات",
        []),  # تُعرض بشكل خاص
    "adm_points": ("🎁 النقاط والمكافآت",
        ["points_referral", "points_account", "points_campaign",
         "trial_plan", "trial_days"]),
    "adm_limits": ("🔒 حدود الخطط",
        ["free_accounts", "free_groups", "free_campaigns",
         "pro_accounts", "pro_groups", "pro_campaigns",
         "unl_accounts", "unl_groups", "unl_campaigns"]),
}


async def _show_settings_group(update, context, group_key):
    """يعرض إعدادات مجموعة معينة مع قيمها الحالية وأزرار تعديل."""
    title, keys = _SETTINGS_GROUPS[group_key]
    text = f"{title}\n━━━━━━━━━━━━━━━━━━\n\nاضغط على أي إعداد لتعديله:\n\n"
    rows = []
    for k in keys:
        desc, _default = S.EDITABLE[k]
        val = await S.get(k)
        val_str = str(val) if val not in (None, "") else "—"
        if len(val_str) > 20:
            val_str = val_str[:20] + "…"
        text += f"• *{desc}:* `{val_str}`\n"
        rows.append([btn(f"✏️ {desc}", f"setk_{k}")])
    rows.append(back_btn("adm_menu"))
    await _edit(update, text, ik(*rows))
    return S_ADMIN


async def cb_adm_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN
    return await _show_settings_group(update, context, "adm_settings")


async def cb_adm_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN
    return await _show_settings_group(update, context, "adm_points")


async def cb_adm_limits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN
    return await _show_settings_group(update, context, "adm_limits")


async def cb_adm_pricing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض الباقات الحالية مع إمكانية تعديل أسعارها."""
    await _answer(update)
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN
    pkgs = await _get_packages()
    text = "💰 *الأسعار والباقات*\n━━━━━━━━━━━━━━━━━━\n\nالباقات الحالية:\n\n"
    rows = []
    for i, p in enumerate(pkgs):
        text += f"• {p['label']} — *{p['price']} {await S.get('currency')}* / {p['days']} يوم\n"
        rows.append([btn(f"✏️ سعر: {p['label']}", f"setprice_{i}")])
    rows.append(back_btn("adm_menu"))
    await _edit(update, text, ik(*rows))
    return S_ADMIN


async def _get_packages():
    """الباقات من DB أو الافتراضي من config."""
    saved = await db.get_setting("packages")
    if saved:
        return saved
    return [dict(p) for p in SUBSCRIPTION_PACKAGES]


async def cb_set_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN
    idx = int(update.callback_query.data.replace("setprice_", ""))
    context.user_data["edit_price_idx"] = idx
    pkgs = await _get_packages()
    p = pkgs[idx]
    await _edit(update,
        f"💰 *تعديل سعر: {p['label']}*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"السعر الحالي: *{p['price']}*\n\n"
        f"أرسل السعر الجديد (رقم فقط):",
        ik(back_btn("adm_pricing"))
    )
    return S_ADMIN_SETTING_VALUE


async def cb_set_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء تعديل إعداد معيّن."""
    await _answer(update)
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN
    key = update.callback_query.data.replace("setk_", "")
    if key not in S.EDITABLE:
        return S_ADMIN
    context.user_data["edit_setting_key"] = key
    desc, _ = S.EDITABLE[key]
    cur = await S.get(key)
    await _edit(update,
        f"✏️ *تعديل: {desc}*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"القيمة الحالية: `{cur}`\n\n"
        f"أرسل القيمة الجديدة:",
        ik(back_btn("adm_menu"))
    )
    return S_ADMIN_SETTING_VALUE


async def adm_got_setting_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام القيمة الجديدة وحفظها."""
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN
    val = update.message.text.strip()

    # تعديل سعر باقة
    if "edit_price_idx" in context.user_data:
        idx = context.user_data.pop("edit_price_idx")
        try:
            price = int(val)
        except ValueError:
            await _send(update, "❌ أرسل رقماً صحيحاً.")
            return S_ADMIN_SETTING_VALUE
        pkgs = await _get_packages()
        pkgs[idx]["price"] = price
        await db.set_setting("packages", pkgs)
        await _send(update, f"✅ *تم تحديث السعر إلى {price}*",
                    inline_kb=ik([btn("💰 الأسعار", "adm_pricing")], back_btn("adm_menu")))
        return S_ADMIN

    # تعديل إعداد عام
    key = context.user_data.pop("edit_setting_key", None)
    if not key:
        return S_ADMIN
    desc, default = S.EDITABLE[key]
    # لو الافتراضي رقم، نتأكد إن القيمة رقم
    if isinstance(default, int):
        try:
            val = int(val)
        except ValueError:
            await _send(update, "❌ هذا الإعداد يحتاج رقماً صحيحاً.")
            return S_ADMIN_SETTING_VALUE
    await S.set(key, val)
    await _send(update,
        f"✅ *تم حفظ الإعداد:*\n{desc} = `{val}`",
        inline_kb=ik(back_btn("adm_menu"))
    )
    return S_ADMIN


async def cb_adm_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تبديل وضع الصيانة."""
    await _answer(update)
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN
    cur = await S.is_maintenance()
    await S.set("maintenance_mode", 0 if cur else 1)
    state = "مغلق ✅ (البوت يعمل)" if cur else "مفعّل 🔧 (البوت متوقف للمستخدمين)"
    await _edit(update,
        f"🔧 *وضع الصيانة*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"الحالة الآن: *{state}*\n\n"
        f"في وضع الصيانة، المستخدمون يرون رسالة 'البوت تحت الصيانة' "
        f"وأنت (الأدمن) تستطيع الاستخدام عادي.",
        ik([btn("🔄 تبديل الحالة", "adm_maintenance")], back_btn("adm_menu"))
    )
    return S_ADMIN


# ── 5) بحث في المستخدمين ──
async def cb_adm_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN
    await _edit(update,
        "🔍 *بحث في المستخدمين*\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل الـ ID أو الاسم أو اليوزر للبحث:",
        ik(back_btn("adm_menu"))
    )
    return S_ADMIN_SEARCH


async def adm_got_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN
    q = update.message.text.strip()
    users = await db.search_users(q)
    if not users:
        await _send(update, "❌ لا يوجد مستخدمون مطابقون.",
                    inline_kb=ik(back_btn("adm_menu")))
        return S_ADMIN
    text = f"🔍 *نتائج البحث ({len(users)})*\n━━━━━━━━━━━━━━━━━━\n\n"
    rows = []
    for u in users[:15]:
        text += (f"• `{u['user_id']}` {_escape_md(u.get('full_name','—'))} "
                 f"— {_plan_label(u.get('plan','free'))}\n")
        rows.append([btn(f"✏️ تعيين خطة: {u['user_id']}", f"adm_setplan_{u['user_id']}")])
    rows.append(back_btn("adm_menu"))
    await _send(update, text, inline_kb=ik(*rows))
    return S_ADMIN


async def cb_adm_setplan_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تعيين خطة لمستخدم من نتائج البحث."""
    await _answer(update)
    uid = int(update.callback_query.data.replace("adm_setplan_", ""))
    context.user_data["adm_uid"] = uid
    rows = [[btn(p["label"], f"adm_plan_{pk}")] for pk, p in PLAN_LIMITS.items()]
    rows.append(back_btn("adm_menu"))
    await _edit(update, f"اختر خطة للمستخدم `{uid}`:", ik(*rows))
    return S_ADMIN_PLAN


# ── 6) إحصائيات رسومية ──
async def cb_adm_stats_pro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN
    st = await db.get_advanced_stats()
    plans = st.get("plans", {})
    total = max(st["users"], 1)
    def barline(label, count):
        pct = int((count / total) * 100)
        filled = pct // 10
        return f"{label}\n{'🟩'*filled}{'⬜'*(10-filled)} {count} ({pct}%)"
    text = (
        "📈 *إحصائيات متقدمة*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 إجمالي المستخدمين: *{st['users']}*\n"
        f"🆕 اليوم: *{st['new_today']}* | الأسبوع: *{st['new_week']}*\n\n"
        f"📊 *توزيع الخطط:*\n"
        + barline("🆓 مجاني", plans.get("free", 0)) + "\n"
        + barline("⭐ Pro", plans.get("pro", 0)) + "\n"
        + barline("👑 Unlimited", plans.get("unlimited", 0)) + "\n\n"
        f"🔗 الحسابات: *{st['accounts']}*\n"
        f"👥 المجموعات: *{st['groups']}*\n"
        f"🚀 الحملات: *{st['campaigns']}*\n"
        f"📤 إجمالي المنشورات: *{st['total_posts']}*"
    )
    await _edit(update, text, ik(back_btn("adm_menu")))
    return S_ADMIN


# ── 9) رسالة موجّهة لفئة ──
async def cb_adm_broadcast_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN
    await _edit(update,
        "📨 *رسالة موجّهة*\n━━━━━━━━━━━━━━━━━━\n\nاختر الفئة المستهدفة:",
        ik(
            [btn("👥 الجميع", "bct_all")],
            [btn("🆓 المجانيون", "bct_free")],
            [btn("⭐ مشتركو Pro", "bct_pro")],
            [btn("👑 Unlimited", "bct_unlimited")],
            back_btn("adm_menu"),
        )
    )
    return S_ADMIN


async def cb_adm_broadcast_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN
    target = update.callback_query.data.replace("bct_", "")
    context.user_data["bc_target"] = target
    labels = {"all": "الجميع", "free": "المجانيين", "pro": "مشتركي Pro", "unlimited": "Unlimited"}
    await _edit(update,
        f"📨 *رسالة إلى: {labels.get(target, target)}*\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل نص الرسالة الآن:",
        ik(back_btn("adm_menu"))
    )
    return S_ADMIN_BROADCAST_PLAN


async def adm_got_targeted_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN
    msg_text = update.message.text.strip()
    target = context.user_data.pop("bc_target", "all")
    if target == "all":
        users = await db.get_all_users()
    else:
        users = await db.get_users_by_plan(target)
    sent = failed = 0
    for u in users:
        try:
            await context.bot.send_message(u["user_id"],
                f"📣 *رسالة من الإدارة:*\n\n{msg_text}",
                parse_mode=ParseMode.MARKDOWN)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await _send(update,
        f"✅ *تم الإرسال!*\n\n📨 إلى: {len(users)} مستخدم\nنجح: {sent} | فشل: {failed}",
        inline_kb=ik([btn("🔐 لوحة الأدمن", "adm_menu")])
    )
    return S_ADMIN


# ── 10) تصدير قاعدة البيانات ──
async def cb_adm_export_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    if update.effective_user.id != ADMIN_ID:
        return S_MAIN
    try:
        msg = update.callback_query.message
        with open(db.DB_PATH, "rb") as f:
            await msg.reply_document(
                document=f, filename="autopost_backup.db",
                caption="🗂️ *نسخة احتياطية كاملة لقاعدة البيانات*\n⚠️ تحتوي على بيانات حساسة — احفظها بأمان!",
                parse_mode=ParseMode.MARKDOWN,
            )
        await _answer(update, "✅ تم تصدير قاعدة البيانات")
    except Exception as e:
        await _edit(update, f"❌ تعذّر التصدير: {_escape_md(str(e))}", ik(back_btn("adm_menu")))
    return S_ADMIN


# ══════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════

def _plan_label(plan: str) -> str:
    return PLAN_LIMITS.get(plan, {}).get("label", plan)


def _user_level(points: int) -> tuple:
    """يرجّع (الاسم, الإيموجي, نقاط المستوى التالي) حسب النقاط."""
    points = points or 0
    if points >= 1000:
        return ("💎 ماسي", "💎", None)
    if points >= 500:
        return ("🥇 ذهبي", "🥇", 1000)
    if points >= 200:
        return ("🥈 فضي", "🥈", 500)
    if points >= 50:
        return ("🥉 برونزي", "🥉", 200)
    return ("🌱 مبتدئ", "🌱", 50)


def _fmt_date(dt_str: str) -> str:
    if not dt_str:
        return ""
    try:
        return datetime.fromisoformat(dt_str).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_str


def _status_label(s: str) -> str:
    return {"pending":"⏳ انتظار","running":"🚀 جارٍ","done":"✅ مكتمل","failed":"❌ فشل","paused":"⏸ متوقف"}.get(s, s)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send(update,
        "ℹ️ *المساعدة والأوامر السريعة*\n━━━━━━━━━━━━━━━━━━\n\n"
        "/start — القائمة الرئيسية\n"
        "/post — إنشاء حملة جديدة فوراً 🚀\n"
        "/stats — إحصائياتك 📊\n"
        "/accounts — حساباتك 👤\n"
        "/groups — مجموعاتك 👥\n"
        "/plan — خطتي 💎\n"
        "/help — هذه الرسالة\n"
        "/admin — لوحة الأدمن (للأدمن فقط)\n\n"
        f"للدعم: {SUPPORT_USERNAME}"
    )


# ── أوامر الاختصار السريعة ──
async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اختصار: إنشاء حملة جديدة مباشرة."""
    if await _maybe_block_maintenance(update):
        return S_MAIN
    uid = update.effective_user.id
    if not await db.get_accounts(uid):
        await _send(update, "❌ يجب ربط حساب أولاً!", inline_kb=ik([btn("👤 الحسابات","accounts")]))
        return S_MAIN
    allowed, msg = await _check_campaign_limit(uid)
    if not allowed:
        await _send(update, msg, inline_kb=ik([btn("💎 ترقية الخطة","plan_upgrade")]))
        return S_MAIN
    context.user_data["camp"] = {}
    await _send(update,
        "📎 *حملة جديدة — الخطوة 1/4 (الميديا)*\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل *صورة أو فيديو* بدون كابشن،\n"
        "أو رابط تيك توك / يوتيوب / ريلز.",
        inline_kb=ik([btn("⏭ بدون ميديا","camp_skip_media")], back_btn("campaigns"))
    )
    return S_CAMP_MEDIA


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اختصار: عرض إحصائيات المستخدم."""
    uid = update.effective_user.id
    s = await db.get_campaign_stats(uid)
    accs = await db.get_accounts(uid)
    grps = await db.get_groups(uid)
    user = await db.get_user(uid)
    bar = "🟩" * (s["success_rate"] // 10) + "⬜" * (10 - s["success_rate"] // 10)
    await _send(update,
        f"📊 *إحصائياتك*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 الحسابات: *{len(accs)}*\n"
        f"👥 المجموعات: *{len(grps)}*\n"
        f"🚀 الحملات: *{s['total']}* (✅{s['done']} ❌{s['failed']})\n"
        f"📤 إجمالي المنشورات: *{s['total_posts']}*\n"
        f"🪙 النقاط: *{user.get('points',0) if user else 0}*\n\n"
        f"نسبة النجاح: *{s['success_rate']}%*\n{bar}"
    )
    return S_MAIN


async def cmd_accounts_short(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _show_accounts(update, context)


async def cmd_groups_short(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _show_groups(update, context)


async def cmd_plan_short(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _show_my_plan(update, context)


async def _maybe_block_maintenance(update) -> bool:
    """يرجّع True لو وضع الصيانة مفعّل والمستخدم مش أدمن."""
    if update.effective_user.id != ADMIN_ID and await S.is_maintenance():
        await _send(update, "🔧 *البوت تحت الصيانة حالياً.*\nبرجاء المحاولة لاحقاً 🙏")
        return True
    return False


async def fallback_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        txt = update.message.text or ""
        if txt in MAIN_NAV:
            return await nav_router(update, context)
    await _send_main_menu(update, context)
    return S_MAIN
# ══════════════════════════════════════════════════════════════
#  ConversationHandler builder
# ══════════════════════════════════════════════════════════════

def build_conversation_handler() -> ConversationHandler:
    all_nav_keys = list(MAIN_NAV.keys())
    nav_filter = filters.TEXT & filters.Regex(
        "^(" + "|".join(re.escape(k) for k in all_nav_keys) + ")$"
    )
    any_text = filters.TEXT & ~filters.COMMAND

    # Shared callbacks available in most states
    shared_cbs = [
        CallbackQueryHandler(cb_main,            pattern="^main$"),
        CallbackQueryHandler(cb_onboard_help,    pattern="^onboard_help$"),
        CallbackQueryHandler(cb_accounts,         pattern="^accounts$"),
        CallbackQueryHandler(cb_groups,           pattern="^groups$"),
        CallbackQueryHandler(cb_campaigns,        pattern="^campaigns$"),
        CallbackQueryHandler(cb_comments,         pattern="^comments$"),
        CallbackQueryHandler(cb_my_plan,          pattern="^my_plan$"),
        CallbackQueryHandler(cb_tools,            pattern="^tools_cb$"),
        CallbackQueryHandler(cb_settings,         pattern="^settings_cb$"),
        CallbackQueryHandler(cb_templates,        pattern="^templates_cb$"),
        CallbackQueryHandler(cb_camp_log,         pattern="^camp_log_cb$"),
        CallbackQueryHandler(cb_adm_menu,         pattern="^adm_menu$"),
        CallbackQueryHandler(cb_plan_upgrade,     pattern="^plan_upgrade$"),
        CallbackQueryHandler(cb_lang,             pattern="^lang_(ar|en)$"),
        CallbackQueryHandler(cb_sub_select,       pattern="^sub_(pro|unl)_\\d+_\\d+$"),
        CallbackQueryHandler(cb_sub_approve,      pattern="^sub_approve_\\d+$"),
        CallbackQueryHandler(cb_sub_reject,       pattern="^sub_reject_\\d+$"),
    ]

    return ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CommandHandler("post", cmd_post),
            CommandHandler("stats", cmd_stats),
            CommandHandler("accounts", cmd_accounts_short),
            CommandHandler("groups", cmd_groups_short),
            CommandHandler("plan", cmd_plan_short),
        ],
        states={
            S_MAIN: shared_cbs + [MessageHandler(nav_filter, nav_router)],

            S_ACCOUNTS: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_acc_add,        pattern="^acc_add$"),
                CallbackQueryHandler(cb_acc_detail,     pattern="^acc_detail_\\d+$"),
                CallbackQueryHandler(cb_acc_del,        pattern="^acc_del_\\d+$"),
                CallbackQueryHandler(cb_acc_del_confirm, pattern="^acc_delok_\\d+$"),
                CallbackQueryHandler(cb_acc_check,      pattern="^acc_check_\\d+$"),
                CallbackQueryHandler(cb_acc_check_all,  pattern="^acc_check_all$"),
                CallbackQueryHandler(cb_acc_fetch_grp,  pattern="^acc_fetch_grp_\\d+$"),
                CallbackQueryHandler(cb_acc_fetch_pg,   pattern="^acc_fetch_pg_\\d+$"),
                CallbackQueryHandler(cb_acc_report,     pattern="^acc_report_\\d+$"),
                CallbackQueryHandler(cb_acc_change_proxy, pattern="^acc_proxy_\\d+$"),
                CallbackQueryHandler(cb_acc_backup,      pattern="^acc_backup$"),
            ],
            S_ACC_NAME: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, acc_got_name),
                CallbackQueryHandler(cb_main,     pattern="^main$"),
                CallbackQueryHandler(cb_accounts, pattern="^accounts$"),
            ],
            S_ACC_COOKIES: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, acc_got_cookies),
                CallbackQueryHandler(cb_main,     pattern="^main$"),
                CallbackQueryHandler(cb_accounts, pattern="^accounts$"),
                CallbackQueryHandler(cb_acc_retry, pattern="^acc_retry$"),
            ],
            S_ACC_PROXY: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, acc_got_proxy),
                CallbackQueryHandler(cb_main,           pattern="^main$"),
                CallbackQueryHandler(cb_acc_skip_proxy, pattern="^acc_skip_proxy$"),
                CallbackQueryHandler(cb_acc_continue,   pattern="^acc_continue$"),
                CallbackQueryHandler(cb_acc_retry,      pattern="^acc_retry$"),
                CallbackQueryHandler(cb_accounts,       pattern="^accounts$"),
            ],

            S_GROUPS: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_grp_mine,        pattern="^grp_mine$"),
                CallbackQueryHandler(cb_grp_fetch,       pattern="^grp_fetch_\\d+$"),
                CallbackQueryHandler(cb_grp_search,      pattern="^grp_search(_join)?$"),
                CallbackQueryHandler(cb_grp_delete_all,  pattern="^grp_delete_all$"),
                CallbackQueryHandler(cb_grp_confirm_del, pattern="^grp_confirm_del$"),
                CallbackQueryHandler(cb_grp_lists,       pattern="^grp_lists$"),
                CallbackQueryHandler(cb_grp_view,        pattern="^grp_view$"),
                CallbackQueryHandler(cb_grp_check_post,  pattern="^grp_check_post$"),
                CallbackQueryHandler(cb_grp_vip,         pattern="^(grp_other|grp_members|grp_upload)$"),
                CallbackQueryHandler(cb_grp_export,      pattern="^grp_export$"),
            ],
            S_GRP_SEARCH: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, grp_got_search),
                CallbackQueryHandler(cb_main,   pattern="^main$"),
                CallbackQueryHandler(cb_groups, pattern="^groups$"),
            ],

            S_PAGES: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_ppost_now,       pattern="^ppost_now$"),
                CallbackQueryHandler(cb_ppost_schedule,  pattern="^ppost_schedule$"),
                CallbackQueryHandler(cb_ppost_tiktok,    pattern="^ppost_tiktok$"),
            ],
            S_PAGE_POST: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_ppost_now,       pattern="^ppost_now$"),
                CallbackQueryHandler(cb_ppost_schedule,  pattern="^ppost_schedule$"),
                CallbackQueryHandler(cb_ppost_tiktok,    pattern="^ppost_tiktok$"),
                CallbackQueryHandler(cb_pg_save_fav,     pattern="^pg_save_fav$"),
                CallbackQueryHandler(cb_pg_use_fav,      pattern="^pg_use_fav$"),
                CallbackQueryHandler(cb_pg_manage_fav,   pattern="^pg_manage_fav$"),
                CallbackQueryHandler(cb_pg_sel,          pattern="^pg_sel_\\d+$"),
                CallbackQueryHandler(cb_pg_sel_all,      pattern="^pg_sel_all$"),
                CallbackQueryHandler(cb_pg_sel_none,     pattern="^pg_sel_none$"),
                CallbackQueryHandler(cb_pg_confirm_sel,  pattern="^pg_confirm_sel$"),
                CallbackQueryHandler(cb_pg_dist,         pattern="^pg_dist_(spread|repeat)$"),
                CallbackQueryHandler(cb_camp_skip_media, pattern="^camp_skip_media$"),
            ],
            S_STORY_PAGES: [
                CallbackQueryHandler(cb_story_pg_sel,  pattern="^stpg_\\d+$"),
                CallbackQueryHandler(cb_story_pg_all,  pattern="^stpg_all$"),
                CallbackQueryHandler(cb_story_pg_none, pattern="^stpg_none$"),
                CallbackQueryHandler(cb_story_pg_done, pattern="^stpg_done$"),
                CallbackQueryHandler(cb_pages,         pattern="^pages$"),
                CallbackQueryHandler(cb_main,          pattern="^main$"),
            ],
            S_PAGE_STORY_IMG: [
                MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.IMAGE | filters.Document.VIDEO, story_got_image),
                MessageHandler(any_text, story_got_image),
                CallbackQueryHandler(cb_pages, pattern="^pages$"),
                CallbackQueryHandler(cb_main,  pattern="^main$"),
            ],
            S_PAGE_STORY_LINK: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, story_got_link),
                CallbackQueryHandler(cb_main,            pattern="^main$"),
                CallbackQueryHandler(cb_story_skip_link, pattern="^story_skip_link$"),
                CallbackQueryHandler(cb_story_now,       pattern="^story_now$"),
                CallbackQueryHandler(cb_story_confirm,   pattern="^story_confirm$"),
                CallbackQueryHandler(cb_story_edit,      pattern="^story_edit$"),
                CallbackQueryHandler(cb_pages,           pattern="^pages$"),
            ],

            S_CAMPAIGNS: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_camp_new,        pattern="^camp_new$"),
                CallbackQueryHandler(cb_camp_log,        pattern="^camp_scheduled$"),
                CallbackQueryHandler(cb_camp_stats,      pattern="^camp_stats$"),
                CallbackQueryHandler(cb_camp_calendar,   pattern="^camp_calendar$"),
                CallbackQueryHandler(cb_camp_resume_draft, pattern="^camp_resume_draft$"),
                CallbackQueryHandler(cb_camp_del_draft,  pattern="^camp_del_draft$"),
            ],
            S_CAMP_CAPTION: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, camp_got_caption),
                CallbackQueryHandler(cb_main,          pattern="^main$"),
                CallbackQueryHandler(cb_camp_skip_cap, pattern="^camp_skip_cap$"),
                CallbackQueryHandler(cb_camp_quick_tpl, pattern="^camp_quick_tpl$"),
                CallbackQueryHandler(cb_quick_tpl_pick, pattern="^qtpl_\\d+$"),
                CallbackQueryHandler(cb_camp_use_tpl,  pattern="^camp_use_tpl$"),
                CallbackQueryHandler(cb_camp_ab,       pattern="^camp_ab$"),
                CallbackQueryHandler(cb_campaigns,     pattern="^campaigns$"),
            ],
            S_CAMP_AB_A: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, camp_got_ab_a),
                CallbackQueryHandler(cb_main,      pattern="^main$"),
                CallbackQueryHandler(cb_campaigns, pattern="^campaigns$"),
            ],
            S_CAMP_AB_B: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, camp_got_ab_b),
                CallbackQueryHandler(cb_main,      pattern="^main$"),
                CallbackQueryHandler(cb_campaigns, pattern="^campaigns$"),
            ],
            S_CAMP_MEDIA: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(filters.VIDEO | filters.PHOTO, camp_got_media),
                MessageHandler(any_text, camp_got_media),
                CallbackQueryHandler(cb_main,            pattern="^main$"),
                CallbackQueryHandler(cb_camp_skip_media, pattern="^camp_skip_media$"),
            ],
            S_CAMP_TARGETS: [
                CallbackQueryHandler(cb_camp_tgt_groups, pattern="^camp_tgt_groups$"),
                CallbackQueryHandler(cb_camp_tgt_pages,  pattern="^camp_tgt_pages$"),
                CallbackQueryHandler(cb_tsel,            pattern="^tsel_\\d+$"),
                CallbackQueryHandler(cb_tsel_all,        pattern="^tsel_all$"),
                CallbackQueryHandler(cb_tsel_none,       pattern="^tsel_none$"),
                CallbackQueryHandler(cb_camp_confirm_tgt,pattern="^camp_confirm_tgt$"),
                CallbackQueryHandler(cb_campaigns,       pattern="^campaigns$"),
            ],
            S_CAMP_SCHEDULE: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, camp_got_schedule),
                CallbackQueryHandler(cb_main,               pattern="^main$"),
                CallbackQueryHandler(cb_camp_now,          pattern="^camp_now$"),
                CallbackQueryHandler(cb_camp_sched_prompt, pattern="^camp_sched_prompt$"),
                CallbackQueryHandler(cb_camp_sched_confirm,pattern="^camp_sched_confirm$"),
                CallbackQueryHandler(cb_camp_edit_cap,     pattern="^camp_edit_cap$"),
                CallbackQueryHandler(cb_camp_recurring,    pattern="^camp_recurring$"),
                CallbackQueryHandler(cb_camp_set_recurring, pattern="^rec_(daily|weekly)$"),
                CallbackQueryHandler(cb_camp_save_draft,   pattern="^camp_save_draft$"),
                CallbackQueryHandler(cb_campaigns,         pattern="^campaigns$"),
            ],

            S_COMMENTS: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_cmt_vip, pattern="^(cmt_add|cmt_reply|cmt_mention|cmt_chatbot)$"),
            ],
            S_CMT_URL: [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_main,      pattern="^main$"),
                CallbackQueryHandler(cb_cmt_acc,   pattern="^cmt_acc_\\d+$"),
                CallbackQueryHandler(cb_cmt_acc,   pattern="^cmtr_acc_\\d+$"),
                CallbackQueryHandler(cb_cmt_acc,   pattern="^cmtm_acc_\\d+$"),
                MessageHandler(any_text, cmt_got_url),
                CallbackQueryHandler(cb_comments,  pattern="^comments$"),
            ],
            S_CMT_TEXT: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, cmt_got_text),
                CallbackQueryHandler(cb_main,        pattern="^main$"),
                CallbackQueryHandler(cb_cmt_confirm, pattern="^cmt_confirm$"),
                CallbackQueryHandler(cb_comments,    pattern="^comments$"),
            ],

            S_PAGE_BOT: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_pagebot_new,  pattern="^pagebot_new$"),
                CallbackQueryHandler(cb_pagebot_del,  pattern="^pagebot_del_\\d+$"),
                CallbackQueryHandler(cb_pbot_pg,      pattern="^pbot_pg_.+$"),
            ],
            S_PAGE_BOT_URL: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, pagebot_got_url),
                CallbackQueryHandler(cb_main, pattern="^main$"),
                CallbackQueryHandler(lambda u,c: _show_page_bot(u,c), pattern="^page_bot$"),
            ],
            S_PAGE_BOT_TPL: [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_pbot_tpl, pattern="^pbot_tpl_.+$"),
                CallbackQueryHandler(cb_main, pattern="^main$"),
                CallbackQueryHandler(lambda u,c: _show_page_bot(u,c), pattern="^page_bot$"),
            ],
            S_PAGE_BOT_KW: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, pagebot_got_kw),
                CallbackQueryHandler(cb_main, pattern="^main$"),
                CallbackQueryHandler(lambda u,c: _show_page_bot(u,c), pattern="^page_bot$"),
            ],
            S_PAGE_BOT_RCMT: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, pagebot_got_rcmt),
                CallbackQueryHandler(cb_main,           pattern="^main$"),
                CallbackQueryHandler(cb_pbot_rcmt_skip, pattern="^pbot_rcmt_skip$"),
                CallbackQueryHandler(lambda u,c: _show_page_bot(u,c), pattern="^page_bot$"),
            ],
            S_PAGE_BOT_RDM: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, pagebot_got_rdm),
                CallbackQueryHandler(cb_main,          pattern="^main$"),
                CallbackQueryHandler(cb_pbot_rdm_skip, pattern="^pbot_rdm_skip$"),
                CallbackQueryHandler(lambda u,c: _show_page_bot(u,c), pattern="^page_bot$"),
            ],

            S_MY_PLAN: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_plan_usage,        pattern="^plan_usage$"),
                CallbackQueryHandler(cb_plan_details,      pattern="^plan_details$"),
                CallbackQueryHandler(cb_plan_trial,        pattern="^plan_trial$"),
                CallbackQueryHandler(cb_plan_referral,     pattern="^plan_referral$"),
                CallbackQueryHandler(cb_ref_leaderboard,   pattern="^ref_leaderboard$"),
                CallbackQueryHandler(cb_plan_points,       pattern="^plan_points$"),
                CallbackQueryHandler(cb_plan_redeem,       pattern="^(plan_redeem|redeem_\\d+)$"),
                CallbackQueryHandler(cb_activate_code_btn, pattern="^activate_code_btn$"),
                CallbackQueryHandler(cb_plan_upgrade,      pattern="^plan_upgrade$"),
            ],
            S_ACTIVATE_CODE: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, activate_got_code),
                CallbackQueryHandler(cb_main,    pattern="^main$"),
                CallbackQueryHandler(cb_my_plan, pattern="^my_plan$"),
            ],

            S_TOOLS: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_rate_val, pattern="^r[1-5]$"),
                CallbackQueryHandler(cb_activity_log, pattern="^activity_log_cb$"),
            ],
            S_SETTINGS: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_set_delay,   pattern="^set_delay$"),
                CallbackQueryHandler(cb_delay_val,   pattern="^delay_\\d+$"),
                CallbackQueryHandler(cb_set_antiban, pattern="^set_antiban$"),
                CallbackQueryHandler(cb_ab_val,      pattern="^ab_(low|medium|high)$"),
                CallbackQueryHandler(cb_set_notif,   pattern="^set_notif$"),
                CallbackQueryHandler(cb_set_lang_from_settings, pattern="^set_lang_cb$"),
                CallbackQueryHandler(cb_lang,        pattern="^lang_(ar|en)$"),
            ],
            S_TEMPLATES: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_tpl_type,  pattern="^tpl_(post|reply|smart|chatbot)$"),
                CallbackQueryHandler(cb_tpl_add,   pattern="^tpl_add$"),
                CallbackQueryHandler(cb_tpl_use,   pattern="^tpl_use_\\d+$"),
                CallbackQueryHandler(cb_tpl_del,   pattern="^tpl_del_\\d+$"),
            ],
            S_TPL_TITLE: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, tpl_got_title),
                CallbackQueryHandler(cb_main,      pattern="^main$"),
                CallbackQueryHandler(cb_templates, pattern="^templates_cb$"),
            ],
            S_TPL_CONTENT: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, tpl_got_content),
                CallbackQueryHandler(cb_main, pattern="^main$"),
            ],

            S_ADMIN: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_adm_users,        pattern="^adm_users$"),
                CallbackQueryHandler(cb_adm_stats,        pattern="^adm_stats$"),
                CallbackQueryHandler(cb_adm_assign,       pattern="^adm_assign$"),
                CallbackQueryHandler(cb_adm_broadcast,    pattern="^adm_broadcast$"),
                CallbackQueryHandler(cb_adm_plans,        pattern="^adm_plans$"),
                CallbackQueryHandler(cb_adm_promos,       pattern="^adm_promos$"),
                CallbackQueryHandler(cb_adm_new_promo,    pattern="^adm_new_promo$"),
                CallbackQueryHandler(cb_adm_force,        pattern="^adm_force_\\d+$"),
                CallbackQueryHandler(cb_adm_plan,         pattern="^adm_plan_\\w+$"),
                CallbackQueryHandler(cb_adm_sub_requests, pattern="^adm_sub_requests$"),
                CallbackQueryHandler(cb_sub_approve,      pattern="^sub_approve_\\d+$"),
                CallbackQueryHandler(cb_sub_reject,       pattern="^sub_reject_\\d+$"),
                CallbackQueryHandler(cb_adm_settings,     pattern="^adm_settings$"),
                CallbackQueryHandler(cb_adm_pricing,      pattern="^adm_pricing$"),
                CallbackQueryHandler(cb_adm_points,       pattern="^adm_points$"),
                CallbackQueryHandler(cb_adm_limits,       pattern="^adm_limits$"),
                CallbackQueryHandler(cb_adm_maintenance,  pattern="^adm_maintenance$"),
                CallbackQueryHandler(cb_set_key,          pattern="^setk_\\w+$"),
                CallbackQueryHandler(cb_set_price,        pattern="^setprice_\\d+$"),
                CallbackQueryHandler(cb_adm_search,       pattern="^adm_search$"),
                CallbackQueryHandler(cb_adm_setplan_user, pattern="^adm_setplan_\\d+$"),
                CallbackQueryHandler(cb_adm_stats_pro,    pattern="^adm_stats_pro$"),
                CallbackQueryHandler(cb_adm_broadcast_target, pattern="^adm_broadcast_target$"),
                CallbackQueryHandler(cb_adm_broadcast_pick, pattern="^bct_(all|free|pro|unlimited)$"),
                CallbackQueryHandler(cb_adm_export_db,    pattern="^adm_export_db$"),
            ],
            S_ADMIN_SEARCH: [
                MessageHandler(any_text, adm_got_search),
                CallbackQueryHandler(cb_adm_menu, pattern="^adm_menu$"),
                CallbackQueryHandler(cb_main,     pattern="^main$"),
            ],
            S_ADMIN_BROADCAST_PLAN: [
                MessageHandler(any_text, adm_got_targeted_broadcast),
                CallbackQueryHandler(cb_adm_menu, pattern="^adm_menu$"),
                CallbackQueryHandler(cb_main,     pattern="^main$"),
            ],
            S_ADMIN_SETTING_VALUE: [
                MessageHandler(any_text, adm_got_setting_value),
                CallbackQueryHandler(cb_adm_menu,    pattern="^adm_menu$"),
                CallbackQueryHandler(cb_adm_settings, pattern="^adm_settings$"),
                CallbackQueryHandler(cb_adm_pricing,  pattern="^adm_pricing$"),
                CallbackQueryHandler(cb_adm_points,   pattern="^adm_points$"),
                CallbackQueryHandler(cb_adm_limits,   pattern="^adm_limits$"),
                CallbackQueryHandler(cb_main,        pattern="^main$"),
            ],
            S_ADMIN_BROADCAST: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, adm_got_broadcast),
                CallbackQueryHandler(cb_main,     pattern="^main$"),
                CallbackQueryHandler(cb_adm_menu, pattern="^adm_menu$"),
            ],
            S_ADMIN_UID: [
                MessageHandler(nav_filter, nav_router),
                MessageHandler(any_text, adm_got_uid),
                CallbackQueryHandler(cb_main,     pattern="^main$"),
                CallbackQueryHandler(cb_adm_menu, pattern="^adm_menu$"),
            ],
            S_ADMIN_PLAN: [
                CallbackQueryHandler(cb_adm_plan,  pattern="^adm_plan_\\w+$"),
                CallbackQueryHandler(cb_adm_force, pattern="^adm_force_\\d+$"),
                CallbackQueryHandler(cb_adm_menu,  pattern="^adm_menu$"),
            ],
            S_ADMIN_DAYS: [
                CallbackQueryHandler(cb_adm_days,  pattern="^adm_days_\\d+$"),
                CallbackQueryHandler(cb_adm_menu,  pattern="^adm_menu$"),
            ],
            S_ADMIN_PROMO: [
                CallbackQueryHandler(cb_promo_plan, pattern="^promo_p_\\w+$"),
                CallbackQueryHandler(cb_promo_days, pattern="^pd_\\d+$"),
                CallbackQueryHandler(cb_adm_menu,   pattern="^adm_menu$"),
            ],

            S_SUB_SCREENSHOT: [
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, sub_got_screenshot),
                MessageHandler(filters.Document.ALL, sub_got_screenshot),
                CallbackQueryHandler(cb_plan_upgrade, pattern="^plan_upgrade$"),
                CallbackQueryHandler(cb_my_plan,      pattern="^my_plan$"),
            ],
        },
        fallbacks=[
            CommandHandler("start", cmd_start),
            CommandHandler("help",  cmd_help),
            CommandHandler("admin", cmd_admin),
            CommandHandler("post", cmd_post),
            CommandHandler("stats", cmd_stats),
            CommandHandler("accounts", cmd_accounts_short),
            CommandHandler("groups", cmd_groups_short),
            CommandHandler("plan", cmd_plan_short),
            MessageHandler(nav_filter, nav_router),
            CallbackQueryHandler(cb_main, pattern="^main$"),
            MessageHandler(any_text, fallback_msg),
        ],
        allow_reentry=True,
        per_message=False,
    )
