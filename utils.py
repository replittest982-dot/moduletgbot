import os
import datetime
import asyncio
import logging
import re
import textwrap
from typing import Dict, Any, Optional, Union, List, Tuple
from contextlib import suppress
from dateutil import parser 
from aiogram.fsm.state import StatesGroup, State 
from aiogram.types import Message, CallbackQuery
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from telethon import TelegramClient
import aiosqlite

from .config import SESSIONS_DIR, ADMIN_ID, MOSCOW_TZ

logger = logging.getLogger(__name__)

# =========================================================================
# I. FSM States
# =========================================================================

class TelethonAuth(StatesGroup):
    PHONE = State()
    CODE = State()
    PASSWORD = State()
    WAITING_FOR_QR_LOGIN = State()
    QR_PASSWORD = State()

class AdminState(StatesGroup):
    CREATING_PROMO_CODE = State()

class UserState(StatesGroup):
    WAITING_FOR_PROMO_CODE = State()
    WAITING_FOR_LOGOUT_CONFIRM = State()

class DropUserState(StatesGroup):
    WAITING_FOR_NUMB_INPUT = State()
    WAITING_FOR_REPORT_INPUT = State()

# =========================================================================
# II. ГЛОБАЛЬНОЕ ХРАНИЛИЩЕ
# =========================================================================

class GlobalStorage:
    """Хранилище активных клиентов и временных данных Telethon."""
    def __init__(self):
        self.active_clients: Dict[int, TelegramClient] = {}
        self.active_workers: Dict[int, asyncio.Task] = {}
        self.temp_data: Dict[int, Dict[str, Any]] = {}
        self.active_tasks: Dict[int, Dict[str, asyncio.Task]] = {}
        self.process_progress: Dict[int, Dict[str, Any]] = {}
        # (chat_id, thread_id) -> pc_name
        self.drop_mapping: Dict[Tuple[int, int], str] = {} 
        
    def _get_session_path(self, user_id: int, temp: bool = False) -> str:
        name = f"session_{user_id}{'_temp' if temp else ''}.session"
        return os.path.join(SESSIONS_DIR, name)
        
    def get_session_path(self, user_id: int) -> str:
        return self._get_session_path(user_id, temp=False)
        
    def get_temp_session_path(self, user_id: int) -> str:
        return self._get_session_path(user_id, temp=True)
        
    def delete_session_file(self, user_id: int, temp: bool = False):
        path = self._get_session_path(user_id, temp)
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"Deleted session file: {path}")

    def rename_session_file(self, user_id: int):
        temp_path = self.get_temp_session_path(user_id)
        final_path = self.get_session_path(user_id)
        if os.path.exists(temp_path):
            os.rename(temp_path, final_path)
            logger.info(f"Renamed temp session for {user_id} to final.")
            return True
        return False

# =========================================================================
# III. ХЕЛПЕРЫ И MIDDLEWARE
# =========================================================================

def get_user_id_from_update(update: Union[Message, CallbackQuery]) -> Optional[int]:
    if update.from_user: return update.from_user.id
    return None

def check_valid_phone(phone: str) -> Optional[str]:
    phone = re.sub(r'[^\d+]', '', phone) 
    if re.fullmatch(r'^\+\d{10,15}$', phone): return phone
    return None

def format_timedelta(delta: datetime.timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days > 0: parts.append(f"{days} д.")
    if hours > 0: parts.append(f"{hours} ч.")
    if minutes > 0: parts.append(f"{minutes} м.")
    if seconds > 0 or not parts: parts.append(f"{seconds} с.")
    return " ".join(parts[:3])

async def delete_messages_after(chat_id: Union[int, str], message_ids: Union[int, List[int]], delay: int, bot):
    await asyncio.sleep(delay)
    if not isinstance(message_ids, list):
        message_ids = [message_ids]
    
    with suppress(Exception):
        await bot.delete_messages(chat_id=chat_id, message_ids=message_ids)

def format_drop_report(row: aiosqlite.Row) -> str:
    start_time = parser.isoparse(row['start_time']).astimezone(MOSCOW_TZ)
    last_status_time = parser.isoparse(row['last_status_time']).astimezone(MOSCOW_TZ)
    
    now_aware = datetime.datetime.now(MOSCOW_TZ)
    
    total_seconds = int((now_aware - start_time).total_seconds())
    prosto_seconds = row['prosto_seconds']
    work_seconds = total_seconds - prosto_seconds
    
    return textwrap.dedent(f"""
    📊 **Отчет по Drop-сессии**
    
    **ПК / Дроп:** `{row['pc_name']}` / `{row['drop_id']}`
    **Номер:** `{row['phone']}`
    **Текущий статус:** `{row['status']}`
    
    **Начало работы:** {start_time.strftime('%d.%m %H:%M:%S')}
    **Последний статус:** {last_status_time.strftime('%d.%m %H:%M:%S')}
    
    **Общее время:** {format_timedelta(datetime.timedelta(seconds=total_seconds))}
    **Время в работе:** {format_timedelta(datetime.timedelta(seconds=work_seconds))}
    **Время простоя:** {format_timedelta(datetime.timedelta(seconds=prosto_seconds))}
    """)

class DependencyInjectorMiddleware(BaseMiddleware):
    """Внедряет db, tm, store во все обработчики через kwargs."""
    def __init__(self, data: Dict[str, Any]):
        self.data = data
        super().__init__()

    async def __call__(self, handler, event: Union[Message, CallbackQuery], data: Dict[str, Any]):
        data.update(self.data) 
        return await handler(event, data)
