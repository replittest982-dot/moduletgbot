import aiosqlite
import logging
from dateutil import parser
from pytz import timezone
from datetime import datetime, timedelta
import re

MOSCOW_TZ = timezone('Europe/Moscow') 
logger = logging.getLogger(__name__)

# --- УТИЛИТА ---
def format_timedelta(td: timedelta) -> str:
    seconds = int(td.total_seconds())
    periods = [
        ('год', 60 * 60 * 24 * 365), ('месяц', 60 * 60 * 24 * 30),
        ('день', 60 * 60 * 24), ('час', 60 * 60),
        ('минута', 60), ('секунда', 1)
    ]
    parts = []
    for period, value in periods:
        if seconds >= value:
            num = seconds // value
            seconds %= value
            parts.append(f"{num} {period}{'а' if num % 10 in [2,3,4] and num % 100 not in [12,13,14] else ''}{'ов' if num % 10 == 0 or num % 10 in [5,6,7,8,9,0] or (10 <= num % 100 <= 20) else ''}")
    return ", ".join(parts[:2]) if parts else "меньше секунды"


class AsyncDatabase:
    def __init__(self, db_path: str = "app.db"):
        self.db_path = db_path
        self.conn: aiosqlite.Connection = None 

    async def init(self):
        if self.conn is None:
            self.conn = await aiosqlite.connect(self.db_path) 
            
        async with self.conn:
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    telethon_active INTEGER DEFAULT 0,
                    session_str TEXT,  # ✅ ИСПРАВЛЕНИЕ 3: Добавлен столбец
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
                )
            """)
            
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    user_id INTEGER PRIMARY KEY, end_date TEXT, is_admin_sub INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_user_id ON subscriptions (user_id)")

            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS promo_codes (
                    code TEXT PRIMARY KEY, days INTEGER NOT NULL, max_uses INTEGER NOT NULL, used_count INTEGER DEFAULT 0
                )
            """)
            
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS drop_sessions (
                    user_id INTEGER PRIMARY KEY, pc_name TEXT UNIQUE, phone TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS temp_sessions (
                    user_id INTEGER PRIMARY KEY, phone TEXT, qr_login_data TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            await self.conn.commit()

    # ✅ ИСПРАВЛЕНИЕ 6: Метод закрытия
    async def close(self):
        if self.conn:
            await self.conn.close()
            self.conn = None
            
    async def get_user(self, user_id: int):
        async with self.conn:
            cursor = await self.conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
            row = await cursor.fetchone()
            if row:
                columns = [desc[0] for desc in cursor.description]
                return dict(zip(columns, row))
            return None

    # ✅ ИСПРАВЛЕНИЕ 8: Защита от SQL Injection
    async def update_user(self, user_id: int, **kwargs):
        if not kwargs: return 
        async with self.conn:
            user = await self.get_user(user_id)
            
            if not user:
                keys = ['user_id'] + list(kwargs.keys())
                placeholders = ', '.join(['?'] * len(keys))
                query = f"INSERT OR IGNORE INTO users ({', '.join(keys)}) VALUES ({placeholders})"
                await self.conn.execute(query, [user_id] + list(kwargs.values()))
            else:
                set_clause = ', '.join([f"{k}=?" for k in kwargs.keys()]) 
                query = f"UPDATE users SET {set_clause} WHERE user_id=?"
                await self.conn.execute(query, [*kwargs.values(), user_id])
            
            await self.conn.commit()

    async def get_subscription_end_date(self, user_id: int):
        async with self.conn:
            row = await self.conn.execute_fetchone("SELECT end_date FROM subscriptions WHERE user_id=?", (user_id,))
            if row and row[0]:
                end_date_str = row[0]
                try:
                    # ✅ ИСПРАВЛЕНИЕ 7: Обработка часового пояса
                    naive_dt = parser.parse(end_date_str)
                    return MOSCOW_TZ.localize(naive_dt)
                except Exception as e:
                    logger.error(f"Date parsing error for user {user_id}: {e}")
                    return None
            return None

    async def get_subscription_status(self, user_id: int, admin_id: int = None) -> tuple[bool, str]:
        end_date = await self.get_subscription_end_date(user_id)
        is_admin = user_id == admin_id
        
        if is_admin: return True, "👑 Администратор"

        now_aware = datetime.now(MOSCOW_TZ)
        if end_date and end_date > now_aware:
            remaining = end_date - now_aware
            return True, f"Активна (до {end_date.strftime('%d.%m.%Y %H:%M')}, осталось {format_timedelta(remaining)})"
        
        return False, "🔴 Не активна"

    # ✅ ИСПРАВЛЕНИЕ 18: Использование транзакции
    async def apply_promo_code(self, user_id: int, code: str) -> tuple[bool, str]:
        code = code.upper()
        async with self.conn:
            promo = await self.conn.execute_fetchone("SELECT days, max_uses, used_count FROM promo_codes WHERE code=?", (code,))
            if not promo: return False, "❌ Промокод не найден."

            days, max_uses, used_count = promo
            if max_uses != -1 and used_count >= max_uses: return False, "❌ Промокод закончился."

            try:
                await self.conn.execute("UPDATE promo_codes SET used_count = used_count + 1 WHERE code=?", (code,))
                end_date = await self.get_subscription_end_date(user_id)
                now_aware = datetime.now(MOSCOW_TZ)
                
                new_end_date = (end_date if end_date and end_date > now_aware else now_aware) + timedelta(days=days)

                await self.conn.execute(
                    "INSERT OR REPLACE INTO subscriptions (user_id, end_date) VALUES (?, ?)", 
                    (user_id, new_end_date.strftime('%Y-%m-%d %H:%M:%S'))
                )
                
                await self.conn.commit()
                return True, f"✅ Промокод **{code}** активирован! Подписка продлена до **{new_end_date.strftime('%d.%m.%Y %H:%M')}**."

            except Exception as e:
                await self.conn.rollback()
                logger.error(f"Transaction failed for promo {code}: {e}")
                return False, "❌ Ошибка при активации промокода (rollback)."

    async def create_promo_code(self, code: str, days: int, max_uses: int) -> bool:
        code = code.upper()
        async with self.conn:
            try:
                await self.conn.execute(
                    "INSERT INTO promo_codes (code, days, max_uses) VALUES (?, ?, ?)",
                    (code, days, max_uses)
                )
                await self.conn.commit()
                return True
            except aiosqlite.IntegrityError: return False
            except Exception as e:
                logger.error(f"Error creating promo code: {e}")
                return False
    
    # ✅ ИСПРАВЛЕНИЕ 20: INSERT OR REPLACE
    async def update_drop_session(self, user_id: int, pc_name: str, phone: str = None) -> None:
        async with self.conn:
            await self.conn.execute("""
                INSERT OR REPLACE INTO drop_sessions (user_id, pc_name, phone) VALUES (?, ?, ?)
            """, (user_id, pc_name, phone))
            await self.conn.commit()
            
    async def delete_drop_session(self, user_id: int) -> None:
        async with self.conn:
            await self.conn.execute("DELETE FROM drop_sessions WHERE user_id=?", (user_id,))
            await self.conn.commit()
