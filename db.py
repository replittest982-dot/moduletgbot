import aiosqlite
import pytz
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List

# --- КОНФИГУРАЦИЯ ---
from config import TIMEZONE_MSK

logger = logging.getLogger(__name__)

class AsyncDatabase:
    def __init__(self, db_path):
        self.db_path = db_path
        self.TIMEZONE_MSK = TIMEZONE_MSK

    def get_current_time_msk(self) -> datetime:
        return datetime.now(self.TIMEZONE_MSK)

    def to_msk_aware(self, dt_str: str) -> datetime:
        if not dt_str: return datetime.fromtimestamp(0, self.TIMEZONE_MSK) 
        try:
            naive_dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            return self.TIMEZONE_MSK.localize(naive_dt)
        except ValueError:
            return datetime.fromtimestamp(0, self.TIMEZONE_MSK)
        
    def _calculate_new_end_date(self, current_end_date_str: Optional[str], days_to_add: int) -> str:
        now = self.get_current_time_msk()
        start_date = now
        
        if current_end_date_str:
            try:
                current_end = self.to_msk_aware(current_end_date_str)
                if current_end > now:
                    start_date = current_end
            except:
                pass 

        new_end_date = start_date + timedelta(days=days_to_add)
        return new_end_date.strftime('%Y-%m-%d %H:%M:%S')

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA foreign_keys=ON;")
            await db.execute("""CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    subscription_active BOOLEAN DEFAULT 0,
                    subscription_end_date TEXT,
                    telethon_active BOOLEAN DEFAULT 0
            )""")
            await db.execute("""CREATE TABLE IF NOT EXISTS promo_codes (
                    code TEXT PRIMARY KEY,
                    days INTEGER NOT NULL,
                    uses_left INTEGER NOT NULL,
                    created_at TEXT NOT NULL
            )""") 
            await db.commit()
        # logger.info("Database initialized successfully.")

    async def get_user(self, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
            await db.commit()
            async with db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def check_subscription(self, user_id):
        from config import ADMIN_ID 
        if user_id == ADMIN_ID: return True
        
        user = await self.get_user(user_id)
        if not user or not user.get('subscription_active') or not user.get('subscription_end_date'): 
            return False

        try:
            end = self.to_msk_aware(user['subscription_end_date'])
            now = self.get_current_time_msk()
            
            if end > now:
                return True
            else:
                # Автоматическое отключение сессии и подписки
                from telethon_manager import tm 
                if tm:
                    await tm.stop_worker(user_id, silent=True)
                await self.set_telethon_status(user_id, False)
                await self.set_subscription_status(user_id, False, None)
                return False
        except Exception:
            return False

    async def set_telethon_status(self, user_id, status):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET telethon_active=? WHERE user_id=?", (1 if status else 0, user_id))
            await db.commit()
            
    async def set_subscription_status(self, user_id, status, end_date_str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET subscription_active=?, subscription_end_date=? WHERE user_id=?", (1 if status else 0, end_date_str, user_id))
            await db.commit()

    async def activate_promo_code(self, user_id: int, code: str) -> Optional[int]:
        promo = await self.get_promo_code(code)
        if not promo or (promo['uses_left'] is not None and promo['uses_left'] == 0):
            return None

        user = await self.get_user(user_id)
        days = promo['days']
        new_end_date_str = self._calculate_new_end_date(user.get('subscription_end_date'), days)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET subscription_active=1, subscription_end_date=? WHERE user_id=?", (new_end_date_str, user_id))
            if promo['uses_left'] != -1: 
                 await db.execute("UPDATE promo_codes SET uses_left = uses_left - 1 WHERE code=?", (code.upper(),))
            await db.commit()
        return days 

    async def get_promo_code(self, code: str):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM promo_codes WHERE code=?", (code.upper(),)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_active_telethon_users(self):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT user_id FROM users WHERE telethon_active=1") as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]
                
    async def create_promo_code(self, code: str, days: int, uses: int):
        async with aiosqlite.connect(self.db_path) as db:
            now_str = self.get_current_time_msk().strftime('%Y-%m-%d %H:%M:%S')
            uses_value = uses if uses != 0 else -1 
            try:
                await db.execute("INSERT INTO promo_codes (code, days, uses_left, created_at) VALUES (?, ?, ?, ?)", (code.upper(), days, uses_value, now_str))
                await db.commit()
                return True
            except aiosqlite.IntegrityError:
                return False 

    async def get_all_promo_codes(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM promo_codes ORDER BY created_at DESC") as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def delete_promo_code(self, code: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM promo_codes WHERE code=?", (code.upper(),))
            await db.commit()
            return db.total_changes > 0

    async def get_all_users_count(self):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(user_id) FROM users") as cursor:
                return (await cursor.fetchone())[0]

    async def get_active_subs_count(self):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(user_id) FROM users WHERE subscription_active=1") as cursor:
                return (await cursor.fetchone())[0]
