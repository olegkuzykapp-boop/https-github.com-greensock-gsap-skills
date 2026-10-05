import aiosqlite

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT,
    full_name  TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS purchases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    course_id  TEXT NOT NULL,
    amount     INTEGER NOT NULL,
    currency   TEXT NOT NULL,
    charge_id  TEXT UNIQUE NOT NULL,
    refunded   INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


async def init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def upsert_user(user_id: int, username: str | None, full_name: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (user_id, username, full_name) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name",
            (user_id, username, full_name),
        )
        await db.commit()


async def add_purchase(user_id: int, course_id: str, amount: int, currency: str, charge_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO purchases (user_id, course_id, amount, currency, charge_id) VALUES (?, ?, ?, ?, ?)",
            (user_id, course_id, amount, currency, charge_id),
        )
        await db.commit()


async def has_course(user_id: int, course_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM purchases WHERE user_id=? AND course_id=? AND refunded=0", (user_id, course_id)
        )
        return await cur.fetchone() is not None


async def user_courses(user_id: int) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT DISTINCT course_id FROM purchases WHERE user_id=? AND refunded=0", (user_id,)
        )
        return [row[0] for row in await cur.fetchall()]


async def get_purchase(charge_id: str) -> tuple[int, str, int] | None:
    """Повертає (user_id, course_id, refunded) за charge_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, course_id, refunded FROM purchases WHERE charge_id=?", (charge_id,)
        )
        return await cur.fetchone()


async def mark_refunded(charge_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE purchases SET refunded=1 WHERE charge_id=?", (charge_id,))
        await db.commit()


async def stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        cur = await db.execute(
            "SELECT course_id, COUNT(*), SUM(amount) FROM purchases WHERE refunded=0 GROUP BY course_id"
        )
        per_course = await cur.fetchall()
    return {"users": users, "per_course": per_course}


async def all_user_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        return [row[0] for row in await cur.fetchall()]
