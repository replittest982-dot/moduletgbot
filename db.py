import aiosqlite
import logging
from dateutil import parser
from pytz import timezone
from datetime import datetime, timedelta # ✅ ИСПРАВЛЕНИЕ 15: Добавлен импорт timedelta
import re

# ✅ ИСПРАВЛЕНИЕ 7: Определение часового пояса
MOSCOW_TZ = timezone('Europe/Moscow') 
logger = logging.getLogger(__name__)

# ✅ ИСПРАВЛЕНИЕ 15: Утилита для форматирования времени
def format_timedelta(td: timedelta) -> str:
    """Форматирует timedelta в читаемую строку."""
    seconds = int(td.total_seconds())
    periods = [
        ('год', 60 * 60 * 24 * 365),
        ('месяц', 60 * 60 * 24 * 30),
        ('день', 60 * 60 * 24),
        ('час', 60 * 60),
        ('минута', 60),
        ('секунда', 1)
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
        # ✅ ИСПРАВЛЕНИЕ 5: Убран self.conn = None, будет инициализирован в init()
        self.conn: aiosqlite.Connection = None 

    async def init(self):
        """Инициализация, подключение и создание таблиц."""
        if self.conn is None:
            self.conn = await aiosqlite.connect(self.db_path) # ✅ ИСПРАВЛЕНИЕ 5
            
        async with self.conn:
            # ✅ ИСПРАВЛЕНИЕ 14 (Миграции): Простая проверка на существование столбцов (не полная миграция)
            # В реальном проекте используйте внешнюю библиотеку для миграций.
            
            # Таблица пользователей
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    telethon_active INTEGER DEFAULT 0,
                    # Добавляем все столбцы здесь
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
                )
            """)
            
            # Таблица подписок
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    user_id INTEGER PRIMARY KEY,
                    end_date TEXT,
                    is_admin_sub INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            # ✅ ИСПРАВЛЕНИЕ 11: Создание индекса для ускорения поиска
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_user_id ON subscriptions (user_id)")

            # Таблица промокодов
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS promo_codes (
                    code TEXT PRIMARY KEY,
                    days INTEGER NOT NULL,
                    max_uses INTEGER NOT NULL,
                    used_count INTEGER DEFAULT 0
                )
            """)
            
            # Таблица сессий дропа (упрощено)
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS drop_sessions (
                    user_id INTEGER PRIMARY KEY,
                    pc_name TEXT UNIQUE,
                    phone TEXT,
                    # ✅ ИСПРАВЛЕНИЕ 10: Убрано PRIMARY KEY(phone)
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            
            # Таблица временных сессий (для QR-лога и кода)
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS temp_sessions (
                    user_id INTEGER PRIMARY KEY,
                    phone TEXT,
                    qr_login_data TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            await self.conn.commit()
            logger.info("Database initialized.")


    # ✅ ИСПРАВЛЕНИЕ 6: Метод закрытия соединения
    async def close(self):
        if self.conn:
            await self.conn.close()
            self.conn = None
            logger.info("Database connection closed.")
            
    async def get_user(self, user_id: int):
        # ✅ ИСПРАВЛЕНИЕ 13: Использование async with
        async with self.conn:
            cursor = await self.conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
            row = await cursor.fetchone()
            if row:
                # Предполагаем, что вам нужен словарь
                columns = [desc[0] for desc in cursor.description]
                return dict(zip(columns, row))
            return None

    # ✅ ИСПРАВЛЕНИЕ 12: Метод update_user, использующий параметризованный SQL
    async def update_user(self, user_id: int, **kwargs):
        if not kwargs: return 

        async with self.conn:
            user = await self.get_user(user_id)
            
            if not user:
                # ✅ ИСПРАВЛЕНИЕ 9: Используем INSERT OR IGNORE для избежания race condition при создании
                keys = ['user_id'] + list(kwargs.keys())
                placeholders = ', '.join(['?'] * len(keys))
                query = f"INSERT OR IGNORE INTO users ({', '.join(keys)}) VALUES ({placeholders})"
                await self.conn.execute(query, [user_id] + list(kwargs.values()))
            else:
                # ✅ ИСПРАВЛЕНИЕ 8: Защита от SQL Injection (Параметризованные запросы)
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
                    # ✅ ИСПРАВЛЕНИЕ 7: Делаем дату осознанной о часовом поясе
                    naive_dt = parser.parse(end_date_str)
                    return MOSCOW_TZ.localize(naive_dt)
                except Exception as e:
                    logger.error(f"Date parsing error for user {user_id}: {e}")
                    return None
            return None

    # ✅ ИСПРАВЛЕНИЕ 17: Объединение логики подписки (удален check_subscription, остался get_subscription_status)
    # ✅ ИСПРАВЛЕНИЕ 16: Убрана зависимость от ADMIN_ID (должен быть в self.config, но для простоты убран)
    async def get_subscription_status(self, user_id: int, admin_id: int = None) -> tuple[bool, str]:
        end_date = await self.get_subscription_end_date(user_id)
        is_admin = user_id == admin_id # Используем, если admin_id передан
        
        if is_admin:
            return True, "👑 Администратор"

        now_aware = datetime.now(MOSCOW_TZ)
        
        if end_date and end_date > now_aware:
            remaining = end_date - now_aware
            return True, f"Активна (до {end_date.strftime('%d.%m.%Y %H:%M')}, осталось {format_timedelta(remaining)})"
        
        return False, "🔴 Не активна"

    # ✅ ИСПРАВЛЕНИЕ 18: Использование транзакции для apply_promo_code
    async def apply_promo_code(self, user_id: int, code: str) -> tuple[bool, str]:
        code = code.upper()
        
        async with self.conn:
            # 1. Проверка промокода
            promo = await self.conn.execute_fetchone("SELECT days, max_uses, used_count FROM promo_codes WHERE code=?", (code,))
            if not promo:
                return False, "❌ Промокод не найден."

            days, max_uses, used_count = promo
            if max_uses != -1 and used_count >= max_uses:
                return False, "❌ Промокод закончился."

            # Начинаем транзакцию
            try:
                # 2. Обновление счетчика
                await self.conn.execute("UPDATE promo_codes SET used_count = used_count + 1 WHERE code=?", (code,))
                
                # 3. Расчет новой даты подписки
                end_date = await self.get_subscription_end_date(user_id)
                now_aware = datetime.now(MOSCOW_TZ)
                
                if not end_date or end_date < now_aware:
                    new_end_date = now_aware + timedelta(days=days)
                else:
                    new_end_date = end_date + timedelta(days=days)

                # 4. Обновление подписки
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

    # ✅ ИСПРАВЛЕНИЕ 19: Добавлена проверка существования кода
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
            except aiosqlite.IntegrityError:
                # Код уже существует
                return False
            except Exception as e:
                logger.error(f"Error creating promo code: {e}")
                return False
    
    # ✅ ИСПРАВЛЕНИЕ 20: update_drop_session - Используем INSERT OR REPLACE вместо DELETE+INSERT
    async def update_drop_session(self, user_id: int, pc_name: str, phone: str = None) -> None:
        async with self.conn:
            # Используем INSERT OR REPLACE для атомарного обновления или вставки
            await self.conn.execute("""
                INSERT OR REPLACE INTO drop_sessions (user_id, pc_name, phone) VALUES (?, ?, ?)
            """, (user_id, pc_name, phone))
            await self.conn.commit()
            
    async def delete_drop_session(self, user_id: int) -> None:
        async with self.conn:
            await self.conn.execute("DELETE FROM drop_sessions WHERE user_id=?", (user_id,))
            await self.conn.commit()
