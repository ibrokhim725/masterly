"""HTTP/webhook smoke test: health, Telegram webhook auth, mock/Click/Payme.

    py scripts/webhook_smoke.py

Runs against a throwaway SQLite database with a fake Bot/Dispatcher, so it
needs no network and does not touch the running bot.
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_PATH = Path(__file__).resolve().parent / "webhook_smoke.db"
if DB_PATH.exists():
    DB_PATH.unlink()

SECRET = "smoke-webhook-secret"
CLICK_SECRET = "smoke-click-secret"
CLICK_SERVICE_ID = "111"
PAYME_KEY = "smoke-payme-key"

os.environ.update(
    {
        "DATABASE_URL": f"sqlite+aiosqlite:///{DB_PATH.as_posix()}",
        "BOT_TOKEN": "123456:webhook-smoke-token",
        "ADMIN_IDS": "999001",
        "SECRET_KEY": "webhook-smoke-secret-key",
        "PAYMENT_PROVIDER": "mock",
        "ALLOW_MOCK_PAYMENTS": "true",
        "AUTO_CREATE_SCHEMA": "true",
        "PUBLIC_BASE_URL": "http://localhost:8080",
        "WEBHOOK_SECRET": SECRET,
        "CLICK_SERVICE_ID": CLICK_SERVICE_ID,
        "CLICK_MERCHANT_ID": "222",
        "CLICK_SECRET_KEY": CLICK_SECRET,
        "PAYME_MERCHANT_ID": "333",
        "PAYME_KEY": PAYME_KEY,
        "LOG_LEVEL": "WARNING",
    }
)

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from sqlalchemy import select  # noqa: E402

from database.db import dispose_db, init_db, session_scope  # noqa: E402
from database.enums import MasterStatus, OrderStatus, PaymentStatus, UserRole  # noqa: E402
from database.models import Master, MasterService, Order, Payment, Service, User  # noqa: E402
from services import order_service, payment_service  # noqa: E402
from utils.crypto import md5_hex, sign_mock_payment  # noqa: E402
from utils.formatting import quantize  # noqa: E402
from webhook import create_app  # noqa: E402

PASSED: list[str] = []


def ok(label: str) -> None:
    PASSED.append(label)
    print(f"[PASS] {label}")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None):
        self.sent.append((chat_id, text))
        return None


class FakeDispatcher:
    def __init__(self) -> None:
        self.updates: list[object] = []

    async def feed_update(self, bot, update) -> None:
        self.updates.append(update)


async def setup_data() -> dict[str, int]:
    async with session_scope() as session:
        client = User(
            telegram_id=1001,
            full_name="Webhook Client",
            phone="+998901112233",
            language="uz",
            role=UserRole.CLIENT.value,
        )
        master_user = User(
            telegram_id=2002,
            full_name="Webhook Master",
            phone="+998902223344",
            language="uz",
            role=UserRole.MASTER.value,
        )
        session.add_all([client, master_user])
        await session.flush()
        master = Master(
            user_id=master_user.id,
            full_name="Webhook Master",
            phone="+998902223344",
            status=MasterStatus.APPROVED.value,
            experience_years=3,
            service_area="Tashkent",
            is_active=True,
        )
        session.add(master)
        await session.flush()
        service = await session.scalar(select(Service).where(Service.code == "faucet"))
        session.add(
            MasterService(
                master_id=master.id,
                service_id=service.id,
                price=Decimal("100000"),
                is_active=True,
            )
        )
        await session.flush()
        return {
            "client": client.id,
            "master": master.id,
            "master_user": master_user.id,
            "service": service.id,
        }


async def new_accepted_order(ids: dict[str, int]) -> int:
    async with session_scope() as session:
        client = await session.get(User, ids["client"])
        master = await session.get(Master, ids["master"])
        service = await session.get(Service, ids["service"])
        order = await order_service.create_order(
            session,
            client=client,
            service=service,
            master=master,
            price=Decimal("100000"),
            description="Webhook smoke order",
            photo_file_id=None,
            address="Tashkent, Webhook street 1",
            latitude=None,
            longitude=None,
            scheduled_date=date.today() + timedelta(days=1),
            scheduled_time="11:00",
            comment=None,
        )
        order_id = order.id
    async with session_scope() as session:
        master = await session.get(Master, ids["master"])
        await order_service.accept_order(session, order_id, master, ids["master_user"])
    return order_id


def click_sign(data: dict[str, str]) -> str:
    parts = [
        data.get("click_trans_id", ""),
        data.get("service_id", ""),
        CLICK_SECRET,
        data.get("merchant_trans_id", ""),
        data.get("amount", ""),
        data.get("action", ""),
        data.get("sign_time", ""),
    ]
    return md5_hex("".join(parts))


async def main() -> None:
    await init_db()
    bot = FakeBot()
    dispatcher = FakeDispatcher()
    ids = await setup_data()

    app = create_app(bot, dispatcher)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.get("/health")
        payload = await resp.json()
        check(resp.status == 200 and payload["ok"] is True, "health failed")
        ok("GET /health")

        resp = await client.post(
            "/telegram/webhook/wrong-secret", json={"update_id": 1}
        )
        check(resp.status == 404, "telegram webhook accepted wrong path secret")
        resp = await client.post(
            f"/telegram/webhook/{SECRET}",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        check(resp.status == 403, "telegram webhook accepted wrong header secret")
        resp = await client.post(
            f"/telegram/webhook/{SECRET}",
            json={"update_id": 2},
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
        )
        check(resp.status == 200, "telegram webhook rejected valid request")
        check(len(dispatcher.updates) == 1, "update was not fed to dispatcher")
        ok("Telegram webhook path/header secret + dispatcher feed")

        order_id = await new_accepted_order(ids)
        async with session_scope() as session:
            order = await order_service.load_order(session, order_id)
            payment = await payment_service.create_payment(session, order)
            payment_id = payment.id
            signature = sign_mock_payment(payment.id, f"{quantize(payment.amount):.2f}")

        resp = await client.get(f"/payments/mock/pay/{payment_id}?sig=bad")
        check(resp.status == 400, "mock page accepted bad signature")
        resp = await client.get(f"/payments/mock/pay/{payment_id}?sig={signature}")
        check(resp.status == 200, "mock page failed")
        async with session_scope() as session:
            order = await session.get(Order, order_id)
            payment = await session.get(Payment, payment_id)
            check(order.status == OrderStatus.PAID.value, "mock order not paid")
            check(payment.status == PaymentStatus.PAID.value, "mock payment not paid")
        resp = await client.get(f"/payments/mock/pay/{payment_id}?sig={signature}")
        check(resp.status == 200, "mock page not idempotent")
        ok("mock payment page: bad sig rejected, payment applied idempotently")

        payme_order = await new_accepted_order(ids)
        async with session_scope() as session:
            order = await session.get(Order, payme_order)
            public_id = order.public_id
        auth = "Basic " + base64.b64encode(f"Paycom:{PAYME_KEY}".encode()).decode()
        headers = {"Authorization": auth}
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "CheckPerformTransaction",
            "params": {
                "amount": 10_000_000,
                "account": {"order_id": public_id},
            },
        }
        resp = await client.post("/payments/payme", json=body)
        check(resp.status == 401, "payme accepted unauthenticated request")
        resp = await client.post("/payments/payme", json=body, headers=headers)
        data = await resp.json()
        check(data.get("result", {}).get("allow") is True, f"payme check failed: {data}")

        txn = "payme-smoke-1"
        create = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "CreateTransaction",
            "params": {"id": txn, "time": 1_700_000_000_000, "amount": 10_000_000, "account": {"order_id": public_id}},
        }
        resp = await client.post("/payments/payme", json=create, headers=headers)
        data = await resp.json()
        check(data.get("result", {}).get("state") == 1, f"payme create failed: {data}")

        perform = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "PerformTransaction",
            "params": {"id": txn, "time": 1_700_000_100_000},
        }
        resp = await client.post("/payments/payme", json=perform, headers=headers)
        data = await resp.json()
        check(data.get("result", {}).get("state") == 2, f"payme perform failed: {data}")
        async with session_scope() as session:
            order = await session.get(Order, payme_order)
            check(order.status == OrderStatus.PAID.value, "payme order not paid")
        resp = await client.post("/payments/payme", json=perform, headers=headers)
        data = await resp.json()
        check(data.get("result", {}).get("state") == 2, "payme perform not idempotent")
        ok("Payme auth, CheckPerform, Create, Perform (idempotent)")

        click_order = await new_accepted_order(ids)
        async with session_scope() as session:
            order = await session.get(Order, click_order)
            click_public_id = order.public_id
        click_data = {
            "click_trans_id": "ct-1",
            "service_id": CLICK_SERVICE_ID,
            "merchant_trans_id": click_public_id,
            "amount": "100000.00",
            "action": "0",
            "sign_time": "2026-11-15 12:00:00",
            "error": "0",
        }
        click_data["sign_string"] = click_sign(click_data)
        resp = await client.post("/payments/click/prepare", data=click_data)
        data = await resp.json()
        check(data.get("error") == 0, f"click prepare failed: {data}")
        prepare_id = data.get("merchant_prepare_id")
        check(bool(prepare_id), "click prepare id missing")

        complete = dict(click_data)
        complete["action"] = "1"
        complete["merchant_prepare_id"] = str(prepare_id)
        complete["sign_string"] = click_sign(complete)
        resp = await client.post("/payments/click/complete", data=complete)
        data = await resp.json()
        check(data.get("error") == 0, f"click complete failed: {data}")
        async with session_scope() as session:
            order = await session.get(Order, click_order)
            check(order.status == OrderStatus.PAID.value, "click order not paid")

        bad = dict(click_data)
        bad["click_trans_id"] = "ct-2"
        bad["sign_string"] = "deadbeef"
        resp = await client.post("/payments/click/prepare", data=bad)
        data = await resp.json()
        check(data.get("error") == -1, "click accepted bad signature")
        ok("Click prepare/complete signatures + payment applied")

        cross_order = await new_accepted_order(ids)
        async with session_scope() as session:
            order = await session.get(Order, cross_order)
            cross_public_id = order.public_id
        cross = dict(click_data)
        cross["click_trans_id"] = "ct-3"
        cross["merchant_trans_id"] = cross_public_id
        cross["action"] = "1"
        cross["merchant_prepare_id"] = str(prepare_id)
        cross["sign_string"] = click_sign(cross)
        resp = await client.post("/payments/click/complete", data=cross)
        data = await resp.json()
        check(data.get("error") == -6, f"click cross-order prepare id accepted: {data}")
        ok("Click rejects prepare id belonging to another order")

        check(bot.sent, "no notifications were sent during payment flows")
        print(f"\nAll {len(PASSED)} webhook checks passed ({len(bot.sent)} notifications).")
    finally:
        await client.close()
        await dispose_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        if DB_PATH.exists():
            try:
                DB_PATH.unlink()
            except OSError:
                pass
