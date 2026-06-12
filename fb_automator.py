"""
fb_automator.py — Facebook Automation / أتمتة فيسبوك
====================================================

Sections / الأقسام:
  1. Cookie Helpers       —  تحليل الكوكيز وتحويلها (JSON ↔ dict)
  2. Account Name Fetcher —  جلب اسم الحساب من فيسبوك
  3. Login Check          —  فحص تسجيل الدخول
  4. Cookie Diagnostics   —  تشخيص الكوكيز (4 خطوات)
  5. Groups Fetcher       —  سحب المجموعات (mbasic + m.facebook.com)
  6. Pages Fetcher        —  سحب الصفحات (mbasic + m.facebook.com)
  7. FBAutomator Class    —  الكلاس الرئيسي للنشر
      7a. post_to_group   —  نشر على مجموعة (عبر Playwright)
      7b. post_comment    —  تعليق على منشور
      7c. post_story      —  نشر ستوري
  8. Human-like Helpers   —  محاكاة السلوك البشري (scroll, type)
  9. Video Downloader     —  تحميل الفيديو من الروابط (yt-dlp)

Groups/pages fetched via httpx (mbasic + m.facebook.com) — no Playwright needed.
Posting to groups / comments / stories uses Playwright when available.
"""
import os
import re
import json
import html as html_module
import asyncio
import random
import tempfile
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

SESSIONS_DIR = os.path.join(os.path.dirname(__file__), "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

# ════════════════════════════════════════════════════════════
#  1. Cookie Helpers  /  تحليل الكوكيز وتحويلها
#     _parse_cookies, _cookies_to_httpx, _get_c_user, _parse_cookies_list
# ════════════════════════════════════════════════════════════

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Accept-Language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://mbasic.facebook.com/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

def _parse_cookies(raw: str) -> list:
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _cookies_to_httpx(cookies_list: list) -> dict:
    return {c["name"]: str(c["value"]) for c in cookies_list if "name" in c and "value" in c}


def _get_c_user(cookies_list: list) -> str:
    for c in cookies_list:
        if c.get("name") == "c_user":
            return str(c.get("value", ""))
    return ""


def _clean_name(raw: str) -> str:
    """Decode HTML entities and clean whitespace from a name."""
    try:
        name = html_module.unescape(raw)
        name = re.sub(r'\s+', ' ', name).strip()
        return name
    except Exception:
        return raw.strip()


_NAME_SKIP = {
    "facebook", "log in", "sign up", "error", "login", "sign in",
    "خطأ", "تسجيل", "غير متوفر", "هذا المتصفح", "فيسبوك",
    "not available", "not supported", "page not found",
    "checkpoint", "security", "متصفح", "مجموعات", "أصدقاء",
    "الرسائل", "الإشعارات", "groups", "friends", "messages",
}

def _valid_name(n: str) -> bool:
    if not n or len(n.strip()) < 2:
        return False
    nl = n.lower().strip()
    # Reject if any skip word found
    if any(w in nl for w in _NAME_SKIP):
        return False
    # Reject if it looks like a number or count (e.g. "٩٩", "99")
    if re.fullmatch(r'[\d\u0660-\u0669٪%+\s]+', nl):
        return False
    # Must contain at least one letter (Arabic or Latin)
    if not re.search(r'[\u0600-\u06FF\u0750-\u077FA-Za-z]', n):
        return False
    return True

def _strip_site_suffix(raw: str) -> str:
    """Remove ' | Facebook', ' | فيسبوك', ' - Facebook' etc from title."""
    for sep in [' | ', ' - ', ' – ', ' — ']:
        if sep in raw:
            parts = [p.strip() for p in raw.split(sep)]
            # Return the part that's most likely the name (not containing 'facebook'/'فيسبوك')
            for p in parts:
                if p and 'facebook' not in p.lower() and 'فيسبوك' not in p:
                    return p
    return raw.strip()

# ════════════════════════════════════════════════════════════
#  2. Account Name Fetcher  /  جلب اسم الحساب
#     get_account_name(cookies) → tries mbasic + m.facebook
#     Uses 7 extraction patterns to handle Arabic / English names
# ════════════════════════════════════════════════════════════

async def get_account_name(cookies_json: str) -> str:
    cookies = _parse_cookies(cookies_json)
    jar = _cookies_to_httpx(cookies)
    c_user = _get_c_user(cookies)
    if not jar or not c_user:
        return f"حساب_{c_user or 'مجهول'}"

    def _extract_name(body: str) -> Optional[str]:
        # 1. <title> tag
        m = re.search(r'<title>([^<]+)</title>', body, re.IGNORECASE)
        if m:
            name = _strip_site_suffix(_clean_name(m.group(1)))
            if _valid_name(name):
                return name

        # 2. og:title meta tag
        m_og = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\'<]{2,80})["\']',
            body, re.IGNORECASE
        )
        if m_og:
            name = _strip_site_suffix(_clean_name(m_og.group(1)))
            if _valid_name(name):
                return name

        # 3. First <h1> on page
        m_h1 = re.search(r'<h1[^>]*>\s*([^<]{2,80})\s*</h1>', body)
        if m_h1:
            name = _clean_name(m_h1.group(1))
            if _valid_name(name):
                return name

        # 4. mbasic: <strong> inside #root
        m_profile = re.search(
            r'<div\s+id=["\']root["\'][^>]*>.*?<strong[^>]*>([^<]{2,80})</strong>',
            body, re.DOTALL
        )
        if m_profile:
            name = _clean_name(m_profile.group(1))
            if _valid_name(name):
                return name

        # 5. m.facebook.com: header profile name area
        m_header = re.search(
            r'<header[^>]*>.*?<h2[^>]*>\s*([^<]{2,80})\s*</h2>',
            body, re.DOTALL
        )
        if m_header:
            name = _clean_name(m_header.group(1))
            if _valid_name(name):
                return name

        # 6. Profile picture alt attribute (fb puts name there)
        m_alt = re.search(
            r'<img[^>]*alt=["\']([^"\'<]{2,60})["\'][^>]*>',
            body, re.IGNORECASE
        )
        if m_alt:
            name = _clean_name(m_alt.group(1))
            if _valid_name(name):
                return name

        # 7. JSON-LD structured data (ProfilePage name)
        m_json = re.search(
            r'"@type"\s*:\s*"Person"[^}]*"name"\s*:\s*"([^"]{2,80})"',
            body, re.DOTALL
        )
        if m_json:
            name = _clean_name(m_json.group(1))
            if _valid_name(name):
                return name

        return None

    strategies = [
        ("mbasic", HEADERS, [
            f"https://mbasic.facebook.com/profile.php?id={c_user}",
            f"https://mbasic.facebook.com/{c_user}",
            "https://mbasic.facebook.com/me",
        ]),
        ("mobile", _MOBILE_HEADERS, [
            f"https://m.facebook.com/profile.php?id={c_user}",
            f"https://m.facebook.com/{c_user}",
            "https://m.facebook.com/me",
        ]),
    ]

    for label, headers, urls in strategies:
        try:
            async with httpx.AsyncClient(
                headers=headers, cookies=jar,
                follow_redirects=True, timeout=25,
                verify=False
            ) as client:
                for url in urls:
                    try:
                        r = await client.get(url)
                        if r.status_code != 200:
                            continue
                        name = _extract_name(r.text)
                        if name:
                            return name
                    except Exception as e:
                        logger.debug(f"get_account_name [{label}] {url}: {e}")
                        continue
        except Exception as e:
            logger.debug(f"get_account_name [{label}] client: {e}")

    return f"حساب_{c_user}"

# ════════════════════════════════════════════════════════════
#  3. Login Check  /  فحص تسجيل الدخول
#     check_login(cookies) → True/False
#     Tries multiple mbasic endpoints for redirect detection
# ════════════════════════════════════════════════════════════

async def check_login(cookies_json: str) -> bool:
    cookies = _parse_cookies(cookies_json)
    jar = _cookies_to_httpx(cookies)
    c_user = _get_c_user(cookies)
    if not jar or not c_user:
        return False
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, cookies=jar,
            follow_redirects=True, timeout=20,
            verify=False
        ) as client:
            # Try multiple endpoints in case one redirects
            for url in [
                f"https://mbasic.facebook.com/profile.php?id={c_user}",
                "https://mbasic.facebook.com/",
                "https://m.facebook.com/",
            ]:
                try:
                    r = await client.get(url)
                    if r.status_code != 200:
                        continue
                    url_str = str(r.url)
                    if "login" in url_str.lower() or "checkpoint" in url_str.lower():
                        continue
                    body = r.text
                    if c_user in body:
                        return True
                except Exception:
                    continue
    except Exception as e:
        logger.error(f"check_login error: {e}")
    return False

# ════════════════════════════════════════════════════════════
#  4. Cookie Diagnostics  /  تشخيص الكوكيز
#     diagnose_cookies(cookies) → dict with 4-step diagnosis
#     Tests format, c_user, xs, and HTTP login
# ════════════════════════════════════════════════════════════

async def diagnose_cookies(cookies_json: str) -> dict:
    cookies = _parse_cookies(cookies_json)
    result = {
        "success": False, "account_name": None, "c_user": None,
        "summary": "", "suggestion": "", "steps": [],
        "checkpoint": False,
    }

    step1 = {"name": "تحليل تنسيق الكوكيز", "passed": False, "detail": ""}
    if cookies:
        step1["passed"] = True
        step1["detail"] = f"تم العثور على {len(cookies)} كوكيز"
    else:
        step1["detail"] = "لم يتم العثور على أي كوكيز — التنسيق غير صحيح"
        result["steps"] = [step1]
        result["summary"] = "❌ تنسيق الكوكيز غير صحيح"
        result["suggestion"] = "تأكد من نسخ الكوكيز بصيغة JSON من إضافة Cookie-Editor"
        return result
    result["steps"].append(step1)

    step2 = {"name": "معرف الحساب (c_user)", "passed": False, "detail": ""}
    c_user = _get_c_user(cookies)
    if c_user:
        step2["passed"] = True
        step2["detail"] = f"✅ {c_user}"
        result["c_user"] = c_user
    else:
        step2["detail"] = "❌ غير موجود"
        result["steps"].extend([step1, step2])
        result["summary"] = "❌ الكوكيز لا تحتوي على c_user — يلزم تسجيل دخول جديد"
        result["suggestion"] = "سجّل الدخول فيسبوك في المتصفح، ثم استخدم Cookie-Editor لنسخ الكوكيز"
        return result
    result["steps"].append(step2)

    step3 = {"name": "رمز الجلسة (xs)", "passed": False, "detail": ""}
    xs_found = any(c.get("name") == "xs" for c in cookies)
    if xs_found:
        step3["passed"] = True
        step3["detail"] = "✅ موجود"
    else:
        step3["detail"] = "⚠️ غير موجود — بعض الميزات قد لا تعمل"
    result["steps"].append(step3)

    step4 = {"name": "اختبار الدخول لخوادم فيسبوك", "passed": False, "detail": ""}
    jar = _cookies_to_httpx(cookies)
    if not jar:
        step4["detail"] = "❌ فشل تحويل الكوكيز"
        result["steps"].append(step4)
        return result

    test_pages = [
        ("mbasic الرئيسية", "https://mbasic.facebook.com/"),
        ("mbasic بروفايل", f"https://mbasic.facebook.com/profile.php?id={c_user}"),
        ("موبايل (m.facebook)", "https://m.facebook.com/"),
    ]

    try:
        async with httpx.AsyncClient(
            headers=HEADERS, cookies=jar,
            follow_redirects=True, timeout=25,
            verify=False
        ) as client:
            for label, url in test_pages:
                try:
                    r = await client.get(url)
                    url_str = str(r.url)
                    body_lower = r.text.lower()

                    if "login" in url_str.lower():
                        step4["detail"] += f"\n• {label}: ❌ تم التوجيه لتسجيل الدخول"
                        continue

                    if any(t in body_lower for t in ["checkpoint", "حسابك مقيد", "your account is restricted"]):
                        step4["detail"] += f"\n• {label}: ⚠️ الحساب في Checkpoint أمني"
                        result["checkpoint"] = True
                        continue

                    if c_user in r.text:
                        step4["passed"] = True
                        step4["detail"] += f"\n• {label}: ✅ نجاح"
                        name_m = re.search(r'<title>([^<]+)</title>', r.text, re.IGNORECASE)
                        if name_m:
                            name = _strip_site_suffix(_clean_name(name_m.group(1)))
                            if _valid_name(name):
                                result["account_name"] = name
                    else:
                        step4["detail"] += f"\n• {label}: ⚠️ تم الدخول ولكن لم يتم العثور على معرف الحساب"

                except Exception as e:
                    step4["detail"] += f"\n• {label}: ❌ خطأ: {str(e)[:60]}"
    except Exception as e:
        step4["detail"] += f"\n• ❌ فشل الاتصال بالخادم: {str(e)[:100]}"

    result["steps"].append(step4)

    if step4["passed"]:
        result["success"] = True
        name_str = f" ({result['account_name']})" if result.get("account_name") else ""
        result["summary"] = f"✅ الكوكيز صالحة!{name_str}"
        result["suggestion"] = "يمكنك سحب المجموعات والصفحات الآن"
    elif result.get("checkpoint"):
        result["summary"] = "⚠️ الحساب بحاجة إلى فتح Checkpoint"
        result["suggestion"] = "افتح فيسبوك في متصفح عادي، أكمل التحقق الأمني، ثم حدّث الكوكيز"
    else:
        result["summary"] = "❌ الكوكيز غير صالحة أو منتهية الصلاحية"
        result["suggestion"] = "سجّل الدخول فيسبوك في المتصفح، استخدم Cookie-Editor، وانسخ الكوكيز الجديدة"

    return result


# ════════════════════════════════════════════════════════════
#  5. Groups Fetcher  /  سحب المجموعات
#     fetch_groups(cookies) → list[dict]
#     Tries mbasic + m.facebook.com with multiple HTML parsers
# ════════════════════════════════════════════════════════════

_MOBILE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    "Accept-Language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://m.facebook.com/",
    "Connection": "keep-alive",
}

async def fetch_groups(cookies_json: str, max_pages: int = 5) -> list:
    cookies = _parse_cookies(cookies_json)
    jar = _cookies_to_httpx(cookies)
    if not jar:
        logger.warning("fetch_groups: no cookies to send")
        return []

    groups = []
    seen = set()

    # Strategy A: mbasic.facebook.com
    # Strategy B: m.facebook.com (mobile)
    strategies = [
        {
            "name": "mbasic",
            "headers": HEADERS,
            "base": "https://mbasic.facebook.com",
            "urls": [
                "/groups/?seemore=1",
                "/groups/",
                "/groups",
            ],
            "is_mobile": False,
        },
        {
            "name": "mobile",
            "headers": _MOBILE_HEADERS,
            "base": "https://m.facebook.com",
            "urls": [
                "/groups/",
                "/groups",
                "/groups/?seemore=1",
            ],
            "is_mobile": True,
        },
    ]

    async with httpx.AsyncClient(
        cookies=jar, follow_redirects=True, timeout=30,
        verify=False
    ) as client:
        for strategy in strategies:
            if groups:
                break
            for path in strategy["urls"]:
                if groups:
                    break
                url = strategy["base"] + path
                try:
                    r = await client.get(url, headers=strategy["headers"])
                    if r.status_code != 200:
                        logger.debug(f"fetch_groups: {url} -> {r.status_code}")
                        continue
                    body = r.text
                    url_str = str(r.url)

                    # Check for login/checkpoint redirect
                    if "login" in url_str.lower() or "checkpoint" in url_str.lower():
                        logger.debug(f"fetch_groups: redirected to login at {url}")
                        continue

                    logger.info(f"fetch_groups: trying {url} ({len(body)} bytes)")

                    if strategy["is_mobile"]:
                        _extract_groups_mobile(body, groups, seen)
                    else:
                        _extract_groups_from_html(body, groups, seen)

                    if groups:
                        logger.info(f"fetch_groups: found {len(groups)} groups via {url}")
                    else:
                        # Try next page if first page had nothing
                        next_url = _find_next_groups_url(body, strategy["base"])
                        for _ in range(max_pages - 1):
                            if not next_url:
                                break
                            try:
                                r2 = await client.get(next_url, headers=strategy["headers"])
                                if r2.status_code != 200:
                                    break
                                if strategy["is_mobile"]:
                                    _extract_groups_mobile(r2.text, groups, seen)
                                else:
                                    _extract_groups_from_html(r2.text, groups, seen)
                                next_url = _find_next_groups_url(r2.text, strategy["base"])
                                await asyncio.sleep(random.uniform(0.8, 1.8))
                            except Exception:
                                break

                except Exception as e:
                    logger.debug(f"fetch_groups error {url}: {e}")
                    continue

    if not groups:
        logger.warning("fetch_groups: no groups found with any strategy")
    else:
        logger.info(f"fetch_groups: total {len(groups)} groups")
    return groups


_SKIP_GROUP_SLUGS = {
    "feed", "discover", "joins", "create", "search", "members",
    "about", "videos", "photos", "your_groups", "suggested",
}

def _add_group(groups, seen, gid, name, url=None):
    if gid and gid not in seen and name and len(name) > 1:
        seen.add(gid)
        groups.append({
            "group_id":      gid,
            "group_name":    name[:100],
            "group_url":     url or f"https://www.facebook.com/groups/{gid}",
            "members_count": 0,
        })


def _extract_groups_from_html(html: str, groups: list, seen: set):
    """Extract groups from mbasic HTML into the groups list."""

    # Pattern 1: /groups/NUMERIC_ID — most reliable
    for m in re.finditer(
        r'href="[^"]*?/groups/(\d{6,20})[^"]*"[^>]*>([^<]{2,100})</a>',
        html
    ):
        gid, name = m.group(1), _clean_name(m.group(2))
        _add_group(groups, seen, gid, name)

    # Pattern 2: /groups/SLUG — word-based group IDs
    for m2 in re.finditer(
        r'href="[^"]*?/groups/([A-Za-z][A-Za-z0-9._\-]{3,80})[/"?][^"]*"[^>]*>([^<]{2,100})</a>',
        html
    ):
        slug, name = m2.group(1).rstrip('/'), _clean_name(m2.group(2))
        if slug.lower() not in _SKIP_GROUP_SLUGS and not slug.isdigit():
            _add_group(groups, seen, slug, name)

    # Pattern 3: JSON — "id":"GROUPID","name":"GROUP NAME"
    for m3 in re.finditer(
        r'"id"\s*:\s*"(\d{6,20})"[^}]{1,300}"name"\s*:\s*"([^"\\]{2,80})"',
        html
    ):
        gid, name = m3.group(1), _clean_name(m3.group(2))
        _add_group(groups, seen, gid, name)

    # Pattern 4: Any <a> with /groups/ number in href (catches more formats)
    for m4 in re.finditer(
        r'<a\s+href="[^"]*/groups/(\d+)[/?"][^"]*"[^>]*>\s*([^<]{2,100})\s*</a>',
        html
    ):
        gid, name = m4.group(1), _clean_name(m4.group(2))
        _add_group(groups, seen, gid, name)

    # Pattern 5: JSON-LD / structured data for groups
    for m5 in re.finditer(
        r'"group_id"\s*:\s*"(\d+)"',
        html
    ):
        gid = m5.group(1)
        if gid not in seen:
            _add_group(groups, seen, gid, f"مجموعة {gid}")


def _extract_groups_mobile(html: str, groups: list, seen: set):
    """Extract groups from m.facebook.com (mobile) HTML."""
    # Pattern 1: Mobile group links format
    for m in re.finditer(
        r'<a\s+href="(/groups/\d+/)[^"]*"[^>]*>\s*(?:<[^>]+>\s*)*([^<]{2,100})',
        html
    ):
        href = m.group(1)
        name = _clean_name(m.group(2))
        gid = href.rstrip('/').split('/')[-1]
        if gid and gid.isdigit():
            _add_group(groups, seen, gid, name)

    # Pattern 2: mbasic patterns (mobile site often uses similar structure)
    _extract_groups_from_html(html, groups, seen)

    # Pattern 3: Items in a structured list with data-* attributes
    for m in re.finditer(
        r'data-group-id=["\'](\d+)["\'][^>]*>.*?<span[^>]*>([^<]{2,100})</span>',
        html, re.DOTALL
    ):
        gid, name = m.group(1), _clean_name(m.group(2))
        _add_group(groups, seen, gid, name)

    # Pattern 4: aria-label on group links
    for m in re.finditer(
        r'href="/groups/(\d+)/[^"]*"[^>]*aria-label=["\']([^\'"]{2,100})["\']',
        html
    ):
        gid, name = m.group(1), _clean_name(m.group(2))
        _add_group(groups, seen, gid, name)


def _find_next_groups_url(html: str, base_url: str = "https://mbasic.facebook.com") -> Optional[str]:
    """Find the 'see more groups' / next page URL."""
    patterns = [
        r'href="(/groups/[^"]*seemore[^"]*)"',
        r'href="(/groups/[^"]*\?[^"]*cursor[^"]*)"',
        r'href="(/groups/[^"]*page=\d+[^"]*)"',
        r'<a[^>]*href="(/groups/[^"]*more[^"]*)"',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return base_url + m.group(1)
    return None

# ════════════════════════════════════════════════════════════
#  6. Pages Fetcher  /  سحب الصفحات
#     fetch_pages(cookies) → list[dict]
#     Tries mbasic + m.facebook.com HTML parsers
# ════════════════════════════════════════════════════════════

async def fetch_pages(cookies_json: str) -> list:
    cookies = _parse_cookies(cookies_json)
    jar = _cookies_to_httpx(cookies)
    if not jar:
        return []

    pages = []
    seen = set()

    # Strategy A: mbasic
    # Strategy B: m.facebook.com
    strategies = [
        {
            "name": "mbasic",
            "headers": HEADERS,
            "urls": [
                "https://mbasic.facebook.com/pages/?category=your_pages",
                "https://mbasic.facebook.com/pages/",
                "https://mbasic.facebook.com/bookmarks/pages/",
                "https://mbasic.facebook.com/me/pages/",
            ],
            "is_mobile": False,
        },
        {
            "name": "mobile",
            "headers": _MOBILE_HEADERS,
            "urls": [
                "https://m.facebook.com/pages/?category=your_pages",
                "https://m.facebook.com/pages/",
                "https://m.facebook.com/bookmarks/pages/",
                "https://m.facebook.com/me/pages/",
            ],
            "is_mobile": True,
        },
    ]

    async with httpx.AsyncClient(
        cookies=jar, follow_redirects=True, timeout=30,
        verify=False
    ) as client:
        for strategy in strategies:
            if pages:
                break
            html_combined = ""
            for url in strategy["urls"]:
                if pages:
                    break
                try:
                    r = await client.get(url, headers=strategy["headers"])
                    if r.status_code == 200:
                        url_str = str(r.url)
                        if "login" in url_str.lower() or "checkpoint" in url_str.lower():
                            continue
                        html_combined += r.text + "\n"
                        # Extract page by page — stop as soon as we find any
                        if strategy["is_mobile"]:
                            _extract_pages_mobile(r.text, pages, seen)
                        else:
                            _extract_pages_from_html(r.text, pages, seen)
                        await asyncio.sleep(random.uniform(0.5, 1.2))
                except Exception as e:
                    logger.debug(f"fetch_pages url={url} error: {e}")

            if not pages and html_combined:
                # Try combined extraction as fallback
                _extract_pages_from_html(html_combined, pages, seen)

    logger.info(f"Fetched {len(pages)} pages")
    return pages


def _extract_pages_from_html(html: str, pages: list, seen: set):
    """Extract managed pages from mbasic HTML."""
    skip_slugs = {
        "groups", "profile.php", "people", "pages", "home",
        "friends", "messages", "notifications", "bookmarks",
        "settings", "help", "privacy", "login", "logout",
        "recover", "checkpoint", "search", "videos", "photos",
        "stories", "events", "marketplace", "gaming",
    }

    # Pattern 1: /pages/PageName/PageID/ format
    for m in re.finditer(
        r'href="(?:https://mbasic\.facebook\.com)?/pages/([^/"]+)/(\d+)/?[^"]*"[^>]*>\s*([^<]{2,80})\s*</a>',
        html
    ):
        slug = m.group(1)
        pid = m.group(2)
        name = _clean_name(m.group(3))
        key = pid or slug
        if key and key not in seen and name and len(name) > 1:
            seen.add(key)
            pages.append({
                "page_id":      pid or slug,
                "page_name":    name[:100],
                "access_token": "",
            })

    # Pattern 2: /<slug>/?ref=... links (simple page slugs) — strict: must not be a known non-page slug
    _nav_slugs = {
        "groups", "profile.php", "people", "pages", "home", "friends",
        "messages", "notifications", "bookmarks", "settings", "help",
        "privacy", "login", "logout", "recover", "checkpoint", "search",
        "videos", "photos", "stories", "events", "marketplace", "gaming",
        "feeds", "watch", "reels", "explore", "saved", "me",
    }
    for m2 in re.finditer(
        r'href="(?:https://mbasic\.facebook\.com)?/([A-Za-z][A-Za-z0-9._]{3,50})/manage/?\?(?:ref|sk)[^"]*"[^>]*>\s*([^<]{2,80})\s*</a>',
        html
    ):
        slug = m2.group(1)
        name = _clean_name(m2.group(2))
        if slug and slug not in seen and slug.lower() not in skip_slugs and name and len(name) > 1:
            seen.add(slug)
            pages.append({
                "page_id":      slug,
                "page_name":    name[:100],
                "access_token": "",
            })

    # Pattern 3: JSON embedded page data
    for m3 in re.finditer(r'"pageID"\s*:\s*"?(\d{6,20})"?.*?"name"\s*:\s*"([^"]{2,80})"', html):
        pid = m3.group(1)
        name = _clean_name(m3.group(2))
        if pid and pid not in seen and name:
            seen.add(pid)
            pages.append({
                "page_id":      pid,
                "page_name":    name[:100],
                "access_token": "",
            })

    # Pattern 4: Generic page links with manage/admin text nearby
    for m4 in re.finditer(
        r'href="[^"]*/([A-Za-z][A-Za-z0-9._\-]{3,50})/?\?(?:sk|ref)=[^"]*"[^>]*>\s*([^<]{2,80})\s*</a>',
        html
    ):
        slug = m4.group(1)
        name = _clean_name(m4.group(2))
        if slug and slug not in seen and slug.lower() not in skip_slugs and name and len(name) > 1:
            seen.add(slug)
            pages.append({
                "page_id":      slug,
                "page_name":    name[:100],
                "access_token": "",
            })

    # Pattern 5: JSON-LD / script data with page info
    for m5 in re.finditer(
        r'"page_id"\s*:\s*"(\d+)"[^}]*?"page_name"\s*:\s*"([^"]+)"',
        html
    ):
        pid = m5.group(1)
        name = _clean_name(m5.group(2))
        if pid and pid not in seen and name:
            seen.add(pid)
            pages.append({
                "page_id":      pid,
                "page_name":    name[:100],
                "access_token": "",
            })


def _extract_pages_mobile(html: str, pages: list, seen: set):
    """Extract managed pages from m.facebook.com HTML."""
    skip_ids = {"home", "profile", "friends", "messages", "search", "settings"}

    # Pattern 1: Mobile page links with /pages/ ID
    for m in re.finditer(
        r'<a\s+href="(/pages/\d+/)[^"]*"[^>]*>\s*(?:<[^>]+>\s*)*([^<]{2,80})',
        html
    ):
        href = m.group(1)
        name = _clean_name(m.group(2))
        pid = href.rstrip('/').split('/')[-1]
        if pid and pid.isdigit() and pid not in seen and name and len(name) > 1:
            seen.add(pid)
            pages.append({
                "page_id":      pid,
                "page_name":    name[:100],
                "access_token": "",
            })

    # Pattern 2: Fallback to mbasic extraction (works on mobile too)
    _extract_pages_from_html(html, pages, seen)

    # Pattern 3: aria-label on page links
    for m in re.finditer(
        r'href="/pages/\d+/[^"]*"[^>]*aria-label=["\']([^\'"]{2,80})["\']',
        html
    ):
        name = _clean_name(m.group(1))
        # Look for numeric ID near this match
        pid_match = re.search(r'href="/pages/(\d+)/', html[max(0, m.start()-200):m.start()+200])
        if pid_match and pid_match.group(1) not in seen:
            pid = pid_match.group(1)
            seen.add(pid)
            pages.append({
                "page_id":      pid,
                "page_name":    name[:100],
                "access_token": "",
            })

# ════════════════════════════════════════════════════════════
#  7. FBAutomator Class  /  الكلاس الرئيسي للنشر
#     Wraps Playwright browser for Facebook posting.
#     Methods:
#       7a. __init__        — إعداد المتصفح والحساب
#       7b. fetch_groups    — جلب المجموعات (عبر httpx)
#       7c. fetch_pages     — جلب الصفحات (عبر httpx)
#       7d. post_to_group   — نشر منشور في مجموعة
#       7e. post_comment    — تعليق على منشور
#       7f. post_story      — نشر ستوري
#       7g. close           — إغلاق المتصفح
# ════════════════════════════════════════════════════════════

class FBAutomator:
    def __init__(self, account_id: int, cookies_json: str, proxy: str = None):
        self.account_id  = account_id
        self.cookies_json = cookies_json
        self.cookies     = _parse_cookies(cookies_json)
        self.proxy       = proxy
        self.session_dir = os.path.join(SESSIONS_DIR, str(account_id))
        os.makedirs(self.session_dir, exist_ok=True)

    async def check_login(self) -> bool:
        return await check_login(self.cookies_json)

    async def diagnose(self) -> dict:
        return await diagnose_cookies(self.cookies_json)

    # ── 7b. fetch_groups / جلب المجموعات (يدفع للدالة الرئيسية) ──
    async def fetch_groups(self) -> list:
        return await fetch_groups(self.cookies_json)

    # ── 7c. fetch_pages / جلب الصفحات (يدفع للدالة الرئيسية) ──
    async def fetch_pages(self) -> list:
        return await fetch_pages(self.cookies_json)

    # ── 7d. Browser setup / إعداد المتصفح ──
    async def _get_browser_context(self, playwright):
        launch_kw = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        if self.proxy:
            launch_kw["proxy"] = {"server": self.proxy}
        browser = await playwright.chromium.launch(**launch_kw)
        context = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 390, "height": 844},
            locale="ar-EG",
        )
        if self.cookies:
            await context.add_cookies(self.cookies)
        return browser, context

    # ── 7d. post_to_group / نشر منشور في مجموعة ──
    async def post_to_group(
        self,
        group_id: str,
        caption: str,
        media_path: str = None,
        delay_range: tuple = (40, 120),
        anti_ban_level: str = "medium",
    ) -> dict:
        min_d, max_d = delay_range
        if anti_ban_level == "high":
            min_d = int(min_d * 1.5)
            max_d = int(max_d * 2)

        await asyncio.sleep(random.uniform(min_d, max_d))

        if not PLAYWRIGHT_OK:
            logger.info(f"[MOCK POST] group={group_id} caption={caption[:40]}")
            return {"success": True, "mock": True}

        try:
            async with async_playwright() as pw:
                browser, ctx = await self._get_browser_context(pw)
                page = await ctx.new_page()
                await page.goto(f"https://m.facebook.com/groups/{group_id}", timeout=30000)
                await asyncio.sleep(random.uniform(2, 5))
                await _human_scroll(page, times=2)

                post_box = await page.query_selector(
                    "[data-testid='status-attachment-mentions-input'], [role='textbox']"
                )
                if not post_box:
                    await browser.close()
                    return {"success": False, "error": "post box not found"}

                await post_box.click()
                await asyncio.sleep(1)
                if caption:
                    await _human_type(page, post_box, caption)
                    await asyncio.sleep(random.uniform(1, 3))

                if media_path and os.path.exists(media_path):
                    file_input = await page.query_selector("input[type='file']")
                    if file_input:
                        await file_input.set_input_files(media_path)
                        await asyncio.sleep(random.uniform(3, 8))

                submit = await page.query_selector(
                    "[data-testid='react-composer-post-button'], button[type='submit']"
                )
                if submit:
                    await submit.click()
                    await asyncio.sleep(random.uniform(3, 6))

                await browser.close()
                return {"success": True}
        except Exception as e:
            logger.error(f"post_to_group error [{group_id}]: {e}")
            return {"success": False, "error": str(e)}

    # ── 7e. post_comment / تعليق على منشور ──
    async def post_comment(self, post_url: str, comment_text: str) -> tuple:
        if not PLAYWRIGHT_OK:
            logger.info(f"[MOCK COMMENT] url={post_url[:60]} text={comment_text[:40]}")
            return True, None
        try:
            async with async_playwright() as pw:
                browser, ctx = await self._get_browser_context(pw)
                page = await ctx.new_page()
                await page.goto(post_url, timeout=30000)
                await asyncio.sleep(random.uniform(3, 6))
                await _human_scroll(page, times=3)

                cmt_box = await page.query_selector(
                    "[data-testid='UFI2CommentEditableArea/root'], [aria-label='اكتب تعليقاً'], [role='textbox']"
                )
                if not cmt_box:
                    await browser.close()
                    return False, "لم يتم العثور على صندوق التعليقات"

                await cmt_box.click()
                await asyncio.sleep(random.uniform(0.5, 1.5))
                await _human_type(page, cmt_box, comment_text)
                await asyncio.sleep(random.uniform(1, 3))

                send_btn = await page.query_selector(
                    "[data-testid='UFI2CommentEditableArea/submit'], button[type='submit']"
                )
                if send_btn:
                    await send_btn.click()
                else:
                    await cmt_box.press("Enter")

                await asyncio.sleep(random.uniform(2, 5))
                await browser.close()
                return True, None
        except Exception as e:
            logger.error(f"post_comment error: {e}")
            return False, str(e)

    # ── 7f. post_story / نشر ستوري ──
    async def post_story(
        self,
        media_path: str,
        link: str = None,
    ) -> dict:
        if not PLAYWRIGHT_OK:
            logger.info(f"[MOCK STORY] media={media_path}")
            return {"success": True, "mock": True}

        try:
            async with async_playwright() as pw:
                browser, ctx = await self._get_browser_context(pw)
                page = await ctx.new_page()

                # go to m.facebook.com home and try to create story
                await page.goto("https://m.facebook.com/", timeout=30000)
                await asyncio.sleep(random.uniform(3, 6))

                # try clicking "create story" button
                story_btn = await page.query_selector(
                    "[data-sigil='create_story'], [aria-label*='story'], a[href*='story']"
                )
                if story_btn:
                    await story_btn.click()
                    await asyncio.sleep(random.uniform(2, 5))

                    # upload media
                    file_input = await page.query_selector("input[type='file']")
                    if file_input and os.path.exists(media_path):
                        await file_input.set_input_files(media_path)
                        await asyncio.sleep(random.uniform(3, 7))

                        # submit
                        submit_btn = await page.query_selector(
                            "button[type='submit'], button[data-sigil*='submit']"
                        )
                        if submit_btn:
                            await submit_btn.click()
                            await asyncio.sleep(random.uniform(3, 5))
                            await browser.close()
                            return {"success": True}
                        await browser.close()
                        return {"success": False, "error": "لم نجد زر الإرسال"}

                await browser.close()
                return {"success": False, "error": "لم نجد زر إنشاء ستوري"}
        except Exception as e:
            logger.error(f"post_story error: {e}")
            return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════════
#  8. Human-like Helpers / محاكاة السلوك البشري
#     _human_scroll(page, times) — scroll smoothly
#     _human_type(page, element, text) — type like a human
# ════════════════════════════════════════════════════════════

async def _human_scroll(page, times: int = 3):
    for _ in range(times):
        await page.evaluate(f"window.scrollBy(0, {random.randint(300, 700)})")
        await asyncio.sleep(random.uniform(0.5, 1.5))


async def _human_type(page, element, text: str):
    for char in text:
        await element.type(char, delay=random.randint(30, 120))
        if random.random() < 0.05:
            await asyncio.sleep(random.uniform(0.2, 0.8))

# ════════════════════════════════════════════════════════════
#  9. Video Downloader / تحميل الفيديو
#     download_video(url) → (path|None, error|None)
#     Uses yt-dlp for downloading, supports Facebook, YouTube, etc.
# ════════════════════════════════════════════════════════════

async def download_video(url: str):
    try:
        import yt_dlp
        import hashlib
        uid = hashlib.md5(url.encode()).hexdigest()[:8]
        out_path = os.path.join(tempfile.gettempdir(), f"video_{uid}.mp4")
        ydl_opts = {
            "outtmpl":      out_path,
            "format":       "mp4/best[ext=mp4]/best",
            "quiet":        True,
            "no_warnings":  True,
            "max_filesize": 50 * 1024 * 1024,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        if os.path.exists(out_path):
            return out_path
    except Exception as e:
        logger.error(f"download_video error: {e}")
    return None
