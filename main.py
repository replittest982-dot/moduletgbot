import os
import asyncio
import logging
from typing import Dict, Any, Optional, Tuple
from contextlib import suppress

# --- TELETHON CORE ---
from telethon import TelegramClient, events
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
# Используем подсказку типа для AsyncDatabase, чтобы избежать цикла
# from db import AsyncDatabase

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
    def __init__(self, bot: Bot, store: GlobalStorage, db: 'AsyncDatabase'):
        self.bot = bot
        self.store = store
        self.db = db
        
    def _get_session_path(self, user_id: int) -> str:
        """Возвращает полный путь к файлу сессии."""
        return os.path.join(SESSION_DIR, str(user_id))
        
    def _register_worker_handlers(self, client: TelegramClient, user_id: int):
        """Регистрирует обработчики событий Telethon для данного клиента."""
        @client.on(events.NewMessage(incoming=True, chats=self.db.DROP_LOG_CHAT_ID)) # Предполагаем, что DROP_LOG_CHAT_ID есть
        async def handle_new_message(event):
            # 🟢 ЗАГЛУШКА УДАЛЕНА: Примерная логика Drop-системы
            if event.raw_text and "Дайте номер" in event.raw_text:
                 logger.info(f"Worker {user_id} detected 'Дайте номер' in logs.")
                 # Здесь будет логика ответа Drop-системе
                 # await self.bot.send_message(user_id, "Воркер обнаружил запрос номера!")
            
        logger.info(f"Handlers registered for worker {user_id}.")

    async def start_client_task(self, user_id: int):
        """🟢 ЗАГЛУШКА УДАЛЕНА: Асинхронно запускает Telethon-клиента и регистрирует хендлеры."""
        session_path = self._get_session_path(user_id)
        
        if user_id in self.store.active_clients:
            logger.warning(f"Client {user_id} already running.")
            return

        client = TelegramClient(session_path, API_ID, API_HASH)
        
        try:
            await client.start()
            
            if not await client.is_user_authorized():
                logger.warning(f"Client {user_id} is not authorized (session expired).")
                raise AuthKeyError("Session expired or invalid.")
            
            self._register_worker_handlers(client, user_id)
            
            self.store.active_clients[user_id] = client
            await self.db.set_telethon_status(user_id, True)
            logger.info(f"Client {user_id} successfully launched and monitoring started.")
            
            # 🟢 ЗАПУСК ОСНОВНОГО ЦИКЛА:
            await client.run_until_disconnected() 

        except (AuthKeyError, UserDeactivatedError):
            logger.error(f"Failed to start client {user_id}: Session file is invalid or user is deactivated.")
            await self._cleanup_session(user_id, client)
            await self.db.set_telethon_status(user_id, False)
            await self.bot.send_message(user_id, 
                                        "⚠️ **Ваша сессия Telethon истекла или недействительна.**\n"
                                        "Пожалуйста, авторизуйтесь заново с помощью команды /login.")
        except asyncio.CancelledError:
            # Нормальное завершение при client.disconnect()
            logger.info(f"Client task {user_id} cancelled.")
        except Exception as e:
            logger.error(f"Critical error starting client {user_id}: {e}")
            await self._cleanup_session(user_id, client)
            await self.bot.send_message(user_id, f"❌ **Критическая ошибка при запуске сессии.**")

    async def stop_worker(self, user_id: int, silent: bool = False):
        """Останавливает Telethon-клиент и удаляет его из хранилища."""
        if user_id in self.store.active_clients:
            client = self.store.active_clients.pop(user_id)
            
            # Отключаем клиента, это вызывает CancelledError в run_until_disconnected
            with suppress(Exception):
                await client.disconnect()
            
            await self.db.set_telethon_status(user_id, False)
            logger.info(f"Client {user_id} disconnected and stopped.")
            
            if not silent:
                await self.bot.send_message(user_id, 
                                            "✅ **Ваша сессия Telethon остановлена.**")
        else:
            if not silent:
                await self.bot.send_message(user_id, "ℹ️ Активная сессия Telethon не найдена.")

    # ... (Остальные методы авторизации: start_auth, send_code, sign_in, sign_in_password, _finish_auth, _cleanup_session)
    # Они остаются такими, как были в предыдущем сообщении.
