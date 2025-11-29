import asyncio
import logging
import os
import shutil
import time
from telethon import TelegramClient, events
from telethon.tl.types import UserStatusOnline, PeerUser
from telethon.errors import SessionPasswordNeededError, FloodWaitError, AuthKeyUnregisteredError, PasswordHashInvalidError
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)

class TelethonManager:
    def __init__(self, db, config):
        self.db = db
        self.config = config
        self.API_ID = config.API_ID
        self.API_HASH = config.API_HASH
        self.TEMP_DIR = config.TEMP_DIR
        self.store = self.GlobalStore() # Инициализация store

        # ✅ ИСПРАВЛЕНИЕ 52: Создание временной директории
        os.makedirs(self.TEMP_DIR, exist_ok=True)
        
    class GlobalStore:
        """Хранилище для активных клиентов, временных данных и прогресса."""
        def __init__(self):
            self.active_workers: dict[int, TelegramClient] = {} # {user_id: client}
            self.temp_data: dict[int, dict] = {} # {user_id: {'phone':..., 'client':..., 'qr_login_data':...}}
            self.process_progress: dict[int, str] = {}
            self.drop_mapping: dict[str, int] = {} # {pc_name: chat_id}
            
    # --- Client Management ---
    
    async def create_client(self, user_id: int, session_file: str = None) -> TelegramClient:
        # ✅ ИСПРАВЛЕНИЕ 45: Проверка на существование клиента (для избежания 'event loop changed')
        if user_id in self.store.active_workers:
            client = self.store.active_workers[user_id]
            if await client.is_connected():
                return client
            # Если не подключен, дисконнектим и удаляем старый, чтобы создать новый
            await client.disconnect()
            del self.store.active_workers[user_id]
            
        if session_file is None:
            session_file = os.path.join(self.TEMP_DIR, f"session_{user_id}")
            
        client = TelegramClient(session_file, self.API_ID, self.API_HASH, 
                                device_model='AiogramBotWorker', system_version='1.0')
        self.store.active_workers[user_id] = client
        return client

    async def register_handlers(self, client: TelegramClient, user_id: int):
        # ✅ ИСПРАВЛЕНИЕ 44: Функция для регистрации хендлеров
        @client.on(events.NewMessage(chats=[PeerUser(user_id)])) # Пример, слушаем только сообщения от себя
        async def handler_test(event):
             if event.text and event.text.lower() == 'ping':
                 await event.reply('Pong!')
                 
        # ... (Добавьте сюда все ваши реальные хендлеры для сбора/дропа) ...
        pass


    # --- Auth Methods ---
    
    async def send_code(self, user_id: int, phone: str) -> str:
        client = await self.create_client(user_id)
        self.store.temp_data[user_id] = {'client': client, 'phone': phone}
        try:
            await client.connect()
            result = await client.send_code_request(phone)
            self.store.temp_data[user_id]['phone_code_hash'] = result.phone_code_hash
            return "Код отправлен."
        except FloodWaitError as e:
            # ✅ ИСПРАВЛЕНИЕ 47: Обработка FloodWait
            logger.warning(f"FloodWait on send_code for {user_id}: {e}")
            return f"❌ Превышен лимит запросов. Попробуйте через {e.seconds} секунд."
        except Exception as e:
            await self.stop_worker(user_id, delete_session=True)
            return f"❌ Ошибка отправки кода: {e}"

    async def sign_in(self, user_id: int, code: str) -> tuple[bool, str, str | None]:
        data = self.store.temp_data.get(user_id)
        if not data: return False, "❌ Сессия утеряна.", None
        client = data['client']
        
        try:
            await client.sign_in(data['phone'], code, phone_code_hash=data['phone_code_hash'])
            # ✅ ИСПРАВЛЕНИЕ 46: Проверка авторизации и сохранение сессии
            session_str = StringSession.save(client.session)
            await self.db.update_user(user_id, telethon_active=1, session_str=session_str)
            del self.store.temp_data[user_id]
            return True, "✅ Успешный вход!", None
        except SessionPasswordNeededError:
            return False, "⚠️ Требуется 2FA пароль.", None
        except FloodWaitError as e:
            return False, f"❌ Превышен лимит: {e.seconds}с.", None
        except Exception as e:
            # ✅ ИСПРАВЛЕНИЕ 48: Обработка других ошибок входа
            await self.stop_worker(user_id, delete_session=True)
            return False, f"❌ Ошибка входа: {e}", None

    async def sign_in_password(self, user_id: int, password: str) -> tuple[bool, str]:
        data = self.store.temp_data.get(user_id)
        if not data: return False, "❌ Сессия утеряна."
        client = data['client']

        try:
            await client.sign_in(password=password)
            # ✅ ИСПРАВЛЕНИЕ 46: Проверка авторизации и сохранение сессии
            session_str = StringSession.save(client.session)
            await self.db.update_user(user_id, telethon_active=1, session_str=session_str)
            del self.store.temp_data[user_id]
            return True, "✅ Успешный вход!"
        except (PasswordHashInvalidError, SessionPasswordNeededError):
            return False, "❌ Неверный 2FA пароль."
        except Exception as e:
            await self.stop_worker(user_id, delete_session=True)
            return False, f"❌ Ошибка: {e}"

    async def start_qr_login(self, user_id: int) -> str:
        client = await self.create_client(user_id)
        self.store.temp_data[user_id] = {'client': client}
        try:
            await client.connect()
            qr_login = await client.qr_login()
            self.store.temp_data[user_id]['qr_login_data'] = qr_login
            return qr_login.url
        except Exception as e:
            await self.stop_worker(user_id, delete_session=True)
            raise e

    async def check_qr_login(self, user_id: int, qr_data, client: TelegramClient) -> tuple[bool, str]:
        # ✅ ИСПРАВЛЕНИЕ 43: Добавление try/except для предотвращения сбоя
        try:
            await qr_data.wait()
            # Проверка, если требуется 2FA
            if await client.is_user_authorized():
                # Успех
                session_str = StringSession.save(client.session)
                await self.db.update_user(user_id, telethon_active=1, session_str=session_str)
                del self.store.temp_data[user_id]
                return True, "✅ Успешный вход по QR-коду!"
            else:
                # Требуется пароль (редкий случай для QR)
                return False, "⚠️ Требуется 2FA пароль."

        except FloodWaitError as e:
            return False, f"❌ Превышен лимит: {e.seconds}с."
        except Exception as e:
            return False, f"❌ Ошибка ожидания QR: {e}"

    # --- Worker Control ---
    
    async def start_client_task(self, user_id: int) -> bool:
        # ✅ ИСПРАВЛЕНИЕ 49: Правильная последовательность запуска
        try:
            user_data = await self.db.get_user(user_id)
            session_str = user_data.get('session_str')
            if not session_str:
                return False
                
            client = await self.create_client(user_id, session_file=StringSession(session_str))
            
            await client.connect()
            if not await client.is_user_authorized():
                 raise AuthKeyUnregisteredError('Session expired.')
            
            # 1. Регистрация хендлеров ДО запуска
            await self.register_handlers(client, user_id)
            
            # 2. Запуск фоновой задачи
            client.start()
            
            # 3. Сохранение клиента в активных (уже сделано в create_client)
            await self.db.update_user(user_id, telethon_active=2) # 2 = running
            return True
            
        except AuthKeyUnregisteredError:
            await self.stop_worker(user_id, delete_session=True)
            await self.db.update_user(user_id, telethon_active=0)
            return False
        except Exception as e:
            logger.error(f"Error starting client task for {user_id}: {e}", exc_info=True)
            await self.stop_worker(user_id, delete_session=True)
            return False

    async def stop_worker(self, user_id: int, delete_session: bool = False):
        # ✅ ИСПРАВЛЕНИЕ 50: Проверка на существование и подключение клиента
        if user_id in self.store.active_workers:
            client = self.store.active_workers[user_id]
            try:
                if client.is_connected(): # Проверка, что client.is_connected()
                    await client.disconnect()
            except Exception:
                pass # Игнорируем ошибки при отключении
            
            del self.store.active_workers[user_id]

        # ✅ ИСПРАВЛЕНИЕ 51: Очистка временных данных
        self.store.temp_data.pop(user_id, None) 
        self.store.process_progress.pop(user_id, None)

        if delete_session:
            # Обновление БД
            await self.db.update_user(user_id, telethon_active=0, session_str=None)
            # Удаление физического файла сессии
            session_file = os.path.join(self.TEMP_DIR, f"session_{user_id}.session")
            if os.path.exists(session_file):
                os.remove(session_file)
        else:
             await self.db.update_user(user_id, telethon_active=1) # 1 = ready

    # --- Drop Logic (Пример) ---
    # ✅ ИСПРАВЛЕНИЕ 41: Исправлено обращение к message_obj
    async def process_pc_start(self, user_id: int, message_obj: Message, pc_name: str) -> str:
        """Регистрирует chat_id для получения дропа по имени ПК."""
        # message_obj: aiogram.types.Message
        chat_id = message_obj.chat.id
        
        # Сохранение в память и БД
        self.store.drop_mapping[pc_name] = chat_id
        await self.db.update_drop_session(user_id, pc_name, phone=None) 
        
        return f"✅ ПК **{pc_name}** привязан к чату **{chat_id}**"
