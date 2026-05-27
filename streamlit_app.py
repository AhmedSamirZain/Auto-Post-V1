import streamlit as st
import threading
import os
import sys

st.title("🤖 Auto Post Telegram Bot")
st.write("البوت يعمل الآن في الخلفية بنجاح... 🚀")

# نضمن إن السيستم شايف المجلد الحالي عشان ما يرفعش خطأ في الاستيراد
sys.path.append(os.path.dirname(__file__))

def run_bot():
    # تثبيت متصفح بلاي رايت داخل السيرفر تلقائياً عشان الفيس بوك
    os.system("playwright install chromium")
    
    # استدعاء دالة main الفعالة من ملف bot.py بتاعك
    from bot import main
    try:
        main()
    except Exception as e:
        print(f"Bot error: {e}")

# تشغيل البوت في Thread منفصل عشان السيرفر ما يعلقش
if "bot_thread" not in st.session_state:
    st.session_state.bot_thread = threading.Thread(target=run_bot, daemon=True)
    st.session_state.bot_thread.start()
