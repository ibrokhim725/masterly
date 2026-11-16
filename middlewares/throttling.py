from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from config import settings


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self._last: dict[int, float] = {}
        self._window = max(settings.RATE_LIMIT_SECONDS, 0.0)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if self._window <= 0 or data.get("is_admin"):
            return await handler(event, data)

        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        now = time.monotonic()
        previous = self._last.get(user.id, 0.0)
        if now - previous < self._window:
            if isinstance(event, CallbackQuery):
                await event.answer("⏳", show_alert=False)
            return None
        self._last[user.id] = now

        if len(self._last) > 50_000:
            cutoff = now - 3600
            self._last = {key: value for key, value in self._last.items() if value >= cutoff}

        return await handler(event, data)
