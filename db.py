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
            return datetime.fromtimestamp(0, self.TIMEZONE_MSK) # Минимальная дата
        try:
            # Парсинг без учета TZ, затем локализация как MSK
            naive_dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            return self.TIMEZONE_MSK.localize(naive_dt)
        except ValueError:
            logger.warning(f"Failed to parse datetime string: {dt_str}")
            return datetime.fromtimestamp(0, self.TIMEZONE_MSK)
        
    def _calculate_new_end_date(self, current_end_date_str: Optional[str], days_to_add: int) -> str:
        """Рассчитывает новую дату окончания подписки."""
        now = self.get_current_time_msk()
        start_date = now
        
        # Если подписка еще активна, продлеваем с даты окончания
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
            
            # --- 2. PROMO CODES TABLE ---
            await db.execute("""CREATE TABLE IF NOT EXISTS promo_codes (
                    code TEXT PRIMARY KEY,
                    days INTEGER NOT NULL,
                    max_uses INTEGER NOT NULL,
                    current_uses INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
            )""") 
            
            # --- 3. DROP SESSIONS TABLE ---
            await db.execute("""CREATE TABLE IF NOT EXISTS drop_sessions (
                    drop_id INTEGER PRIMARY KEY,
                    pc_name TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    thread_id INTEGER,
                    phone TEXT,
                    status TEXT DEFAULT 'start', -- 'дайте номер', 'в работе', 'error', 'slet', 'замена', 'повтор', 'завершен'
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

    async def check_subscription(self, user_id: int) -> bool:
        """Проверяет, активна ли подписка, и деактивирует, если срок истек."""
        if user_id == ADMIN_ID: return True # Админ всегда активен
        
        user = await self.get_user(user_id)
        if not user or not user.get('subscription_active') or not user.get('subscription_end_date'): 
            return False

        try:
            end = self.to_msk_aware(user['subscription_end_date'])
            now = self.get_current_time_msk()
            
            if end > now:
                return True
            else:
                # Деактивация
                from telethon_manager import tm 
                if tm: await tm.stop_worker(user_id, silent=True)
                await self.set_telethon_status(user_id, False)
                await self.set_subscription_status(user_id, False, None) # Очищаем дату
                return False
        except Exception as e:
            logger.error(f"Subscription check error for {user_id}: {e}")
            return False
            
    async def set_subscription_status(self, user_id: int, active: bool, end_date_str: Optional[str]):
         """Устанавливает статус подписки и дату окончания."""
         async with aiosqlite.connect(self.db_path) as db:
             await db.execute("UPDATE users SET subscription_active=?, subscription_end_date=? WHERE user_id=?", 
                             (1 if active else 0, end_date_str, user_id))
             await db.commit()

    async def activate_promo_code(self, user_id: int, code: str) -> Optional[int]:
        """Активирует промокод для пользователя."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM promo_codes WHERE code=?", (code.upper(),)) as cursor:
                promo = await cursor.fetchone()
                if not promo: return None

            promo = dict(promo)
            # Проверка лимита (max_uses == 0 означает бесконечно)
            if promo['max_uses'] != 0 and promo['current_uses'] >= promo['max_uses']:
                return None

            user = await self.get_user(user_id)
            days = promo['days']
            new_end_date_str = self._calculate_new_end_date(user.get('subscription_end_date'), days)

            # Обновление пользователя
            await db.execute("UPDATE users SET subscription_active=1, subscription_end_date=? WHERE user_id=?", 
                            (new_end_date_str, user_id))
            
            # Увеличение счетчика использования
            await db.execute("UPDATE promo_codes SET current_uses = current_uses + 1 WHERE code=?", (code.upper(),))
            await db.commit()
            return days 

    # =========================================================================
    # DROP SYSTEM METHODS
    # =========================================================================

    async def start_pc_session(self, pc_name: str, chat_id: int, thread_id: Optional[int]) -> int:
        """Создает новую запись Drop-сессии."""
        now_str = self.get_current_time_msk().strftime('%Y-%m-%d %H:%M:%S')
        async with aiosqlite.connect(self.db_path) as db:
            # Статус 'дайте номер' - ожидание номера
            cursor = await db.execute("INSERT INTO drop_sessions (pc_name, chat_id, thread_id, status, start_time, last_status_time) VALUES (?, ?, ?, 'дайте номер', ?, ?)", 
                                     (pc_name.upper(), chat_id, thread_id, now_str, now_str))
            await db.commit()
            return cursor.lastrowid

    async def get_current_pc_session(self, chat_id: int, thread_id: Optional[int]) -> Optional[Dict[str, Any]]:
        """Получает последнюю активную сессию для данного чата/топика."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            # Ищем последнюю сессию, привязанную к chat_id и thread_id
            query = "SELECT * FROM drop_sessions WHERE chat_id=? AND thread_id=? ORDER BY drop_id DESC LIMIT 1"
            async with db.execute(query, (chat_id, thread_id)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
                
    async def update_drop_status(self, drop_id: int, new_status: str, phone: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Обновляет статус Drop-сессии, рассчитывает время простоя/работы."""
        now = self.get_current_time_msk()
        now_str = now.strftime('%Y-%m-%d %H:%M:%S')
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            async with db.execute("SELECT * FROM drop_sessions WHERE drop_id=?", (drop_id,)) as cursor:
                current_session = await cursor.fetchone()
                if not current_session: return None
                current_session = dict(current_session)
                
            old_status = current_session['status']
            last_status_time = self.to_msk_aware(current_session['last_status_time'])
            prosto_seconds = current_session['prosto_seconds']
            
            # Статусы, которые считаются простоем
            prosto_statuses = ["дайте номер", "error", "slet", "замена", "повтор", "start"]
            is_old_prosto = old_status in prosto_statuses
            
            # Логика расчета времени простоя
            if is_old_prosto and new_status not in ['в работе', 'завершен']:
                # Остаемся в простое или переходим из одного простоя в другой
                prosto_seconds += int((now - last_status_time).total_seconds())
            elif is_old_prosto and new_status in ['в работе', 'завершен']:
                 # Переход из простоя в работу/завершение (фиксируем последний период простоя)
                 prosto_seconds += int((now - last_status_time).total_seconds())

            # 3. Обновление записи
            update_sql = "UPDATE drop_sessions SET status=?, last_status_time=?, prosto_seconds=? "
            params: List[Any] = [new_status, now_str, prosto_seconds]

            if phone:
                update_sql += ", phone=? "
                params.append(phone)
                
            update_sql += "WHERE drop_id=?"
            params.append(drop_id)

            await db.execute(update_sql, tuple(params))
            await db.commit()
            
            async with db.execute("SELECT * FROM drop_sessions WHERE drop_id=?", (drop_id,)) as cursor:
                updated_session = await cursor.fetchone()
                return dict(updated_session)

    def calculate_times(self, session: Dict[str, Any]) -> Dict[str, Any]:
        """Рассчитывает общее время, время работы и время простоя для отчета."""
        start_time = self.to_msk_aware(session['start_time'])
        last_status_time = self.to_msk_aware(session['last_status_time'])
        now = self.get_current_time_msk()
        
        total_seconds = int((now - start_time).total_seconds())
        prosto_seconds = session['prosto_seconds']
        
        # Если статус сейчас "простойный", нужно добавить время с последнего обновления (last_status_time) до now
        prosto_statuses = ["дайте номер", "error", "slet", "замена", "повтор", "start"]
        if session['status'] in prosto_statuses:
            prosto_seconds += int((now - last_status_time).total_seconds())

        work_seconds = total_seconds - prosto_seconds
        
        def format_sec(sec):
            """Форматирует секунды в HH:MM:SS."""
            h = sec // 3600
            m = (sec % 3600) // 60
            s = sec % 60
            return f"{h:02}:{m:02}:{s:02}"

        return {
            'total_time': format_sec(total_seconds),
            'work_time': format_sec(max(0, work_seconds)), 
            'prosto_time': format_sec(max(0, prosto_seconds)),
            'prosto_seconds_raw': max(0, prosto_seconds) # Сырое значение для дальнейших расчетов
        }
