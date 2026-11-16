from __future__ import annotations

import base64
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from aiohttp import web
from sqlalchemy import select

from config import settings
from database.db import healthcheck, session_scope
from database.enums import PaymentProvider, PaymentStatus
from database.models import Payment
from services import notification_service, order_service, payment_service
from services.payment_service import PaymentError, money_str
from utils.crypto import verify_mock_signature

logger = logging.getLogger(__name__)

RETURN_HTML = """<!doctype html>
<html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Masterly</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f4f7fb;
color:#17202a;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:#fff;padding:32px;border-radius:18px;box-shadow:0 12px 40px rgba(20,50,80,.12);
max-width:420px;text-align:center}}
h1{{font-size:22px;margin:0 0 10px}}p{{color:#52606d;line-height:1.5}}
.badge{{font-size:44px;margin-bottom:8px}}
</style></head><body><div class="card">
<div class="badge">{icon}</div><h1>{title}</h1><p>{message}</p>
<p>Telegram ilovasiga qaytishingiz mumkin.</p>
</div></body></html>"""


def _page(title: str, message: str, icon: str = "✅", status: int = 200) -> web.Response:
    return web.Response(
        text=RETURN_HTML.format(icon=icon, title=title, message=message),
        content_type="text/html",
        status=status,
    )


async def health(request: web.Request) -> web.Response:
    ok = await healthcheck()
    return web.json_response(
        {"ok": ok, "service": "masterly-bot"}, status=200 if ok else 503
    )


async def telegram_webhook(request: web.Request) -> web.Response:
    if request.match_info.get("secret") != settings.WEBHOOK_SECRET:
        raise web.HTTPNotFound()
    header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if header_secret and header_secret != settings.WEBHOOK_SECRET:
        raise web.HTTPForbidden()
    bot: Bot = request.app["bot"]
    dispatcher: Dispatcher = request.app["dispatcher"]
    try:
        update = Update.model_validate(await request.json())
    except Exception:
        raise web.HTTPBadRequest()
    await dispatcher.feed_update(bot, update)
    return web.json_response({"ok": True})


async def mock_payment(request: web.Request) -> web.Response:
    if not settings.ALLOW_MOCK_PAYMENTS:
        raise web.HTTPNotFound()
    bot: Bot = request.app["bot"]
    try:
        payment_id = int(request.match_info["payment_id"])
    except (KeyError, ValueError):
        raise web.HTTPNotFound()

    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        if payment is None or payment.provider != PaymentProvider.MOCK.value:
            raise web.HTTPNotFound()
        if not verify_mock_signature(
            payment.id, money_str(payment.amount), request.query.get("sig", "")
        ):
            raise web.HTTPBadRequest(text="Invalid signature")

        if payment.status == PaymentStatus.PAID.value:
            return _page("To'lov allaqachon qabul qilingan", "Buyurtma to'langan.")

        try:
            order = await payment_service.mark_paid(
                session,
                payment.id,
                provider_txn_id=f"mock-{payment.id}",
                payload={"provider": "mock", "source": "local_page"},
            )
        except PaymentError as exc:
            return _page("To'lov amalga oshmadi", exc.key, icon="⚠️", status=400)

        if order is not None:
            order_id = order.id
            await session.flush()
            loaded = await order_service.load_order(session, order_id)
            if loaded is not None:
                await notification_service.notify_payment_success(bot, session, loaded)

    return _page(
        "To'lov muvaffaqiyatli",
        "Buyurtma to'landi. Mablag' escrow hisobida saqlanadi.",
    )


async def click_payment(request: web.Request, action: str) -> web.Response:
    async with session_scope() as session:
        data = dict(await request.post())
        order = await order_service.load_order_by_public_id(
            session, str(data.get("merchant_trans_id", ""))
        )
        was_paid = bool(order and order.paid_at)
        result = await payment_service.handle_click(session, data, action)
        if order is not None and result.get("error") == 0 and action == "1" and not was_paid:
            order_id = order.id
            await session.flush()
            loaded = await order_service.load_order(session, order_id)
            if loaded is not None and loaded.paid_at:
                await notification_service.notify_payment_success(
                    request.app["bot"], session, loaded
                )
    return web.json_response(result)


async def click_prepare(request: web.Request) -> web.Response:
    return await click_payment(request, "0")


async def click_complete(request: web.Request) -> web.Response:
    return await click_payment(request, "1")


def _payme_authorized(request: web.Request) -> bool:
    if not settings.PAYME_KEY:
        return False
    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
    except Exception:
        return False
    _, _, key = decoded.partition(":")
    return key == settings.PAYME_KEY


async def payme(request: web.Request) -> web.Response:
    if not _payme_authorized(request):
        return web.json_response(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32504, "message": "Unauthorized"}},
            status=401,
        )
    try:
        payload = await request.json()
    except Exception:
        return web.json_response(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        )

    method = payload.get("method")
    params = payload.get("params") or {}
    txn_id = str(params.get("id", "")) if isinstance(params, dict) else ""
    was_paid = False
    async with session_scope() as session:
        if method == "PerformTransaction" and txn_id:
            existing = await session.scalar(
                select(Payment).where(
                    Payment.provider == PaymentProvider.PAYME.value,
                    Payment.provider_txn_id == txn_id,
                )
            )
            was_paid = bool(existing and existing.status == PaymentStatus.PAID.value)
        response = await payment_service.handle_payme(session, payload)
        result = response.get("result") or {}
        payment_id = result.get("transaction")
        if payment_id and not was_paid:
            payment = await session.get(Payment, int(payment_id))
            if payment is not None and payment.status == PaymentStatus.PAID.value:
                await session.flush()
                order = await order_service.load_order(session, payment.order_id)
                if order is not None:
                    await notification_service.notify_payment_success(
                        request.app["bot"], session, order
                    )
    return web.json_response(response)


async def payment_return(request: web.Request) -> web.Response:
    return _page(
        "To'lov qayta ishlanmoqda",
        "To'lov natijasi tasdiqlangach Telegram orqali xabar beramiz.",
        icon="⏳",
    )


def create_app(bot: Bot, dispatcher: Dispatcher) -> web.Application:
    app = web.Application(client_max_size=1024 * 1024)
    app["bot"] = bot
    app["dispatcher"] = dispatcher
    app.router.add_get("/health", health)
    app.router.add_post("/telegram/webhook/{secret}", telegram_webhook)
    app.router.add_get("/payments/mock/pay/{payment_id}", mock_payment)
    app.router.add_post("/payments/click/prepare", click_prepare)
    app.router.add_post("/payments/click/complete", click_complete)
    app.router.add_post("/payments/payme", payme)
    app.router.add_get("/payments/return", payment_return)
    return app
