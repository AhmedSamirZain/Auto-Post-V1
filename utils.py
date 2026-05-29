import os
import re
import json
import random
import asyncio
import logging
from datetime import datetime
from urllib.parse import unquote

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int = 0) -> int:
    try:
        return int(os.getenv(name) or default)
    except (TypeError, ValueError):
        return default


ADMIN_ID = _int_env("ADMIN_ID", 0)


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def format_date(dt_str: str) -> str:
    if not dt_str:
        return "—"
    try:
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_str


def parse_cookie_string(raw: str) -> list:
    """
    Parse a raw cookie string like:
      "c_user=123456; xs=abcdef; datr=xxx"
    into a list of dicts suitable for Playwright:
      [{"name": "c_user", "value": "123456", "domain": ".facebook.com", ...}]
    Also accepts JSON format (list or dict) for backward compatibility.
    """
    raw = raw.strip()

    # Try JSON first (backward-compat)
    if raw.startswith("[") or raw.startswith("{"):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return _normalize_cookie_list(data)
            if isinstance(data, dict):
                return _normalize_cookie_list([data])
        except Exception:
            pass

    # Parse raw "key=value; key=value" string
    cookies = []
    parts = raw.split(";")
    for part in parts:
        part = part.strip()
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        cookies.append({
            "name": name,
            "value": unquote(value),
            "domain": ".facebook.com",
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "sameSite": "None",
        })
    return cookies


def _normalize_cookie_list(lst: list) -> list:
    result = []
    for c in lst:
        if not isinstance(c, dict):
            continue
        name = c.get("name") or c.get("key", "")
        value = c.get("value", "")
        if not name:
            continue
        result.append({
            "name": name,
            "value": str(value),
            "domain": c.get("domain", ".facebook.com"),
            "path": c.get("path", "/"),
            "secure": c.get("secure", True),
            "httpOnly": c.get("httpOnly", False),
            "sameSite": c.get("sameSite", "None"),
        })
    return result


def validate_cookies(raw: str) -> bool:
    """Accepts raw cookie string OR JSON. Returns True if parseable and non-empty."""
    if not raw or not raw.strip():
        return False
    cookies = parse_cookie_string(raw.strip())
    return len(cookies) > 0


def cookies_to_json(raw: str) -> str:
    """Convert any cookie format to JSON string for storage."""
    cookies = parse_cookie_string(raw)
    return json.dumps(cookies, ensure_ascii=False)


async def random_delay(min_sec: float = 5.0, max_sec: float = 15.0):
    await asyncio.sleep(random.uniform(min_sec, max_sec))


def extract_fb_group_id(url: str) -> str:
    patterns = [
        r"facebook\.com/groups/([^/?&#]+)",
        r"fb\.com/groups/([^/?&#]+)",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return url.strip()


def extract_fb_page_id(url: str) -> str:
    patterns = [
        r"facebook\.com/([^/?&#]+)",
        r"fb\.com/([^/?&#]+)",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            name = m.group(1)
            if name not in ("groups", "profile.php", "people", "pages"):
                return name
    return url.strip()


def truncate(text: str, max_len: int = 50) -> str:
    if not text:
        return ""
    return text[:max_len] + ("…" if len(text) > max_len else "")


def plan_label(plan: str) -> str:
    labels = {
        "free": "🆓 مجاني",
        "pro": "⭐ Pro",
        "unlimited": "👑 Unlimited",
    }
    return labels.get(plan, plan)


def anti_ban_label(level: str) -> str:
    return {"low": "🟢 منخفض", "medium": "🟡 متوسط", "high": "🔴 عالي"}.get(level, level)


STATUS_MAP = {
    "pending": ("⏳", "قيد الانتظار"),
    "running": ("🚀", "جارٍ التنفيذ"),
    "done": ("✅", "مكتمل"),
    "failed": ("❌", "فشل"),
    "paused": ("⏸", "متوقف"),
}


def status_label(status: str) -> str:
    e, t = STATUS_MAP.get(status, ("❓", status))
    return f"{e} {t}"


def build_welcome(full_name: str) -> str:
    return (
        f"🎉 *أهلاً بك يا {full_name}!*\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"مرحباً في بوت *Auto Post* ⚡\n\n"
        f"🚀 أداة النشر التلقائي الأذكى في مجموعات فيسبوك:\n"
        f"📢 نشر وجدولة تلقائية\n"
        f"🔍 بحث وانضمام ذكي\n"
        f"📊 تقارير وإحصائيات مفصلة\n"
        f"🔒 حماية كاملة من الحظر\n\n"
        f"ابدأ بربط حسابك من القائمة 👇"
    )


# ── Main ReplyKeyboard layout ─────────────────────────────────────────────────

MAIN_REPLY_BUTTONS = [
    ["👤 الحسابات", "👥 المجموعات"],
    ["📄 الصفحات", "🚀 الحملات"],
    ["📦 القوالب", "💬 التعليقات"],
    ["🗂 سجل الحملات", "💎 خطتي"],
    ["🧰 الأدوات", "🔧 الصيانة"],
    ["⚙️ الإعدادات"],
]

BUTTON_TEXT_MAP = {
    "👤 الحسابات": "accounts",
    "👥 المجموعات": "groups",
    "📄 الصفحات": "pages",
    "🚀 الحملات": "campaigns",
    "📦 القوالب": "templates",
    "💬 التعليقات": "comments",
    "🗂 سجل الحملات": "campaign_log",
    "💎 خطتي": "my_plan",
    "🧰 الأدوات": "tools",
    "🔧 الصيانة": "maintenance",
    "⚙️ الإعدادات": "settings",
    "🏠 الرئيسية": "main_menu",
}
