import asyncio
import logging
import os
import random
import textwrap
from typing import Dict, Any, Optional, Tuple, List
from io import BytesIO

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, PhoneNumberInvalidError, AuthKeyUnregisteredError, ChannelPrivateError
from telethon.tl.types import User
import qrcode
from PIL import Image

# АБСОЛЮТНЫЕ ИМПОРТЫ
from config import API_ID, API_HASH, ADMIN_ID, QR_TIMEOUT, FLOOD_TASK_TIMEOUT
from utils import GlobalStorage, check_valid_phone 
from db import AsyncDatabase 

logger = logging.getLogger(__name__)

class TelethonManager:
    def __init__(self, bot, store: GlobalStorage, db: AsyncDatabase):
        self.bot = bot
        self.store = store
        self.db = db

    # --- Worker Management ---
    async def _start_worker(self, client: TelegramClient, user_id: int):
        client.add_event_handler(
            self._handle_outgoing_message, 
            events.NewMessage(outgoing=True)
        )
        task = asyncio.create_task(client.run_until_disconnected())
        self.store.active_workers[user_id] = task
        self.store.active_clients[user_id] = client
        await self.db.update_user(user_id, telethon_active=1)
        logger.info(f"Telethon worker started for {user_id}")
        return client.get_me()

    async def start_client_task(self, user_id: int) -> bool:
        if user_id in self.store.active_workers:
            logger.warning(f"Worker for {user_id} already running.")
            return True

        session_path = self.store.get_session_path(user_id)
        if not os.path.exists(session_path): return False

        client = TelegramClient(session_path, API_ID, API_HASH)
        
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise AuthKeyUnregisteredError("Session invalid")
                
            await self._start_worker(client, user_id)
            return True
        except AuthKeyUnregisteredError:
            logger.error(f"Session file found for {user_id} but not authorized.")
            await self.db.update_user(user_id, telethon_active=0)
            self.store.delete_session_file(user_id)
            return False
        except Exception as e:
            logger.error(f"Error starting client {user_id}: {e}")
            return False

    async def stop_worker(self, user_id: int, delete_session: bool = False):
        if user_id in self.store.active_workers:
            self.store.active_workers[user_id].cancel()
            del self.store.active_workers[user_id]

        if user_id in self.store.active_clients:
            client = self.store.active_clients.pop(user_id)
            await client.disconnect()
            
        if user_id in self.store.active_tasks:
            for task in self.store.active_tasks[user_id].values():
                task.cancel()
            del self.store.active_tasks[user_id]
            
        if user_id in self.store.process_progress:
            del self.store.process_progress[user_id]

        await self.db.update_user(user_id, telethon_active=0)
        
        if delete_session:
            self.store.delete_session_file(user_id)
            self.store.delete_session_file(user_id, temp=True)
            logger.info(f"Session deleted for {user_id}.")
        
        logger.info(f"Telethon worker stopped for {user_id}. Delete session: {delete_session}")
        
    async def stop_all_workers(self):
        tasks = [self.stop_worker(uid) for uid in list(self.store.active_workers.keys())]
        await asyncio.gather(*tasks)

    # --- Auth Logic ---
    async def _get_client(self, user_id: int, temp: bool = False) -> TelegramClient:
        session_path = self.store._get_session_path(user_id, temp)
        return TelegramClient(session_path, API_ID, API_HASH)

    async def finalize_login(self, user_id: int, client: TelegramClient):
        self.store.rename_session_file(user_id)
        await self._start_worker(client, user_id)
        self.store.delete_session_file(user_id, temp=True)
        self.store.temp_data.pop(user_id, None)
        
    async def send_code(self, user_id: int, phone: str) -> str:
        client = await self._get_client(user_id, temp=True)
        await client.connect()
        try:
            result = await client.send_code_request(phone)
            self.store.temp_data[user_id] = {'phone': phone, 'phone_code_hash': result.phone_code_hash, 'client': client}
            return "✅ Код отправлен."
        except PhoneNumberInvalidError:
            await client.disconnect()
            self.store.delete_session_file(user_id, temp=True)
            return "❌ Неверный номер телефона."
        except Exception as e:
            await client.disconnect()
            self.store.delete_session_file(user_id, temp=True)
            logger.error(f"Error sending code for {user_id}: {e}")
            return "❌ Неизвестная ошибка при отправке кода."

    async def sign_in(self, user_id: int, code: str) -> Tuple[bool, str, Optional[TelegramClient]]:
        data = self.store.temp_data.get(user_id)
        if not data or 'client' not in data:
            return False, "❌ Сессия авторизации утеряна. Начните заново.", None

        client = data['client']
        try:
            # ИСПРАВЛЕНИЕ: Используем phone_code_hash
            await client.sign_in(data['phone'], code, phone_code_hash=data['phone_code_hash'])
            await self.finalize_login(user_id, client)
            return True, "✅ **Успешный вход!** Worker запущен.", client
        except SessionPasswordNeededError:
            return False, "⚠️ Требуется ввод пароля 2FA. Введите пароль:", client
        except Exception as e:
            await client.disconnect()
            self.store.delete_session_file(user_id, temp=True)
            logger.error(f"Error signing in for {user_id}: {e}")
            return False, f"❌ Ошибка входа: {e}. Сессия удалена.", None

    async def sign_in_password(self, user_id: int, password: str, qr_login: bool = False) -> Tuple[bool, str]:
        data = self.store.temp_data.get(user_id)
        if not data or 'client' not in data: return False, "❌ Сессия утеряна. Начните заново."

        client = data['client']
        try:
            await client.sign_in(password=password)
            await self.finalize_login(user_id, client)
            return True, "✅ **Успешный вход!** Worker запущен."
        except Exception as e:
            await client.disconnect()
            self.store.delete_session_file(user_id, temp=True)
            logger.error(f"Error signing in password for {user_id}: {e}")
            return False, "❌ Неверный пароль или неизвестная ошибка. Сессия удалена."

    # --- QR Auth Logic ---
    async def start_qr_login(self, user_id: int) -> Tuple[str, Any]:
        client = await self._get_client(user_id, temp=True)
        await client.connect()
        qr_login_data = await client.qr_login()
        self.store.temp_data[user_id] = {'client': client, 'qr_login_data': qr_login_data}
        return qr_login_data.url, qr_login_data.image

    async def check_qr_login(self, user_id: int, qr_login_data: Any, client: TelegramClient) -> Tuple[bool, str]:
        try:
            await client.check_qr_login(qr_login_data) # wait_for делается в handler
            await self.finalize_login(user_id, client)
            return True, "✅ **Успешный вход по QR-коду!** Worker запущен."
        except SessionPasswordNeededError:
            return False, "⚠️ Требуется ввод пароля 2FA. Введите пароль:"
        except Exception as e:
            await client.disconnect()
            self.store.delete_session_file(user_id, temp=True)
            logger.error(f"QR login failed for {user_id}: {e}")
            return False, "❌ Произошла ошибка. Сессия удалена."
            
    # --- Telethon Command Handler ---
    
    async def _send_and_delete(self, client: TelegramClient, peer_id: Any, text: str, msg_ids: List[int], delay: int = 5):
        try:
            sent_msg = await client.send_message(peer_id, text)
            msg_ids.append(sent_msg.id)
            await asyncio.sleep(delay)
            await client.delete_messages(peer_id, msg_ids, revoke=True)
        except Exception as e:
             logger.error(f"Error sending/deleting message in Telethon: {e}")

    async def _handle_outgoing_message(self, event):
        message = event.message
        uid = event.client.session.path.split('_')[-1].split('.')[0]
        try:
            user_id = int(uid)
        except ValueError:
            return 
            
        if not message.text or not message.text.startswith('.'):
            return

        client = event.client
        command = message.text.split()[0].lower()
        args = message.text.split()[1:]

        if user_id != ADMIN_ID:
            is_active = await self.db.check_subscription(user_id, ADMIN_ID)
            if not is_active:
                await self._send_and_delete(client, message.peer_id, "Нет активной подписки.", [message.id], 3)
                return

        if command == '.флуд':
            await self._run_flood_task(user_id, client, message, args)
        elif command == '.стопфлуд':
            await self._stop_flood_tasks(user_id, client, message)
        elif command == '.лс':
            await self._run_ls_task(user_id, client, message)
        elif command == '.чекгруппу':
            await self._run_checkgroup_task(user_id, client, message, args)
        elif command == '.статус':
            await self._show_status(user_id, client, message)
        elif command == '.пкстарт' or command == '.пкворк':
            await self._handle_drop_mapping(client, message, args)

    # --- Telethon Command Implementations ---
    
    async def _run_flood_task(self, user_id, client, message, args):
        if len(args) < 3:
            return await self._send_and_delete(client, message.peer_id, "Формат: .флуд <кол-во> <текст> <задержка> [цель]", [message.id])

        try:
            count = int(args[0])
            text = args[1]
            delay = float(args[2])
            target_peer = args[3] if len(args) > 3 else message.peer_id
        except ValueError:
            return await self._send_and_delete(client, message.peer_id, "Неверный формат.", [message.id])

        task_key = f"flood_{random.randint(1000, 9999)}"
        if user_id not in self.store.active_tasks: self.store.active_tasks[user_id] = {}
        
        async def flood_worker():
            i = 0
            while count <= 0 or i < count:
                try:
                    await client.send_message(target_peer, text)
                    i += 1
                    self.store.process_progress[user_id] = {'type': 'flood', 'sent': i, 'total': count if count > 0 else '∞', 'key': task_key}
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Flood error for {user_id}: {e}")
                    break
        
            if user_id in self.store.active_tasks and task_key in self.store.active_tasks[user_id]:
                del self.store.active_tasks[user_id][task_key]
                if self.store.process_progress.get(user_id, {}).get('key') == task_key:
                    del self.store.process_progress[user_id]
            
            await self._send_and_delete(client, message.peer_id, f"✅ Флуд '{task_key}' завершен. Отправлено {i}.", [], FLOOD_TASK_TIMEOUT)
            
        self.store.active_tasks[user_id][task_key] = asyncio.create_task(flood_worker())
        await self._send_and_delete(client, message.peer_id, f"🟢 Флуд запущен. Ключ: {task_key}.", [message.id], FLOOD_TASK_TIMEOUT)

    async def _stop_flood_tasks(self, user_id, client, message):
        if user_id in self.store.active_tasks:
            flood_tasks = {k: t for k, t in self.store.active_tasks[user_id].items() if k.startswith('flood_')}
            for key, task in flood_tasks.items():
                task.cancel()
                del self.store.active_tasks[user_id][key]
            if self.store.process_progress.get(user_id, {}).get('type') == 'flood':
                del self.store.process_progress[user_id]
            await self._send_and_delete(client, message.peer_id, "🛑 Флуды остановлены.", [message.id], FLOOD_TASK_TIMEOUT)
        else:
            await self._send_and_delete(client, message.peer_id, "Нет активных задач.", [message.id], FLOOD_TASK_TIMEOUT)

    async def _run_ls_task(self, user_id, client, message):
        parts = message.text.split('\n')
        if len(parts) < 2:
            return await self._send_and_delete(client, message.peer_id, "Формат: .лс <текст>\n<@username1>\n...", [message.id])
        
        message_txt = parts[0][len(".лс"):].strip()
        recipients = [r.strip() for r in parts[1:] if r.strip()]
        await client.delete_messages(message.peer_id, [message.id], revoke=True)

        async def ls_worker():
            report = []
            for recipient in recipients:
                try:
                    await client.send_message(recipient, message_txt)
                    report.append(f"✅ {recipient}")
                except Exception as e:
                    report.append(f"❌ {recipient}: {e}")
                await asyncio.sleep(0.5)
            await self.bot.send_message(user_id, f"**Отчет .лс:**\n" + "\n".join(report), parse_mode='Markdown')
            
        asyncio.create_task(ls_worker())
        await client.send_message(message.peer_id, f"🟢 Рассылка {len(recipients)} получателям.")
        
    async def _run_checkgroup_task(self, user_id, client, message, args):
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        target_chat = args[0] if args else message.peer_id
        task_key = "checkgroup"
        if user_id in self.store.active_tasks and task_key in self.store.active_tasks[user_id]:
             self.store.active_tasks[user_id][task_key].cancel()

        status_msg = await client.send_message(message.peer_id, "🟢 Сканирование...")
        await client.delete_messages(message.peer_id, [message.id], revoke=True)

        async def checkgroup_worker():
            users = {}
            processed_count = 0
            try:
                entity = await client.get_entity(target_chat)
                async for msg in client.iter_messages(entity, reverse=True):
                    processed_count += 1
                    if msg.sender and isinstance(msg.sender, User) and msg.sender.id not in users:
                        users[msg.sender.id] = {'id': msg.sender.id, 'username': msg.sender.username or '—', 'name': (msg.sender.first_name or '') + (msg.sender.last_name or '')}
                    self.store.process_progress[user_id] = {'type': 'checkgroup', 'processed': processed_count, 'total_users': len(users), 'key': task_key, 'peer_name': entity.title or str(entity.id)}
                    await asyncio.sleep(0.01)
            except Exception as e:
                logger.error(f"Checkgroup error: {e}")
                return

            report_data = ["Имя | @username | ID"]
            for u in users.values(): report_data.append(f"{u['name']} | @{u['username']} | {u['id']}")
            peer_name = entity.title or str(entity.id)
            report_data.insert(0, f"Отчет: {peer_name}, найдено {len(users)}.")
            
            self.store.process_progress[user_id]['report_data'] = "\n".join(report_data)
            self.store.process_progress[user_id]['peer_name'] = peer_name

            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Файлом .txt", callback_data="report_send")],[InlineKeyboardButton(text="Удалить отчёт", callback_data="report_delete")]])
            await self.bot.send_message(user_id, f"✅ **Готово!** Найдено: **{len(users)}** в `{peer_name}`.", reply_markup=kb, parse_mode='Markdown')
            await client.delete_messages(message.peer_id, [status_msg.id], revoke=True)
            if user_id in self.store.active_tasks and task_key in self.store.active_tasks[user_id]: del self.store.active_tasks[user_id][task_key]

        if user_id not in self.store.active_tasks: self.store.active_tasks[user_id] = {}
        self.store.active_tasks[user_id][task_key] = asyncio.create_task(checkgroup_worker())

    async def _show_status(self, user_id, client, message):
        await client.delete_messages(message.peer_id, [message.id], revoke=True)
        progress = self.store.process_progress.get(user_id)
        status_text = "Нет активных задач."
        if progress:
            if progress['type'] == 'flood': status_text = f"Флуд: {progress['sent']}/{progress['total']}."
            elif progress['type'] == 'checkgroup': status_text = f"Скан `{progress['peer_name']}`: {progress['processed']} сообщ, {progress['total_users']} юзеров."
        status_msg = await client.send_message(message.peer_id, status_text)
        await asyncio.sleep(5)
        await client.delete_messages(message.peer_id, [status_msg.id], revoke=True)

    async def _handle_drop_mapping(self, client: TelegramClient, message, args: List[str]):
        if not args: return await self._send_and_delete(client, message.peer_id, "Формат: .пкстарт <НазваниеПК>", [message.id])
        pc_name = args[0].upper()
        thread_id = message.reply_to_msg_id if message.is_topic_message else 0
        self.store.drop_mapping[(message.peer_id.channel_id, thread_id)] = pc_name
        await self._send_and_delete(client, message.peer_id, f"✅ Привязан ПК: **{pc_name}**.", [message.id], FLOOD_TASK_TIMEOUT)
