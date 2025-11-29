import asyncio
import logging
import os
import re
import random
import string
import traceback
import io
from datetime import datetime
from typing import Dict, Optional, List, Union, Any
from contextlib import suppress

# --- AIOGRAM ---
from aiogram import Bot, types
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InputFile
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

# --- TELETHON ---
from telethon import TelegramClient, events
from telethon.tl.types import User, Channel, Chat, InputPeerUser
from telethon.errors import (
    FloodWaitError, SessionPasswordNeededError, PhoneNumberInvalidError, 
    AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedBanError,
    PasswordHashInvalidError
)

# --- QR Code ---
import qrcode
from PIL import Image

# --- LOCAL IMPORTS ---
from config import API_ID, API_HASH
# from db import AsyncDatabase # Импорт базы данных будет через __init__

logger = logging.getLogger(__name__)

SESSION_DIR = 'sessions'
tm: Optional['TelethonManager'] = None # Глобальная переменная

# =========================================================================
# I. ГЛОБАЛЬНОЕ ХРАНИЛИЩЕ И FSM-СОСТОЯНИЯ TELETHON
# =========================================================================

class GlobalStorage:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.temp_auth_clients: Dict[int, TelegramClient] = {} 
        # process_progress: {user_id: {'type': 'flood'/'checkgroup', 'stop': bool, 'report_data': dict, ...}}
        self.process_progress: Dict[int, Dict] = {} 
        self.active_workers: Dict[int, TelegramClient] = {} 
        # worker_tasks: {user_id: {'flood_xxx': Task, 'checkgroup_yyy': Task, ...}}
        self.worker_tasks: Dict[int, Dict[str, asyncio.Task]] = {} 
        self.last_user_request: Dict[int, datetime] = {}
        # Хранилище для QR-объектов Telethon
        self.qr_login_objects: Dict[int, Any] = {} 

# --- FSM States for Telethon Auth (Duplicated in handlers.py for visibility) ---
class TelethonAuth(types.StatesGroup):
    PHONE = types.State() 
    CODE = types.State()  
    PASSWORD = types.State() 
    QR_WAIT = types.State()

# =========================================================================
# II. TELETHON MANAGER
# =========================================================================

class TelethonManager:
    def __init__(self, bot_instance: Bot, store: GlobalStorage, db_instance: Any):
        global tm
        self.bot = bot_instance
        self.store = store
        self.db = db_instance
        tm = self 
        self.API_ID = API_ID
        self.API_HASH = API_HASH
        self.client = lambda path, api_id, api_hash: TelegramClient(path, api_id, api_hash, device_model="StatPro Worker", flood_sleep_threshold=15)
        self.QR_TIMEOUT = 120 # 2 минуты

    async def _send_to_bot_user(self, user_id, message, reply_markup=None, photo_buffer=None):
        try:
            if photo_buffer:
                # Отправка изображения (QR)
                photo_buffer.seek(0)
                await self.bot.send_photo(user_id, photo_buffer, caption=message, reply_markup=reply_markup)
            else:
                await self.bot.send_message(user_id, message, disable_notification=False, reply_markup=reply_markup)
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
        with suppress(FileNotFoundError):
            await asyncio.to_thread(os.rename, temp_path, final_path)
        
        # Отключение и удаление временного клиента/QR-объекта
        if client := self.store.temp_auth_clients.pop(user_id, None):
            if client.is_connected(): await client.disconnect()
        self.store.qr_login_objects.pop(user_id, None)

        await state.clear()
        
        name = getattr(user_info, 'first_name', 'Аккаунт')
        await original_message.answer(f"🎉 **Успешная авторизация!** Аккаунт **{name}** привязан.")
        
        # Запуск воркера (если есть подписка)
        if await self.db.check_subscription(user_id):
            await self.start_client_task(user_id)
        
        # Импорт функции обновления меню из handlers
        from handlers import update_menu_after_action
        await update_menu_after_action(user_id, state, self.bot, callback_data="worker_menu", edit_message=original_message)

    async def _run_worker(self, user_id):
        # ... (логика запуска воркера)
        path = os.path.join(SESSION_DIR, f'session_{user_id}')
        client = self.client(path, self.API_ID, self.API_HASH)
        
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
            await self.delete_session_file(user_id)
        except Exception as e:
            logger.critical(f"Worker {user_id} failed: {e}", exc_info=True)
            await self._send_to_bot_user(user_id, f"💔 Аккаунт отключился: `{e.__class__.__name__}`.")
        finally:
            await self.stop_worker(user_id, silent=True) 
            await self.db.set_telethon_status(user_id, False)

    async def delete_session_file(self, user_id: int):
        """Останавливает воркер и удаляет все файлы сессии."""
        await self.stop_worker(user_id, silent=True)
        session_file = os.path.join(SESSION_DIR, f'session_{user_id}.session')
        temp_file = os.path.join(SESSION_DIR, f'temp_{user_id}.session')
        with suppress(FileNotFoundError):
            await asyncio.to_thread(os.remove, session_file)
            await asyncio.to_thread(os.remove, temp_file)
        await self.db.set_telethon_status(user_id, False)

    async def stop_worker(self, user_id, silent=False):
        # ... (логика остановки)
        async with self.store.lock:
            client = self.store.active_workers.pop(user_id, None)
            tasks = self.store.worker_tasks.pop(user_id, {})
            # Отменяем все активные задачи (флуд, сканирование)
            for task_key, t in tasks.items():
                if not t.done(): t.cancel()
            
            # Также очищаем progress, если задача была остановлена извне
            self.store.process_progress.pop(user_id, None)

        if client:
            try:
                if client.is_connected(): await client.disconnect()
                if not silent: await self._send_to_bot_user(user_id, "🛑 Аккаунт остановлен.")
            except Exception as e:
                logger.warning(f"Error disconnecting {user_id}: {e}")

        await self.db.set_telethon_status(user_id, False)
        
    async def stop_task(self, user_id: int, task_key: str):
        """Останавливает конкретную задачу (например, флуд или чекгруппу)."""
        async with self.store.lock:
            task = self.store.worker_tasks.get(user_id, {}).pop(task_key, None)
            if task and not task.done():
                task.cancel()
            # Очищаем progress
            self.store.process_progress.pop(user_id, None)


    # =========================================================================
    # III. WORKER MESSAGE HANDLER (COMMANDS: .флуд, .стопфлуд, .лс, .статус)
    # =========================================================================

    async def worker_message_handler(self, user_id, client, event):
        if not event.text or not event.text.startswith('.'): return
        msg = event.text.strip()
        parts = msg.split()
        cmd = parts[0].lower()
        chat = event.chat_id
        
        # --- ПРОВЕРКА ПОДПИСКИ ---
        if not await self.db.check_subscription(user_id):
            temp = await client.send_message(chat, "⚠️ **Нет активной подписки.** Активируйте ее в ЛС бота.", reply_to=event.message.id)
            with suppress(): await asyncio.gather(event.delete(), asyncio.sleep(3), temp.delete())
            return
            
        with suppress(): await event.delete() 

        # --- .ФЛУД ---
        if cmd == '.флуд':
            # ... (логика флуда) ...
            try:
                if len(parts) < 3: 
                    temp = await client.send_message(chat, "⚠️ Формат: `.флуд [кол-во] [текст] [задержка]`", reply_to=event.message.id)
                    with suppress(): await asyncio.gather(asyncio.sleep(2), temp.delete())
                    return
                
                count = int(parts[1])
                delay_str = parts[-1]
                if delay_str.replace('.', '', 1).isdigit():
                    delay = max(0.5, float(delay_str)) 
                    text = " ".join(parts[2:-1])
                else:
                    delay = 0.5
                    text = " ".join(parts[2:])
                
                task_key = f'flood_{random.randint(1000, 9999)}'
                
                async with self.store.lock:
                    if any(key.startswith('flood_') for key in self.store.worker_tasks.get(user_id, {})):
                        temp = await client.send_message(chat, "⚠️ Флуд уже активен. `.стопфлуд`")
                        with suppress(): await asyncio.gather(asyncio.sleep(2), temp.delete())
                        return
                        
                    self.store.process_progress[user_id] = {'type': 'flood', 'stop': False, 'count_limit': count, 'current_count': 0, 'chat_id': chat}
                
                task = asyncio.create_task(self._flood_task(client, chat, text, count, delay, user_id, task_key))
                async with self.store.lock:
                    self.store.worker_tasks.setdefault(user_id, {})[task_key] = task

                temp = await client.send_message(chat, f"🚀 Флуд запущен: {count} шт, {delay}с.")
                with suppress(): await asyncio.gather(asyncio.sleep(2), temp.delete())
                
            except Exception as e:
                temp = await client.send_message(chat, f"❌ Ошибка: `{e.__class__.__name__}`")
                with suppress(): await asyncio.gather(asyncio.sleep(2), temp.delete())

        # --- .СТОПФЛУД ---
        elif cmd == '.стопфлуд':
            async with self.store.lock:
                flood_tasks = {key: task for key, task in self.store.worker_tasks.get(user_id, {}).items() if key.startswith('flood_')}
                if not flood_tasks:
                    temp = await client.send_message(chat, "⚠️ Нет активного флуда.")
                    with suppress(): await asyncio.gather(asyncio.sleep(2), temp.delete())
                    return
                
                # Отменяем все задачи флуда
                for key, task in flood_tasks.items():
                    if not task.done(): task.cancel()
                    self.store.worker_tasks[user_id].pop(key, None)
                self.store.process_progress.pop(user_id, None)
                
                temp = await client.send_message(chat, "🛑 Флуд остановлен.")
                with suppress(): await asyncio.gather(asyncio.sleep(2), temp.delete())

        # --- .ЧЕКГРУППУ ---
        elif cmd == '.чекгруппу':
            # Отправляем в ЛС бота меню выбора диапазона
            from handlers import get_check_group_menu_keyboard
            await self._send_to_bot_user(user_id, "📊 **Выберите диапазон сканирования:**", reply_markup=get_check_group_menu_keyboard(chat))
            temp = await client.send_message(chat, "➡️ Отправлено меню сканирования в ЛС бота.", reply_to=event.message.id)
            with suppress(): await asyncio.gather(asyncio.sleep(3), temp.delete())

        # --- .СТАТУС ---
        elif cmd == '.статус':
            progress = self.store.process_progress.get(user_id)
            if not progress:
                status_text = "✅ **Активных задач нет.**"
            else:
                p_type = progress['type']
                if p_type == 'flood':
                    limit_str = f"/{progress['count_limit']}" if progress['count_limit'] > 0 else " (∞)"
                    status_text = f"📢 **Флуд активен:** {progress['current_count']}{limit_str} сообщений."
                elif p_type == 'checkgroup':
                    min_u = progress.get('min_users', 0)
                    max_u = progress.get('max_users', '∞')
                    msg_count = progress.get('processed_messages', 0)
                    status_text = (
                        f"🔄 **Сканирование активно** (Чекгруппа)\n"
                        f"📈 Сообщений: `{msg_count}`\n"
                        f"🎯 Диапазон: `{min_u}`-`{max_u}`"
                    )
                else:
                    status_text = f"⚙️ **Активная задача:** `{p_type}`."
            
            temp = await client.send_message(chat, status_text)
            with suppress(): await asyncio.gather(asyncio.sleep(5), temp.delete())


    async def _flood_task(self, client, chat, text, count, delay, user_id, task_key):
        i = 0
        max_limit = 5000 if count <= 0 else count 
        
        while i < max_limit: 
            async with self.store.lock: 
                # Проверка на отмену (например, командой .стопфлуд или остановкой воркера)
                if self.store.process_progress.get(user_id, {}).get('stop', False): break
            
            try:
                await client.send_message(chat, text)
                i += 1
                async with self.store.lock:
                    self.store.process_progress.get(user_id, {})['current_count'] = i
                
                await asyncio.sleep(delay)
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + random.randint(1, 5)) 
            except asyncio.CancelledError:
                break
            except Exception:
                break
        
        # Очистка прогресса и задачи после завершения
        async with self.store.lock:
            self.store.process_progress.pop(user_id, None)
            self.store.worker_tasks.get(user_id, {}).pop(task_key, None)


    # =========================================================================
    # IV. АСИНХРОННЫЕ ЗАДАЧИ (ЧЕКГРУППУ)
    # =========================================================================
    
    async def check_group_task(self, client, chat_id, min_users, max_users, user_id, task_key, original_message: Message):
        """Асинхронно сканирует группу на предмет уникальных пользователей."""
        users: Dict[int, Dict] = {}
        total_messages = 0
        
        try:
            chat = await client.get_entity(chat_id)
            
            # Обновление прогресса
            async def update_progress_periodically():
                while True:
                    await asyncio.sleep(5) 
                    async with self.store.lock:
                        if self.store.process_progress.get(user_id, {}).get('stop'): return 
                        if not self.store.process_progress.get(user_id): return
                        self.store.process_progress[user_id]['user_count'] = len(users)

            progress_task = asyncio.create_task(update_progress_periodically())

            # Динамический лимит для iter_messages, чтобы не загружать слишком много
            async for message in client.iter_messages(chat, limit=100000):
                total_messages += 1
                
                async with self.store.lock:
                    if self.store.process_progress.get(user_id, {}).get('stop'): break
                
                # Достигли лимита
                if max_users is not None and len(users) >= max_users: break
                
                if message.sender and isinstance(message.sender, User):
                    user_id_tg = message.sender.id
                    if user_id_tg not in users:
                        users[user_id_tg] = {
                            'id': user_id_tg,
                            'username': message.sender.username,
                            'first_name': message.sender.first_name
                        }
                        
            progress_task.cancel() # Отменяем фоновое обновление прогресса
            
            # Проверка, был ли лимит достигнут или сканирование отменено
            if len(users) < min_users:
                 result_text = f"⚠️ Сканирование завершено, но найдено только `{len(users)}` пользователей (минимум `{min_users}`)."
            else:
                 result_text = "✅ **Готово!**"

            # Сохраняем результат
            async with self.store.lock:
                self.store.process_progress[user_id].update({
                    'report_data': users,
                    'peer_name': chat.title or str(chat.id),
                    'count': len(users),
                    'scanned_messages': total_messages
                })
                self.store.process_progress[user_id]['user_count'] = len(users) # Финальный подсчет

            from handlers import get_report_keyboard
            
            await self._send_to_bot_user(user_id,
                f"{result_text}\n"
                f"📊 Найдено: `{len(users)}` пользователей\n"
                f"📄 Просканировано: `{total_messages}` сообщений",
                reply_markup=get_report_keyboard(task_key, user_id)
            )
            
            # Удаляем задачу после успешного завершения
            async with self.store.lock:
                 self.store.worker_tasks.get(user_id, {}).pop(task_key, None)
                 
        except asyncio.CancelledError:
            with suppress(): progress_task.cancel()
            await self._send_to_bot_user(user_id, "🛑 **Сканирование остановлено.**")
        except Exception as e:
            logger.error(f"Check Group Task Error for {user_id}: {e}", exc_info=True)
            await self._send_to_bot_user(user_id, f"❌ **Ошибка сканирования:** `{e.__class__.__name__}`.")
        finally:
            async with self.store.lock:
                # Очистка прогресса (если не было успешного завершения)
                if self.store.process_progress.get(user_id, {}).get('task_key') == task_key:
                    self.store.process_progress.pop(user_id, None)


    # =========================================================================
    # V. QR-CODE AUTHENTICATION
    # =========================================================================

    async def start_qr_login(self, user_id: int, state: FSMContext, original_message: Message):
        """Начинает процесс QR-авторизации."""
        temp_path = os.path.join(SESSION_DIR, f'temp_{user_id}')
        
        client = self.store.temp_auth_clients.get(user_id)
        if client and client.is_connected(): await client.disconnect()
        
        client = self.client(temp_path, self.API_ID, self.API_HASH)
        self.store.temp_auth_clients[user_id] = client
        
        await client.connect()

        try:
            qr_login = await client.request_qr_login()
            self.store.qr_login_objects[user_id] = qr_login
            await state.update_data(qr_object=qr_login, original_message=original_message)
            await state.set_state(TelethonAuth.QR_WAIT)
            
            # --- Генерация QR-кода ---
            qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
            qr.add_data(qr_login.url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white").convert('RGB')
            
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            buffer.seek(0)
            
            await self._send_to_bot_user(
                user_id,
                f"⏳ **Отсканируйте QR-код в течение {self.QR_TIMEOUT} секунд.**\n"
                f"Откройте Telegram → Настройки → Устройства → Привязать устройство.",
                photo_buffer=buffer
            )
            
            # Запускаем фоновую задачу ожидания сканирования
            task = asyncio.create_task(self._wait_for_qr_scan(user_id, state, original_message))
            self.store.worker_tasks.setdefault(user_id, {})['qr_wait'] = task
            
        except Exception as e:
            logger.error(f"QR Auth Error: {e}", exc_info=True)
            if client.is_connected(): await client.disconnect()
            await state.clear()
            await original_message.answer("❌ Ошибка запуска QR-авторизации.")

    async def _wait_for_qr_scan(self, user_id: int, state: FSMContext, original_message: Message):
        """Фоновая задача, которая ждет, пока пользователь отсканирует QR-код."""
        client = self.store.temp_auth_clients.get(user_id)
        qr_login = self.store.qr_login_objects.get(user_id)
        
        if not client or not qr_login: return

        try:
            # Ожидание логина (пока Telethon не получит ответ)
            user_info = await asyncio.wait_for(client.qr_login(qr_login), timeout=self.QR_TIMEOUT)
            
            # Проверка, нужен ли пароль (2FA)
            if isinstance(user_info, SessionPasswordNeededError):
                 await self.bot.send_message(user_id, "⚠️ **Введите облачный пароль (2FA):**")
                 await state.set_state(TelethonAuth.PASSWORD)
            else:
                # Успешный логин без 2FA
                await self._finalize_auth(user_id, original_message, state, user_info)
                
        except asyncio.TimeoutError:
            await self.bot.send_message(user_id, "❌ **Время QR-авторизации истекло.** Попробуйте снова.")
            await client.disconnect()
            await state.clear()
        except asyncio.CancelledError:
            # Задача была отменена (например, при выходе из меню)
            pass
        except Exception as e:
            logger.error(f"QR Scan Error: {e}", exc_info=True)
            await self.bot.send_message(user_id, f"❌ Непредвиденная ошибка при QR-логине: `{e.__class__.__name__}`")
            await client.disconnect()
        finally:
            self.store.qr_login_objects.pop(user_id, None)
            self.store.worker_tasks.get(user_id, {}).pop('qr_wait', None)
