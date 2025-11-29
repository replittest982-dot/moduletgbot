import asyncio
import logging
import os
import time

from telethon import TelegramClient, events
from telethon.tl.types import PeerUser, PeerChannel, PeerChat

# Вам нужно убедиться, что эти импорты корректны для вашей структуры проекта
# Например:
# from config import API_ID, API_HASH, TEMP_DIR, ADMIN_ID
# from db import AsyncDatabase
# from utils import GlobalStorage # Или как вы называете ваше глобальное хранилище
# ...

logger = logging.getLogger(__name__)

# --- ВАЖНО: Замените на ваши реальные классы и константы ---
# class AsyncDatabase:
#     async def get_session_data(self, user_id): return {'phone': '+7...', 'session_name': 'user_sess'}
#     async def update_user(self, user_id, **kwargs): pass
#     async def update_chat_pc_mapping(self, chat_id, topic_id, pc_name, user_id): pass
#     async def get_user_id_by_session_name(self, session_name): return 12345
#     async def get_admin_id(self): return 123456789

# class GlobalStorage:
#     def __init__(self):
#         self.active_workers = {}  # {user_id: Task}
#         self.process_progress = {} # {user_id: {'type': 'flood', ...}}
#         self.temp_data = {} # {user_id: {'qr_login_data': obj, 'client': client}}
#         self.drop_mapping = {} # {(chat_id, topic_id): 'PC_Name'}

# class Config:
#     API_ID = 12345
#     API_HASH = 'YOUR_HASH'
#     TEMP_DIR = 'sessions'
#     ADMIN_ID = 123456789
# -----------------------------------------------------------------------


class TelethonManager:
    def __init__(self, db, store, config):
        self.db = db
        self.store = store
        self.config = config
        self.sessions = {}  # {user_id: TelethonClient}
        self.qr_sessions = {} # {user_id: (Client, QRLogin)}

        # Убедимся, что папка для сессий существует
        if not os.path.exists(self.config.TEMP_DIR):
            os.makedirs(self.config.TEMP_DIR)

    # ----------------------------------------------------
    # ⚙️ Базовые методы клиента
    # ----------------------------------------------------

    def get_session_path(self, user_id: int):
        """Возвращает путь к файлу сессии."""
        return os.path.join(self.config.TEMP_DIR, f"user_{user_id}")

    async def create_client(self, user_id: int) -> TelegramClient:
        """Создает и подключает клиент, используя данные из БД."""
        
        # Если клиент уже есть, возвращаем его
        if user_id in self.sessions:
            return self.sessions[user_id]
        
        session_path = self.get_session_path(user_id)
        
        # Получаем данные сессии из БД (например, телефон для именования сессии)
        user_session_data = await self.db.get_session_data(user_id)
        if not user_session_data or not user_session_data.get('phone'):
             # Создаем временный клиент для аутентификации
             client = TelegramClient(session_path, self.config.API_ID, self.config.API_HASH)
        else:
             # Используем phone для уникальности, если нужно
             client = TelegramClient(session_path, self.config.API_ID, self.config.API_HASH)
        
        # Подключаемся, но не авторизуемся, если это новый клиент
        if not client.is_connected():
            await client.connect()
            
        return client

    async def finalize_auth(self, user_id: int, client: TelegramClient):
        """Завершает аутентификацию, обновляет статус в БД и сохраняет клиента."""
        self.sessions[user_id] = client
        await self.db.update_user(user_id, telethon_active=1)
        # Сохраняем клиента, чтобы он не был собран сборщиком мусора
        await client.get_me() # Простой запрос для проверки и сохранения сессии
        
    # ----------------------------------------------------
    # 📱 Phone Login
    # ----------------------------------------------------

    async def send_code(self, user_id: int, phone: str) -> str:
        """Отправляет код подтверждения на телефон."""
        try:
            client = await self.create_client(user_id)
            sent_code = await client.send_code_request(phone)
            
            # Сохраняем временные данные для проверки
            self.store.temp_data[user_id] = {
                'phone': phone,
                'sent_code': sent_code,
                'client': client
            }
            return "✅ Код отправлен."
        except Exception as e:
            logger.error(f"Error sending code for {user_id}: {e}")
            return f"❌ Ошибка отправки кода: {e}"

    async def sign_in(self, user_id: int, code: str):
        """Проверяет код и пытается войти."""
        data = self.store.temp_data.get(user_id)
        if not data:
            return False, "❌ Сессия аутентификации утеряна.", None

        client, phone, sent_code = data['client'], data['phone'], data['sent_code']
        
        try:
            await client.sign_in(phone, code, password=None) # Пробуем без пароля
            await self.finalize_auth(user_id, client)
            return True, "✅ Успешный вход!", client
        except Exception as e:
            if "Password required" in str(e):
                return False, "⚠️ Требуется пароль 2FA.", client
            
            logger.error(f"Sign in error for {user_id}: {e}")
            return False, f"❌ Ошибка входа: {e}", client

    async def sign_in_password(self, user_id: int, password: str):
        """Проверяет пароль 2FA и завершает вход."""
        data = self.store.temp_data.get(user_id)
        if not data:
            return False, "❌ Сессия аутентификации утеряна."

        client, phone = data['client'], data['phone']
        
        try:
            await client.sign_in(phone, password=password)
            await self.finalize_auth(user_id, client)
            return True, "✅ Успешный вход!"
        except Exception as e:
            logger.error(f"Password error for {user_id}: {e}")
            return False, f"❌ Ошибка пароля: {e}"

    # ----------------------------------------------------
    # 🖼️ QR Login (Исправлено)
    # ----------------------------------------------------
    async def start_qr_login(self, user_id: int) -> str:
        """Начинает процесс QR-логина и возвращает URL."""
        client = await self.create_client(user_id)
        
        # client.qr_login() возвращает QRLogin object
        qr_login_data = await client.qr_login() 
        url = qr_login_data.url
        
        # Сохраняем сессию QR и клиента в store.temp_data для check_qr_login
        # Используем store.temp_data, т.к. handlers.py ожидает данные именно там
        self.store.temp_data[user_id] = {
            'qr_login_data': qr_login_data, 
            'client': client
        }
        
        # Возвращаем только URL. Генерация изображения происходит в handlers.py
        return url

    async def check_qr_login(self, user_id: int, qr_login_data, client):
        """Проверяет статус QR-логина."""
        try:
            # Ожидаем завершения аутентификации (внутри этого метода Telethon ждет скана)
            await qr_login_data.wait() 
            
            if await client.is_user_authorized():
                # Успешный вход
                await self.finalize_auth(user_id, client)
                return True, "✅ Успешный вход через QR-код!"
            
            # Если не авторизован, но wait() прошел, это может быть 2FA
            return False, "⚠️ Требуется пароль 2FA."
            
        except asyncio.CancelledError:
            # Если отменено по таймауту или вручную
            return False, "❌ QR-авторизация отменена или истек таймаут."
        except Exception as e:
            error_str = str(e)
            if "Password required" in error_str:
                return False, "⚠️ Требуется пароль 2FA."
            elif "not yet logged in" in error_str:
                return False, "❌ QR-код не был отсканирован или авторизация отклонена."
            
            logger.error(f"Check QR Login error for {user_id}: {e}")
            return False, f"❌ Ошибка QR-логина: {e}"
        finally:
             # Очищаем временные данные, даже если был сбой
            if user_id in self.store.temp_data:
                del self.store.temp_data[user_id]

    # ----------------------------------------------------
    # 🏃 Worker Logic (Обработчик событий)
    # ----------------------------------------------------

    def register_handlers(self, client: TelegramClient, user_id: int):
        """Регистрирует обработчики для исходящих сообщений Telethon."""
        
        # Создаем обработчик для конкретного клиента
        @client.on(events.NewMessage(outgoing=True, pattern=r'^\.(.+)'))
        async def handle_outgoing_commands(event):
            # Извлекаем текст команды без точки
            full_command = event.pattern_match.group(1).strip()
            command, *args = full_command.split(maxsplit=1)
            args_str = args[0] if args else ""
            
            # --- Логика команд Worker'а ---
            
            if command == 'пкстарт':
                # .пкстарт <НазваниеПК>
                if not args_str:
                    return await event.reply("❌ Укажите имя ПК: `.пкстарт PC1`")
                
                pc_name = args_str.strip()
                response = await self.process_pc_start(user_id, event.message, pc_name)
                await event.reply(response)
            
            elif command == 'стопфлуд':
                # Логика остановки флуда (вам нужно реализовать ее в self.store)
                # ... (ваша логика остановки)
                await event.reply("🛑 Все активные задачи остановлены (если были).")

            # --- Добавьте здесь логику для .флуд, .лс, .чекгруппу и т.д. ---
            # ...
            
            else:
                # Если команда не найдена, не отвечаем, чтобы не засорять чат
                pass
        
        # Возвращаем функцию, чтобы ее можно было отменить (хотя здесь она регистрируется)
        return handle_outgoing_commands

    async def start_client_task(self, user_id: int) -> bool:
        """Запускает клиент Telethon и его обработчики в фоновом режиме."""
        if user_id in self.store.active_workers:
            logger.warning(f"Worker for {user_id} already running.")
            return True

        client = self.sessions.get(user_id)
        if not client or not await client.is_user_authorized():
            logger.error(f"Cannot start worker for {user_id}: client not authorized.")
            return False

        try:
            # Запускаем клиента и регистрируем обработчики
            self.register_handlers(client, user_id)
            await client.start() # Это запускает клиент в фоновом режиме
            
            # Сохраняем задачу (например, для контроля)
            self.store.active_workers[user_id] = client # Храним сам клиент
            return True
        except Exception as e:
            logger.error(f"Failed to start Telethon worker for {user_id}: {e}")
            return False

    async def stop_worker(self, user_id: int, delete_session: bool = False):
        """Останавливает клиент и удаляет сессию при необходимости."""
        
        # 1. Останавливаем клиент Telethon
        client = self.sessions.pop(user_id, None)
        if client:
            try:
                await client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting client {user_id}: {e}")
        
        # 2. Удаляем из активных задач
        self.store.active_workers.pop(user_id, None)
        
        # 3. Обновляем БД
        await self.db.update_user(user_id, telethon_active=0)
        
        # 4. Удаляем файл сессии, если нужно
        if delete_session:
            session_path = self.get_session_path(user_id)
            try:
                os.remove(f"{session_path}.session")
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.error(f"Error deleting session file for {user_id}: {e}")

    # ----------------------------------------------------
    # 🖥️ Drop/ПК-Logic
    # ----------------------------------------------------
    
    # 💡 ВАЖНО: Убедитесь, что этот метод использует вашу БД и GlobalStorage
    async def process_pc_start(self, user_id: int, message_obj, pc_name: str) -> str:
        """
        Привязывает название ПК к текущему чату/топику.
        Вызывается из обработчика исходящих сообщений Telethon.
        """
        chat_id = message_obj.chat_id
        # Проверяем, что это не обычный чат, а топик (если применимо)
        topic_id = message_obj.reply_to_msg_id if message_obj.reply_to_msg_id != None else 0
        
        # Ключ: (chat_id, topic_id)
        key = (chat_id, topic_id)
        
        # Сохраняем привязку в глобальном хранилище
        self.store.drop_mapping[key] = pc_name
        
        # Сохраняем привязку в БД (если нужно, чтобы она сохранилась после перезапуска)
        await self.db.update_chat_pc_mapping(chat_id, topic_id, pc_name, user_id)
        
        return f"✅ ПК **{pc_name}** привязан к этому чату/топику! Дропы могут использовать команды /numb, /vstal и т.д. в этом чате."
