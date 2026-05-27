"""
fb_automator.py — Facebook automation
Groups/pages fetched via httpx (mbasic.facebook.com) — no Playwright needed.
Posting uses Playwright when available.
"""
import os
import re
import json
import html as html_module
import asyncio
import random
import logging
import httpx

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

SESSIONS_DIR = os.path.join(os.path.dirname(__file__), "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

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


async def get_account_name(cookies_json: str) -> str:
    cookies = _parse_cookies(cookies_json)
    jar = _cookies_to_httpx(cookies)
    c_user = _get_c_user(cookies)
    if not jar or not c_user:
        return f"حساب_{c_user or 'مجهول'}"

    urls_to_try = [
        f"https://mbasic.facebook.com/profile.php?id={c_user}&v=info",
        f"https://mbasic.facebook.com/{c_user}",
        "https://mbasic.facebook.com/me",
        "https://mbasic.facebook.com/home.php",
    ]

    try:
        async with httpx.AsyncClient(
            headers=HEADERS, cookies=jar,
            follow_redirects=True, timeout=25,
            verify=False
        ) as client:
            for url in urls_to_try:
                try:
                    r = await client.get(url)
                    if r.status_code != 200:
                        continue
                    body = r.text

                    # Try <title> tag first
                    m = re.search(r'<title>([^<]+)</title>', body)
                    if m:
                        name = _clean_name(m.group(1))
                        skip_words = [
                            "facebook", "log in", "sign up", "error", "login",
                            "خطأ", "تسجيل", "غير متوفر", "هذا المتصفح",
                            "not available", "not supported", "page not found",
                        ]
                        if name and not any(w in name.lower() for w in skip_words) and len(name) > 1:
                            return name

                    # Try profile header / h1 / strong tags
                    for pat in [
                        r'<h1[^>]*>([^<]{2,60})</h1>',
                        r'<strong[^>]*>([^<]{2,60})</strong>',
                        r'id="[^"]*name[^"]*"[^>]*>([^<]{2,60})<',
                        r'"name":"([^"]{2,60})"',
                    ]:
                        m2 = re.search(pat, body)
                        if m2:
                            name = _clean_name(m2.group(1))
                            if name and len(name) > 1 and "facebook" not in name.lower():
                                return name
                except Exception as e:
                    logger.debug(f"get_account_name url={url} error: {e}")
                    continue
    except Exception as e:
        logger.error(f"get_account_name error: {e}")

    return f"حساب_{c_user}"


async def check_login(cookies_json: str) -> bool:
    cookies = _parse_cookies(cookies_json)
    jar = _cookies_to_httpx(cookies)
    c_user = _get_c_user(cookies)
    if not jar or not c_user:
        return False
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, cookies=jar,
            follow_redirects=False, timeout=20,
            verify=False
        ) as client:
            r = await client.get("https://mbasic.facebook.com/")
            if r.status_code in (301, 302):
                loc = r.headers.get("location", "")
                return "login" not in loc and "checkpoint" not in loc
            if r.status_code == 200:
                body = r.text
                return c_user in body and "login" not in r.url.path
    except Exception as e:
        logger.error(f"check_login error: {e}")
    return False


async def fetch_groups(cookies_json: str, max_pages: int = 8) -> list:
    cookies = _parse_cookies(cookies_json)
    jar = _cookies_to_httpx(cookies)
    if not jar:
        return []

    groups = []
    seen = set()

    # Multiple starting URLs to maximize coverage
    start_urls = [
        "https://mbasic.facebook.com/groups/?seemore=1",
        "https://mbasic.facebook.com/groups/",
        "https://mbasic.facebook.com/groups",
    ]

    async with httpx.AsyncClient(
        headers=HEADERS, cookies=jar,
        follow_redirects=True, timeout=30,
        verify=False
    ) as client:

        # Try each starting URL, use first that returns 200
        start_url = None
        for su in start_urls:
            try:
                test_r = await client.get(su)
                if test_r.status_code == 200 and "group" in test_r.text.lower():
                    start_url = su
                    # Process this first page immediately
                    html = test_r.text
                    _extract_groups_from_html(html, groups, seen)
                    # Find next page
                    next_url = _find_next_groups_url(html)
                    break
            except Exception:
                continue

        if not start_url:
            logger.warning("fetch_groups: could not reach any group listing page")
            return groups

        # Paginate through remaining pages
        url = next_url if 'next_url' in dir() else None
        for page_num in range(max_pages - 1):
            if not url:
                break
            try:
                r = await client.get(url)
                if r.status_code != 200:
                    break
                html = r.text
                before = len(groups)
                _extract_groups_from_html(html, groups, seen)
                if len(groups) == before:
                    # No new groups found, stop
                    break
                url = _find_next_groups_url(html)
                await asyncio.sleep(random.uniform(0.8, 1.8))
            except Exception as e:
                logger.error(f"fetch_groups page error: {e}")
                break

    logger.info(f"Fetched {len(groups)} groups")
    return groups


def _extract_groups_from_html(html: str, groups: list, seen: set):
    """Extract groups from mbasic HTML into the groups list."""

    # Pattern 1: numeric group IDs
    for m in re.finditer(
        r'href="(?:https://mbasic\.facebook\.com)?(/groups/(\d{6,20})/?[^"]*)"[^>]*>([^<]{2,80})</a>',
        html
    ):
        gid = m.group(2)
        name = _clean_name(m.group(3))
        if gid and gid not in seen and name and len(name) > 1:
            seen.add(gid)
            groups.append({
                "group_id":      gid,
                "group_name":    name[:100],
                "group_url":     f"https://www.facebook.com/groups/{gid}",
                "members_count": 0,
            })

    # Pattern 2: slug-based group IDs
    for m2 in re.finditer(
        r'href="(?:https://mbasic\.facebook\.com)?(/groups/([A-Za-z][A-Za-z0-9._]{3,})/?)(?:\?[^"]*)?"\s*[^>]*>([^<]{2,80})</a>',
        html
    ):
        slug = m2.group(2)
        name = _clean_name(m2.group(3))
        skip_slugs = {"feed", "discover", "joins", "create", "search", "members", "about", "videos", "photos"}
        if slug and slug not in seen and slug not in skip_slugs and name and len(name) > 1:
            seen.add(slug)
            groups.append({
                "group_id":      slug,
                "group_name":    name[:100],
                "group_url":     f"https://www.facebook.com/groups/{slug}",
                "members_count": 0,
            })

    # Pattern 3: data-store or JSON embedded groups
    for m3 in re.finditer(r'"groupID"\s*:\s*"?(\d{6,20})"?.*?"name"\s*:\s*"([^"]{2,80})"', html):
        gid = m3.group(1)
        name = _clean_name(m3.group(2))
        if gid and gid not in seen and name:
            seen.add(gid)
            groups.append({
                "group_id":      gid,
                "group_name":    name[:100],
                "group_url":     f"https://www.facebook.com/groups/{gid}",
                "members_count": 0,
            })


def _find_next_groups_url(html: str) -> str | None:
    """Find the 'see more groups' / next page URL."""
    patterns = [
        r'href="(/groups/[^"]*seemore[^"]*)"',
        r'href="(/groups/[^"]*\?[^"]*cursor[^"]*)"',
        r'href="(/groups/[^"]*page=\d+[^"]*)"',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return "https://mbasic.facebook.com" + m.group(1)
    return None


async def fetch_pages(cookies_json: str) -> list:
    cookies = _parse_cookies(cookies_json)
    jar = _cookies_to_httpx(cookies)
    if not jar:
        return []

    pages = []
    seen = set()

    page_urls = [
        "https://mbasic.facebook.com/pages/?category=your_pages",
        "https://mbasic.facebook.com/pages/",
        "https://mbasic.facebook.com/bookmarks/pages/",
        "https://mbasic.facebook.com/me/pages/",
    ]

    async with httpx.AsyncClient(
        headers=HEADERS, cookies=jar,
        follow_redirects=True, timeout=30,
        verify=False
    ) as client:
        html_combined = ""
        for url in page_urls:
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    html_combined += r.text + "\n"
                    await asyncio.sleep(random.uniform(0.5, 1.2))
            except Exception as e:
                logger.debug(f"fetch_pages url={url} error: {e}")

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

    # Pattern 2: /<slug>/?ref=... links (simple page slugs)
    for m2 in re.finditer(
        r'href="(?:https://mbasic\.facebook\.com)?/([A-Za-z][A-Za-z0-9._]{3,50})/?\?(?:ref|sk)[^"]*"[^>]*>\s*([^<]{2,80})\s*</a>',
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

    async def fetch_groups(self) -> list:
        return await fetch_groups(self.cookies_json)

    async def fetch_pages(self) -> list:
        return await fetch_pages(self.cookies_json)

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


async def _human_scroll(page, times: int = 3):
    for _ in range(times):
        await page.evaluate(f"window.scrollBy(0, {random.randint(300, 700)})")
        await asyncio.sleep(random.uniform(0.5, 1.5))


async def _human_type(page, element, text: str):
    for char in text:
        await element.type(char, delay=random.randint(30, 120))
        if random.random() < 0.05:
            await asyncio.sleep(random.uniform(0.2, 0.8))


async def download_video(url: str):
    try:
        import yt_dlp
        uid = abs(hash(url)) % 100000
        out_path = f"/tmp/video_{uid}.mp4"
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
