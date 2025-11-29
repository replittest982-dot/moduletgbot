import asyncio
import logging
import os
import shutil
import time
from telethon import TelegramClient, events
from telethon.tl.types import PeerUser
from telethon.errors import SessionPasswordNeededError, FloodWaitError, AuthKeyUnregisteredError
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)

class TelethonManager:
    def __init__(self, db, config):
        self.db = db
        self.config = config
        self.API_ID = config.API_ID
        self.API_HASH = config.API_HASH
        self.TEMP_DIR = config.TEMP_DIR
        self.store = self.GlobalStore()

        os.makedirs(self.TEMP_DIR, exist_ok=True)
        
    class GlobalStore:
        def __init__(self):
            self.active_workers: dict[int, TelegramClient] = {} 
            self.temp_data: dict[int, dict] = {} 
            self.process_progress: dict[int, str] = {}
            self.drop_mapping: dict[str, int] = {}
            
    async def create_client(self, user_id: int, session_file: str = None) -> TelegramClient:
        # ... (логика создания клиента) ...
        if user_id in self.store.active_workers:
            # ... (проверка подключения) ...
            pass

        if session_file is None:
            session_file = os.path.join(self.TEMP_DIR, f"session_{user_id}")
            
        client = TelegramClient(session_file, self.API_ID, self.API_HASH, 
                                device_model='AiogramBotWorker', system_version='1.0')
        self.store.active_workers[user_id] = client
        return client

    async def register_handlers(self, client: TelegramClient, user_id: int):
        # ... (Ваши хендлеры Telethon) ...
        pass

    async def send_code(self, user_id: int, phone: str) -> str:
        # ... (логика send_code) ...
        pass

    async def sign_in(self, user_id: int, code: str) -> tuple[bool, str, str | None]:
        # ... (логика sign_in) ...
        pass

    async def sign_in_password(self, user_id: int, password: str) -> tuple[bool, str]:
        # ... (логика sign_in_password) ...
        pass

    async def start_qr_login(self, user_id: int) -> str:
        # ... (логика start_qr_login) ...
        pass

    async def check_qr_login(self, user_id: int, qr_data, client: TelegramClient) -> tuple[bool, str]:
        # ... (логика check_qr_login) ...
        pass

    # ✅ ИСПРАВЛЕНИЕ 1: Полный блок try/except
    async def start_client_task(self, user_id: int) -> bool:
        try:
            user_data = await self.db.get_user(user_id)
            session_str = user_data.get('session_str')
            if not session_str:
                return False
                
            client = await self.create_client(user_id, session_file=StringSession(session_str))
            
            await client.connect()
            if not await client.is_user_authorized():
                 raise AuthKeyUnregisteredError('Session expired.')
            
            await self.register_handlers(client, user_id)
            client.start()
            
            await self.db.update_user(user_id, telethon_active=2) # 2 = running
            return True
            
        except AuthKeyUnregisteredError:
            await self.stop_worker(user_id, delete_session=True)
            await self.db.update_user(user_id, telethon_active=0)
            return False
        except Exception as e: 
            logger.error(f"Start client error {user_id}: {e}", exc_info=True)
            await self.stop_worker(user_id, delete_session=True)
            return False

    # ✅ ИСПРАВЛЕНИЕ 7: Переопределена полная логика stop_worker
    async def stop_worker(self, user_id: int, delete_session: bool = False):
        if user_id in self.store.active_workers:
            client = self.store.active_workers[user_id]
            
            try:
                if await client.is_connected():
                    await client.disconnect()
            except Exception:
                pass 

            del self.store.active_workers[user_id]
            
            self.store.temp_data.pop(user_id, None) 
            self.store.process_progress.pop(user_id, None)

            if delete_session:
                session_file = os.path.join(self.TEMP_DIR, f"session_{user_id}.session")
                if os.path.exists(session_file):
                    os.remove(session_file)
                await self.db.update_user(user_id, telethon_active=0, session_str=None)
            else:
                 await self.db.update_user(user_id, telethon_active=1) 
        else:
             if delete_session:
                 await self.db.update_user(user_id, telethon_active=0, session_str=None)

    async def process_pc_start(self, user_id: int, message_obj, pc_name: str) -> str:
        # message_obj: aiogram.types.Message
        chat_id = message_obj.chat.id
        self.store.drop_mapping[pc_name] = chat_id
        await self.db.update_drop_session(user_id, pc_name, phone=None) 
        return f"✅ ПК **{pc_name}** привязан к чату **{chat_id}**"
