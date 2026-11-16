from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.enums import (
    BalanceTxType,
    OrderStatus,
    PaymentStatus,
    WithdrawalStatus,
)
from database.models import (
    BalanceTransaction,
    Master,
    MasterBalance,
    MasterCard,
    Order,
    Payment,
    WithdrawalRequest,
)
from services.settings_service import get_commission_percent
from utils.formatting import quantize

logger = logging.getLogger(__name__)


class BalanceError(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


async def get_or_create_balance(
    session: AsyncSession, master_id: int, lock: bool = False
) -> MasterBalance:
    statement = select(MasterBalance).where(MasterBalance.master_id == master_id)
    if lock:
        statement = statement.with_for_update()
    balance = await session.scalar(statement)
    if balance is None:
        balance = MasterBalance(master_id=master_id)
        session.add(balance)
        await session.flush()
    return balance


def _add_tx(
    session: AsyncSession,
    balance: MasterBalance,
    tx_type: BalanceTxType,
    amount: Decimal,
    description: str | None = None,
    order_id: int | None = None,
    withdrawal_id: int | None = None,
) -> BalanceTransaction:
    tx = BalanceTransaction(
        master_id=balance.master_id,
        order_id=order_id,
        withdrawal_id=withdrawal_id,
        tx_type=tx_type.value,
        amount=quantize(amount),
        available_after=balance.available,
        pending_after=balance.pending,
        locked_after=balance.locked,
        description=description,
    )
    session.add(tx)
    return tx


async def hold_escrow(session: AsyncSession, order: Order) -> None:
    if order.master_id is None:
        raise BalanceError("order_not_found")
    balance = await get_or_create_balance(session, order.master_id, lock=True)
    amount = quantize(order.price)
    balance.pending = quantize(balance.pending + amount)
    _add_tx(
        session,
        balance,
        BalanceTxType.ESCROW_HOLD,
        amount,
        description=f"Escrow hold for order {order.public_id}",
        order_id=order.id,
    )
    logger.info("Escrow held: order=%s master=%s amount=%s", order.public_id, order.master_id, amount)


async def release_escrow(session: AsyncSession, order: Order) -> tuple[Decimal, Decimal]:
    if order.master_id is None:
        raise BalanceError("order_not_found")
    if order.escrow_released:
        raise BalanceError("order_not_pending")

    master = await session.get(Master, order.master_id, with_for_update=True)
    if master is None:
        raise BalanceError("not_found")

    balance = await get_or_create_balance(session, master.id, lock=True)
    amount = quantize(order.price)
    commission_percent = (
        Decimal(master.commission_percent)
        if master.commission_percent is not None
        else await get_commission_percent(session)
    )
    commission = quantize(amount * commission_percent / Decimal("100"))
    income = quantize(amount - commission)

    if balance.pending < amount:
        logger.warning(
            "Pending balance mismatch for master %s: pending=%s amount=%s",
            master.id,
            balance.pending,
            amount,
        )
    balance.pending = quantize(max(Decimal("0.00"), balance.pending - amount))
    balance.available = quantize(balance.available + income)
    balance.total_earned = quantize(balance.total_earned + income)
    balance.commission_total = quantize(balance.commission_total + commission)

    _add_tx(
        session,
        balance,
        BalanceTxType.RELEASE,
        income,
        description=f"Income for order {order.public_id} (gross {amount})",
        order_id=order.id,
    )
    _add_tx(
        session,
        balance,
        BalanceTxType.COMMISSION,
        commission,
        description=f"Platform commission for order {order.public_id}",
        order_id=order.id,
    )

    order.status = OrderStatus.COMPLETED.value
    order.confirmed_at = datetime.now(timezone.utc)
    order.commission_amount = commission
    order.master_earning = income
    order.escrow_released = True
    master.completed_orders = (master.completed_orders or 0) + 1

    logger.info(
        "Escrow released: order=%s master=%s income=%s commission=%s",
        order.public_id,
        master.id,
        income,
        commission,
    )
    return income, commission


async def refund_escrow(
    session: AsyncSession, order: Order, reason: str, mark_cancelled: bool = True
) -> bool:
    if order.escrow_released:
        return False
    if order.master_id is None:
        return False

    balance = await get_or_create_balance(session, order.master_id, lock=True)
    amount = quantize(order.price)
    balance.pending = quantize(max(Decimal("0.00"), balance.pending - amount))
    order.escrow_released = True
    if mark_cancelled:
        order.status = OrderStatus.CANCELLED.value
        order.cancelled_at = datetime.now(timezone.utc)
        order.cancel_reason = reason

    _add_tx(
        session,
        balance,
        BalanceTxType.REFUND,
        -amount,
        description=f"Refund for order {order.public_id}: {reason}",
        order_id=order.id,
    )

    payment = await session.scalar(
        select(Payment)
        .where(Payment.order_id == order.id, Payment.status == PaymentStatus.PAID.value)
        .order_by(Payment.id.desc())
        .with_for_update()
    )
    if payment is not None:
        payment.status = PaymentStatus.REFUNDED.value

    logger.info("Escrow refunded: order=%s amount=%s reason=%s", order.public_id, amount, reason)
    return True


async def hold_withdrawal(
    session: AsyncSession,
    master: Master,
    amount: Decimal,
    card: MasterCard,
    min_amount: Decimal,
) -> WithdrawalRequest:
    amount = quantize(amount)
    if amount < min_amount:
        raise BalanceError("withdraw_min_error")
    if not card.is_active or card.master_id != master.id:
        raise BalanceError("card_required")

    existing = await session.scalar(
        select(WithdrawalRequest.id).where(
            WithdrawalRequest.master_id == master.id,
            WithdrawalRequest.status.in_(
                [WithdrawalStatus.PENDING.value, WithdrawalStatus.APPROVED.value]
            ),
        )
    )
    if existing:
        raise BalanceError("withdraw_request_pending_exists")

    balance = await get_or_create_balance(session, master.id, lock=True)
    if balance.available < amount:
        raise BalanceError("withdraw_insufficient")

    balance.available = quantize(balance.available - amount)
    balance.locked = quantize(balance.locked + amount)

    request = WithdrawalRequest(
        master_id=master.id,
        card_id=card.id,
        amount=amount,
        card_last4=card.card_last4,
        card_masked=card.card_masked,
        card_holder=card.card_holder,
        status=WithdrawalStatus.PENDING.value,
    )
    session.add(request)
    await session.flush()
    _add_tx(
        session,
        balance,
        BalanceTxType.WITHDRAWAL_HOLD,
        -amount,
        description=f"Withdrawal request #{request.id}",
        withdrawal_id=request.id,
    )
    logger.info("Withdrawal requested: master=%s amount=%s request=%s", master.id, amount, request.id)
    return request


async def approve_withdrawal(
    session: AsyncSession, request: WithdrawalRequest, admin_telegram_id: int
) -> None:
    if request.status != WithdrawalStatus.PENDING.value:
        raise BalanceError("order_not_pending")
    request.status = WithdrawalStatus.APPROVED.value
    request.processed_at = datetime.now(timezone.utc)
    request.processed_by = admin_telegram_id


async def mark_withdrawal_paid(
    session: AsyncSession,
    request: WithdrawalRequest,
    admin_telegram_id: int,
    txn_id: str | None = None,
) -> None:
    if request.status not in {WithdrawalStatus.PENDING.value, WithdrawalStatus.APPROVED.value}:
        raise BalanceError("order_not_pending")

    master = await session.get(Master, request.master_id, with_for_update=True)
    if master is None:
        raise BalanceError("not_found")
    balance = await get_or_create_balance(session, master.id, lock=True)
    amount = quantize(request.amount)
    if balance.locked < amount:
        logger.warning(
            "Locked balance mismatch for master %s: locked=%s amount=%s",
            master.id,
            balance.locked,
            amount,
        )
    balance.locked = quantize(max(Decimal("0.00"), balance.locked - amount))
    balance.total_withdrawn = quantize(balance.total_withdrawn + amount)

    request.status = WithdrawalStatus.PAID.value
    request.provider_txn_id = txn_id
    request.processed_at = datetime.now(timezone.utc)
    request.processed_by = admin_telegram_id

    _add_tx(
        session,
        balance,
        BalanceTxType.WITHDRAWAL_PAID,
        -amount,
        description=f"Withdrawal #{request.id} paid, txn={txn_id or '—'}",
        withdrawal_id=request.id,
    )
    logger.info("Withdrawal paid: request=%s amount=%s", request.id, amount)


async def reject_withdrawal(
    session: AsyncSession,
    request: WithdrawalRequest,
    admin_telegram_id: int,
    reason: str,
) -> None:
    if request.status not in {WithdrawalStatus.PENDING.value, WithdrawalStatus.APPROVED.value}:
        raise BalanceError("order_not_pending")

    master = await session.get(Master, request.master_id, with_for_update=True)
    if master is None:
        raise BalanceError("not_found")
    balance = await get_or_create_balance(session, master.id, lock=True)
    amount = quantize(request.amount)
    balance.locked = quantize(max(Decimal("0.00"), balance.locked - amount))
    balance.available = quantize(balance.available + amount)

    request.status = WithdrawalStatus.REJECTED.value
    request.admin_comment = reason
    request.processed_at = datetime.now(timezone.utc)
    request.processed_by = admin_telegram_id

    _add_tx(
        session,
        balance,
        BalanceTxType.WITHDRAWAL_REFUND,
        amount,
        description=f"Withdrawal #{request.id} rejected: {reason}",
        withdrawal_id=request.id,
    )
    logger.info("Withdrawal rejected: request=%s amount=%s", request.id, amount)
