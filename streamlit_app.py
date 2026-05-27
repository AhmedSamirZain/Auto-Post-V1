import streamlit as st
import subprocess
import os

# إعدادات الصفحة والواجهة
st.set_page_config(page_title="Telegram Bot Control", page_icon="🤖")
st.title("🤖 Auto Post Telegram Bot")
st.write("مرحباً بك يا أحمد! لوحة تحكم البوت الشخصية.")

# دالة تشغيل البوت كعملية منفصلة في الخلفية (تشتغل مرة واحدة بس وتمنع التكرار)
@st.cache_resource
def run_my_bot():
    # 1. تثبيت متصفح بلاي رايت فوراً عشان النشر تلقائي يشتغل
    os.system("python -m playwright install chromium")
    
    # 2. أخذ نسخة من بيئة النظام وحقن الـ Secrets جواها عشان ملف config يشوفها
    bot_env = os.environ.copy()
    for key, value in st.secrets.items():
        bot_env[key] = str(value)
    
    # 3. تشغيل ملف bot.py مباشرة كـ Process منفصل في الخلفية
    process = subprocess.Popen(["python", "bot.py"], env=bot_env)
    return process

# تنفيذ التشغيل تلقائياً أول ما السيرفر يفتح
try:
    bot_process = run_my_bot()
    st.success("🟢 البوت تم تشغيله بنجاح وأمان في الخلفية لمدة 24 ساعة! 🚀")
    st.balloons()
except Exception as e:
    st.error(f"❌ حدث خطأ أثناء تشغيل السيرفر: {e}")
