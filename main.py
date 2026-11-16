from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

from config import settings
from database.db import dispose_db, init_db
from handlers import (
    admin,
    balance,
    common,
    customer,
    language,
    master,
    payments,
    registration,
    start,
)
from middlewares.db import DatabaseMiddleware
from middlewares.i18n import I18nMiddleware
from middlewares.throttling import ThrottlingMiddleware
from middlewares.user import UserMiddleware
from services.scheduler import scheduler_loop
from utils.logging import setup_logging
from webhook import create_app

logger = logging.getLogger(__name__)


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())

    for observer in (dispatcher.message, dispatcher.callback_query):
        observer.outer_middleware(DatabaseMiddleware())
        observer.outer_middleware(UserMiddleware())
        observer.outer_middleware(ThrottlingMiddleware())
        observer.outer_middleware(I18nMiddleware())

    dispatcher.include_routers(
        start.router,
        language.router,
        registration.router,
        customer.router,
        master.router,
        payments.router,
        balance.router,
        admin.router,
        common.router,
    )
    return dispatcher


async def main() -> None:
    setup_logging()
    await init_db()

    bot = Bot(token=settings.BOT_TOKEN)
    dispatcher = build_dispatcher()
    allowed_updates = dispatcher.resolve_used_update_types()

    runner = web.AppRunner(create_app(bot, dispatcher))
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.PORT)
    await site.start()
    logger.info("HTTP server listening on 0.0.0.0:%s", settings.PORT)

    scheduler_task = asyncio.create_task(scheduler_loop(bot))

    try:
        if settings.webhook_enabled:
            await bot.set_webhook(
                settings.webhook_url,
                secret_token=settings.WEBHOOK_SECRET,
                allowed_updates=allowed_updates,
            )
            logger.info("Telegram webhook configured: %s", settings.webhook_url)
            await asyncio.Event().wait()
        else:
            await bot.delete_webhook(drop_pending_updates=False)
            logger.info("Starting Telegram long polling")
            await dispatcher.start_polling(
                bot,
                allowed_updates=allowed_updates,
                handle_signals=True,
            )
    finally:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        await runner.cleanup()
        await bot.session.close()
        await dispose_db()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception:
        logging.getLogger(__name__).exception("Fatal startup error")
        raise
