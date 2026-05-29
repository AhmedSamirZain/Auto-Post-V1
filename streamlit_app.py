import streamlit as st
import os
import sys
import subprocess
import time

st.set_page_config(page_title="Bot Monitor", page_icon="🤖")
st.title("🤖 Auto Post Telegram Bot")

PID_FILE = "bot.pid"
LOG_FILE = "bot_output.log"


# ════════════════════════════════════════════════════════════
#  نقل الأسرار من Streamlit Secrets إلى متغيرات البيئة
#  عشان config.py يقدر يقراها بـ os.getenv
# ════════════════════════════════════════════════════════════
def load_secrets_into_env():
    try:
        for key in ("BOT_TOKEN", "ADMIN_ID", "INSTAPAY_ADDRESS",
                    "VODAFONE_CASH", "COOKIE_SECRET"):
            if key in st.secrets and not os.getenv(key):
                os.environ[key] = str(st.secrets[key])
    except Exception:
        pass


def is_running():
    """يتأكد إن البروسيس المحفوظ في bot.pid لسه شغال فعلاً."""
    if not os.path.exists(PID_FILE):
        return False
    try:
        pid = int(open(PID_FILE).read().strip())
        os.kill(pid, 0)   # إشارة 0 = فحص بدون قتل
        return True
    except Exception:
        return False


def start_bot():
    """يشغّل البوت (main.py) في الخلفية لو مش شغال."""
    if is_running():
        return False
    load_secrets_into_env()
    # نفتح ملف اللوج ونوجّه له خرج البوت
    log = open(LOG_FILE, "a", buffering=1)
    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        stdout=log,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
    )
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))
    return True


# ════════════════════════════════════════════════════════════
#  التشغيل التلقائي: أول ما الصفحة تفتح، شغّل البوت لو متوقف
# ════════════════════════════════════════════════════════════
load_secrets_into_env()
just_started = False
if not is_running():
    just_started = start_bot()
    if just_started:
        time.sleep(3)   # ننتظر شوية عشان البوت يبدأ ويكتب في اللوج


# ── واجهة الحالة ──────────────────────────────────────────────
if is_running():
    st.success("🟢 البوت يعمل")
    if just_started:
        st.info("⚡ تم تشغيل البوت الآن تلقائياً.")
else:
    st.error("🔴 البوت متوقف")
    if st.button("▶️ تشغيل البوت الآن"):
        start_bot()
        time.sleep(3)
        st.rerun()

# ── فحص الأسرار ───────────────────────────────────────────────
if not os.getenv("BOT_TOKEN"):
    st.warning("⚠️ لم يتم العثور على BOT_TOKEN في الأسرار (Secrets). "
               "أضِفه من Settings ⚙️ → Secrets.")

st.subheader("📋 سجل البوت:")
if os.path.exists(LOG_FILE):
    with open(LOG_FILE, "r", errors="replace") as f:
        logs = f.read()
    if logs.strip():
        st.code(logs[-5000:], language="text")
    else:
        st.info("⏳ جاري التشغيل...")
else:
    st.warning("لا يوجد سجل.")

if st.button("🔄 تحديث"):
    st.rerun()
