from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from config import settings
from utils.i18n import translator


class I18nMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        db_user = data.get("db_user")
        lang = getattr(db_user, "language", None) or settings.DEFAULT_LANGUAGE
        data["lang"] = lang
        data["_"] = partial(translator.get, lang)
        return await handler(event, data)
