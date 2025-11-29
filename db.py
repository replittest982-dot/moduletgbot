import aiosqlite
import pytz
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple, Any

# Импортируем TIMEZONE_MSK и ADMIN_ID из локального config.py
from config import TIMEZONE_MSK, ADMIN_ID

logger = logging.getLogger(__name__)

class AsyncDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.TIMEZONE_MSK = TIMEZONE_MSK

    def get_current_time_msk(self) -> datetime:
        """Возвращает текущее время с учетом часового пояса MSK."""
        return datetime.now(self.TIMEZONE_MSK)

    def to_msk_aware(self, dt_str: Optional[str]) -> datetime:
        """Конвертирует строку времени из БД в объект datetime с учетом MSK."""
        if not dt_str: 
            return datetime.fromtimestamp(0, self.TIMEZONE_MSK)
        try:
            naive_dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            return self.TIMEZONE_MSK.localize(naive_dt)
        except ValueError:
            logger.warning(f"Failed to parse datetime string: {dt_str}")
            return datetime.fromtimestamp(0, self.TIMEZONE_MSK)
        
    def _calculate_new_end_date(self, current_end_date_str: Optional[str], days_to_add: int) -> str:
        """Рассчитывает новую дату окончания подписки."""
        now = self.get_current_time_msk()
        start_date = now
        
        if current_end_date_str:
            current_end = self.to_msk_aware(current_end_date_str)
            if current_end > now:
                start_date = current_end
        
        new_end_date = start_date + timedelta(days=days_to_add)
        return new_end_date.strftime('%Y-%m-%d %H:%M:%S')

    async def init(self):
        """Инициализация базы данных и создание всех таблиц."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA foreign_keys=ON;")
            
            # --- 1. USERS TABLE ---
            await db.execute("""CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    subscription_active BOOLEAN DEFAULT 0,
                    subscription_end_date TEXT,
                    telethon_active BOOLEAN DEFAULT 0
            )""")
            
            # --- 2. PROMO CODES TABLE (Добавлено is_active) ---
            await db.execute("""CREATE TABLE IF NOT EXISTS promo_codes (
                    code TEXT PRIMARY KEY,
                    days INTEGER NOT NULL,
                    max_uses INTEGER NOT NULL,
                    current_uses INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TEXT NOT NULL
            )""") 
            
            # --- 3. DROP SESSIONS TABLE ---
            await db.execute("""CREATE TABLE IF NOT EXISTS drop_sessions (
                    drop_id INTEGER PRIMARY KEY,
                    pc_name TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    thread_id INTEGER,
                    phone TEXT,
                    status TEXT DEFAULT 'start', 
                    start_time TEXT NOT NULL,
                    last_status_time TEXT NOT NULL,
                    prosto_seconds INTEGER DEFAULT 0
            )""")
            await db.commit()

    async def get_user(self, user_id):
        """Получает пользователя по ID, создает запись, если не существует."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
            await db.commit()
            async with db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
                
    async def get_active_telethon_users(self) -> List[int]:
        """Возвращает список user_id, у которых telethon_active = 1."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT user_id FROM users WHERE telethon_active=1") as cursor:
                return [row[0] for row in await cursor.fetchall()]

    async def set_telethon_status(self, user_id: int, status: bool):
        """Устанавливает статус активности воркера Telethon (0 или 1)."""
        status_int = 1 if status else 0
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET telethon_active=? WHERE user_id=?", (status_int, user_id))
            await db.commit()
            
    # ... (другие методы подписки, промокодов, и drop-системы)
