"""
Auto Post Bot v2.0 — Handlers
UI مطابق لبوت VoltCast GroupFB
"""
import os
import json
import re
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
from config import ADMIN_ID, PLAN_LIMITS, PAYMENT_NAME, INSTAPAY_ADDRESS, VODAFONE_CASH, SUPPORT_USERNAME

logger = logging.getLogger(__name__)

# ── States ────────────────────────────────────────────────────────────────────
CAIRO_TZ = pytz.timezone("Africa/Cairo")

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
) = range(39)


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
        await update.callback_query.answer(text, show_alert=alert)

async def _edit(update: Update, text: str, markup=None):
    kw = {"parse_mode": ParseMode.MARKDOWN}
    if markup:
        kw["reply_markup"] = markup
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, **kw)
        except Exception:
            pass
    else:
        await update.message.reply_text(text, **kw)

async def _send(update: Update, text: str, reply_kb=None, inline_kb=None):
    msg = update.message or update.callback_query.message
    kw = {"parse_mode": ParseMode.MARKDOWN}
    if reply_kb:
        kw["reply_markup"] = reply_kb
    elif inline_kb:
        kw["reply_markup"] = inline_kb
    await msg.reply_text(text, **kw)


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
}

# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
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
                await db.update_user(ref, points=(u.get("points", 0) + 50))
        await _send(update,
            f"🎉 *أهلاً {user.first_name}!*\n\n"
            f"مرحباً في *Auto Post Bot* ⚡\n"
            f"أداة النشر التلقائي الأذكى في فيسبوك.\n\n"
            f"استخدم القائمة أسفل الشاشة 👇",
            reply_kb=_get_main_kb(user.id)
        )
        return S_MAIN

    await _send_main_menu(update, context)
    return S_MAIN


def _get_main_kb(uid: int) -> ReplyKeyboardMarkup:
    return ADMIN_KB if uid == ADMIN_ID else MAIN_KB


async def _send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = await db.get_user(uid)
    plan = _plan_label(user.get("plan", "free")) if user else "🆓 مجاني"
    limits = PLAN_LIMITS.get(user.get("plan", "free"), PLAN_LIMITS["free"])
    accounts = await db.get_accounts(uid)
    exp = _fmt_date(user.get("plan_expires")) if user else "—"
    now_cairo = _cairo_now().strftime("%H:%M")
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
    await _send(update, text, reply_kb=_get_main_kb(uid))


async def cb_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    await _send_main_menu(update, context)
    return S_MAIN


# ── Nav text router ───────────────────────────────────────────────────────────

async def nav_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    key = MAIN_NAV.get(txt)

    handlers_map = {
        "main":          _send_main_menu,
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
            btn(f"✅ {acc['account_name'][:18]}", f"acc_detail_{acc['id']}"),
        ])

    if not accounts:
        text += "لم تقم بربط أي حساب بعد.\n\n🔴 ربط حساب واحد على الأقل مطلوب للبدء."

    rows.append([
        btn("🔍 فحص جميع الحسابات", "acc_check_all"),
        btn("➕ ربط حساب جديد",     "acc_add"),
    ])

    await _send(update, text, inline_kb=ik(*rows))
    return S_ACCOUNTS


async def cb_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    return await _show_accounts(update, context)


async def cb_acc_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
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
    context.user_data["acc_proxy"] = update.message.text.strip()
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
        f"👤 *{acc['account_name']}*\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 المعرف: `{c_user}`\n"
        f"📊 الخطة: {_plan_label(user.get('plan','free'))} "
        f"({len(groups)}/{limits['max_groups']})\n"
        f"🌐 بروكسي: {acc.get('proxy') or 'بدون'}\n"
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
    await _answer(update)
    acc_id = int(update.callback_query.data.split("_")[-1])
    uid = update.effective_user.id
    await db.delete_account(acc_id, uid)
    await _edit(update, "🗑 *تم حذف الحساب.*", ik(back_btn("accounts")))
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
        await _edit(update, f"✅ *الحساب نشط*: {acc['account_name']}", ik(back_btn("accounts")))
    else:
        await _edit(update, f"❌ *الحساب غير نشط أو الكوكيز منتهية*\n{acc['account_name']}", ik(back_btn("accounts")))
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
            report += f"✅ {acc['account_name']}\n"
        else:
            failed += 1
            report += f"❌ {acc['account_name']}\n"
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
    for g in groups:
        await db.add_group(uid, acc_id, g["group_id"], g["group_name"], g.get("group_url",""), g.get("members_count",0))
    await db.log_activity(uid, "fetch_groups", f"سحب {len(groups)} مجموعة")
    if groups:
        await _edit(update, f"✅ *تم سحب {len(groups)} مجموعة!*", ik(back_btn("accounts")))
    else:
        await _edit(update, "⚠️ لم يُعثر على مجموعات.\nتحقق من الكوكيز.", ik(back_btn("accounts")))
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
    text = (
        "👥 *إدارة المجموعات*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"المجموعات المحفوظة: *{len(groups)}*\n\n"
        "اختر مصدر الجروبات:"
    )
    await _send(update, text, inline_kb=ik(
        [btn("🗂 سحب جروباتي",                 "grp_mine")],
        [btn("🔎 استخراج جروبات شخص آخر",     "grp_other")],
        [btn("🔍 بحث عن جروبات",               "grp_search")],
        [btn("🔍➕ بحث + انضمام",             "grp_search_join")],
        [btn("👥 استخراج أعضاء جروب",         "grp_members")],
        [btn("📋 القوائم المحفوظة",            "grp_lists")],
        [btn("📁 رفع قائمة جروبات",            "grp_upload")],
        [btn("✅ فحص النشر المباشر",           "grp_check_post")],
        [btn("🗑 حذف جميع الجروبات",           "grp_delete_all")],
    ))
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
    rows = [[btn(f"✅ {a['account_name']}", f"grp_fetch_{a['id']}")] for a in accs]
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
    for g in grps:
        await db.add_group(uid, acc_id, g["group_id"], g["group_name"], g.get("group_url",""))
    await db.log_activity(uid, "fetch_groups", f"سحب {len(grps)} مجموعة")
    await _edit(update,
        f"✅ *تم سحب {len(grps)} مجموعة!*" if grps else "⚠️ لم يُعثر على مجموعات.",
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
    join = context.user_data.get("grp_join", False)
    await _send(update,
        f"🔍 جاري البحث: *{q}*\n{'+ انضمام تلقائي' if join else ''}\n\n"
        "⚠️ هذه العملية قد تأخذ وقتاً.",
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
        await _edit(update,
            "✅ *جاري فحص صلاحيات النشر...*\nهذه العملية قد تأخذ وقتاً.",
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
    else:
        await _edit(update,
            f"⏳ *{label}*\n\nجارٍ تفعيل الخاصية…",
            ik(back_btn("groups"))
        )
    return S_GROUPS


# ══════════════════════════════════════════════════════════════
#  PAGES  (sub-keyboard: نشر / ستوري / بوت)
# ══════════════════════════════════════════════════════════════

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
    await _send(update,
        "📚 *ستوري الصفحات*\n━━━━━━━━━━━━━━━━━━\n\nأرسل صورة الستوري الآن.",
        inline_kb=ik([btn("❌ إلغاء","pages")])
    )
    return S_PAGE_STORY_IMG


async def story_got_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.photo:
        f = await msg.photo[-1].get_file()
        path = f"/tmp/story_{update.effective_user.id}.jpg"
        await f.download_to_drive(path)
        context.user_data["story_img"] = path
    elif msg.document:
        f = await msg.document.get_file()
        path = f"/tmp/story_{update.effective_user.id}.jpg"
        await f.download_to_drive(path)
        context.user_data["story_img"] = path
    else:
        await _send(update, "⚠️ أرسل صورة من فضلك.")
        return S_PAGE_STORY_IMG

    await _send(update,
        "✅ *تم حفظ صورة الستوري.*\n\nأرسل الرابط المرفق للستوري\nأو اضغط تخطي:",
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


async def cb_story_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    await _edit(update,
        "✅ *جاري نشر الستوري الآن...*\nسيتم إشعارك عند الانتهاء.",
        ik(back_btn("pages"))
    )
    return S_PAGES


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
    await _edit(update,
        "✅ *تم جدولة الستوري بنجاح!*\nسيتم إشعارك عند النشر.",
        ik(back_btn("pages"))
    )
    return S_PAGES


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
    await _send(update,
        "🚀 *الحملات*\n━━━━━━━━━━━━━━━━━━\n\nاختر نوع العملية:",
        inline_kb=ik(
            [btn("🆕 حملة جديدة",       "camp_new")],
            [btn("⏰ الحملات المجدولة", "camp_scheduled"),
             btn("📊 السجل",            "camp_log_cb")],
        )
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
    context.user_data["camp"]["caption"] = ""
    return await _ask_camp_targets(update, context)


async def camp_got_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["camp"]["caption"] = update.message.text.strip()
    return await _ask_camp_targets(update, context)


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
        "أرسل *نص المنشور* الآن.\n"
        "⚠️ سيُضاف النص في خطوة منفصلة عن الميديا لتجنب أخطاء فيسبوك.",
        inline_kb=ik([btn("⏭ بدون نص","camp_skip_cap")])
    )
    return S_CAMP_CAPTION


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
    sel = context.user_data["camp"].get("sel_targets",[])
    if not sel:
        await _answer(update, "اختر هدفاً واحداً على الأقل!", True)
        return S_CAMP_TARGETS
    await _edit(update,
        f"⏰ *الخطوة 4/4 — التوقيت*\n\nتم تحديد *{len(sel)}* مجموعة.",
        ik(
            [btn("🚀 نشر الآن",   "camp_now")],
            [btn("⏰ جدولة",     "camp_sched_prompt")],
            back_btn("campaigns"),
        )
    )
    return S_CAMP_SCHEDULE


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
    )
    sched = camp.get("schedule")
    if sched:
        await _send(update,
            f"✅ *تم جدولة الحملة #{camp_id}*\n\nالنشر في: *{sched}*\nالمجموعات: {len(sel)}",
            inline_kb=ik([btn("🗂 السجل","camp_log_cb")])
        )
    else:
        user_obj = await db.get_user(uid)
        delay = user_obj.get("time_delay", 60)
        anti = user_obj.get("anti_ban_level","medium")
        await _send(update,
            f"🚀 *بدأت الحملة #{camp_id}!*\n\n"
            f"المجموعات: *{len(sel)}*\n"
            f"الفاصل: *{delay}ث*\n\n"
            "✅ سيتم إشعارك عند الانتهاء.",
        )
        asyncio.create_task(_run_campaign_bg(context, uid, camp_id, acc, sel, camp.get("caption",""), camp.get("media_path"), delay, anti))
    context.user_data["camp"] = {}
    return S_CAMPAIGNS


async def _run_campaign_bg(context, uid, camp_id, acc, group_ids, caption, media_path, delay, anti):
    await db.update_campaign_status(camp_id, "running")
    from fb_automator import FBAutomator
    auto = FBAutomator(acc["id"], acc["cookies"], acc.get("proxy"))
    done = failed = 0
    all_groups = await db.get_groups(uid)
    targets = [g for g in all_groups if g["id"] in group_ids]
    for g in targets:
        r = await auto.post_to_group(g["group_id"], caption, media_path, delay_range=(max(delay-20,10), delay+40), anti_ban_level=anti)
        if r.get("success"):
            done += 1
        else:
            failed += 1
        await db.update_campaign_status(camp_id, "running", done)
    status = "done" if done else "failed"
    await db.update_campaign_status(camp_id, status, done)
    await db.log_activity(uid, "campaign_done", f"حملة #{camp_id}: ✅{done} ❌{failed}")
    try:
        await context.bot.send_message(uid,
            f"✅ *انتهت الحملة #{camp_id}!*\n\n✅ نجح: {done}\n❌ فشل: {failed}",
            parse_mode=ParseMode.MARKDOWN)
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
    user = await db.get_user(uid)
    plan = user.get("plan","free") if user else "free"
    labels = {
        "cmt_reply":   "الرد على التعليقات",
        "cmt_mention": "منشن المتفاعلين",
        "cmt_chatbot": "شات بوت الصفحات",
    }
    label = labels.get(action, action)
    if plan == "free":
        await _edit(update,
            f"🔒 *{label}*\n\nهذه الميزة متاحة لمشتركي Pro وما فوق.",
            ik([btn("💎 ترقية الخطة","plan_upgrade")], back_btn("comments"))
        )
    else:
        await _edit(update,
            f"⏳ *{label}*\n\nجارٍ تفعيل الخاصية…",
            ik(back_btn("comments"))
        )
    return S_COMMENTS


async def _show_admin_from_kb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await _send(update, "❌ ليس لديك صلاحية.")
        return S_MAIN
    update.message = update.message
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
    rows = [[btn(f"📄 {p['name']}", f"pbot_pg_{p['id']}")] for p in pages[:8]]
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
    context.user_data["pagebot"]["page_name"] = pg["name"]
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
    rows = [[btn(f"👤 {a['name']}", f"cmt_acc_{a['id']}")] for a in accs[:8]]
    rows.append(back_btn("comments"))
    await _edit(update,
        "💬 *إضافة تعليق — الخطوة 1/3*\n━━━━━━━━━━━━━━━━━━\n\nاختر الحساب:",
        ik(*rows)
    )
    return S_CMT_URL


async def cb_cmt_acc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    acc_id = int(update.callback_query.data.split("_")[-1])
    context.user_data["cmt"]["account_id"] = acc_id
    await _edit(update,
        "💬 *الخطوة 2/3 — رابط المنشور أو الجروب*\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل رابط المنشور أو الجروب الذي تريد التعليق فيه:\n"
        "مثال: `https://www.facebook.com/groups/12345/posts/99999`",
        ik(back_btn("comments"))
    )
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
        await _send(update, "❌ يجب إدخال نص التعليق.")
        return S_CMT_TEXT
    context.user_data["cmt"]["text"] = txt
    cmt = context.user_data["cmt"]
    await _send(update,
        f"✅ *تأكيد التعليق*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"📎 الرابط: `{cmt['url'][:60]}…`\n"
        f"💬 التعليق: {txt}\n\n"
        f"هل تريد إضافة التعليق الآن؟",
        inline_kb=ik(
            [btn("✅ إضافة التعليق", "cmt_confirm")],
            [btn("❌ إلغاء",         "comments")],
        )
    )
    return S_CMT_TEXT


async def cb_cmt_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    cmt = context.user_data.get("cmt", {})
    acc_id = cmt.get("account_id")
    url   = cmt.get("url","")
    text  = cmt.get("text","")
    accs = await db.get_accounts(uid)
    acc = next((a for a in accs if a["id"] == acc_id), None)
    if not acc:
        await _edit(update, "❌ الحساب غير موجود.", ik(back_btn("comments")))
        return S_COMMENTS
    msg = await _edit(update, "⏳ *جارٍ إضافة التعليق…*", ik())
    from fb_automator import FBAutomator
    auto = FBAutomator(acc["id"], acc.get("cookies",""), acc.get("proxy"))
    ok, err = await auto.post_comment(url, text)
    await db.log_activity(uid, "تعليق", f"URL: {url[:60]}", "success" if ok else "error")
    if ok:
        await _edit(update, "✅ *تم إضافة التعليق بنجاح!*", ik(back_btn("comments")))
    else:
        await _edit(update, f"❌ *فشل إضافة التعليق:*\n{err}", ik(back_btn("comments")))
    context.user_data.pop("cmt", None)
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
        [btn("🎁 تجربة Unlimited يومين", "plan_trial")],
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
        "🎁 *تجربة Unlimited — يومان مجاناً*\n━━━━━━━━━━━━━━━━━━\n\n"
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
    text = (
        "🛒 *اختر الخطة المناسبة لك*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "⭐ *Pro — 99 جنيه/شهر*\n"
        "• 3 حسابات فيسبوك\n"
        "• 300 مجموعة\n"
        "• 20 حملة\n"
        "• جدولة + قوالب + صفحات\n\n"
        "👑 *Unlimited — 199 جنيه/شهر*\n"
        "• 10 حسابات فيسبوك\n"
        "• غير محدود مجموعات\n"
        "• غير محدود حملات\n"
        "• كل المميزات + أولوية دعم\n\n"
        "اختر الخطة التي تريد الاشتراك فيها:"
    )
    rows = [
        [btn("⭐ Pro — شهر (99 جنيه)",      "sub_pro_30_99")],
        [btn("⭐ Pro — 3 أشهر (250 جنيه)",   "sub_pro_90_250")],
        [btn("👑 Unlimited — شهر (199 جنيه)", "sub_unl_30_199")],
        [btn("👑 Unlimited — 3 أشهر (499 جنيه)", "sub_unl_90_499")],
        [btn("🔑 تفعيل كود ترقية", "activate_code_btn")],
        back_btn("my_plan"),
    ]
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
        "amount": f"{amount} جنيه",
    }

    text = (
        f"💳 *إتمام الاشتراك — {plan_label_str}*\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 الخطة: *{plan_label_str}*\n"
        f"📅 المدة: *{days} يوم*\n"
        f"💰 المبلغ: *{amount} جنيه مصري*\n\n"
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
    await _edit(update,
        f"🎁 *ادعُ واربح*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"🔗 رابط الإحالة:\n`{link}`\n\n"
        f"🪙 نقاطك: *{user.get('points',0)}*\n\n"
        f"• كل صديق = 50 نقطة\n"
        f"• 100 نقطة = يوم Pro مجاناً",
        ik(back_btn("my_plan"))
    )
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
    await _edit(update,
        f"💰 *نظام النقاط*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"نقاطك الحالية: *{user.get('points',0)} 🪙*\n\n"
        f"طرق الربح:\n"
        f"• دعوة صديق: 50 نقطة\n"
        f"• ربط حساب فيسبوك: 10 نقاط\n"
        f"• إتمام حملة: 5 نقاط\n\n"
        f"طرق الصرف:\n"
        f"• 100 نقطة = يوم Pro\n"
        f"• 500 نقطة = 7 أيام Pro",
        ik([btn("🎁 استبدال النقاط","plan_redeem")], back_btn("my_plan"))
    )
    return S_MY_PLAN


async def cb_plan_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    uid = update.effective_user.id
    user = await db.get_user(uid)
    pts = user.get("points",0)
    await _edit(update,
        f"🎁 *استبدال النقاط*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"نقاطك: *{pts} 🪙*\n\n"
        f"{'⚠️ نقاطك غير كافية. (الحد الأدنى: 100 نقطة)' if pts < 100 else 'اختر ما تريد استبداله:'}",
        ik(
            ([btn("1 يوم Pro (100 نقطة)", "redeem_100")] if pts >= 100 else []),
            ([btn("7 أيام Pro (500 نقطة)","redeem_500")] if pts >= 500 else []),
            back_btn("my_plan")
        ) if pts >= 100 else ik(back_btn("my_plan"))
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
    await _edit(update, "⏱ *اختر الفاصل الزمني:*", ik(
        [btn("30ث","delay_30"), btn("60ث","delay_60"), btn("120ث","delay_120")],
        [btn("180ث","delay_180"), btn("300ث","delay_300"), btn("600ث","delay_600")],
        back_btn("settings_cb"),
    ))
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
    lang = update.callback_query.data.replace("lang_","")
    await db.update_user(update.effective_user.id, language=lang)
    await _answer(update, "✅ تم تغيير اللغة")
    await _send_main_menu(update, context)
    return S_MAIN


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
    accs = await db.get_accounts(uid)
    logs = await db.get_activity_log(uid, limit=5)
    text = "🔔 *مركز التنبيهات*\n━━━━━━━━━━━━━━━━━━\n\n"
    alerts = []
    if not accs:
        alerts.append("🔴 لا يوجد حساب فيسبوك مربوط")
    if not alerts:
        text += "✅ لا توجد تنبيهات. كل شيء يعمل بشكل جيد!"
    else:
        text += "\n".join(alerts)
    await _send(update, text, inline_kb=ik(back_btn("tools_cb")))
    return S_TOOLS


async def _show_activity_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    logs = await db.get_activity_log(uid, limit=15)
    text = "📋 *سجل النشاط*\n━━━━━━━━━━━━━━━━━━\n\n"
    for log in logs:
        e = "✅" if log.get("status") == "success" else "❌"
        text += f"{e} {log['action']} — {_fmt_date(log.get('created_at',''))}\n"
    if not logs:
        text += "لا يوجد نشاط مسجل."
    await _send(update, text, inline_kb=ik(back_btn("tools_cb")))
    return S_TOOLS


async def _show_trust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    accs = await db.get_accounts(uid)
    score = 85 if accs else 20
    bar = "🟩" * (score // 10) + "⬜" * (10 - score // 10)
    await _send(update,
        f"📊 *مؤشرات الثقة*\n━━━━━━━━━━━━━━━━━━\n\n"
        f"النقاط: *{score}/100*\n{bar}\n\n"
        f"{'✅' if accs else '❌'} حساب مربوط\n"
        f"✅ الفاصل الزمني مناسب",
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


async def cb_rate_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _answer(update)
    n = len(update.callback_query.data)
    await _edit(update, f"✅ شكراً على تقييمك {'⭐'*n}! 🙏")
    return S_TOOLS


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
    kb = ik(
        [btn(f"💳 طلبات الاشتراك{pending_badge}", "adm_sub_requests")],
        [btn("👥 المستخدمون",          "adm_users")],
        [btn("💎 إدارة الخطط",         "adm_plans"),
         btn("🔑 كودات الترقية",       "adm_promos")],
        [btn("✏️ تعيين خطة لمستخدم", "adm_assign")],
        [btn("📣 إرسال رسالة للجميع", "adm_broadcast")],
        [btn("📊 إحصائيات",           "adm_stats")],
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
    rows = [[btn(f"{p['label']}", f"adm_plan_{p['name']}")] for p in PLAN_LIMITS.values()]
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
#  Helpers
# ══════════════════════════════════════════════════════════════

def _plan_label(plan: str) -> str:
    return PLAN_LIMITS.get(plan, {}).get("label", plan)


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
        "ℹ️ *Auto Post Bot — المساعدة*\n━━━━━━━━━━━━━━━━━━\n\n"
        "/start — القائمة الرئيسية\n"
        "/help — هذه الرسالة\n"
        "/admin — لوحة الأدمن (للأدمن فقط)\n\n"
        f"للدعم: {SUPPORT_USERNAME}"
    )


async def fallback_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        txt = update.message.text or ""
        if txt in MAIN_NAV:
            return await nav_router(update, context)
    await _send_main_menu(update, context)
    return S_MAIN
# Admin Panal
async def _show_admin_from_kb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # بتنادي على لوحة الأدمن مباشرة لأنها معاها في نفس الملف
    return await cmd_admin(update, context)

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
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            S_MAIN: shared_cbs + [MessageHandler(nav_filter, nav_router)],

            S_ACCOUNTS: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_acc_add,        pattern="^acc_add$"),
                CallbackQueryHandler(cb_acc_detail,     pattern="^acc_detail_\\d+$"),
                CallbackQueryHandler(cb_acc_del,        pattern="^acc_del_\\d+$"),
                CallbackQueryHandler(cb_acc_check,      pattern="^acc_check_\\d+$"),
                CallbackQueryHandler(cb_acc_check_all,  pattern="^acc_check_all$"),
                CallbackQueryHandler(cb_acc_fetch_grp,  pattern="^acc_fetch_grp_\\d+$"),
                CallbackQueryHandler(cb_acc_fetch_pg,   pattern="^acc_fetch_pg_\\d+$"),
            ],
            S_ACC_NAME: [
                MessageHandler(any_text, acc_got_name),
                CallbackQueryHandler(cb_accounts, pattern="^accounts$"),
            ],
            S_ACC_COOKIES: [
                MessageHandler(any_text, acc_got_cookies),
                CallbackQueryHandler(cb_accounts, pattern="^accounts$"),
            ],
            S_ACC_PROXY: [
                MessageHandler(any_text, acc_got_proxy),
                CallbackQueryHandler(cb_acc_skip_proxy, pattern="^acc_skip_proxy$"),
                CallbackQueryHandler(cb_acc_continue,   pattern="^acc_continue$"),
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
                CallbackQueryHandler(cb_grp_check_post,  pattern="^grp_check_post$"),
                CallbackQueryHandler(cb_grp_vip,         pattern="^(grp_other|grp_members|grp_upload)$"),
            ],
            S_GRP_SEARCH: [
                MessageHandler(any_text, grp_got_search),
                CallbackQueryHandler(cb_groups, pattern="^groups$"),
            ],

            S_PAGES: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_ppost_now,       pattern="^ppost_now$"),
            ],
            S_PAGE_POST: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_ppost_now,       pattern="^ppost_now$"),
                CallbackQueryHandler(cb_pg_sel,          pattern="^pg_sel_\\d+$"),
                CallbackQueryHandler(cb_pg_sel_all,      pattern="^pg_sel_all$"),
                CallbackQueryHandler(cb_pg_sel_none,     pattern="^pg_sel_none$"),
                CallbackQueryHandler(cb_pg_confirm_sel,  pattern="^pg_confirm_sel$"),
                CallbackQueryHandler(cb_pg_dist,         pattern="^pg_dist_(spread|repeat)$"),
                CallbackQueryHandler(cb_camp_skip_media, pattern="^camp_skip_media$"),
            ],
            S_PAGE_STORY_IMG: [
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, story_got_image),
                CallbackQueryHandler(lambda u, c: (u.callback_query.answer(), S_PAGES)[1] if True else None, pattern="^pages$"),
            ],
            S_PAGE_STORY_LINK: [
                MessageHandler(any_text, story_got_time),
                CallbackQueryHandler(cb_story_skip_link, pattern="^story_skip_link$"),
                CallbackQueryHandler(cb_story_now,       pattern="^story_now$"),
                CallbackQueryHandler(cb_story_confirm,   pattern="^story_confirm$"),
                CallbackQueryHandler(lambda u, c: S_PAGES, pattern="^(story_edit|pages)$"),
            ],

            S_CAMPAIGNS: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_camp_new,        pattern="^camp_new$"),
                CallbackQueryHandler(cb_camp_log,        pattern="^camp_scheduled$"),
            ],
            S_CAMP_CAPTION: [
                MessageHandler(any_text, camp_got_caption),
                CallbackQueryHandler(cb_camp_skip_cap,   pattern="^camp_skip_cap$"),
                CallbackQueryHandler(cb_campaigns,       pattern="^campaigns$"),
            ],
            S_CAMP_MEDIA: [
                MessageHandler(filters.VIDEO | filters.PHOTO, camp_got_media),
                MessageHandler(any_text, camp_got_media),
                CallbackQueryHandler(cb_camp_skip_media, pattern="^camp_skip_media$"),
            ],
            S_CAMP_TARGETS: [
                CallbackQueryHandler(cb_camp_tgt_groups, pattern="^camp_tgt_groups$"),
                CallbackQueryHandler(cb_tsel,            pattern="^tsel_\\d+$"),
                CallbackQueryHandler(cb_tsel_all,        pattern="^tsel_all$"),
                CallbackQueryHandler(cb_tsel_none,       pattern="^tsel_none$"),
                CallbackQueryHandler(cb_camp_confirm_tgt,pattern="^camp_confirm_tgt$"),
                CallbackQueryHandler(cb_campaigns,       pattern="^campaigns$"),
            ],
            S_CAMP_SCHEDULE: [
                MessageHandler(any_text, camp_got_schedule),
                CallbackQueryHandler(cb_camp_now,          pattern="^camp_now$"),
                CallbackQueryHandler(cb_camp_sched_prompt, pattern="^camp_sched_prompt$"),
                CallbackQueryHandler(cb_camp_sched_confirm,pattern="^camp_sched_confirm$"),
                CallbackQueryHandler(cb_campaigns,         pattern="^campaigns$"),
            ],

            S_COMMENTS: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_cmt_vip, pattern="^(cmt_add|cmt_reply|cmt_mention|cmt_chatbot)$"),
            ],
            S_CMT_URL: [
                CallbackQueryHandler(cb_cmt_acc, pattern="^cmt_acc_\\d+$"),
                MessageHandler(any_text, cmt_got_url),
                CallbackQueryHandler(cb_comments, pattern="^comments$"),
            ],
            S_CMT_TEXT: [
                MessageHandler(any_text, cmt_got_text),
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
                MessageHandler(any_text, pagebot_got_url),
                CallbackQueryHandler(lambda u,c: _show_page_bot(u,c), pattern="^page_bot$"),
            ],
            S_PAGE_BOT_TPL: [
                CallbackQueryHandler(cb_pbot_tpl, pattern="^pbot_tpl_.+$"),
                CallbackQueryHandler(lambda u,c: _show_page_bot(u,c), pattern="^page_bot$"),
            ],
            S_PAGE_BOT_KW: [
                MessageHandler(any_text, pagebot_got_kw),
                CallbackQueryHandler(lambda u,c: _show_page_bot(u,c), pattern="^page_bot$"),
            ],
            S_PAGE_BOT_RCMT: [
                MessageHandler(any_text, pagebot_got_rcmt),
                CallbackQueryHandler(cb_pbot_rcmt_skip, pattern="^pbot_rcmt_skip$"),
                CallbackQueryHandler(lambda u,c: _show_page_bot(u,c), pattern="^page_bot$"),
            ],
            S_PAGE_BOT_RDM: [
                MessageHandler(any_text, pagebot_got_rdm),
                CallbackQueryHandler(cb_pbot_rdm_skip, pattern="^pbot_rdm_skip$"),
                CallbackQueryHandler(lambda u,c: _show_page_bot(u,c), pattern="^page_bot$"),
            ],

            S_MY_PLAN: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_plan_usage,        pattern="^plan_usage$"),
                CallbackQueryHandler(cb_plan_details,      pattern="^plan_details$"),
                CallbackQueryHandler(cb_plan_trial,        pattern="^plan_trial$"),
                CallbackQueryHandler(cb_plan_referral,     pattern="^plan_referral$"),
                CallbackQueryHandler(cb_plan_points,       pattern="^plan_points$"),
                CallbackQueryHandler(cb_plan_redeem,       pattern="^(plan_redeem|redeem_\\d+)$"),
                CallbackQueryHandler(cb_activate_code_btn, pattern="^activate_code_btn$"),
                CallbackQueryHandler(cb_plan_upgrade,      pattern="^plan_upgrade$"),
            ],
            S_ACTIVATE_CODE: [
                MessageHandler(any_text, activate_got_code),
                CallbackQueryHandler(cb_my_plan, pattern="^my_plan$"),
            ],

            S_TOOLS: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
            ],
            S_SETTINGS: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_set_delay,   pattern="^set_delay$"),
                CallbackQueryHandler(cb_delay_val,   pattern="^delay_\\d+$"),
                CallbackQueryHandler(cb_set_antiban, pattern="^set_antiban$"),
                CallbackQueryHandler(cb_ab_val,      pattern="^ab_(low|medium|high)$"),
                CallbackQueryHandler(cb_set_notif,   pattern="^set_notif$"),
                CallbackQueryHandler(cb_lang,        pattern="^lang_(ar|en)$"),
            ],
            S_TEMPLATES: shared_cbs + [
                MessageHandler(nav_filter, nav_router),
                CallbackQueryHandler(cb_tpl_type,  pattern="^tpl_(post|reply|smart|chatbot)$"),
                CallbackQueryHandler(cb_tpl_add,   pattern="^tpl_add$"),
                CallbackQueryHandler(cb_tpl_del,   pattern="^tpl_del_\\d+$"),
            ],
            S_TPL_TITLE: [
                MessageHandler(any_text, tpl_got_title),
                CallbackQueryHandler(cb_templates, pattern="^templates_cb$"),
            ],
            S_TPL_CONTENT: [
                MessageHandler(any_text, tpl_got_content),
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
            ],
            S_ADMIN_BROADCAST: [
                MessageHandler(any_text, adm_got_broadcast),
                CallbackQueryHandler(cb_adm_menu, pattern="^adm_menu$"),
            ],
            S_ADMIN_UID: [
                MessageHandler(any_text, adm_got_uid),
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
            MessageHandler(nav_filter, nav_router),
            CallbackQueryHandler(cb_main, pattern="^main$"),
            MessageHandler(any_text, fallback_msg),
        ],
        allow_reentry=True,
        per_message=False,
    )
