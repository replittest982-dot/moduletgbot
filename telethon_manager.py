import asyncio
import logging
import os
import time

from telethon import TelegramClient, events
# Для удобства, уберем неиспользуемые импорты PeerX, чтобы не было ошибок, если они не определены
# from telethon.tl.types import PeerUser, PeerChannel, PeerChat 

# --- ВАЖНО: Замените на ваши реальные классы и константы ---
# from db import AsyncDatabase 
# from utils import GlobalStorage 
# from config import API_ID, API_HASH, TEMP_DIR # Предполагаем, что config импортируется в main.py
# --------------------------------------------------------

logger = logging.getLogger(__name__)

# --- Заглушки для типов (УДАЛИТЕ И ЗАМЕНИТЕ НА РЕАЛЬНЫЕ ИМПОРТЫ!) ---
class AsyncDatabase: pass
class GlobalStorage: 
    def __init__(self):
        self.active_workers = {}
        self.process_progress = {}
        self.temp_data = {}
        self.drop_mapping = {}
class Config: pass
# -----------------------------------------------------------------------


class TelethonManager:
    # 💡 ИСПРАВЛЕНИЕ: Конструктор принимает (db, store, config)
    def __init__(self, db: AsyncDatabase, store: GlobalStorage, config):
        self.db = db
        self.store = store
        self.config = config
        self.sessions = {}  # {user_id: TelethonClient}
        self.qr_sessions = {} # {user_id: (Client, QRLogin)}

        # 🚀 ИСПРАВЛЕНИЕ: Использует self.config
        if not os.path.exists(self.config.TEMP_DIR):
            os.makedirs(self.config.TEMP_DIR)

    # ----------------------------------------------------
    # ⚙️ Базовые методы клиента
    # ----------------------------------------------------

    def get_session_path(self, user_id: int):
        """Возвращает путь к файлу сессии."""
        return os.path.join(self.config.TEMP_DIR, f"user_{user_id}")

    async def create_client(self, user_id: int) -> TelegramClient:
        """Создает и подключает клиент."""
        if user_id in self.sessions:
            return self.sessions[user_id]
        
        session_path = self.get_session_path(user_id)
        
        client = TelegramClient(
            session_path, 
            self.config.API_ID, 
            self.config.API_HASH
        )
        
        if not client.is_connected():
            await client.connect()
            
        return client

    async def finalize_auth(self, user_id: int, client: TelegramClient):
        """Завершает аутентификацию, обновляет статус в БД и сохраняет клиента."""
        self.sessions[user_id] = client
        # 💡 Адаптируйте под вашу БД
        await self.db.update_user(user_id, telethon_active=1)
        await client.get_me() 
        
    # ----------------------------------------------------
    # 📱 Phone Login
    # ----------------------------------------------------

    async def send_code(self, user_id: int, phone: str) -> str:
        """Отправляет код подтверждения на телефон."""
        try:
            client = await self.create_client(user_id)
            sent_code = await client.send_code_request(phone)
            
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
            await client.sign_in(phone, code, password=None) 
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
    # 🖼️ QR Login
    # ----------------------------------------------------
    async def start_qr_login(self, user_id: int) -> str:
        """Начинает процесс QR-логина и возвращает URL."""
        client = await self.create_client(user_id)
        
        qr_login_data = await client.qr_login() 
        url = qr_login_data.url
        
        self.store.temp_data[user_id] = {
            'qr_login_data': qr_login_data, 
            'client': client
        }
        
        return url

    async def check_qr_login(self, user_id: int, qr_login_data, client):
        """Проверяет статус QR-логина."""
        try:
            await qr_login_data.wait() 
            
            if await client.is_user_authorized():
                await self.finalize_auth(user_id, client)
                return True, "✅ Успешный вход через QR-код!"
            
            return False, "⚠️ Требуется пароль 2FA."
            
        except asyncio.CancelledError:
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
            if user_id in self.store.temp_data:
                del self.store.temp_data[user_id]

    # ----------------------------------------------------
    # 🏃 Worker Logic (Обработчик событий)
    # ----------------------------------------------------

    def register_handlers(self, client: TelegramClient, user_id: int):
        """Регистрирует обработчики для исходящих сообщений Telethon."""
        
        @client.on(events.NewMessage(outgoing=True, pattern=r'^\.(.+)'))
        async def handle_outgoing_commands(event):
            full_command = event.pattern_match.group(1).strip()
            command, *args = full_command.split(maxsplit=1)
            args_str = args[0] if args else ""
            
            if command == 'пкстарт':
                if not args_str:
                    return await event.reply("❌ Укажите имя ПК: `.пкстарт PC1`")
                
                pc_name = args_str.strip()
                response = await self.process_pc_start(user_id, event.message, pc_name)
                await event.reply(response)
            
            elif command == 'стопфлуд':
                # ... (ваша логика остановки)
                await event.reply("🛑 Все активные задачи остановлены (если были).")
            
            # ... (Ваша логика для других команд) ...
        
        return handle_outgoing_commands

    async def start_client_task(self, user_id: int) -> bool:
        """Запускает клиент Telethon и его обработчики в фоновом режиме."""
        if user_id in self.store.active_workers:
            return True

        client = self.sessions.get(user_id)
        if not client:
             return False

        try:
            if not client.is_connected():
                 await client.connect()
            
            if not await client.is_user_authorized():
                logger.error(f"Cannot start worker for {user_id}: client not authorized.")
                return False
                
            self.register_handlers(client, user_id)
            # В Telethon 1.x и выше client.start() нужно вызвать один раз
            # Если вы используете client.start() в main.py, этот вызов может быть лишним
            # await client.start() 
            self.store.active_workers[user_id] = client
            return True
        except Exception as e:
            logger.error(f"Failed to start Telethon worker for {user_id}: {e}")
            return False

    async def stop_worker(self, user_id: int, delete_session: bool = False):
        """Останавливает клиент и удаляет сессию при необходимости."""
        
        client = self.sessions.pop(user_id, None)
        if client:
            try:
                await client.disconnect() 
            except Exception as e:
                logger.error(f"Error disconnecting client {user_id}: {e}")
        
        self.store.active_workers.pop(user_id, None)
        # 💡 Адаптируйте под вашу БД
        await self.db.update_user(user_id, telethon_active=0)
        
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
    
    async def process_pc_start(self, user_id: int, message_obj, pc_name: str) -> str:
        """Привязывает название ПК к текущему чату/топику."""
        chat_id = message_obj.chat_id
        topic_id = message_obj.reply_to_msg_id if message_obj.reply_to_msg_id != None else 0
        
        key = (chat_id, topic_id)
        
        self.store.drop_mapping[key] = pc_name
        
        
        # 💡 Адаптируйте под вашу БД
        await self.db.update_chat_pc_mapping(chat_id, topic_id, pc_name, user_id)
        
        return f"✅ ПК **{pc_name}** привязан к этому чату/топику!"
