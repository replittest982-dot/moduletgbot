import asyncio
import logging
import os
import re
import random
import string
import traceback
from datetime import datetime
from typing import Dict, Optional, List, Union, Any
from contextlib import suppress

# --- AIOGRAM ---
from aiogram import Bot, types
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

# --- TELETHON ---
from telethon import TelegramClient, events
from telethon.tl.types import User, Channel, Chat
from telethon.errors import (
    FloodWaitError, SessionPasswordNeededError, PhoneNumberInvalidError, 
    AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedBanError
)

# --- LOCAL IMPORTS ---
from config import API_ID, API_HASH
# from db import AsyncDatabase # Импорт базы данных будет через __init__

logger = logging.getLogger(__name__)

SESSION_DIR = 'sessions'
tm = None # Глобальная переменная для доступа к менеджеру из db.py

# =========================================================================
# I. ГЛОБАЛЬНОЕ ХРАНИЛИЩЕ И FSM-СОСТОЯНИЯ TELETHON
# =========================================================================

class GlobalStorage:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.temp_auth_clients: Dict[int, TelegramClient] = {} 
        self.process_progress: Dict[int, Dict] = {}
        self.active_workers: Dict[int, TelegramClient] = {} 
        self.worker_tasks: Dict[int, List[asyncio.Task]] = {} 
        self.last_user_request: Dict[int, datetime] = {}

# --- FSM States for Telethon Auth ---
class TelethonAuth(types.StatesGroup):
    PHONE = types.State() 
    CODE = types.State()  
    PASSWORD = types.State() 
    QR_WAIT = types.State()

# =========================================================================
# II. TELETHON MANAGER
# =========================================================================

class TelethonManager:
    def __init__(self, bot_instance: Bot, store: GlobalStorage, db_instance):
        global tm
        self.bot = bot_instance
        self.store = store
        self.db = db_instance
        tm = self # Установка глобальной ссылки
        self.API_ID = API_ID
        self.API_HASH = API_HASH
    
    async def _send_to_bot_user(self, user_id, message):
        try:
            await self.bot.send_message(user_id, message, disable_notification=False)
        except (TelegramForbiddenError, TelegramBadRequest):
            logger.error(f"Cannot send message to {user_id}. Stopping worker.")
            await self.stop_worker(user_id, silent=True)
        except Exception as e:
            logger.error(f"Unknown error sending message to {user_id}: {e}")

    async def _finalize_auth(self, user_id: int, original_message: Message, state: FSMContext, user_info: Union[User, Channel, Chat]):
        """Завершение авторизации."""
        temp_path = os.path.join(SESSION_DIR, f'temp_{user_id}.session')
        final_path = os.path.join(SESSION_DIR, f'session_{user_id}.session')
        
        # Переименование временного файла сессии
        if await asyncio.to_thread(os.path.exists, temp_path):
            await asyncio.to_thread(os.rename, temp_path, final_path)
        
        # Отключение и удаление временного клиента
        if client := self.store.temp_auth_clients.pop(user_id, None):
            if client.is_connected(): await client.disconnect()

        await state.clear()
        
        name = getattr(user_info, 'first_name', 'Аккаунт')
        await original_message.answer(f"🎉 **Успешная авторизация!** Аккаунт **{name}** привязан.")
        
        # Запуск воркера (если есть подписка)
        if await self.db.check_subscription(user_id):
            await self.start_client_task(user_id)
        
        # Импорт функции обновления меню из handlers
        from handlers import update_menu_after_action
        # message.message_id будет None, если это первое сообщение, 
        # поэтому передаем объект message, который имеет chat.id
        await update_menu_after_action(user_id, state, self.bot, callback_data="worker_menu", edit_message=original_message)


    async def start_client_task(self, user_id):
        if not await self.db.check_subscription(user_id):
            await self._send_to_bot_user(user_id, "⚠️ **Ваша подписка истекла.** Аккаунт не запущен.")
            return

        session_exists = await asyncio.to_thread(os.path.exists, os.path.join(SESSION_DIR, f'session_{user_id}.session'))
        if not session_exists:
            # Не сообщаем об ошибке, если сессия удалена/не существует, 
            # так как пользователь перенаправится на авторизацию.
            await self.db.set_telethon_status(user_id, False)
            return

        await self.stop_worker(user_id)
        
        task = asyncio.create_task(self._run_worker(user_id))
        async with self.store.lock:
            # Очищаем старые задачи
            self.store.worker_tasks.pop(user_id, None)
            self.store.worker_tasks.setdefault(user_id, []).append(task)
        return task

    async def _run_worker(self, user_id):
        path = os.path.join(SESSION_DIR, f'session_{user_id}')
        client = TelegramClient(path, API_ID, API_HASH, device_model="StatPro Worker", flood_sleep_threshold=15)
        
        async with self.store.lock:
            self.store.active_workers[user_id] = client

        @client.on(events.NewMessage(outgoing=True))
        async def handler(event):
            await self.worker_message_handler(user_id, client, event)

        try:
            await client.start(phone=None) 
            me = await client.get_me() 
            await self.db.set_telethon_status(user_id, True)
            await self._send_to_bot_user(user_id, f"🚀 Аккаунт **{me.first_name}** запущен и готов к работе.")
            await client.run_until_disconnected()

        except (AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedBanError):
            await self._send_to_bot_user(user_id, "⚠️ Сессия недействительна. Требуется повторная авторизация.")
            session_file = os.path.join(SESSION_DIR, f'session_{user_id}.session')
            if await asyncio.to_thread(os.path.exists, session_file):
                try: await asyncio.to_thread(os.remove, session_file)
                except: pass
        except Exception as e:
            logger.critical(f"Worker {user_id} failed: {e}", exc_info=True)
            await self._send_to_bot_user(user_id, f"💔 Аккаунт отключился: `{e.__class__.__name__}`.")
        finally:
            await self.stop_worker(user_id, silent=True) 
            await self.db.set_telethon_status(user_id, False)

    async def stop_worker(self, user_id, silent=False):
        async with self.store.lock:
            client = self.store.active_workers.pop(user_id, None)
            tasks = self.store.worker_tasks.pop(user_id, [])
            for t in tasks:
                if not t.done(): t.cancel()

        if client:
            try:
                if client.is_connected(): await client.disconnect()
                if not silent: await self._send_to_bot_user(user_id, "🛑 Аккаунт остановлен.")
            except Exception as e:
                logger.warning(f"Error disconnecting {user_id}: {e}")

        await self.db.set_telethon_status(user_id, False)

    async def worker_message_handler(self, user_id, client, event):
        if not event.text or not event.text.startswith('.'): return
        msg = event.text.strip()
        parts = msg.split()
        cmd = parts[0].lower()
        chat = event.chat_id
        
        # Удаляем команду, если возможно
        with suppress(): await event.delete() 

        if cmd == '.флуд':
            try:
                if len(parts) < 3: 
                    temp = await client.send_message(chat, "⚠️ Формат: `.флуд [кол-во] [текст] [задержка]`", reply_to=event.message.id)
                    await asyncio.sleep(2)
                    with suppress(): await temp.delete()
                    return
                
                count = int(parts[1])
                delay_str = parts[-1]
                if delay_str.replace('.', '', 1).isdigit():
                    delay = max(0.5, float(delay_str)) 
                    text = " ".join(parts[2:-1])
                else:
                    delay = 0.5
                    text = " ".join(parts[2:])
                
                async with self.store.lock:
                    if self.store.process_progress.get(user_id, {}).get('type') == 'flood':
                        temp = await client.send_message(chat, "⚠️ Флуд уже активен. `.стопфлуд`")
                        await asyncio.sleep(2)
                        with suppress(): await temp.delete()
                        return
                        
                    self.store.process_progress[user_id] = {'type': 'flood', 'stop': False}
                
                task = asyncio.create_task(self._flood_task(client, chat, text, count, delay, user_id))
                async with self.store.lock:
                    self.store.worker_tasks.setdefault(user_id, []).append(task)

                temp = await client.send_message(chat, f"🚀 Флуд запущен: {count} шт, {delay}с.")
                await asyncio.sleep(2)
                with suppress(): await temp.delete()
                
            except Exception as e:
                temp = await client.send_message(chat, f"❌ Ошибка: `{e.__class__.__name__}`")
                await asyncio.sleep(2)
                with suppress(): await temp.delete()
        
        elif cmd == '.стопфлуд':
            async with self.store.lock:
                if self.store.process_progress.get(user_id, {}).get('type') == 'flood':
                    self.store.process_progress[user_id]['stop'] = True
                    temp = await client.send_message(chat, "🛑 Флуд остановлен.")
                    await asyncio.sleep(2)
                    with suppress(): await temp.delete()
                else:
                    temp = await client.send_message(chat, "⚠️ Нет активного флуда.")
                    await asyncio.sleep(2)
                    with suppress(): await temp.delete()

    async def _flood_task(self, client, chat, text, count, delay, user_id):
        i = 0
        # count=0 для бесконечного флуда
        max_limit = 5000 if count == 0 else count 
        
        while i < max_limit: 
            async with self.store.lock: 
                if self.store.process_progress.get(user_id, {}).get('stop'): break
            try:
                await client.send_message(chat, text)
                i += 1
                await asyncio.sleep(delay)
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + random.randint(1, 5)) 
            except Exception:
                break
        
        # Очистка прогресса после завершения
        async with self.store.lock:
            self.store.process_progress.pop(user_id, None)
