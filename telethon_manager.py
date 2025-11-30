# telethon_manager.py

import os
import asyncio
import logging
from telethon import TelegramClient, functions, errors
# ✅ Важные импорты для 2FA/Авторизации
from telethon.errors import SessionPasswordNeededError, PhoneCodeExpiredError, PhoneCodeInvalidError

logger = logging.getLogger(__name__)

# Класс для хранения данных сессий (ПРИМЕР)
class SessionStore:
    def __init__(self):
        # Хранит активные рабочие процессы/таски для QR
        self.active_workers = {}
        # Хранит клиенты Telethon
        self.clients = {}

    def get_client(self, user_id):
        return self.clients.get(user_id)

    async def create_client(self, user_id):
        # Создание клиента: имя сессии - это ID пользователя, путь - папка sessions/
        session_name = str(user_id)
        client = TelegramClient(f'sessions/{session_name}', api_id=YOUR_API_ID, api_hash=YOUR_API_HASH)
        # ⚠️ Замените YOUR_API_ID и YOUR_API_HASH на ваши реальные данные!
        
        # Если клиент еще не подключен, подключить его
        if not client.is_connected():
            await client.connect()
        
        self.clients[user_id] = client
        return client

    async def delete_worker(self, user_id):
        worker = self.active_workers.pop(user_id, None)
        if worker:
            worker.cancel()
        
    async def delete_client(self, user_id):
        client = self.clients.pop(user_id, None)
        if client and client.is_connected():
            await client.log_out()
            await client.disconnect()
        # Удаление файла сессии
        session_file = f'sessions/{user_id}.session'
        if os.path.exists(session_file):
            os.remove(session_file)


class TelethonManager:
    def __init__(self, api_id, api_hash):
        self.api_id = api_id
        self.api_hash = api_hash
        self.store = SessionStore()
        logger.info("TelethonManager initialized.")

    def _get_client(self, user_id):
        """Вспомогательная функция для получения или создания клиента."""
        client = self.store.get_client(user_id)
        if not client:
            # ⚠️ ВНИМАНИЕ: Это должно быть асинхронным!
            # Для простоты, если клиент не найден, вернем None и обработаем ошибку выше.
            # В реальном коде, лучше убедиться, что клиент создан заранее или здесь.
            raise RuntimeError(f"Client for {user_id} not initialized.") 
        return client

    async def send_code(self, user_id: int, phone: str):
        """Отправляет код авторизации по номеру телефона."""
        client = await self.store.create_client(user_id)
        
        result = await client.send_code_request(phone)
        return result.phone_code_hash
    
    # ✅ ФИКС: ИСПРАВЛЕНО КОЛИЧЕСТВО АРГУМЕНТОВ
    async def sign_in(self, user_id: int, phone: str, code_hash: str, code: str):
        """Пытается авторизоваться, используя код и хэш."""
        client = self.store.get_client(user_id)
        if not client:
            raise RuntimeError("Client is missing during sign_in.")

        try:
            await client.sign_in(phone, code, phone_code_hash=code_hash)
            
            # Авторизация успешна, удаляем рабочие процессы (если были QR)
            await self.store.delete_worker(user_id) 
            return "success"
        
        except errors.SessionPasswordNeededError:
            # Требуется 2FA пароль
            return "password_required"
        except (PhoneCodeExpiredError, PhoneCodeInvalidError, errors.CodeInvalidError) as e:
            # Неверный или истекший код
            raise e
        except Exception as e:
            # Другие ошибки
            raise e

    async def check_password(self, user_id: int, password: str):
        """Проверяет Облачный пароль (2FA)."""
        client = self._get_client(user_id)
        
        try:
            await client.sign_in(password=password)
            await self.store.delete_worker(user_id) 
            return True
        except errors.PasswordHashInvalidError:
            raise ValueError("Неверный 2FA пароль.")
        except Exception as e:
            raise e

    async def start_qr_login(self, user_id: int):
        """Инициализирует процесс QR-авторизации."""
        client = await self.store.create_client(user_id)
        
        # Запуск рабочего процесса для QR-авторизации
        qr_login = await client.qr_login()
        
        # Сохраняем QR-объект в worker, чтобы можно было проверить статус
        self.store.active_workers[user_id] = qr_login
        
        # Возвращаем URL и сам объект
        return qr_login.url, qr_login
        
    # --- ДОПОЛНИТЕЛЬНЫЙ МЕТОД: ПРОВЕРКА СТАТУСА QR (если требуется) ---
    async def check_qr_status(self, user_id: int):
        """Проверяет статус QR-сессии."""
        qr_login = self.store.active_workers.get(user_id)
        if not qr_login:
            return "expired"

        try:
            await qr_login.wait(timeout=0) # Не ждем, просто проверяем статус
            # Если не вызвало исключения, авторизация завершилась (или требует пароль)
            return "completed" 
        except errors.SessionPasswordNeededError:
            return "password_required"
        except asyncio.TimeoutError:
            return "waiting" # Все еще ждем сканирования
        except errors.QRLoginExpired:
            # Ошибка, что сессия истекла
            await self.store.delete_worker(user_id)
            return "expired"
        except Exception as e:
            logger.error(f"Error checking QR status for {user_id}: {e}")
            await self.store.delete_worker(user_id)
            return "error"
