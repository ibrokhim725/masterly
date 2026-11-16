"""End-to-end smoke test on a throwaway SQLite database.

Run from the project root:

    py scripts/smoke_test.py

The test sets its own environment variables *before* importing the app
modules, so it never touches the real .env database and needs no Telegram
token or network access.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_PATH = Path(__file__).resolve().parent / "smoke_test.db"
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ.update(
    {
        "DATABASE_URL": f"sqlite+aiosqlite:///{DB_PATH.as_posix()}",
        "BOT_TOKEN": "123456:smoke-test-token",
        "ADMIN_IDS": "999001",
        "SECRET_KEY": "smoke-test-secret-key",
        "PAYMENT_PROVIDER": "mock",
        "ALLOW_MOCK_PAYMENTS": "true",
        "AUTO_CREATE_SCHEMA": "true",
        "PUBLIC_BASE_URL": "http://localhost:8080",
        "LOG_LEVEL": "WARNING",
    }
)

from sqlalchemy import func, select  # noqa: E402

from database.db import AsyncSessionLocal, dispose_db, init_db, session_scope  # noqa: E402
from database.enums import (  # noqa: E402
    MasterStatus,
    OrderStatus,
    PaymentStatus,
    UserRole,
    WithdrawalStatus,
)
from database.models import (  # noqa: E402
    Master,
    MasterCard,
    MasterService,
    Notification,
    Order,
    Payment,
    Service,
    User,
    WithdrawalRequest,
)
from services import (  # noqa: E402
    balance_service,
    notification_service,
    order_service,
    payment_service,
)
from utils.crypto import encrypt_card  # noqa: E402
from utils.formatting import quantize  # noqa: E402

PASSED: list[str] = []


def ok(label: str) -> None:
    PASSED.append(label)
    print(f"[PASS] {label}")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeBot:
    """Minimal Bot stand-in that records outgoing messages."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None):
        self.sent.append((chat_id, text))
        return None


async def create_order(session, ids: dict, *, price: str = "100000") -> int:
    client = await session.get(User, ids["client"])
    master = await session.get(Master, ids["master"])
    service = await session.get(Service, ids["service"])
    order = await order_service.create_order(
        session,
        client=client,
        service=service,
        master=master,
        price=Decimal(price),
        description="Smoke test order",
        photo_file_id=None,
        address="Tashkent, Test street 1",
        latitude=None,
        longitude=None,
        scheduled_date=date.today() + timedelta(days=1),
        scheduled_time="10:00",
        comment=None,
    )
    return order.id


async def accept_and_pay(session, ids: dict, order_id: int, txn: str) -> int:
    master = await session.get(Master, ids["master"])
    await order_service.accept_order(session, order_id, master, ids["master_user"])
    order = await order_service.load_order(session, order_id)
    payment = await payment_service.create_payment(session, order)
    check(bool(payment.pay_url), "payment url was not generated")
    await payment_service.mark_paid(
        session, payment.id, provider_txn_id=txn, payload={"source": "smoke"}
    )
    return payment.id


async def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    bot = FakeBot()

    await init_db()
    async with session_scope() as session:
        service_count = await session.scalar(select(func.count(Service.id)))
        check(bool(service_count), "services were not seeded")
    ok("database init + seed")

    ids: dict[str, int] = {}
    async with session_scope() as session:
        client = User(
            telegram_id=1001,
            full_name="Smoke Client",
            phone="+998901112233",
            language="uz",
            role=UserRole.CLIENT.value,
        )
        master_user = User(
            telegram_id=2002,
            full_name="Smoke Master",
            phone="+998902223344",
            language="uz",
            role=UserRole.MASTER.value,
        )
        session.add_all([client, master_user])
        await session.flush()

        master = Master(
            user_id=master_user.id,
            full_name="Smoke Master",
            phone="+998902223344",
            status=MasterStatus.APPROVED.value,
            experience_years=5,
            service_area="Tashkent",
            is_active=True,
        )
        session.add(master)
        await session.flush()

        service = await session.scalar(select(Service).where(Service.code == "faucet"))
        check(service is not None, "faucet service missing")
        session.add(
            MasterService(
                master_id=master.id,
                service_id=service.id,
                price=Decimal("100000"),
                is_active=True,
            )
        )
        card = MasterCard(
            master_id=master.id,
            card_number_encrypted=encrypt_card("8600123456789012"),
            card_last4="9012",
            card_masked="8600 **** **** 9012",
            card_holder="SMOKE MASTER",
            bank_name="Smoke Bank",
            is_default=True,
            is_active=True,
        )
        session.add(card)
        await session.flush()

        ids = {
            "client": client.id,
            "master": master.id,
            "master_user": master_user.id,
            "service": service.id,
            "card": card.id,
        }
    ok("users, master, service price and card created")

    async with session_scope() as session:
        order_id = await create_order(session, ids)
    async with session_scope() as session:
        order = await order_service.load_order(session, order_id)
        await notification_service.notify_new_order(bot, session, order)
        check(order.status == OrderStatus.PENDING_MASTER.value, "order not pending")
    ok("order created + master notified")

    async with session_scope() as session:
        master = await session.get(Master, ids["master"])
        await order_service.accept_order(session, order_id, master, ids["master_user"])
    async with session_scope() as session:
        order = await order_service.load_order(session, order_id)
        check(order.status == OrderStatus.AWAITING_PAYMENT.value, "order not awaiting payment")
        await notification_service.notify_order_accepted(bot, session, order)
    ok("order accepted by master")

    async with session_scope() as session:
        order = await order_service.load_order(session, order_id)
        payment = await payment_service.create_payment(session, order)
        check(payment.provider == "mock", "unexpected provider")
        await payment_service.mark_paid(
            session, payment.id, provider_txn_id="mock-smoke-1", payload={"source": "smoke"}
        )
    async with session_scope() as session:
        order = await order_service.load_order(session, order_id)
        check(order.status == OrderStatus.PAID.value, "order not marked paid")
        balance = await balance_service.get_or_create_balance(session, ids["master"])
        check(
            quantize(balance.pending) == quantize("100000"),
            f"escrow pending mismatch: {balance.pending}",
        )
        await notification_service.notify_payment_success(bot, session, order)
    ok("mock payment marked paid + escrow held")

    async with session_scope() as session:
        master = await session.get(Master, ids["master"])
        await order_service.start_work(session, order_id, master, ids["master_user"])
    async with session_scope() as session:
        master = await session.get(Master, ids["master"])
        await order_service.mark_done(session, order_id, master, ids["master_user"])
    async with session_scope() as session:
        client = await session.get(User, ids["client"])
        order, income, commission = await order_service.confirm_completion(
            session, order_id, client
        )
        check(order.status == OrderStatus.COMPLETED.value, "order not completed")
        check(quantize(income) == quantize("85000"), f"income mismatch: {income}")
        check(quantize(commission) == quantize("15000"), f"commission mismatch: {commission}")
        balance = await balance_service.get_or_create_balance(session, ids["master"])
        check(quantize(balance.available) == quantize("85000"), "available mismatch")
        check(quantize(balance.pending) == quantize("0"), "pending not cleared")
        await notification_service.notify_order_completed(
            bot, session, order, income, commission
        )
    ok("work started, finished, confirmed; escrow split 85/15")

    async with session_scope() as session:
        master = await session.get(Master, ids["master"])
        card = await session.get(MasterCard, ids["card"])
        request = await balance_service.hold_withdrawal(
            session, master, Decimal("85000"), card, Decimal("50000")
        )
        request_id = request.id
        await balance_service.approve_withdrawal(session, request, 999001)
    async with session_scope() as session:
        request = await session.get(WithdrawalRequest, request_id)
        await balance_service.mark_withdrawal_paid(
            session, request, 999001, txn_id="SMOKE-TXN-1"
        )
        balance = await balance_service.get_or_create_balance(session, ids["master"])
        check(quantize(balance.locked) == quantize("0"), "locked not cleared")
        check(quantize(balance.total_withdrawn) == quantize("85000"), "withdrawn mismatch")
        check(request.status == WithdrawalStatus.PAID.value, "withdrawal not paid")
    ok("withdrawal hold, approve and payout")

    async with session_scope() as session:
        order2_id = await create_order(session, ids)
    async with session_scope() as session:
        await accept_and_pay(session, ids, order2_id, "mock-smoke-2")
    async with session_scope() as session:
        client = await session.get(User, ids["client"])
        order, complaint = await order_service.create_dispute(
            session, order2_id, client, "Water is still leaking"
        )
        loaded = await order_service.load_order(session, order2_id)
        await notification_service.notify_dispute(bot, session, loaded, complaint)
    async with session_scope() as session:
        order = await order_service.resolve_dispute(
            session,
            order2_id,
            favor_master=False,
            note="Smoke refund",
            admin_telegram_id=999001,
        )
        check(order.status == OrderStatus.CANCELLED.value, "dispute refund did not cancel order")
        check(order.escrow_released is True, "refund did not release escrow")
        balance = await balance_service.get_or_create_balance(session, ids["master"])
        check(quantize(balance.pending) == quantize("0"), "refund left pending balance")
        loaded = await order_service.load_order(session, order2_id)
        await notification_service.notify_dispute_resolved(
            bot, session, loaded, favor_master=False, note="Smoke refund"
        )
    async with session_scope() as session:
        paid = await session.scalar(
            select(Payment).where(
                Payment.order_id == order2_id,
                Payment.status == PaymentStatus.REFUNDED.value,
            )
        )
        check(paid is not None, "payment was not marked refunded")
    ok("dispute resolved in client favor + payment refunded")

    async with session_scope() as session:
        order3_id = await create_order(session, ids)
    async with session_scope() as session:
        master = await session.get(Master, ids["master"])
        await order_service.accept_order(session, order3_id, master, ids["master_user"])
    async with session_scope() as session:
        order = await order_service.load_order(session, order3_id)
        payment = await payment_service.create_payment(session, order)
        payment3_id = payment.id
    async with session_scope() as session:
        client = await session.get(User, ids["client"])
        await order_service.cancel_order(session, order3_id, client, reason="smoke_cancel")
    async with session_scope() as session:
        payment = await session.get(Payment, payment3_id)
        check(payment.status == PaymentStatus.CANCELLED.value, "pending payment not cancelled")
        order = await session.get(Order, order3_id)
        check(order.status == OrderStatus.CANCELLED.value, "order not cancelled")
    ok("client cancellation also cancels pending payment")

    async with session_scope() as session:
        order4_id = await create_order(session, ids)
    async with session_scope() as session:
        order = await session.get(Order, order4_id)
        order.accept_deadline = datetime.now(timezone.utc) - timedelta(minutes=1)
    async with session_scope() as session:
        result = await order_service.expire_orders(session)
        check(
            any(item.id == order4_id for item in result["master_expired"]),
            "order was not expired",
        )
    async with session_scope() as session:
        order = await session.get(Order, order4_id)
        check(order.status == OrderStatus.EXPIRED.value, "expired order status mismatch")
    ok("accept deadline expiry")

    # Simulate a handler that commits explicitly and only then notifies:
    # the notification row must still be flushed and persisted by the
    # database middleware.
    session = AsyncSessionLocal()
    try:
        client = await session.get(User, ids["client"])
        await session.commit()
        await notification_service._send(
            bot, session, client, "post-commit notification", kind="smoke"
        )
        if session.in_transaction():
            await session.commit()
    finally:
        await session.close()
    async with session_scope() as session:
        count = await session.scalar(
            select(func.count(Notification.id)).where(
                Notification.text == "post-commit notification"
            )
        )
        check(count == 1, f"notification row not persisted: {count}")
    ok("notification persisted after explicit commit")

    async with session_scope() as session:
        notifications = await session.scalar(select(func.count(Notification.id)))
        payments = await session.scalar(select(func.count(Payment.id)))
    check(bot.sent, "no Telegram messages were produced")
    ok(f"persisted notifications={notifications}, payments={payments}")

    await dispose_db()
    print(f"\nAll {len(PASSED)} smoke checks passed.")


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
