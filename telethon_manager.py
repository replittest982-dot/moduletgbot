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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- LOCAL IMPORTS ---
from config import API_ID, API_HASH, SESSION_DIR, TELETHON_TIMEOUT, TARGET_CHANNEL_URL
# db и tm будут импортированы в main.py, поэтому здесь мы используем type hinting
from db import AsyncDatabase

logger = logging.getLogger(__name__)

# --- CORE SETTINGS ---
# Папка для сессий Telethon
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
        # Словарь активных клиентов Telethon: {user_id: TelegramClient}
        self.active_clients: Dict[int, TelegramClient] = {}
        # Хранилище для middleware
        self.store: Dict[int, Any] = {}
        # Хранилище для временных данных авторизации: {user_id: client}
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

    async def start_client_task(self, user_id: int):
        """Асинхронно запускает Telethon-клиента и добавляет его в хранилище."""
        session_path = self._get_session_path(user_id)
        
        # Проверка: если уже запущен, выходим
        if user_id in self.store.active_clients:
            logger.warning(f"Client {user_id} already running.")
            return

        client = TelegramClient(session_path, API_ID, API_HASH)
        
        try:
            await client.start()
            
            # Проверка, что сессия не истекла и аккаунт активен
            if not await client.is_user_authorized():
                logger.warning(f"Client {user_id} is not authorized (session expired).")
                raise AuthKeyError("Session expired or invalid.")
            
            # Регистрируем активный клиент
            self.store.active_clients[user_id] = client
            await self.db.set_telethon_status(user_id, True)
            logger.info(f"Client {user_id} successfully launched.")
            
            # Здесь можно добавить логику запуска воркера (например, хендлеры сообщений)
            # self._register_worker_handlers(client, user_id) 

        except (AuthKeyError, UserDeactivatedError):
            logger.error(f"Failed to start client {user_id}: Session file is invalid or user is deactivated.")
            await self._cleanup_session(user_id, client)
            await self.db.set_telethon_status(user_id, False)
            await self.bot.send_message(user_id, 
                                        "⚠️ **Ваша сессия Telethon истекла или недействительна.**\n"
                                        "Пожалуйста, авторизуйтесь заново с помощью команды /login.")
        except Exception as e:
            logger.error(f"Critical error starting client {user_id}: {e}")
            await self.bot.send_message(user_id, 
                                        f"❌ **Критическая ошибка при запуске сессии.**\n"
                                        f"Попробуйте еще раз или обратитесь в поддержку.")

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
        """Инициализирует процесс авторизации и возвращает (is_password_needed, phone_number)."""
        
        if user_id in self.store.auth_in_progress:
            await self.store.auth_in_progress[user_id].disconnect()
            del self.store.auth_in_progress[user_id]

        session_path = self._get_session_path(user_id)
        client = TelegramClient(session_path, API_ID, API_HASH)
        await client.connect()
        
        self.store.auth_in_progress[user_id] = client
        
        # Если файл сессии уже существует и авторизация действительна
        if await client.is_user_authorized():
            await self.bot.send_message(user_id, "✅ **Ваш аккаунт уже авторизован!** Запуск воркера...")
            await self._finish_auth(user_id)
            return (False, None)

        return (False, None) # Продолжение в handlers.py

    async def send_code(self, user_id: int, phone: str) -> Optional[str]:
        """Отправляет код авторизации и возвращает phone_hash."""
        client = self.store.auth_in_progress.get(user_id)
        if not client: return None

        try:
            result = await client.send_code_request(phone)
            return result.phone_code_hash
        except PhoneNumberInvalidError:
            await self._cleanup_session(user_id, client)
            return "ERROR_INVALID_PHONE"
        except FloodWaitError as e:
            await self._cleanup_session(user_id, client)
            return f"ERROR_FLOOD_WAIT:{e.seconds}"
        except Exception as e:
            logger.error(f"Error sending code for {user_id}: {e}")
            await self._cleanup_session(user_id, client)
            return "ERROR_UNKNOWN"
            
    async def sign_in(self, user_id: int, phone: str, code: str, phone_hash: str) -> Tuple[bool, Optional[str]]:
        """Пытается войти, возвращает (success, result_message)."""
        client = self.store.auth_in_progress.get(user_id)
        if not client: return (False, "Сессия авторизации не найдена.")

        try:
            await client.sign_in(phone, code, phone_code_hash=phone_hash)
            # Успешный вход без двухфакторной аутентификации
            await self._finish_auth(user_id)
            return (True, "✅ **Авторизация успешна!** Воркер запущен.")
            
        except SessionPasswordNeededError:
            # Требуется пароль
            return (False, "PASSWORD_NEEDED")
            
        except (PhoneCodeInvalidError, PhoneCodeEmptyError, PhoneCodeExpiredError):
            return (False, "❌ **Неверный код.** Пожалуйста, попробуйте еще раз.")
        
        except UserDeactivatedError:
             return (False, "❌ **Аккаунт деактивирован.**")

        except RPCError as e:
            logger.error(f"RPC Error during sign in for {user_id}: {e}")
            return (False, f"❌ **Ошибка RPC:** {e}")

        except Exception as e:
            logger.error(f"Unknown Error during sign in for {user_id}: {e}")
            return (False, f"❌ **Неизвестная ошибка:** {e}")


    async def sign_in_password(self, user_id: int, password: str) -> Tuple[bool, Optional[str]]:
        """Пытается войти, используя пароль 2FA."""
        client = self.store.auth_in_progress.get(user_id)
        if not client: return (False, "Сессия авторизации не найдена.")
        
        try:
            await client.sign_in(password=password)
            
            # Успешный вход
            await self._finish_auth(user_id)
            return (True, "✅ **Авторизация успешна!** Воркер запущен.")
            
        except Exception as e:
            logger.error(f"Error during 2FA sign in for {user_id}: {e}")
            return (False, "❌ **Неверный пароль.** Пожалуйста, попробуйте еще раз.")


    async def _finish_auth(self, user_id: int):
        """Завершает процесс авторизации, запускает воркер, очищает временные данные."""
        
        client = self.store.auth_in_progress.pop(user_id, None)
        
        if client:
            if user_id in self.store.active_clients:
                 await self.store.active_clients[user_id].disconnect()
            
            self.store.active_clients[user_id] = client
            await self.db.set_telethon_status(user_id, True)
            
            # Запускаем таску, чтобы не блокировать Aiogram
            asyncio.create_task(self.start_client_task(user_id)) 

        # На этом этапе аккаунт авторизован и воркер должен запуститься через start_client_task
        
    async def _cleanup_session(self, user_id: int, client: Optional[TelegramClient]):
        """Закрывает клиент и удаляет файл сессии."""
        if client:
            with suppress(Exception):
                await client.disconnect()
        
        session_path = self._get_session_path(user_id)
        with suppress(Exception):
            if os.path.exists(session_path):
                os.remove(session_path)

        if user_id in self.store.auth_in_progress:
            del self.store.auth_in_progress[user_id]
