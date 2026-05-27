import streamlit as st
import subprocess
import os
import sys

st.set_page_config(page_title="Reels Bot", page_icon="🤖")
st.title("🤖 Reels Bot Dashboard")

# كود لمنع التكرار
if "bot_process" not in st.session_state:
    st.info("🚀 جاري تشغيل البوت...")
    # تشغيل main.py
    process = subprocess.Popen([sys.executable, "main.py"])
    st.session_state.bot_process = process.pid
    st.success("✅ البوت شغال الآن!")
else:
    st.success(f"✅ البوت مستمر في العمل (PID: {st.session_state.bot_process})")

st.warning("⚠️ لو فيه أخطاء Conflict، تأكد إنك قافل البوت على جهازك الشخصي.")
