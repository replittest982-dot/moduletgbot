import os
import datetime
import asyncio
import logging
import re
import textwrap
from typing import Dict, Any, Optional, Union, List, Tuple
from contextlib import suppress
from aiogram.fsm.state import StatesGroup, State 
from aiogram.types import Message, CallbackQuery
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from telethon import TelegramClient
from dateutil import parser # Добавлен для работы format_drop_report

# УБРАНЫ ТОЧКИ ПЕРЕД ИМЕНАМИ МОДУЛЕЙ
from config import SESSIONS_DIR, ADMIN_ID, MOSCOW_TZ

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
            if os.path.exists(final_path):
                os.remove(final_path)
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

def format_drop_report(session_data: dict) -> str:
    """Форматирует данные сессии дропа в читаемый отчет."""
    
    try:
        start_time = parser.isoparse(session_data['start_time']).astimezone(MOSCOW_TZ)
        last_status_time = parser.isoparse(session_data['last_status_time']).astimezone(MOSCOW_TZ)
    except Exception:
         return "❌ Ошибка форматирования даты сессии."
    
    # Расчет времени работы
    now_aware = datetime.datetime.now(MOSCOW_TZ)
    total_time = now_aware - start_time
    
    # Расчет простоя
    prosto_time = datetime.timedelta(seconds=session_data.get('prosto_seconds', 0))
    work_time_delta = total_time - prosto_time

    return textwrap.dedent(f"""
        **📢 Отчет по сессии**
        ---
        **🖥️ ПК:** `{session_data['pc_name']}`
        **👤 ID дропа:** `{session_data['drop_id']}`
        **📞 Номер (ID):** `{session_data['phone']}`
        **📊 Текущий статус:** `{session_data['status'].upper()}`
        ---
        **🟢 Время в работе:** {format_timedelta(work_time_delta)}
        **🔴 Время простоя:** {format_timedelta(prosto_time)}
        **⏳ Всего в сессии:** {format_timedelta(total_time)}
        ---
        **🕐 Старт:** {start_time.strftime('%H:%M:%S %d.%m')}
        **🔄 Обновление:** {last_status_time.strftime('%H:%M:%S %d.%m')}
    """).strip()

class DependencyInjectorMiddleware(BaseMiddleware):
    def __init__(self, data: Dict[str, Any]):
        self.data = data
        super().__init__()

    async def __call__(self, handler, event: Union[Message, CallbackQuery], data: Dict[str, Any]):
        data.update(self.data) 
        return await handler(event, data)
