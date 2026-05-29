import aiosqlite
import json
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "autopost.db")


# ════════════════════════════════════════════════════════════
#  تشفير الكوكيز (Cookie Encryption) — شفّاف ومتوافق مع القديم
#  يستخدم مفتاح COOKIE_SECRET من متغيرات البيئة.
#  لو المفتاح مش موجود أو المكتبة غير متاحة، يخزّن نص عادي (مع تحذير).
#  الكوكيز القديمة (نص عادي) تظل تعمل تلقائياً.
# ════════════════════════════════════════════════════════════
import base64 as _b64
import hashlib as _hashlib
import logging as _logging

_clog = _logging.getLogger(__name__)
_ENC_PREFIX = "enc::"

def _get_fernet():
    """يجهّز Fernet من COOKIE_SECRET لو متاح."""
    secret = os.getenv("COOKIE_SECRET")
    if not secret:
        return None
    try:
        from cryptography.fernet import Fernet
        # نشتق مفتاح 32 بايت ثابت من السر
        key = _b64.urlsafe_b64encode(_hashlib.sha256(secret.encode()).digest())
        return Fernet(key)
    except Exception:
        return None


def encrypt_cookies(plain: str) -> str:
    """يشفّر الكوكيز قبل الحفظ. يرجّع النص العادي لو التشفير غير متاح."""
    if not plain:
        return plain
    f = _get_fernet()
    if not f:
        return plain  # نص عادي (متوافق مع القديم)
    try:
        token = f.encrypt(plain.encode()).decode()
        return _ENC_PREFIX + token
    except Exception:
        return plain


def decrypt_cookies(stored: str) -> str:
    """يفكّ تشفير الكوكيز عند القراءة. يدعم النص العادي القديم تلقائياً."""
    if not stored or not stored.startswith(_ENC_PREFIX):
        return stored  # نص عادي قديم — يرجّع كما هو
    f = _get_fernet()
    if not f:
        _clog.warning("كوكيز مشفّرة لكن COOKIE_SECRET غير موجود — تعذّر فك التشفير.")
        return ""
    try:
        return f.decrypt(stored[len(_ENC_PREFIX):].encode()).decode()
    except Exception as e:
        _clog.error(f"فشل فك تشفير الكوكيز: {e}")
        return ""


async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                language TEXT DEFAULT 'ar',
                plan TEXT DEFAULT 'free',
                plan_expires TEXT,
                points INTEGER DEFAULT 0,
                referrer_id INTEGER,
                joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
                notifications INTEGER DEFAULT 1,
                time_delay INTEGER DEFAULT 60,
                anti_ban_level TEXT DEFAULT 'medium'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS fb_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                account_name TEXT,
                cookies TEXT,
                proxy TEXT,
                is_active INTEGER DEFAULT 1,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS fb_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                account_id INTEGER,
                group_id TEXT,
                group_name TEXT,
                group_url TEXT,
                can_post INTEGER DEFAULT 1,
                members_count INTEGER DEFAULT 0,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS fb_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                account_id INTEGER,
                page_id TEXT,
                page_name TEXT,
                access_token TEXT,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                template_type TEXT DEFAULT 'post',
                title TEXT,
                content TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                account_id INTEGER,
                title TEXT,
                content TEXT,
                media_path TEXT,
                media_type TEXT,
                targets TEXT,
                target_type TEXT,
                schedule_time TEXT,
                recurring TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                posts_done INTEGER DEFAULT 0,
                posts_total INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS group_lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                list_name TEXT,
                group_ids TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT,
                details TEXT,
                status TEXT DEFAULT 'success',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                label TEXT,
                duration_days INTEGER DEFAULT 30,
                max_accounts INTEGER DEFAULT 1,
                max_groups INTEGER DEFAULT 50,
                max_campaigns_per_day INTEGER DEFAULT 5,
                features TEXT DEFAULT '[]',
                price_text TEXT DEFAULT 'مجاني',
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE,
                plan TEXT,
                duration_days INTEGER DEFAULT 30,
                max_uses INTEGER DEFAULT 1,
                uses INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS page_bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                account_id INTEGER,
                page_id TEXT,
                page_name TEXT,
                post_url TEXT,
                template_name TEXT,
                keywords TEXT DEFAULT '[]',
                reply_comment TEXT DEFAULT '',
                reply_dm TEXT DEFAULT '',
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS replied_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id INTEGER,
                comment_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscription_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plan TEXT NOT NULL,
                duration_days INTEGER NOT NULL,
                amount TEXT NOT NULL,
                screenshot_file_id TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        # ── ترقيات تلقائية للأعمدة الجديدة (migrations) ──
        async def _safe_add_col(table, col, decl):
            try:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            except Exception:
                pass  # العمود موجود بالفعل
        await _safe_add_col("campaigns", "recurring", "TEXT DEFAULT ''")
        await db.commit()

        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.commit()
        await _seed_default_plans(db)
        await db.commit()


async def _seed_default_plans(db):
    existing = await (await db.execute("SELECT COUNT(*) FROM plans")).fetchone()
    if existing[0] == 0:
        plans = [
            ("free", "🆓 مجاني", 0, 1, 30, 2, '["نشر في مجموعات"]', "مجاني"),
            ("pro", "⭐ Pro", 30, 3, 200, 20, '["نشر في مجموعات","نشر في صفحات","جدولة","قوالب"]', "مدفوع"),
            ("unlimited", "👑 Unlimited", 30, 10, 1000, 999, '["جميع المميزات","أولوية الدعم","بدون قيود"]', "مدفوع"),
        ]
        for p in plans:
            await db.execute(
                """INSERT OR IGNORE INTO plans
                   (name, label, duration_days, max_accounts, max_groups, max_campaigns_per_day, features, price_text)
                   VALUES (?,?,?,?,?,?,?,?)""",
                p
            )


# ── Users ─────────────────────────────────────────────────────────────────────

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            user = dict(row)
    # ── إنهاء الاشتراك تلقائياً عند انتهاء مدته (downgrade) ──
    exp = user.get("plan_expires")
    if exp and user.get("plan", "free") != "free":
        try:
            exp_dt = datetime.strptime(exp, "%Y-%m-%d %H:%M")
            if datetime.utcnow() > exp_dt:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE users SET plan = 'free', plan_expires = NULL WHERE user_id = ?",
                        (user_id,)
                    )
                    await db.commit()
                user["plan"] = "free"
                user["plan_expires"] = None
                user["_just_expired"] = True   # علامة عشان نقدر نبلّغ المستخدم
        except Exception:
            pass
    return user


async def create_user(user_id: int, username: str, full_name: str, referrer_id: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name, referrer_id) VALUES (?,?,?,?)",
            (user_id, username or "", full_name or "", referrer_id)
        )
        await db.commit()


async def update_user(user_id: int, **kwargs):
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [user_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {cols} WHERE user_id = ?", vals)
        await db.commit()


async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users ORDER BY joined_at DESC") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        accounts = (await (await db.execute("SELECT COUNT(*) FROM fb_accounts WHERE is_active=1")).fetchone())[0]
        campaigns = (await (await db.execute("SELECT COUNT(*) FROM campaigns")).fetchone())[0]
        pro_users = (await (await db.execute("SELECT COUNT(*) FROM users WHERE plan != 'free'")).fetchone())[0]
        return {"users": users, "accounts": accounts, "campaigns": campaigns, "pro_users": pro_users}


# ── Plans ─────────────────────────────────────────────────────────────────────

async def get_plans(active_only: bool = True):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = "SELECT * FROM plans" + (" WHERE is_active=1" if active_only else "") + " ORDER BY id"
        async with db.execute(q) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_plan(plan_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM plans WHERE name = ?", (plan_name,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def add_plan(name: str, label: str, duration_days: int, max_accounts: int,
                   max_groups: int, max_campaigns: int, features: list, price_text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO plans (name, label, duration_days, max_accounts, max_groups,
               max_campaigns_per_day, features, price_text) VALUES (?,?,?,?,?,?,?,?)""",
            (name, label, duration_days, max_accounts, max_groups, max_campaigns,
             json.dumps(features, ensure_ascii=False), price_text)
        )
        await db.commit()


async def update_plan(plan_id: int, **kwargs):
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [plan_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE plans SET {cols} WHERE id = ?", vals)
        await db.commit()


async def delete_plan(plan_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE plans SET is_active = 0 WHERE id = ?", (plan_id,))
        await db.commit()


async def assign_user_plan(user_id: int, plan_name: str, days: int):
    expires = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M") if days > 0 else None
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET plan = ?, plan_expires = ? WHERE user_id = ?",
            (plan_name, expires, user_id)
        )
        await db.commit()


async def extend_user_plan(user_id: int, days: int):
    user = await get_user(user_id)
    if not user:
        return False
    current_exp = user.get("plan_expires")
    if current_exp:
        try:
            base = datetime.strptime(current_exp, "%Y-%m-%d %H:%M")
        except Exception:
            base = datetime.utcnow()
    else:
        base = datetime.utcnow()
    new_exp = (base + timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET plan_expires = ? WHERE user_id = ?", (new_exp, user_id))
        await db.commit()
    return True


# ── Promo codes ───────────────────────────────────────────────────────────────

async def create_promo_code(code: str, plan: str, duration_days: int, max_uses: int = 1):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO promo_codes (code, plan, duration_days, max_uses) VALUES (?,?,?,?)",
                (code.upper(), plan, duration_days, max_uses)
            )
            await db.commit()
            return True
        except Exception:
            return False


async def use_promo_code(code: str, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM promo_codes WHERE code = ? AND is_active = 1", (code.upper(),)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        row = dict(row)
        if row["uses"] >= row["max_uses"]:
            return None
        await db.execute(
            "UPDATE promo_codes SET uses = uses + 1 WHERE id = ?", (row["id"],)
        )
        if row["uses"] + 1 >= row["max_uses"]:
            await db.execute("UPDATE promo_codes SET is_active = 0 WHERE id = ?", (row["id"],))
        await db.commit()
        return row


# ── FB Accounts ───────────────────────────────────────────────────────────────

async def get_accounts(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM fb_accounts WHERE user_id = ? AND is_active = 1 ORDER BY added_at DESC",
            (user_id,)
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    # فك تشفير الكوكيز عند القراءة (شفّاف للمستدعي)
    for r in rows:
        if r.get("cookies"):
            r["cookies"] = decrypt_cookies(r["cookies"])
    return rows


async def add_account(user_id: int, account_name: str, cookies: str, proxy: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO fb_accounts (user_id, account_name, cookies, proxy) VALUES (?,?,?,?)",
            (user_id, account_name, encrypt_cookies(cookies), proxy)
        )
        await db.commit()
        return cur.lastrowid


async def update_account_proxy(account_id: int, user_id: int, proxy: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE fb_accounts SET proxy = ? WHERE id = ? AND user_id = ?",
            (proxy, account_id, user_id)
        )
        await db.commit()


async def update_account_name(account_id: int, user_id: int, name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE fb_accounts SET account_name = ? WHERE id = ? AND user_id = ?",
            (name, account_id, user_id)
        )
        await db.commit()


async def delete_account(account_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE fb_accounts SET is_active = 0 WHERE id = ? AND user_id = ?",
            (account_id, user_id)
        )
        await db.commit()


# ── Groups ────────────────────────────────────────────────────────────────────

async def get_groups(user_id: int, account_id: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if account_id:
            async with db.execute(
                "SELECT * FROM fb_groups WHERE user_id = ? AND account_id = ? ORDER BY group_name",
                (user_id, account_id)
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT * FROM fb_groups WHERE user_id = ? ORDER BY group_name", (user_id,)
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def add_group(user_id: int, account_id: int, group_id: str, group_name: str,
                    group_url: str = "", members_count: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO fb_groups
               (user_id, account_id, group_id, group_name, group_url, members_count)
               VALUES (?,?,?,?,?,?)""",
            (user_id, account_id, group_id, group_name, group_url, members_count)
        )
        await db.commit()


async def delete_groups(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM fb_groups WHERE user_id = ?", (user_id,))
        await db.commit()


# ── Pages ─────────────────────────────────────────────────────────────────────

async def get_pages(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM fb_pages WHERE user_id = ? ORDER BY page_name", (user_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def add_page(user_id: int, account_id: int, page_id: str, page_name: str, access_token: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO fb_pages (user_id, account_id, page_id, page_name, access_token) VALUES (?,?,?,?,?)",
            (user_id, account_id, page_id, page_name, access_token)
        )
        await db.commit()


async def delete_page(page_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM fb_pages WHERE id = ? AND user_id = ?", (page_id, user_id))
        await db.commit()


# ── Templates ─────────────────────────────────────────────────────────────────

async def get_templates(user_id: int, template_type: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if template_type:
            async with db.execute(
                "SELECT * FROM templates WHERE user_id = ? AND template_type = ? ORDER BY created_at DESC",
                (user_id, template_type)
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT * FROM templates WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def add_template(user_id: int, title: str, content: str, template_type: str = "post"):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO templates (user_id, title, content, template_type) VALUES (?,?,?,?)",
            (user_id, title, content, template_type)
        )
        await db.commit()
        return cur.lastrowid


async def delete_template(template_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM templates WHERE id = ? AND user_id = ?", (template_id, user_id))
        await db.commit()


# ── Campaigns ─────────────────────────────────────────────────────────────────

async def add_campaign(user_id: int, account_id: int, title: str, content: str,
                       targets: list, target_type: str, media_path: str = None,
                       media_type: str = None, schedule_time: str = None,
                       recurring: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO campaigns
               (user_id, account_id, title, content, targets, target_type,
                media_path, media_type, schedule_time, recurring, posts_total)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (user_id, account_id, title, content, json.dumps(targets), target_type,
             media_path, media_type, schedule_time, recurring or "", len(targets))
        )
        await db.commit()
        return cur.lastrowid


async def get_campaigns(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM campaigns WHERE user_id = ? ORDER BY created_at DESC LIMIT 20", (user_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def update_campaign_status(campaign_id: int, status: str, posts_done: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if posts_done is not None:
            await db.execute(
                "UPDATE campaigns SET status = ?, posts_done = ? WHERE id = ?",
                (status, posts_done, campaign_id)
            )
        else:
            await db.execute("UPDATE campaigns SET status = ? WHERE id = ?", (status, campaign_id))
        await db.commit()


# ── Group Lists ───────────────────────────────────────────────────────────────

async def save_group_list(user_id: int, list_name: str, group_ids: list):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO group_lists (user_id, list_name, group_ids) VALUES (?,?,?)",
            (user_id, list_name, json.dumps(group_ids))
        )
        await db.commit()
        return cur.lastrowid


async def get_group_lists(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM group_lists WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── Activity Log ──────────────────────────────────────────────────────────────

async def add_page_bot(user_id: int, account_id: int, page_id: str, page_name: str,
                       post_url: str, template_name: str, keywords: list,
                       reply_comment: str, reply_dm: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO page_bots
               (user_id, account_id, page_id, page_name, post_url, template_name,
                keywords, reply_comment, reply_dm)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (user_id, account_id, page_id, page_name, post_url, template_name,
             json.dumps(keywords, ensure_ascii=False), reply_comment, reply_dm)
        )
        await db.commit()
        return cur.lastrowid


async def get_page_bots(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM page_bots WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC",
            (user_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def is_comment_replied(bot_id: int, comment_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM replied_comments WHERE bot_id = ? AND comment_id = ?",
            (bot_id, comment_id)
        ) as cur:
            return await cur.fetchone() is not None


async def mark_comment_replied(bot_id: int, comment_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO replied_comments (bot_id, comment_id) VALUES (?,?)",
            (bot_id, comment_id)
        )
        await db.commit()


async def get_all_active_page_bots():
    """كل بوتات الصفحات النشطة (لكل المستخدمين) — يستخدمها الـ worker."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM page_bots WHERE is_active = 1"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def delete_page_bot(bot_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE page_bots SET is_active = 0 WHERE id = ? AND user_id = ?",
            (bot_id, user_id)
        )
        await db.commit()


async def get_pending_campaigns():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM campaigns WHERE status = 'pending' AND schedule_time IS NOT NULL"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── Subscription Requests ─────────────────────────────────────────────────────

async def create_subscription_request(user_id: int, plan: str, duration_days: int,
                                       amount: str, screenshot_file_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO subscription_requests
               (user_id, plan, duration_days, amount, screenshot_file_id, status)
               VALUES (?,?,?,?,?,'pending')""",
            (user_id, plan, duration_days, amount, screenshot_file_id)
        )
        await db.commit()
        return cur.lastrowid


async def get_subscription_request(request_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM subscription_requests WHERE id = ?", (request_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_subscription_request(request_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE subscription_requests SET status = ?, reviewed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, request_id)
        )
        await db.commit()


async def get_pending_subscription_requests():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM subscription_requests WHERE status = 'pending' ORDER BY created_at DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def log_activity(user_id: int, action: str, details: str, status: str = "success"):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO activity_log (user_id, action, details, status) VALUES (?,?,?,?)",
            (user_id, action, details, status)
        )
        await db.commit()


async def get_activity_log(user_id: int, limit: int = 20):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM activity_log WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ════════════════════════════════════════════════════════════
#  إضافات جديدة (نسخ احتياطي / إحصائيات / تصدير)
# ════════════════════════════════════════════════════════════

async def export_accounts(user_id: int) -> list:
    """تصدير حسابات المستخدم (مع الكوكيز مفكوكة) للنسخ الاحتياطي."""
    return await get_accounts(user_id)


async def import_account(user_id: int, account_name: str, cookies: str, proxy: str = None):
    """استيراد حساب من نسخة احتياطية."""
    return await add_account(user_id, account_name, cookies, proxy)


async def get_campaign_stats(user_id: int) -> dict:
    """إحصائيات حملات المستخدم: الإجمالي، الناجحة، الفاشلة، الجارية، نسبة النجاح."""
    camps = await get_campaigns(user_id)
    total = len(camps)
    done = sum(1 for c in camps if c.get("status") == "done")
    failed = sum(1 for c in camps if c.get("status") == "failed")
    running = sum(1 for c in camps if c.get("status") == "running")
    total_posts = sum(c.get("posts_done", 0) for c in camps)
    success_rate = round((done / total) * 100) if total else 0
    return {
        "total": total, "done": done, "failed": failed,
        "running": running, "total_posts": total_posts,
        "success_rate": success_rate,
    }


async def get_recurring_campaigns():
    """الحملات المتكررة المستحقة للتشغيل."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute(
                "SELECT * FROM campaigns WHERE recurring IS NOT NULL AND recurring != '' AND status != 'cancelled'"
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]
        except Exception:
            return []


# ════════════════════════════════════════════════════════════
#  إعدادات البوت القابلة للتعديل من لوحة الأدمن (DB key-value)
#  تغطّي على قيم config.py — تُعدّل من الواجهة بدون لمس الكود.
# ════════════════════════════════════════════════════════════
import json as _json_s

_settings_cache = None

async def _load_settings():
    global _settings_cache
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT key, value FROM bot_settings") as cur:
            rows = await cur.fetchall()
    _settings_cache = {r["key"]: r["value"] for r in rows}
    return _settings_cache


async def get_setting(key: str, default=None):
    """يقرأ إعداداً من قاعدة البيانات (مع كاش)."""
    global _settings_cache
    if _settings_cache is None:
        await _load_settings()
    raw = _settings_cache.get(key)
    if raw is None:
        return default
    # نحاول نفك JSON (للأرقام/القوائم/القواميس)
    try:
        return _json_s.loads(raw)
    except Exception:
        return raw


async def set_setting(key: str, value):
    """يحفظ إعداداً في قاعدة البيانات."""
    global _settings_cache
    raw = value if isinstance(value, str) else _json_s.dumps(value, ensure_ascii=False)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO bot_settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, raw)
        )
        await db.commit()
    if _settings_cache is None:
        _settings_cache = {}
    _settings_cache[key] = raw


async def get_all_settings() -> dict:
    if _settings_cache is None:
        await _load_settings()
    return dict(_settings_cache)


# ════════════════════════════════════════════════════════════
#  أدوات الأدمن المتقدمة
# ════════════════════════════════════════════════════════════

async def search_users(query: str, limit: int = 20):
    """بحث في المستخدمين بالـ ID أو الاسم أو اليوزر."""
    q = f"%{query}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE "
            "CAST(user_id AS TEXT) LIKE ? OR full_name LIKE ? OR username LIKE ? "
            "ORDER BY joined_at DESC LIMIT ?",
            (q, q, q, limit)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_users_by_plan(plan: str):
    """كل المستخدمين في خطة معينة (للرسائل الموجّهة)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE plan = ?", (plan,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_advanced_stats() -> dict:
    """إحصائيات متقدمة للأدمن."""
    from datetime import datetime, timedelta
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        stats = {}
        async with db.execute("SELECT COUNT(*) c FROM users") as cur:
            stats["users"] = (await cur.fetchone())["c"]
        # نشطين آخر 7 أيام (انضموا)
        week = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
        async with db.execute("SELECT COUNT(*) c FROM users WHERE joined_at >= ?", (week,)) as cur:
            stats["new_week"] = (await cur.fetchone())["c"]
        today = datetime.utcnow().strftime("%Y-%m-%d")
        async with db.execute("SELECT COUNT(*) c FROM users WHERE joined_at >= ?", (today,)) as cur:
            stats["new_today"] = (await cur.fetchone())["c"]
        # توزيع الخطط
        async with db.execute("SELECT plan, COUNT(*) c FROM users GROUP BY plan") as cur:
            stats["plans"] = {r["plan"]: r["c"] for r in await cur.fetchall()}
        async with db.execute("SELECT COUNT(*) c FROM fb_accounts WHERE is_active=1") as cur:
            stats["accounts"] = (await cur.fetchone())["c"]
        async with db.execute("SELECT COUNT(*) c FROM fb_groups") as cur:
            stats["groups"] = (await cur.fetchone())["c"]
        async with db.execute("SELECT COUNT(*) c FROM campaigns") as cur:
            stats["campaigns"] = (await cur.fetchone())["c"]
        async with db.execute("SELECT COALESCE(SUM(posts_done),0) s FROM campaigns") as cur:
            stats["total_posts"] = (await cur.fetchone())["s"]
    return stats


# ════════════════════════════════════════════════════════════
#  تنبيهات استباقية + المسودّات + الإحالات (إضافات متقدمة)
# ════════════════════════════════════════════════════════════

async def get_users_expiring_soon(days: int = 1):
    """مستخدمون اشتراكهم ينتهي خلال (days) أيام — للتنبيه الاستباقي."""
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    soon = now + timedelta(days=days)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE plan != 'free' AND plan_expires IS NOT NULL"
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    result = []
    for u in rows:
        try:
            exp = datetime.strptime(u["plan_expires"], "%Y-%m-%d %H:%M")
            if now < exp <= soon:
                result.append(u)
        except Exception:
            continue
    return result


async def mark_notified(user_id: int, kind: str):
    """تسجيل إرسال تنبيه معيّن لمنع التكرار (kind مثل expiry_2025-06-01)."""
    await set_setting(f"notified_{user_id}_{kind}", "1")


async def was_notified(user_id: int, kind: str) -> bool:
    return bool(await get_setting(f"notified_{user_id}_{kind}"))


# ── المسودّات (Drafts) ──
async def save_draft(user_id: int, data: dict):
    """يحفظ مسودّة حملة للمستخدم (واحدة لكل مستخدم)."""
    await set_setting(f"draft_{user_id}", data)


async def get_draft(user_id: int):
    return await get_setting(f"draft_{user_id}")


async def delete_draft(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM bot_settings WHERE key = ?", (f"draft_{user_id}",))
        await db.commit()
    global _settings_cache
    if _settings_cache is not None:
        _settings_cache.pop(f"draft_{user_id}", None)


# ── الإحالات المتقدمة ──
async def get_referral_stats(user_id: int) -> dict:
    """إحصائيات إحالات المستخدم: عدد المُحالين + النقاط المكتسبة."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COUNT(*) c FROM users WHERE referrer_id = ?", (user_id,)
        ) as cur:
            count = (await cur.fetchone())["c"]
        # المُحالون الذين أصبحوا مشتركين (تحويل)
        async with db.execute(
            "SELECT COUNT(*) c FROM users WHERE referrer_id = ? AND plan != 'free'",
            (user_id,)
        ) as cur:
            converted = (await cur.fetchone())["c"]
    return {"total": count, "converted": converted}


async def get_top_referrers(limit: int = 10):
    """أعلى المُحيلين (للوحة المتصدرين)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT referrer_id, COUNT(*) c FROM users "
            "WHERE referrer_id IS NOT NULL GROUP BY referrer_id ORDER BY c DESC LIMIT ?",
            (limit,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]
