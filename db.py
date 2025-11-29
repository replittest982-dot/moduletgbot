import aiosqlite
import datetime
from typing import Optional, List, Tuple
from dateutil import parser
import logging

from .config import MOSCOW_TZ, DB_PATH
from .utils import format_timedelta # Импортируем из utils

logger = logging.getLogger(__name__)

class AsyncDatabase:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None

    async def init(self):
        if not self.conn:
            self.conn = await aiosqlite.connect(self.db_path)
            self.conn.row_factory = aiosqlite.Row 
            await self._create_tables()
        logger.info("Database initialized.")

    async def _create_tables(self):
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                subscription_active BOOLEAN DEFAULT 0,
                subscription_end_date TEXT,
                telethon_active BOOLEAN DEFAULT 0
            )
        """)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                days INTEGER,
                is_active BOOLEAN DEFAULT 1,
                max_uses INTEGER,
                current_uses INTEGER DEFAULT 0
            )
        """)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS drop_sessions (
                phone TEXT PRIMARY KEY,
                pc_name TEXT,
                drop_id INTEGER,
                status TEXT,
                start_time TEXT,
                last_status_time TEXT,
                prosto_seconds INTEGER DEFAULT 0
            )
        """)
        await self.conn.commit()
    
    # --- Users & Subscription ---
    
    async def get_user(self, user_id: int) -> Optional[aiosqlite.Row]:
        cursor = await self.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return await cursor.fetchone()

    async def update_user(self, user_id: int, **kwargs):
        # ... (логика обновления пользователя, как в монолите) ...
        set_parts = [f"{k} = ?" for k in kwargs]
        values = list(kwargs.values())
        values.append(user_id)
        
        user = await self.get_user(user_id)
        if not user:
             keys = ', '.join(['user_id'] + list(kwargs.keys()))
             placeholders = ', '.join(['?'] * (len(kwargs) + 1))
             await self.conn.execute(f"INSERT INTO users ({keys}) VALUES ({placeholders})", [user_id] + list(kwargs.values()))
        else:
             await self.conn.execute(
                 f"UPDATE users SET {', '.join(set_parts)} WHERE user_id = ?", values
             )
        await self.conn.commit()

    async def get_active_telethon_users(self) -> List[int]:
        cursor = await self.conn.execute("SELECT user_id FROM users WHERE telethon_active = 1")
        return [row['user_id'] for row in await cursor.fetchall()]

    async def check_subscription(self, user_id: int, admin_id: int) -> bool:
        if user_id == admin_id: return True
        
        user = await self.get_user(user_id)
        if not user or not user['subscription_active']: return False

        end_date_str = user['subscription_end_date']
        if not end_date_str:
            await self.update_user(user_id, subscription_active=0)
            return False

        try:
            end_date_aware = parser.isoparse(end_date_str).astimezone(MOSCOW_TZ)
        except:
            await self.update_user(user_id, subscription_active=0)
            return False
            
        now_aware = datetime.datetime.now(MOSCOW_TZ)
        
        if end_date_aware <= now_aware:
            await self.update_user(user_id, subscription_active=0)
            return False
        
        return True

    async def get_subscription_status(self, user_id: int, admin_id: int) -> Tuple[bool, str]:
        if user_id == admin_id:
            return True, "🟢 Бессрочная (Админ)"
        
        is_active = await self.check_subscription(user_id, admin_id)
        if not is_active:
            return False, "🔴 Не активна / Истекла"
            
        user = await self.get_user(user_id)
        end_date_str = user['subscription_end_date']
        
        try:
            end_date_aware = parser.isoparse(end_date_str).astimezone(MOSCOW_TZ)
        except:
            return False, "🔴 Не активна (Ошибка даты)"

        now_aware = datetime.datetime.now(MOSCOW_TZ)
        delta = end_date_aware - now_aware
        
        return True, f"🟢 До {end_date_aware.strftime('%d.%m.%Y')} ({format_timedelta(delta)})"
        
    # --- Promo Codes ---
        
    async def apply_promo_code(self, user_id: int, code: str) -> Tuple[bool, str]:
        # ... (логика применения промокода, как в монолите) ...
        cursor = await self.conn.execute(
            "SELECT days, is_active, max_uses, current_uses FROM promo_codes WHERE code = ?", (code.upper(),)
        )
        promo = await cursor.fetchone()
        
        if not promo: return False, "❌ Промокод не найден."
            
        days, is_active, max_uses, current_uses = promo['days'], promo['is_active'], promo['max_uses'], promo['current_uses']
        
        if not is_active or current_uses >= max_uses: return False, "❌ Промокод не активен или исчерпал лимит использований."
            
        user = await self.get_user(user_id)
        now_aware = datetime.datetime.now(MOSCOW_TZ)
        
        start_date = now_aware
        if user and user.get('subscription_active') and user.get('subscription_end_date'):
            try:
                current_end = parser.isoparse(user['subscription_end_date']).astimezone(MOSCOW_TZ)
            except:
                current_end = now_aware
            start_date = max(current_end, now_aware)
        
        new_end_date = start_date + datetime.timedelta(days=days)
        new_end_date_str = new_end_date.isoformat()
        
        await self.update_user(user_id, subscription_active=1, subscription_end_date=new_end_date_str)
        
        new_uses = current_uses + 1
        new_active = 0 if new_uses >= max_uses else 1
        
        await self.conn.execute(
            "UPDATE promo_codes SET current_uses = ?, is_active = ? WHERE code = ?", 
            (new_uses, new_active, code.upper())
        )
        await self.conn.commit()
        
        return True, f"✅ Подписка продлена до **{new_end_date.strftime('%d.%m.%Y %H:%M')} (MSK)**."

    async def create_promo_code(self, code: str, days: int, max_uses: int) -> bool:
        try:
            await self.conn.execute(
                "INSERT INTO promo_codes (code, days, max_uses) VALUES (?, ?, ?)",
                (code.upper(), days, max_uses)
            )
            await self.conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    # --- DROP_SESSIONS Methods ---
    
    async def create_drop_session(self, pc_name: str, drop_id: int):
        # ... (логика создания drop-сессии, как в монолите) ...
        now_str = datetime.datetime.now(MOSCOW_TZ).isoformat()
        phone_flag = f"TEMP_{pc_name}_{drop_id}_{int(datetime.datetime.now().timestamp())}"
        
        await self.conn.execute("""
            INSERT INTO drop_sessions (phone, pc_name, drop_id, status, start_time, last_status_time, prosto_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (phone_flag, pc_name, drop_id, "дайте номер", now_str, now_str, 0))
        await self.conn.commit()
        return phone_flag

    async def update_drop_session(self, current_phone: str, new_phone: Optional[str] = None, status: Optional[str] = None, pc_name: Optional[str] = None, drop_id: Optional[int] = None):
        # ... (логика обновления drop-сессии, как в монолите) ...
        cursor = await self.conn.execute("SELECT * FROM drop_sessions WHERE phone = ?", (current_phone,))
        row = await cursor.fetchone()
        if not row: return False, "❌ Сессия не найдена."
        
        current_data = dict(row)
        now_aware = datetime.datetime.now(MOSCOW_TZ)
        
        update_parts = {}
        
        if new_phone and new_phone != current_phone:
            await self.conn.execute("DELETE FROM drop_sessions WHERE phone = ?", (current_phone,))
            current_data['phone'] = new_phone
            update_parts['phone'] = new_phone
        
        if status and status != current_data['status']:
            last_status_time = parser.isoparse(current_data['last_status_time']).astimezone(MOSCOW_TZ)
            IDLE_STATUSES = ["дайте номер", "error", "slet", "замена", "повтор"]

            if current_data['status'] in IDLE_STATUSES and status == "в работе":
                time_idle = now_aware - last_status_time
                current_data['prosto_seconds'] += int(time_idle.total_seconds())
                update_parts['prosto_seconds'] = current_data['prosto_seconds']
            
            update_parts['status'] = status
            update_parts['last_status_time'] = now_aware.isoformat()
        
        if update_parts:
            if 'phone' in update_parts and update_parts['phone'] != current_phone:
                keys = list(current_data.keys())
                values = list(current_data.values())
                await self.conn.execute(f"INSERT INTO drop_sessions ({', '.join(keys)}) VALUES ({', '.join(['?'] * len(keys))})", values)
            else:
                set_parts = [f"{k} = ?" for k in update_parts.keys()]
                values = list(update_parts.values())
                values.append(current_phone)
                await self.conn.execute(f"UPDATE drop_sessions SET {', '.join(set_parts)} WHERE phone = ?", values)
            await self.conn.commit()
            return True, "✅ Статус обновлен."
            
        return True, "✅ Обновление не потребовалось."
        
    async def get_latest_drop_session(self, drop_id: int) -> Optional[aiosqlite.Row]:
        cursor = await self.conn.execute(
            "SELECT * FROM drop_sessions WHERE drop_id = ? ORDER BY last_status_time DESC LIMIT 1", (drop_id,)
        )
        return await cursor.fetchone()
