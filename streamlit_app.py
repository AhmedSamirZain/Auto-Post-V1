import streamlit as st
import os, sys, asyncio, datetime
import ctypes

st.set_page_config(page_title="Auto Post Bot — Dashboard", page_icon="🤖", layout="wide")

# ── استيراد دوال قاعدة البيانات من main.py ──
import importlib.util as iu
spec = iu.spec_from_file_location("main", os.path.join(os.path.dirname(__file__), "main.py"))
main = iu.module_from_spec(spec)
spec.loader.exec_module(main)

base_dir = os.path.dirname(os.path.abspath(__file__))
pid_file = os.path.join(base_dir, "bot.pid")

def is_running():
    if not os.path.exists(pid_file):
        return False
    try:
        with open(pid_file) as f:
            pid = int(f.read().strip())
        if sys.platform == "win32":
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(1, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False

@st.cache_data(ttl=10)
def get_db_stats():
    try:
        return asyncio.run(main.get_stats())
    except Exception as e:
        return {"error": str(e)}

@st.cache_data(ttl=10)
def get_all_users():
    try:
        return asyncio.run(main.get_all_users())
    except Exception as e:
        return []

# ═══════ HEADER ═══════

col1, col2 = st.columns([6, 1])
with col1:
    st.title("🤖 Auto Post Bot — Dashboard")
    st.caption("يتحدّث كل 10 ثوانٍ")
with col2:
    running = is_running()
    st.markdown(f"<h2 style='text-align:center;'>{'🟢' if running else '🔴'}</h2>", unsafe_allow_html=True)
    st.caption("شغال" if running else "متوقف")

# ═══════ STATS ═══════

stats = get_db_stats()
if "error" in stats:
    st.error(f"⚠️ {stats['error']}")
else:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("👤 المستخدمون", stats.get("users", "—"))
    c2.metric("💎 Pro+", stats.get("pro_users", "—"))
    c3.metric("🔗 حسابات FB", stats.get("accounts", "—"))
    c4.metric("🚀 الحملات", stats.get("campaigns", "—"))

# ═══════ USERS TABLE ═══════

users = get_all_users()
if users:
    st.subheader(f"👥 المستخدمين ({len(users)})")
    rows = []
    for u in users:
        plan = u.get("plan", "free")
        expires = u.get("plan_expires") or ""
        if expires:
            try:
                dt = datetime.datetime.fromisoformat(expires)
                expires = dt.strftime("%Y-%m-%d")
            except Exception:
                pass
        rows.append({
            "ID": u["user_id"],
            "الاسم": (u.get("full_name") or u.get("username") or "—")[:20],
            "الخطة": {"free": "مجاني", "pro": "Pro", "unlimited": "Unlimited"}.get(plan, plan),
            "تنتهي": expires,
            "النقاط": u.get("points", 0),
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

st.divider()
if st.button("🔄 تحديث"):
    st.cache_data.clear()
    st.rerun()
