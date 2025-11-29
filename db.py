import aiosqlite
import logging
from dateutil import parser
from pytz import timezone
from datetime import datetime, timedelta
import re
from typing import Optional, Tuple, Dict, Any

MOSCOW_TZ = timezone('Europe/Moscow') 
logger = logging.getLogger(__name__)

def format_timedelta(td: timedelta) -> str:
    """Форматирует объект timedelta в читаемую строку (например, '1 день, 5 часов')."""
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
            # Простая логика склонения: не идеальна, но достаточна для двух первых частей
            if num % 10 == 1 and num % 100 != 11:
                parts.append(f"{num} {period}")
            elif 2 <= num % 10 <= 4 and (num % 100 < 10 or num % 100 >= 20):
                parts.append(f"{num} {period}а")
            else:
                parts.append(f"{num} {period}ов")

    # Возвращаем максимум две самые значимые части
    return ", ".join(parts[:2]) if parts else "меньше минуты"

class AsyncDatabase:
    """Асинхронный класс для работы с базой данных SQLite."""
    def __init__(self, db_path: str = "app.db"):
        self.db_path = db_path
        self.conn: aiosqlite.Connection = None 

    async def init(self):
        """Инициализирует подключение к БД и создает таблицы, если они не существуют."""
        if self.conn is None:
            # 1. Создаем соединение
            self.conn = await aiosqlite.connect(self.db_path) 
            self.conn.row_factory = aiosqlite.Row 
            
        # ❌ УДАЛЕНО: async with self.conn:
        # ✅ ИСПОЛЬЗУЕМ ПРЯМОЕ ВЫПОЛНЕНИЕ КОМАНД:
        
        # 1. Таблица Users
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                telethon_active INTEGER DEFAULT 0, 
                session_str TEXT, 
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
            )
        """)
        # 2. Таблица Subscriptions
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER PRIMARY KEY, 
                end_date TEXT, 
                is_admin_sub INTEGER DEFAULT 0, 
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        """)
        await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_user_id ON subscriptions (user_id)")
        
        # 3. Таблица Promo Codes
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY, 
                days INTEGER NOT NULL, 
                max_uses INTEGER NOT NULL, 
                used_count INTEGER DEFAULT 0
            )
        """)
        
        # 4. Таблица Drop Sessions
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS drop_sessions (
                user_id INTEGER PRIMARY KEY, 
                pc_name TEXT UNIQUE, 
                phone TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        """)
        
        # 5. Таблица Temp Sessions
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS temp_sessions (
                user_id INTEGER PRIMARY KEY, 
                phone TEXT, 
                qr_login_data TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        """)
        await self.conn.commit()


    async def close(self):
        """Закрывает подключение к БД."""
        if self.conn:
            await self.conn.close()
            self.conn = None

    # --- USER METHODS ---

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получает данные пользователя."""
        async with self.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            # Если пользователь не найден, создаем его
            await self.add_user(user_id)
            return await self.get_user(user_id)

    async def add_user(self, user_id: int):
        """Добавляет нового пользователя, если он не существует."""
        try:
            await self.conn.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            await self.conn.commit()
        except aiosqlite.IntegrityError:
            pass
        except Exception as e:
            logger.error(f"Error adding user {user_id}: {e}")

    async def update_user(self, user_id: int, **kwargs):
        """Обновляет поля пользователя (telethon_active, session_str)."""
        if not kwargs:
            return
        
        set_clause = ", ".join([f"{key} = ?" for key in kwargs])
        values = list(kwargs.values())
        values.append(user_id)

        try:
            await self.conn.execute(f"UPDATE users SET {set_clause} WHERE user_id = ?", values)
            await self.conn.commit()
        except Exception as e:
            logger.error(f"Error updating user {user_id}: {e}")

    # --- SUBSCRIPTION METHODS ---

    async def get_subscription_status(self, user_id: int, admin_id: int) -> Tuple[bool, str]:
        """
        Проверяет статус подписки пользователя.
        Возвращает (is_subscribed: bool, status_text: str).
        """
        if user_id == admin_id:
            return True, "✅ Активна (Админ)"

        now_utc = datetime.now(timezone('UTC'))

        async with self.conn.execute("SELECT end_date FROM subscriptions WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()

        if row and row['end_date']:
            try:
                end_date = parser.parse(row['end_date']).astimezone(timezone('UTC'))
            except Exception:
                return False, "❌ Не активна (ошибка даты)"

            if end_date > now_utc:
                remaining = end_date - now_utc
                return True, f"🟢 Активна. Осталось: **{format_timedelta(remaining)}**"
            else:
                return False, "❌ Не активна (истекла)"
        
        return False, "❌ Не активна (нет подписки)"

    async def add_subscription(self, user_id: int, days: int, is_admin_sub: bool = False):
        """Добавляет или продлевает подписку."""
        now_utc = datetime.now(timezone('UTC'))

        async with self.conn.execute("SELECT end_date FROM subscriptions WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()

        current_end_date = now_utc
        if row and row['end_date']:
            try:
                existing_date = parser.parse(row['end_date']).astimezone(timezone('UTC'))
                if existing_date > now_utc:
                    current_end_date = existing_date
            except Exception:
                pass 

        new_end_date = current_end_date + timedelta(days=days)
        new_end_date_str = new_end_date.strftime('%Y-%m-%d %H:%M:%S')
        admin_sub_val = 1 if is_admin_sub else 0

        await self.conn.execute(
            """INSERT INTO subscriptions (user_id, end_date, is_admin_sub) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET end_date = excluded.end_date, is_admin_sub = excluded.is_admin_sub""",
            (user_id, new_end_date_str, admin_sub_val)
        )
        await self.conn.commit()

    # --- PROMO CODE METHODS ---

    async def create_promo_code(self, code: str, days: int, max_uses: int) -> bool:
        """Создает новый промокод."""
        try:
            await self.conn.execute(
                "INSERT INTO promo_codes (code, days, max_uses) VALUES (?, ?, ?)",
                (code.upper(), days, max_uses)
            )
            await self.conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False
        except Exception as e:
            logger.error(f"Error creating promo code {code}: {e}")
            return False

    async def apply_promo_code(self, user_id: int, code: str) -> Tuple[bool, str]:
        """Применяет промокод к пользователю."""
        code = code.upper()

        async with self.conn:
            async with self.conn.execute("SELECT days, max_uses, used_count FROM promo_codes WHERE code = ?", (code,)) as cursor:
                promo = await cursor.fetchone()
            
            if not promo:
                return False, "❌ Промокод не найден."
            
            days = promo['days']
            max_uses = promo['max_uses']
            used_count = promo['used_count']
            
            if max_uses != 0 and used_count >= max_uses:
                return False, "❌ Промокод истек (достигнут лимит использований)."

            try:
                await self.conn.execute(
                    "UPDATE promo_codes SET used_count = used_count + 1 WHERE code = ?", 
                    (code,)
                )
                await self.add_subscription(user_id, days, is_admin_sub=False)
                await self.conn.commit()

                return True, f"✅ Подписка продлена на **{days}** дней! Наслаждайтесь."

            except Exception as e:
                logger.error(f"Error applying promo code {code} to user {user_id}: {e}")
                return False, "❌ Ошибка при активации промокода."

    # --- DROP SESSION METHODS (PLACEHOLDERS) ---

    async def update_drop_session(self, user_id: int, pc_name: str, phone: Optional[str] = None) -> bool:
        """Обновляет или добавляет информацию о drop-сессии."""
        try:
            if phone:
                await self.conn.execute(
                    """INSERT INTO drop_sessions (user_id, pc_name, phone) VALUES (?, ?, ?)
                       ON CONFLICT(user_id) DO UPDATE SET pc_name = excluded.pc_name, phone = excluded.phone""",
                    (user_id, pc_name, phone)
                )
            else:
                 await self.conn.execute(
                    """INSERT INTO drop_sessions (user_id, pc_name) VALUES (?, ?)
                       ON CONFLICT(user_id) DO UPDATE SET pc_name = excluded.pc_name""",
                    (user_id, pc_name)
                )
            await self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error updating drop session for {user_id}: {e}")
            return False
            
    # --- TEMP SESSIONS METHODS (PLACEHOLDERS) ---

    async def set_temp_session(self, user_id: int, phone: Optional[str] = None, qr_data: Optional[str] = None) -> bool:
        """Сохраняет временные данные авторизации (для QR/SMS логина)."""
        set_clause = []
        values = []
        if phone is not None:
            set_clause.append("phone = ?")
            values.append(phone)
        if qr_data is not None:
            set_clause.append("qr_login_data = ?")
            values.append(qr_data)

        if not set_clause:
            return True

        values.append(user_id)
        
        try:
            await self.conn.execute(
                f"""INSERT INTO temp_sessions (user_id, {', '.join([c.split(' ')[0] for c in set_clause])}) VALUES ({'?, ' * len(set_clause)}?)
                   ON CONFLICT(user_id) DO UPDATE SET {', '.join(set_clause)}""",
                tuple(values)
            )
            await self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error setting temp session for {user_id}: {e}")
            return False

    async def get_temp_session(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получает временные данные авторизации."""
        async with self.conn.execute("SELECT * FROM temp_sessions WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def delete_temp_session(self, user_id: int):
        """Удаляет временные данные авторизации."""
        await self.conn.execute("DELETE FROM temp_sessions WHERE user_id = ?", (user_id,))
        await self.conn.commit()
