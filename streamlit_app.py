import streamlit as st
import os

st.set_page_config(page_title="Bot Monitor", page_icon="🤖")
st.title("🤖 Auto Post Telegram Bot")

pid_file = "bot.pid"
log_file = "bot_output.log"

def is_running():
    if not os.path.exists(pid_file):
        return False
    try:
        pid = int(open(pid_file).read().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False

if is_running():
    st.success("🟢 البوت يعمل")
else:
    st.error("🔴 البوت متوقف")

st.subheader("📋 سجل البوت:")
if os.path.exists(log_file):
    with open(log_file, "r", errors="replace") as f:
        logs = f.read()
    if logs.strip():
        st.code(logs[-5000:], language="text")
    else:
        st.info("⏳ جاري التشغيل...")
else:
    st.warning("لا يوجد سجل.")

if st.button("🔄 تحديث"):
    st.rerun()
