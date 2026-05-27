import streamlit as st
import subprocess
import os
import sys

st.set_page_config(page_title="Telegram Bot Monitor", page_icon="🤖")
st.title("🤖 Auto Post Telegram Bot")

# 1. حقن الأسرار في بيئة النظام
for key, value in st.secrets.items():
    os.environ[key] = str(value)

# دالة لتشغيل البوت وحفظ العملية في الكاش لمنع التكرار
@st.cache_resource
def start_bot_process():
    # تثبيت بلاي رايت
    os.system("python -m playwright install chromium")
    
    # تشغيل ملف bot.py وتوجيه المخرجات لملف نصي لقراءتها
    log_file = open("bot_output.log", "w")
    process = subprocess.Popen(
        [sys.executable, "bot.py"],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=os.environ.copy()
    )
    return process

# تشغيل البوت
try:
    process = start_bot_process()
    st.success("🟢 سيرفر ستريمليت أطلق العملية في الخلفية!")
except Exception as e:
    st.error(f"❌ فشل إطلاق العملية: {e}")

# 2. عرض الـ Logs الحقيقية للبوت جوه الصفحة عشان نشوف المشكلة
st.subheader("📋 شاشة مراقبة أخطاء البوت الداخلية (Bot Terminal Live):")

if os.path.exists("bot_output.log"):
    with open("bot_output.log", "r") as f:
        logs = f.read()
    
    if logs.strip() == "":
        st.info("⏳ البوت يبدأ التشغيل الآن... انتظر ثواني واعمل ريفريش للصفحة.")
    else:
        st.code(logs, language="text")
else:
    st.warning("🔄 لم يتم إنشاء ملف السجلات بعد.")

# زرار لتحديث الشاشة يدوياً وقراءة الجديد
if st.button("🔄 تحديث الشاشة وقراءة الأخطاء"):
    st.rerun()
