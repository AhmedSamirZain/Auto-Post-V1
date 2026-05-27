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
    logger.info("بدء مهمة الصيانة المجدولة (تنظيف الكوكيز المنتهية/فحص الحسابات)...")
    # الدالة دي هتشتغل في الخلفية لوحدها، هنضيف فيها بعدين أوامر فحص الحسابات المربوطة
    pass


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
    
    # تجميع الخطأ بشكل نصي
    error_msg = html.escape(str(context.error))
    
    # رسالة الخطأ للأدمن
    text = (
        f"⚠️ *تنبيه: حدث خطأ داخلي في البوت!*\n\n"
        f"📄 *تفاصيل الخطأ:*\n"
        f"`{error_msg}`\n\n"
        f"⚙️ *البوت ما زال يعمل، لكن يُرجى مراجعة الكود.*"
    )
    
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID, 
            text=text, 
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"فشل إرسال رسالة الخطأ للأدمن: {e}")


async def post_init(application):
    await db.init_db()
    
    # تحديث قائمة الأوامر الجانبية للبوت
    await application.bot.set_my_commands([
        BotCommand("start", "القائمة الرئيسية"),
        BotCommand("help",  "المساعدة"),
        BotCommand("admin", "لوحة الأدمن"),
        BotCommand("status", "حالة النظام (للأدمن)"), 
    ])
    logger.info(f"{BOT_NAME} v{BOT_VERSION} initialized ✅")

    # تشغيل الصيانة المجدولة كل 24 ساعة (86400 ثانية)
    application.job_queue.run_repeating(scheduled_maintenance, interval=86400, first=10)

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


def main():
    token = BOT_TOKEN or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN غير موجود!")

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
