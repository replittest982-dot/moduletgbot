import aiosqlite
import pytz
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple, Any

# --- LOCAL IMPORTS ---
from config import TIMEZONE_MSK, ADMIN_ID

logger = logging.getLogger(__name__)

class AsyncDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.TIMEZONE_MSK = TIMEZONE_MSK

    # ... (Методы времени, init, get_user, set_telethon_status, и т.д. - остаются без изменений) ...

    async def create_promo_code(self, code: str, days: int, max_uses: int) -> bool:
        """Создает новый промокод."""
        now = self.get_current_time_msk().strftime('%Y-%m-%d %H:%M:%S')
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute("""
                    INSERT INTO promo_codes (code, days, max_uses, created_at)
                    VALUES (?, ?, ?, ?)
                """, (code, days, max_uses, now))
                await db.commit()
                return True
            except aiosqlite.IntegrityError:
                return False
    
    async def apply_promo_code(self, user_id: int, code: str) -> Tuple[bool, str]:
        """Применяет промокод к пользователю."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM promo_codes WHERE code=?", (code,)) as cursor:
                promo = await cursor.fetchone()
            
            if not promo: return False, "❌ Промокод не найден."
            if not promo['is_active']: return False, "❌ Промокод не активен."
            if promo['max_uses'] <= promo['current_uses']: return False, "❌ Промокод исчерпал лимит использований."
            
            user = await self.get_user(user_id)
            new_end_date_str = self._calculate_new_end_date(user['subscription_end_date'], promo['days'])
            
            # 1. Обновляем пользователя
            await db.execute("""
                UPDATE users 
                SET subscription_active=1, subscription_end_date=? 
                WHERE user_id=?
            """, (new_end_date_str, user_id))
            
            # 2. Обновляем использование промокода
            new_uses = promo['current_uses'] + 1
            is_active = 1 if new_uses < promo['max_uses'] else 0
            await db.execute("""
                UPDATE promo_codes 
                SET current_uses=?, is_active=? 
                WHERE code=?
            """, (new_uses, is_active, code))

            await db.commit()
            return True, f"✅ **Подписка активирована на {promo['days']} дней!** Истекает: `{new_end_date_str}`"

    async def check_subscription(self, user_id: int) -> bool:
        """Проверяет, активна ли подписка, и деактивирует, если срок истек."""
        
        # 🟢 ИСПРАВЛЕНО: Отложенный импорт tm для избежания цикла
        tm = None 
        try: from main import tm 
        except ImportError: pass 
            
        if user_id == ADMIN_ID: return True
        
        user = await self.get_user(user_id)
        if not user or not user.get('subscription_active') or not user.get('subscription_end_date'): 
            return False

        try:
            end = self.to_msk_aware(user['subscription_end_date'])
            now = self.get_current_time_msk()
            
            if end > now: return True
            else:
                if tm: await tm.stop_worker(user_id, silent=True)
                await self.set_telethon_status(user_id, False) 
                await self.set_subscription_status(user_id, False, None) 
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
