from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import User

logger = logging.getLogger(__name__)


def _extract_tg_user(event: TelegramObject) -> TgUser | None:
    if isinstance(event, Message):
        return event.from_user
    if isinstance(event, CallbackQuery):
        return event.from_user
    return None


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user") or _extract_tg_user(event)
        if tg_user is None or tg_user.is_bot:
            return await handler(event, data)

        session: AsyncSession = data["session"]
        db_user = await session.scalar(select(User).where(User.telegram_id == tg_user.id))

        if db_user is None:
            db_user = User(
                telegram_id=tg_user.id,
                username=tg_user.username,
                full_name=tg_user.full_name,
            )
            session.add(db_user)
            await session.flush()
            logger.info("New user created: telegram_id=%s", tg_user.id)
        else:
            changed = False
            if tg_user.username != db_user.username:
                db_user.username = tg_user.username
                changed = True
            if tg_user.full_name and tg_user.full_name != db_user.full_name:
                db_user.full_name = tg_user.full_name
                changed = True
            db_user.last_seen_at = datetime.now(timezone.utc)
            if changed:
                logger.debug("User profile refreshed: telegram_id=%s", tg_user.id)

        data["db_user"] = db_user
        data["user"] = db_user
        data["is_admin"] = tg_user.id in settings.admin_ids

        if db_user.is_blocked:
            if isinstance(event, CallbackQuery):
                await event.answer("🚫", show_alert=False)
            elif isinstance(event, Message):
                await event.answer("🚫 Siz bloklangansiz / Вы заблокированы.")
            return None

        return await handler(event, data)
