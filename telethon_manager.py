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
        @client.on(events.NewMessage()) 
        async def handle_new_message(event):
            # ВОРКЕР: Здесь будет ваша логика Drop-системы
            if event.raw_text and "status" in event.raw_text.lower() and event.is_private:
                logger.info(f"Worker {user_id} detected 'status' message.")
            
        logger.info(f"Handlers registered for worker {user_id}.")

    async def start_client_task(self, user_id: int):
        """Асинхронно запускает Telethon-клиента и регистрирует хендлеры."""
        session_path = self._get_session_path(user_id)
        
        if user_id in self.store.active_clients:
            logger.warning(f"Client {user_id} already running.")
            return

        client = TelegramClient(session_path, API_ID, API_HASH)
        
        try:
            await client.start()
            
            if not await client.is_user_authorized():
                raise AuthKeyError("Session expired or invalid.")
            
            self._register_worker_handlers(client, user_id)
            
            self.store.active_clients[user_id] = client
            await self.db.set_telethon_status(user_id, True)
            logger.info(f"Client {user_id} successfully launched and monitoring started.")
            
            # Запускаем основной цикл клиента, пока он не будет отключен
            await client.run_until_disconnected() 

        except (AuthKeyError, UserDeactivatedError):
            logger.error(f"Failed to start client {user_id}: Session expired.")
            await self._cleanup_session(user_id, client)
            await self.db.set_telethon_status(user_id, False)
            await self.bot.send_message(user_id, "⚠️ **Ваша сессия Telethon истекла или недействительна.**\nПожалуйста, авторизуйтесь заново с помощью команды /login.")
        except asyncio.CancelledError:
            logger.info(f"Client task {user_id} cancelled (disconnected).")
        except Exception as e:
            logger.error(f"Critical error starting client {user_id}: {e}", exc_info=True)
            await self._cleanup_session(user_id, client)
            await self.bot.send_message(user_id, f"❌ **Критическая ошибка при запуске сессии.**")

    async def stop_worker(self, user_id: int, silent: bool = False):
        """Останавливает Telethon-клиент и удаляет его из хранилища."""
        if user_id in self.store.active_clients:
            client = self.store.active_clients.pop(user_id)
            
            # Отключаем клиента
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

    async def start_auth(self, user_id: int) -> Tuple[bool, Optional[str]]:
        """Инициализирует процесс авторизации."""
        session_path = self._get_session_path(user_id)
        
        # 1. Если уже авторизуется, сбрасываем
        if user_id in self.store.auth_in_progress:
            await self.bot.send_message(user_id, "⚠️ **Предыдущая попытка авторизации отменена.** Начнем заново.")
            await self._cleanup_session(user_id, self.store.auth_in_progress.pop(user_id))
            
        # 2. Если воркер уже запущен, останавливаем его
        if user_id in self.store.active_clients:
             await self.stop_worker(user_id, silent=True)
             
        # 3. Создаем новый клиент
        client = TelegramClient(session_path, API_ID, API_HASH)
        self.store.auth_in_progress[user_id] = client

        try:
            await client.connect()
            if await client.is_user_authorized():
                await self._finish_auth(user_id)
                return True, "✅ Вы уже авторизованы! Воркер запущен."
            
            # Клиент готов к получению номера
            return False, None # Начинаем FSM
            
        except Exception as e:
            logger.error(f"Auth connection error for {user_id}: {e}")
            await self._cleanup_session(user_id, client)
            return False, f"❌ Ошибка подключения: {e}"

    async def send_code(self, user_id: int, phone: str) -> Optional[str]:
        """Отправляет код авторизации и возвращает phone_hash."""
        client = self.store.auth_in_progress.get(user_id)
        if not client: return None
        
        try:
            result = await client.send_code_request(phone)
            self.store.store[user_id] = {'phone': phone, 'phone_hash': result.phone_code_hash}
            return result.phone_code_hash
            
        except PhoneNumberInvalidError:
            await self._cleanup_session(user_id, client)
            return "❌ Неверный формат номера телефона."
        except PhoneNumberUnoccupiedError:
            await self._cleanup_session(user_id, client)
            return "❌ Пользователь с таким номером не найден."
        except FloodWaitError as e:
            await self._cleanup_session(user_id, client)
            return f"❌ Превышен лимит запросов. Попробуйте через {e.seconds} секунд."
        except Exception as e:
            logger.error(f"Send code error for {user_id}: {e}")
            await self._cleanup_session(user_id, client)
            return f"❌ Неизвестная ошибка: {e}"

    async def sign_in(self, user_id: int, phone: str, code: str, phone_hash: str) -> Tuple[bool, Optional[str]]:
        """Пытается войти, возвращает (success, result_message)."""
        client = self.store.auth_in_progress.get(user_id)
        if not client: return False, "❌ Сессия авторизации утеряна."
        
        try:
            await client.sign_in(phone, code, phone_hash=phone_hash)
            await self._finish_auth(user_id)
            return True, "✅ Успешный вход! Воркер запущен."
            
        except SessionPasswordNeededError:
            return False, "⚠️ **Требуется двухфакторная аутентификация.** Введите ваш облачный пароль."
        except PhoneCodeInvalidError:
            return False, "❌ Неверный код. Попробуйте снова."
        except (PhoneCodeEmptyError, PhoneCodeExpiredError):
            return False, "❌ Код истек или недействителен. Начните /login заново."
        except Exception as e:
            logger.error(f"Sign in error for {user_id}: {e}")
            await self._cleanup_session(user_id, client)
            return False, f"❌ Неизвестная ошибка: {e}"

    async def sign_in_password(self, user_id: int, password: str) -> Tuple[bool, Optional[str]]:
        """Пытается войти, используя пароль 2FA."""
        client = self.store.auth_in_progress.get(user_id)
        if not client: return False, "❌ Сессия авторизации утеряна."

        try:
            await client.sign_in(password=password)
            await self._finish_auth(user_id)
            return True, "✅ Успешный вход! Воркер запущен."
            
        except AuthKeyError:
            await self._cleanup_session(user_id, client)
            return False, "❌ Неверный пароль. Авторизуйтесь /login заново."
        except Exception as e:
            logger.error(f"Password sign in error for {user_id}: {e}")
            await self._cleanup_session(user_id, client)
            return False, f"❌ Неизвестная ошибка: {e}"

    async def _finish_auth(self, user_id: int):
        """Завершает процесс авторизации, запускает воркер, очищает временные данные."""
        client = self.store.auth_in_progress.pop(user_id, None)
        self.store.store.pop(user_id, None) # Чистим временные данные
        
        if client:
            if user_id in self.store.active_clients:
                 await self.store.active_clients[user_id].disconnect()
            
            self.store.active_clients[user_id] = client
            await self.db.set_telethon_status(user_id, True)
            
            # 🟢 УЛУЧШЕНИЕ: Запускаем воркер как отдельную таску
            asyncio.create_task(self.start_client_task(user_id)) 
        
    async def _cleanup_session(self, user_id: int, client: Optional[TelegramClient]):
        """Закрывает клиент и удаляет файл сессии."""
        if client:
            with suppress(Exception): await client.disconnect()
        session_path = self._get_session_path(user_id)
        with suppress(Exception):
            if os.path.exists(session_path): os.remove(session_path)
        if user_id in self.store.auth_in_progress:
            del self.store.auth_in_progress[user_id]
        self.store.store.pop(user_id, None) # Чистим временные данные
