from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.enums import ComplaintStatus, OrderStatus, PaymentStatus, UserRole, WithdrawalStatus
from database.models import (
    AdminSetting,
    Complaint,
    Master,
    MasterBalance,
    Order,
    Payment,
    User,
    WithdrawalRequest,
)


async def get_setting(session: AsyncSession, key: str, default: str | None = None) -> str | None:
    value = await session.scalar(select(AdminSetting.value).where(AdminSetting.key == key))
    return value if value is not None else default


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(AdminSetting, key)
    if row is None:
        session.add(AdminSetting(key=key, value=value))
    else:
        row.value = value


async def get_decimal_setting(
    session: AsyncSession, key: str, default: Decimal
) -> Decimal:
    raw = await get_setting(session, key)
    if raw is None:
        return default
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError):
        return default


async def get_int_setting(session: AsyncSession, key: str, default: int) -> int:
    raw = await get_setting(session, key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


async def get_commission_percent(session: AsyncSession) -> Decimal:
    return await get_decimal_setting(session, "commission_percent", settings.commission_percent)


async def get_min_withdrawal(session: AsyncSession) -> Decimal:
    return await get_decimal_setting(session, "min_withdrawal", settings.min_withdrawal)


async def get_accept_timeout_minutes(session: AsyncSession) -> int:
    return await get_int_setting(
        session, "master_accept_timeout_minutes", settings.MASTER_ACCEPT_TIMEOUT_MINUTES
    )


async def get_payment_timeout_minutes(session: AsyncSession) -> int:
    return await get_int_setting(
        session, "payment_timeout_minutes", settings.PAYMENT_TIMEOUT_MINUTES
    )


async def get_platform_stats(session: AsyncSession) -> dict:
    total_users = await session.scalar(select(func.count(User.id))) or 0
    total_clients = (
        await session.scalar(
            select(func.count(User.id)).where(User.role == UserRole.CLIENT.value)
        )
        or 0
    )
    total_masters = (
        await session.scalar(
            select(func.count(Master.id)).where(Master.status == "approved")
        )
        or 0
    )
    pending_masters = (
        await session.scalar(select(func.count(Master.id)).where(Master.status == "pending"))
        or 0
    )
    total_orders = await session.scalar(select(func.count(Order.id))) or 0
    active_orders = (
        await session.scalar(
            select(func.count(Order.id)).where(
                Order.status.in_(
                    [
                        OrderStatus.PENDING_MASTER.value,
                        OrderStatus.AWAITING_PAYMENT.value,
                        OrderStatus.PAID.value,
                        OrderStatus.IN_PROGRESS.value,
                        OrderStatus.AWAITING_CONFIRMATION.value,
                        OrderStatus.DISPUTED.value,
                    ]
                )
            )
        )
        or 0
    )
    completed_orders = (
        await session.scalar(
            select(func.count(Order.id)).where(Order.status == OrderStatus.COMPLETED.value)
        )
        or 0
    )
    cancelled_orders = (
        await session.scalar(
            select(func.count(Order.id)).where(Order.status == OrderStatus.CANCELLED.value)
        )
        or 0
    )
    paid_total = (
        await session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == PaymentStatus.PAID
            )
        )
        or Decimal("0")
    )
    commission_total = (
        await session.scalar(
            select(func.coalesce(func.sum(Order.commission_amount), 0)).where(
                Order.status == OrderStatus.COMPLETED
            )
        )
        or Decimal("0")
    )
    escrow_held = (
        await session.scalar(select(func.coalesce(func.sum(MasterBalance.pending), 0)))
        or Decimal("0")
    )
    pending_withdrawals_total = (
        await session.scalar(
            select(func.coalesce(func.sum(WithdrawalRequest.amount), 0)).where(
                WithdrawalRequest.status.in_(
                    [WithdrawalStatus.PENDING.value, WithdrawalStatus.APPROVED.value]
                )
            )
        )
        or Decimal("0")
    )
    open_complaints = (
        await session.scalar(
            select(func.count(Complaint.id)).where(
                Complaint.status.in_([ComplaintStatus.OPEN.value, ComplaintStatus.IN_REVIEW.value])
            )
        )
        or 0
    )
    return {
        "users": total_users,
        "clients": total_clients,
        "masters": total_masters,
        "pending_masters": pending_masters,
        "orders": total_orders,
        "active_orders": active_orders,
        "completed_orders": completed_orders,
        "cancelled_orders": cancelled_orders,
        "paid_total": paid_total,
        "commission_total": commission_total,
        "escrow_held": escrow_held,
        "pending_withdrawals": pending_withdrawals_total,
        "open_complaints": open_complaints,
    }
