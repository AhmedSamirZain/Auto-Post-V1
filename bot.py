"""
Auto Post Bot v2.0 — Entry point
"""
import os
import logging
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from telegram import BotCommand
from telegram.constants import ParseMode

import database as db
from config import BOT_TOKEN, ADMIN_ID, BOT_NAME, BOT_VERSION
from handlers import build_conversation_handler, cmd_admin, cmd_help, fallback_msg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def post_init(application):
    await db.init_db()
    await application.bot.set_my_commands([
        BotCommand("start", "القائمة الرئيسية"),
        BotCommand("help",  "المساعدة"),
        BotCommand("admin", "لوحة الأدمن"),
    ])
    logger.info(f"{BOT_NAME} v{BOT_VERSION} initialized ✅")

    # إشعار الأدمن بتشغيل البوت
    stats = await db.get_stats()
    try:
        await application.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🟢 *{BOT_NAME} v{BOT_VERSION} — تم التشغيل بنجاح!*\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 المستخدمون: *{stats['users']}*\n"
                f"💎 المشتركون: *{stats['pro_users']}*\n"
                f"🔗 الحسابات: *{stats['accounts']}*\n"
                f"🚀 الحملات: *{stats['campaigns']}*\n\n"
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

    app.add_handler(build_conversation_handler())
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(MessageHandler(filters.COMMAND, fallback_msg))

    logger.info(f"Starting {BOT_NAME}...")
    app.run_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
