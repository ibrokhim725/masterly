from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import AsyncSessionLocal

logger = logging.getLogger(__name__)


class DatabaseMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session: AsyncSession = AsyncSessionLocal()
        data["session"] = session
        try:
            result = await handler(event, data)
            if session.in_transaction():
                await session.commit()
            return result
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
