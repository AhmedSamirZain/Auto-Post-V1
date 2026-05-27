"""
fb_automator.py — Facebook automation
Groups/pages fetched via httpx (mbasic.facebook.com) — no Playwright needed.
Posting uses Playwright when available.
"""
import os
import re
import json
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
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 12; SM-G998B) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "ar,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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


async def get_account_name(cookies_json: str) -> str:
    cookies = _parse_cookies(cookies_json)
    jar = _cookies_to_httpx(cookies)
    c_user = _get_c_user(cookies)
    if not jar or not c_user:
        return f"حساب_{c_user or 'مجهول'}"
    try:
        async with httpx.AsyncClient(headers=HEADERS, cookies=jar, follow_redirects=True, timeout=20) as client:
            r = await client.get(f"https://mbasic.facebook.com/profile.php?id={c_user}")
            if r.status_code == 200:
                m = re.search(r'<title>([^<]+)</title>', r.text)
                if m:
                    name = m.group(1).strip()
                    if name and "facebook" not in name.lower() and "log" not in name.lower():
                        return name
            r2 = await client.get("https://mbasic.facebook.com/me")
            if r2.status_code == 200:
                m2 = re.search(r'<title>([^<]+)</title>', r2.text)
                if m2:
                    name = m2.group(1).strip()
                    if name and "facebook" not in name.lower():
                        return name
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
        async with httpx.AsyncClient(headers=HEADERS, cookies=jar, follow_redirects=False, timeout=15) as client:
            r = await client.get("https://mbasic.facebook.com/me")
            if r.status_code in (301, 302):
                loc = r.headers.get("location", "")
                return "login" not in loc and "checkpoint" not in loc
            if r.status_code == 200:
                body = r.text
                return "login" not in r.url.path and c_user in body
    except Exception as e:
        logger.error(f"check_login error: {e}")
    return False


async def fetch_groups(cookies_json: str, max_pages: int = 5) -> list:
    cookies = _parse_cookies(cookies_json)
    jar = _cookies_to_httpx(cookies)
    if not jar:
        return []

    groups = []
    seen = set()

    async with httpx.AsyncClient(headers=HEADERS, cookies=jar, follow_redirects=True, timeout=30) as client:
        url = "https://mbasic.facebook.com/groups/?seemore=1"
        for _ in range(max_pages):
            if not url:
                break
            try:
                r = await client.get(url)
                if r.status_code != 200:
                    break
                html = r.text

                for m in re.finditer(
                    r'href="(/groups/(\d+)[^"]*)"[^>]*>([^<]{2,80})</a>', html
                ):
                    gid = m.group(2)
                    name = m.group(3).strip()
                    if gid and gid not in seen and name:
                        seen.add(gid)
                        groups.append({
                            "group_id":      gid,
                            "group_name":    name[:100],
                            "group_url":     f"https://www.facebook.com/groups/{gid}",
                            "members_count": 0,
                        })

                for m2 in re.finditer(
                    r'href="(/groups/([^/"?]+)/)[^"]*"[^>]*>([^<]{2,80})</a>', html
                ):
                    slug = m2.group(2)
                    name = m2.group(3).strip()
                    if slug and slug not in seen and slug not in ("feed","discover","joins","") and not slug.isdigit() and name:
                        seen.add(slug)
                        groups.append({
                            "group_id":      slug,
                            "group_name":    name[:100],
                            "group_url":     f"https://www.facebook.com/groups/{slug}",
                            "members_count": 0,
                        })

                next_m = re.search(r'href="(/groups/[^"]*seemore[^"]*)"', html)
                if next_m:
                    url = "https://mbasic.facebook.com" + next_m.group(1)
                else:
                    break
                await asyncio.sleep(random.uniform(1, 2))
            except Exception as e:
                logger.error(f"fetch_groups page error: {e}")
                break

    logger.info(f"Fetched {len(groups)} groups")
    return groups


async def fetch_pages(cookies_json: str) -> list:
    cookies = _parse_cookies(cookies_json)
    jar = _cookies_to_httpx(cookies)
    if not jar:
        return []

    pages = []
    seen = set()

    async with httpx.AsyncClient(headers=HEADERS, cookies=jar, follow_redirects=True, timeout=30) as client:
        try:
            r = await client.get("https://mbasic.facebook.com/pages/?category=your_pages")
            if r.status_code != 200:
                r = await client.get("https://mbasic.facebook.com/bookmarks/pages/")
            html = r.text

            for m in re.finditer(
                r'href="(/([^/"?]+)/\?ref[^"]*)"[^>]*>\s*([^<]{2,60})\s*</a>', html
            ):
                slug = m.group(2)
                name = m.group(3).strip()
                if slug and slug not in seen and name and len(slug) > 3:
                    seen.add(slug)
                    pages.append({
                        "page_id":      slug,
                        "page_name":    name[:100],
                        "access_token": "",
                    })

            for m2 in re.finditer(
                r'href="/pages/([^/"?]+)[^"]*"[^>]*>\s*([^<]{2,60})\s*</a>', html
            ):
                pid = m2.group(1)
                name = m2.group(2).strip()
                if pid and pid not in seen and name:
                    seen.add(pid)
                    pages.append({
                        "page_id":      pid,
                        "page_name":    name[:100],
                        "access_token": "",
                    })
        except Exception as e:
            logger.error(f"fetch_pages error: {e}")

    logger.info(f"Fetched {len(pages)} pages")
    return pages


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
                    from playwright.async_api import Keys
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
