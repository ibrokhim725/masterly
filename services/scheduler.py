from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from database.db import session_scope
from services import notification_service, order_service

logger = logging.getLogger(__name__)


async def _notify_expired(bot: Bot, master_ids: list[int], payment_ids: list[int]) -> None:
    if not master_ids and not payment_ids:
        return
    async with session_scope() as session:
        for order_id in master_ids:
            order = await order_service.load_order(session, order_id)
            if order is not None:
                await notification_service.notify_order_expired(bot, session, order)
        for order_id in payment_ids:
            order = await order_service.load_order(session, order_id)
            if order is not None:
                await notification_service.notify_payment_expired(bot, session, order)


async def scheduler_loop(bot: Bot, interval: int = 30) -> None:
    logger.info("Order expiration scheduler started (interval=%ss)", interval)
    while True:
        try:
            async with session_scope() as session:
                expired = await order_service.expire_orders(session)
                master_ids = [order.id for order in expired["master_expired"]]
                payment_ids = [order.id for order in expired["payment_expired"]]
                for order in expired["master_expired"]:
                    logger.info("Order expired (master timeout): %s", order.public_id)
                for order in expired["payment_expired"]:
                    logger.info("Order expired (payment timeout): %s", order.public_id)
            await _notify_expired(bot, master_ids, payment_ids)
        except asyncio.CancelledError:
            logger.info("Scheduler stopped")
            raise
        except Exception:
            logger.exception("Scheduler iteration failed")
        await asyncio.sleep(interval)
