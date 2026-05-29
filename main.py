"""
Auto Post Bot v2.0 — Entry point
"""
import os
import logging
import traceback
import html
from datetime import datetime

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram import BotCommand, Update
from telegram.constants import ParseMode

import database as db
from config import BOT_TOKEN, ADMIN_ID, BOT_NAME, BOT_VERSION
from handlers import build_conversation_handler, cmd_admin, cmd_help, fallback_msg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# حفظ وقت تشغيل البوت لحساب الـ Uptime
START_TIME = datetime.now()

# ==========================================
# 1️⃣ إضافة: جدولة المهام (Scheduled Tasks)
# ==========================================
async def scheduled_maintenance(context: ContextTypes.DEFAULT_TYPE):
    logger.info("صيانة دورية: فحص الحملات المجدولة المستحقة...")
    pass


async def cleanup_temp_files(context: ContextTypes.DEFAULT_TYPE):
    """تنظيف ملفات الميديا المؤقتة الأقدم من ساعة (تمنع امتلاء السيرفر)."""
    import glob
    import time as _t
    try:
        now = _t.time()
        for pattern in ("/tmp/vid_*.mp4", "/tmp/img_*.jpg", "/tmp/story_*.jpg", "/tmp/story_*.mp4", "/tmp/video_*.mp4"):
            for fpath in glob.glob(pattern):
                try:
                    if now - os.path.getmtime(fpath) > 3600:  # أقدم من ساعة
                        os.remove(fpath)
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"cleanup_temp_files error: {e}")


async def proactive_notifications(context: ContextTypes.DEFAULT_TYPE):
    """تنبيهات استباقية: تحذير المستخدمين قبل انتهاء اشتراكهم."""
    try:
        from datetime import datetime
        users = await db.get_users_expiring_soon(days=2)
        for u in users:
            uid = u["user_id"]
            exp = u.get("plan_expires", "")
            kind = f"expiry_{exp[:10]}"
            if await db.was_notified(uid, kind):
                continue
            if not u.get("notifications", 1):
                continue
            try:
                await context.bot.send_message(
                    uid,
                    "⏰ *تنبيه: اشتراكك قارب على الانتهاء!*\n\n"
                    f"ينتهي في: *{exp}*\n\n"
                    "جدّد الآن لتستمر في الاستفادة من كل المميزات 💎\n"
                    "اكتب /plan للتجديد.",
                    parse_mode="Markdown",
                )
                await db.mark_notified(uid, kind)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"proactive_notifications error: {e}")


async def run_page_bots(context: ContextTypes.DEFAULT_TYPE):
    """worker بوت الصفحات: يقرأ التعليقات ويرد تلقائياً على الكلمات المفتاحية."""
    try:
        import json as _json
        from fb_automator import FBAutomator
        bots = await db.get_all_active_page_bots()
        for bot in bots:
            post_url = bot.get("post_url")
            if not post_url:
                continue
            try:
                keywords = _json.loads(bot.get("keywords", "[]"))
            except Exception:
                keywords = []
            reply_text = bot.get("reply_comment", "")
            if not reply_text:
                continue
            uid = bot["user_id"]
            accs = await db.get_accounts(uid)
            if not accs:
                continue
            acc = next((a for a in accs if a["id"] == bot.get("account_id")), accs[0])
            auto = FBAutomator(acc["id"], acc.get("cookies", ""), acc.get("proxy"))
            try:
                comments = await auto.read_post_comments(post_url, limit=15)
            except Exception:
                comments = []
            for c in comments:
                cid = c.get("id", "")
                ctext = (c.get("text") or "").lower()
                # لو فيه كلمات مفتاحية، لازم تتطابق؛ لو مفيش، نرد على الكل
                if keywords and not any(k.strip().lower() in ctext for k in keywords if k.strip()):
                    continue
                if await db.is_comment_replied(bot["id"], cid):
                    continue
                try:
                    await auto.post_comment(post_url, reply_text)
                    await db.mark_comment_replied(bot["id"], cid)
                    await db.log_activity(uid, "بوت الصفحات", f"رد تلقائي على تعليق", "success")
                except Exception as e:
                    logger.error(f"page bot reply error: {e}")
    except Exception as e:
        logger.error(f"run_page_bots error: {e}")


async def check_scheduled_campaigns(context: ContextTypes.DEFAULT_TYPE):
    """تعمل كل دقيقة: تشغّل أي حملة مجدولة حان وقتها."""
    try:
        import pytz
        from datetime import datetime as _dt
        cairo = pytz.timezone("Africa/Cairo")
        now = _dt.now(cairo)
        pending = await db.get_pending_campaigns()
        if not pending:
            return
        import handlers as H
        for camp in pending:
            sched = camp.get("schedule_time")
            if not sched:
                continue
            # صيغة الجدولة: "%Y-%m-%d %H:%M"
            try:
                naive = _dt.strptime(sched, "%Y-%m-%d %H:%M")
                sched_dt = cairo.localize(naive)
            except Exception:
                continue
            if sched_dt <= now:
                uid = camp["user_id"]
                accs = await db.get_accounts(uid)
                if not accs:
                    await db.update_campaign_status(camp["id"], "failed")
                    continue
                import json as _json
                try:
                    target_ids = _json.loads(camp.get("targets", "[]"))
                except Exception:
                    target_ids = []
                user_obj = await db.get_user(uid)
                delay = (user_obj or {}).get("time_delay", 60)
                anti = (user_obj or {}).get("anti_ban_level", "medium")
                await db.update_campaign_status(camp["id"], "running")
                # تشغيل الحملة في الخلفية بنفس محرك handlers
                import asyncio as _aio
                if (camp.get("target_type") or "groups") == "pages":
                    _aio.create_task(H._run_page_campaign_bg(
                        context, uid, camp["id"], accs[0], target_ids,
                        camp.get("content", ""), camp.get("media_path"), delay
                    ))
                else:
                    _aio.create_task(H._run_campaign_bg(
                        context, uid, camp["id"], accs[0], target_ids,
                        camp.get("content", ""), camp.get("media_path"), delay, anti
                    ))
                logger.info(f"تشغيل الحملة المجدولة #{camp['id']} للمستخدم {uid}")
                # إعادة جدولة الحملة المتكررة للموعد القادم
                rec = camp.get("recurring") or ""
                if rec in ("daily", "weekly"):
                    from datetime import timedelta as _td
                    delta = _td(days=1) if rec == "daily" else _td(days=7)
                    next_dt = (sched_dt + delta).strftime("%Y-%m-%d %H:%M")
                    try:
                        import aiosqlite as _sq
                        async with _sq.connect(db.DB_PATH) as _c:
                            await _c.execute(
                                "UPDATE campaigns SET schedule_time=?, status='pending' WHERE id=?",
                                (next_dt, camp["id"]))
                            await _c.commit()
                        logger.info(f"إعادة جدولة الحملة المتكررة #{camp['id']} -> {next_dt}")
                    except Exception as _e:
                        logger.error(f"recurring reschedule error: {_e}")
    except Exception as e:
        logger.error(f"check_scheduled_campaigns error: {e}")



async def check_scheduled_stories(context: ContextTypes.DEFAULT_TYPE):
    """تعمل كل دقيقة: تنشر أي ستوري صفحات مجدول حان موعده."""
    try:
        import json as _json
        import pytz
        from datetime import datetime as _dt
        from fb_automator import FBAutomator

        cairo = pytz.timezone("Africa/Cairo")
        now = _dt.now(cairo)
        pending = await db.get_pending_stories()
        if not pending:
            return

        for st in pending:
            sched = st.get("schedule_time")
            try:
                naive = _dt.strptime(sched, "%Y-%m-%d %H:%M")
                sched_dt = cairo.localize(naive)
            except Exception:
                await db.update_scheduled_story_status(st["id"], "failed")
                continue
            if sched_dt > now:
                continue

            uid = st["user_id"]
            await db.update_scheduled_story_status(st["id"], "running")
            try:
                page_ids = _json.loads(st.get("page_ids") or "[]")
            except Exception:
                page_ids = []
            accs = await db.get_accounts(uid)
            if not accs:
                await db.update_scheduled_story_status(st["id"], "failed")
                continue
            acc = next((a for a in accs if a["id"] == st.get("account_id")), accs[0])
            pages = await db.get_pages(uid)
            target_pages = [p for p in pages if p["id"] in page_ids]
            if not target_pages:
                await db.update_scheduled_story_status(st["id"], "failed")
                continue

            auto = FBAutomator(acc["id"], acc.get("cookies", ""), acc.get("proxy"))
            done = failed = 0
            for pg in target_pages:
                try:
                    res = await auto.post_story(
                        pg.get("page_id", ""),
                        st.get("media_path"),
                        st.get("link"),
                        st.get("story_text", ""),
                    )
                except Exception as e:
                    res = {"success": False, "error": str(e)}
                if res.get("success"):
                    done += 1
                else:
                    failed += 1
                await db.update_scheduled_story_status(st["id"], "running", done, failed)

            status = "done" if done else "failed"
            await db.update_scheduled_story_status(st["id"], status, done, failed)
            await db.log_activity(uid, "نشر ستوري مجدول", f"ستوري #{st['id']}: ✅{done} ❌{failed}", status)

            # تنظيف الميديا المحفوظة بعد التنفيذ
            media_path = st.get("media_path")
            try:
                if media_path and os.path.exists(media_path):
                    base = os.path.abspath(os.path.join(os.path.dirname(__file__), "data", "scheduled_media"))
                    if os.path.abspath(media_path).startswith(base):
                        os.remove(media_path)
            except Exception:
                pass

            try:
                await context.bot.send_message(
                    uid,
                    f"✅ *تم تنفيذ الستوري المجدول #{st['id']}*\n\n"
                    f"✅ نجح: *{done}*\n❌ فشل: *{failed}*",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
    except Exception as e:
        logger.error(f"check_scheduled_stories error: {e}")


# ==========================================
# 2️⃣ إضافة: فحص الحالة (Health Check)
# ==========================================
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # التأكد إن اللي بيطلب الحالة هو الأدمن فقط
    if str(update.effective_user.id) != str(ADMIN_ID):
        return

    uptime = datetime.now() - START_TIME
    uptime_str = str(uptime).split('.')[0] # إزالة الأجزاء من الثانية لشكل أنظف

    try:
        stats = await db.get_stats()
    except Exception:
        stats = {"users": "?", "pro_users": "?", "accounts": "?", "campaigns": "?"}

    text = (
        f"📊 *حالة نظام البوت (Health Check)*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ *وقت التشغيل المستمر:* `{uptime_str}`\n"
        f"🤖 *إصدار البوت:* `{BOT_VERSION}`\n"
        f"👥 *إجمالي المستخدمين:* `{stats.get('users', 0)}`\n"
        f"🔄 *حالة الخوادم:* `🟢 تعمل بكفاءة`\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ==========================================
# 3️⃣ إضافة: اصطياد الأخطاء (Global Error Handler)
# ==========================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

    err = context.error
    # نتجاهل أخطاء تليجرام الشائعة غير المؤثرة (لا نزعج الأدمن بها)
    ignorable = ("message is not modified", "query is too old",
                 "message to edit not found", "message can't be deleted")
    err_str = str(err)
    if any(ig in err_str.lower() for ig in ignorable):
        return

    # رسالة الخطأ للأدمن — نص عادي بدون Markdown لتجنب فشل ثانوي
    error_msg = err_str[:500]
    text = (
        "⚠️ تنبيه: حدث خطأ داخلي في البوت!\n\n"
        "📄 تفاصيل الخطأ:\n"
        f"{error_msg}\n\n"
        "⚙️ البوت ما زال يعمل بشكل طبيعي."
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=text)
    except Exception as e:
        logger.error(f"فشل إرسال رسالة الخطأ للأدمن: {e}")


async def post_init(application):
    await db.init_db()
    
    # تحديث قائمة الأوامر الجانبية للبوت
    await application.bot.set_my_commands([
        BotCommand("start", "القائمة الرئيسية"),
        BotCommand("post",  "🚀 حملة جديدة"),
        BotCommand("stats", "📊 إحصائياتي"),
        BotCommand("accounts", "👤 حساباتي"),
        BotCommand("groups", "👥 مجموعاتي"),
        BotCommand("plan",  "💎 خطتي"),
        BotCommand("help",  "المساعدة"),
        BotCommand("admin", "لوحة الأدمن"),
        BotCommand("status", "حالة النظام (للأدمن)"),
    ])
    logger.info(f"{BOT_NAME} v{BOT_VERSION} initialized ✅")
    # 👇 السطرين الجداد هنا بالظبط لإظهار زرار الأربع نقط (Menu) غصب عن تليجرام
    from telegram import MenuButtonCommands
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    # تشغيل الصيانة المجدولة كل 24 ساعة (86400 ثانية)
    application.job_queue.run_repeating(scheduled_maintenance, interval=86400, first=10)
    # فحص الحملات المجدولة كل 60 ثانية
    application.job_queue.run_repeating(check_scheduled_campaigns, interval=60, first=20)
    # فحص ستوري الصفحات المجدول كل 60 ثانية
    application.job_queue.run_repeating(check_scheduled_stories, interval=60, first=25)
    # تنظيف الملفات المؤقتة كل ساعة
    application.job_queue.run_repeating(cleanup_temp_files, interval=3600, first=300)
    # تشغيل بوتات الصفحات (رد تلقائي على التعليقات) كل 5 دقائق
    application.job_queue.run_repeating(run_page_bots, interval=300, first=60)
    # تنبيهات استباقية (انتهاء الاشتراك) كل 6 ساعات
    application.job_queue.run_repeating(proactive_notifications, interval=21600, first=120)

    # إشعار الأدمن بتشغيل البوت
    try:
        stats = await db.get_stats()
        await application.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🟢 *{BOT_NAME} v{BOT_VERSION} — تم التشغيل بنجاح!*\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 المستخدمون: *{stats.get('users', 0)}*\n"
                f"💎 المشتركون: *{stats.get('pro_users', 0)}*\n"
                f"🔗 الحسابات: *{stats.get('accounts', 0)}*\n"
                f"🚀 الحملات: *{stats.get('campaigns', 0)}*\n\n"
                f"🕐 وقت التشغيل: الآن"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.warning(f"Could not notify admin: {e}")


def ensure_chromium():
    """يتأكد إن متصفح Chromium الخاص بـ Playwright مثبّت، ويثبّته تلقائياً لو مش موجود.
    يشتغل مرة واحدة عند بدء التشغيل — يفيد على أي استضافة (Streamlit/VPS/Replit)
    من غير أي أوامر يدوية. لو فشل، البوت يكمّل في الوضع التجريبي بدون ما يقع.
    """
    import subprocess
    import sys
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright غير مثبّت — البوت سيعمل في الوضع التجريبي (mock).")
        return

    # نتأكد إن المتصفح موجود فعلاً
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            b.close()
        logger.info("✅ متصفح Chromium جاهز — النشر الحقيقي مفعّل.")
        return
    except Exception:
        logger.info("⏳ متصفح Chromium غير موجود — جاري تثبيته لأول مرة...")

    # تثبيت المتصفح تلقائياً
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True, timeout=600,
        )
        logger.info("✅ تم تثبيت متصفح Chromium بنجاح — النشر الحقيقي مفعّل.")
    except Exception as e:
        logger.warning(
            f"⚠️ تعذّر تثبيت المتصفح ({e}). "
            "البوت سيعمل في الوضع التجريبي (mock) — كل الأزرار تشتغل لكن بدون نشر حقيقي."
        )


def main():
    token = BOT_TOKEN or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN غير موجود!")

    # تثبيت متصفح النشر تلقائياً (مرة واحدة) — يعمل على أي استضافة
    ensure_chromium()

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    # ربط معالج الأخطاء العالمي (Error Handler)
    app.add_error_handler(error_handler)

    # ربط الأوامر (Handlers)
    app.add_handler(build_conversation_handler())
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(CommandHandler("status", cmd_status)) # أمر الحالة الجديد
    app.add_handler(MessageHandler(filters.COMMAND, fallback_msg))

    logger.info(f"Starting {BOT_NAME}...")
    app.run_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
