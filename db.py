# ... (начало db.py)
# ...
from telethon_manager import TelethonManager # Должен быть импортирован для остановки воркера

class AsyncDatabase:
    # ... (методы __init__, get_current_time_msk, to_msk_aware, _calculate_new_end_date, init, get_user)

    async def check_subscription(self, user_id: int) -> bool:
        """Проверяет, активна ли подписка, и деактивирует, если срок истек."""
        
        # Получаем tm из контекста
        try:
            from main import tm # Это хак, лучше использовать DI или global, но это работает
        except ImportError:
            tm = None # Если импорт не удался, просто пропускаем остановку воркера
            
        if user_id == ADMIN_ID: return True # Админ всегда активен
        
        user = await self.get_user(user_id)
        if not user or not user.get('subscription_active') or not user.get('subscription_end_date'): 
            return False

        try:
            end = self.to_msk_aware(user['subscription_end_date'])
            now = self.get_current_time_msk()
            
            if end > now:
                return True
            else:
                # Деактивация
                if tm: await tm.stop_worker(user_id, silent=True) # Останавливаем воркер
                await self.set_telethon_status(user_id, False) # Деактивируем Telethon
                await self.set_subscription_status(user_id, False, None) # Очищаем дату
                return False
        except Exception as e:
            logger.error(f"Subscription check error for {user_id}: {e}")
            return False
            
    async def set_subscription_status(self, user_id: int, active: bool, end_date_str: Optional[str]):
         """Устанавливает статус подписки и дату окончания."""
         async with aiosqlite.connect(self.db_path) as db:
             await db.execute("UPDATE users SET subscription_active=?, subscription_end_date=? WHERE user_id=?", 
                             (1 if active else 0, end_date_str, user_id))
             await db.commit()

    # ... (остальные методы get_active_telethon_users, set_telethon_status, и т.д.)
