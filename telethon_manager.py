import os
import asyncio
import logging
from typing import Dict, Any, Optional, Tuple
from contextlib import suppress

# --- TELETHON CORE ---
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError, PhoneNumberUnoccupiedError, 
    PhoneNumberInvalidError, AuthKeyError, PhoneCodeInvalidError, 
    FloodWaitError, RPCError, UserDeactivatedError, 
    PhoneCodeEmptyError, PhoneCodeExpiredError
)

# --- AIOGRAM 3.X IMPORTS ---
from aiogram.fsm.state import StatesGroup, State 
from aiogram import Bot

# --- LOCAL IMPORTS ---
from config import API_ID, API_HASH, SESSION_DIR, TELETHON_TIMEOUT
# ❌ ИМПОРТ AsyncDatabase УДАЛЕН ИЗ ЭТОГО МЕСТА!

logger = logging.getLogger(__name__)

# --- CORE SETTINGS ---
SESSION_DIR = "sessions"

# --- STATES ---
class TelethonAuth(StatesGroup):
    """Состояния для процесса авторизации Telethon-клиента."""
    phone = State()
    code = State()
    password = State()

# --- GLOBAL STORAGE ---
class GlobalStorage:
    """Глобальное хранилище активных клиентов и данных."""
    def __init__(self):
        self.active_clients: Dict[int, TelegramClient] = {}
        self.store: Dict[int, Any] = {}
        self.auth_in_progress: Dict[int, TelegramClient] = {}

# --- MANAGER CLASS ---
class TelethonManager:
    """Менеджер для управления сессиями Telethon."""
    # 🟢 ИСПРАВЛЕНИЕ: Используем подсказку типа 'AsyncDatabase'
    def __init__(self, bot: Bot, store: GlobalStorage, db: 'AsyncDatabase'):
        self.bot = bot
        self.store = store
        self.db = db
        
    def _get_session_path(self, user_id: int) -> str:
        """Возвращает полный путь к файлу сессии."""
        return os.path.join(SESSION_DIR, str(user_id))

    async def start_client_task(self, user_id: int):
        """Асинхронно запускает Telethon-клиента и добавляет его в хранилище."""
        # ... (логика запуска клиента) ...
        pass # Сокращено для читаемости

    async def stop_worker(self, user_id: int, silent: bool = False):
        """Останавливает Telethon-клиент и удаляет его из хранилища."""
        if user_id in self.store.active_clients:
            client = self.store.active_clients.pop(user_id)
            with suppress(Exception):
                await client.disconnect()
            
            await self.db.set_telethon_status(user_id, False)
            logger.info(f"Client {user_id} disconnected and stopped.")
            
            if not silent:
                await self.bot.send_message(user_id, 
                                            "✅ **Ваша сессия Telethon остановлена.**\n"
                                            "Данные аккаунта сохранены, но воркер не работает. Для запуска используйте /login.")
        else:
            if not silent:
                await self.bot.send_message(user_id, "ℹ️ Активная сессия Telethon не найдена.")

    async def start_auth(self, user_id: int) -> Tuple[bool, Optional[str]]:
        """Инициализирует процесс авторизации."""
        # ... (логика start_auth) ...
        return (False, None) # Сокращено для читаемости

    async def send_code(self, user_id: int, phone: str) -> Optional[str]:
        # ... (логика send_code) ...
        pass # Сокращено для читаемости
            
    async def sign_in(self, user_id: int, phone: str, code: str, phone_hash: str) -> Tuple[bool, Optional[str]]:
        # ... (логика sign_in) ...
        pass # Сокращено для читаемости

    async def sign_in_password(self, user_id: int, password: str) -> Tuple[bool, Optional[str]]:
        # ... (логика sign_in_password) ...
        pass # Сокращено для читаемости

    async def _finish_auth(self, user_id: int):
        # ... (логика _finish_auth) ...
        pass # Сокращено для читаемости
        
    async def _cleanup_session(self, user_id: int, client: Optional[TelegramClient]):
        # ... (логика _cleanup_session) ...
        pass # Сокращено для читаемости
