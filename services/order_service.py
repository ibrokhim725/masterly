from __future__ import annotations

import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.enums import (
    ComplaintStatus,
    MasterStatus,
    OrderStatus,
    PaymentStatus,
    UserRole,
)
from database.models import (
    Complaint,
    Master,
    MasterService,
    Order,
    OrderStatusHistory,
    Payment,
    Service,
    User,
)
from services import balance_service
from services.settings_service import (
    get_accept_timeout_minutes,
    get_commission_percent,
    get_payment_timeout_minutes,
)
from utils.formatting import quantize

logger = logging.getLogger(__name__)


class OrderError(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


async def generate_public_id(session: AsyncSession) -> str:
    for _ in range(25):
        candidate = f"SX-{secrets.token_hex(3).upper()}"
        exists = await session.scalar(select(Order.id).where(Order.public_id == candidate))
        if not exists:
            return candidate
    raise OrderError("error_generic")


async def load_order(session: AsyncSession, order_id: int) -> Order | None:
    return await session.scalar(
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.client),
            selectinload(Order.master).selectinload(Master.user),
            selectinload(Order.service),
            selectinload(Order.review),
            selectinload(Order.payments),
        )
    )


async def load_order_by_public_id(session: AsyncSession, public_id: str) -> Order | None:
    return await session.scalar(
        select(Order)
        .where(Order.public_id == public_id)
        .options(
            selectinload(Order.client),
            selectinload(Order.master).selectinload(Master.user),
            selectinload(Order.service),
            selectinload(Order.review),
            selectinload(Order.payments),
        )
    )


def add_history(
    session: AsyncSession,
    order: Order,
    status: str,
    note: str | None = None,
    actor_telegram_id: int | None = None,
) -> None:
    session.add(
        OrderStatusHistory(
            order_id=order.id,
            status=status,
            note=note,
            changed_by=actor_telegram_id,
        )
    )


async def list_masters_for_service(
    session: AsyncSession,
    service_id: int,
    exclude_master_id: int | None = None,
    limit: int = 10,
) -> list[tuple[Master, Decimal]]:
    statement = (
        select(Master, MasterService.price)
        .join(MasterService, MasterService.master_id == Master.id)
        .join(User, User.id == Master.user_id)
        .where(
            MasterService.service_id == service_id,
            MasterService.is_active.is_(True),
            Master.status == MasterStatus.APPROVED.value,
            Master.is_active.is_(True),
            User.is_blocked.is_(False),
        )
        .order_by(Master.rating.desc(), Master.completed_orders.desc(), Master.id.desc())
        .limit(limit)
    )
    if exclude_master_id:
        statement = statement.where(Master.id != exclude_master_id)
    rows = (await session.execute(statement)).all()
    return [(row[0], row[1]) for row in rows]


async def create_order(
    session: AsyncSession,
    *,
    client: User,
    service: Service,
    master: Master,
    price: Decimal,
    description: str | None,
    photo_file_id: str | None,
    address: str,
    latitude: float | None,
    longitude: float | None,
    scheduled_date: date,
    scheduled_time: str,
    comment: str | None,
) -> Order:
    timeout = await get_accept_timeout_minutes(session)
    accept_deadline = datetime.now(timezone.utc) + timedelta(minutes=timeout)
    order = Order(
        public_id=await generate_public_id(session),
        client_id=client.id,
        master_id=master.id,
        service_id=service.id,
        service_name=service.name(client.language or "uz"),
        status=OrderStatus.PENDING_MASTER.value,
        price=quantize(price),
        description=description,
        photo_file_id=photo_file_id,
        address=address,
        latitude=Decimal(str(latitude)) if latitude is not None else None,
        longitude=Decimal(str(longitude)) if longitude is not None else None,
        scheduled_date=scheduled_date,
        scheduled_time=scheduled_time,
        client_comment=comment,
        accept_deadline=accept_deadline,
    )
    session.add(order)
    await session.flush()
    add_history(
        session,
        order,
        OrderStatus.PENDING_MASTER.value,
        note=f"Order created, accept deadline {accept_deadline.isoformat()}",
        actor_telegram_id=client.telegram_id,
    )
    logger.info(
        "Order created: %s client=%s master=%s price=%s",
        order.public_id,
        client.id,
        master.id,
        order.price,
    )
    return order


async def accept_order(
    session: AsyncSession, order_id: int, master: Master, actor_telegram_id: int
) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise OrderError("order_not_found")
    if order.master_id != master.id:
        raise OrderError("only_master_can")
    if order.status == OrderStatus.AWAITING_PAYMENT.value:
        raise OrderError("order_already_taken")
    if order.status != OrderStatus.PENDING_MASTER.value:
        raise OrderError("order_not_pending")

    now = datetime.now(timezone.utc)
    deadline = order.accept_deadline
    if deadline is not None and deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if deadline is not None and deadline < now:
        order.status = OrderStatus.EXPIRED.value
        add_history(
            session,
            order,
            OrderStatus.EXPIRED.value,
            note="Master tried to accept after deadline",
            actor_telegram_id=actor_telegram_id,
        )
        raise OrderError("order_not_pending")

    payment_timeout = await get_payment_timeout_minutes(session)
    order.status = OrderStatus.AWAITING_PAYMENT.value
    order.accepted_at = now
    order.payment_deadline = now + timedelta(minutes=payment_timeout)
    add_history(
        session,
        order,
        OrderStatus.AWAITING_PAYMENT.value,
        note="Master accepted the order",
        actor_telegram_id=actor_telegram_id,
    )
    logger.info("Order %s accepted by master %s", order.public_id, master.id)
    return order


async def reject_order(
    session: AsyncSession, order_id: int, master: Master, actor_telegram_id: int
) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise OrderError("order_not_found")
    if order.master_id != master.id:
        raise OrderError("only_master_can")
    if order.status != OrderStatus.PENDING_MASTER.value:
        raise OrderError("order_not_pending")

    order.status = OrderStatus.REJECTED_BY_MASTER.value
    order.cancelled_at = datetime.now(timezone.utc)
    order.cancel_reason = "master_rejected"
    add_history(
        session,
        order,
        OrderStatus.REJECTED_BY_MASTER.value,
        note="Master rejected the order",
        actor_telegram_id=actor_telegram_id,
    )
    logger.info("Order %s rejected by master %s", order.public_id, master.id)
    return order


async def reassign_order(
    session: AsyncSession,
    order_id: int,
    new_master_id: int,
    client: User,
) -> tuple[Order, Master, Decimal]:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise OrderError("order_not_found")
    if order.client_id != client.id:
        raise OrderError("not_your_order")
    if order.status not in {
        OrderStatus.REJECTED_BY_MASTER.value,
        OrderStatus.EXPIRED.value,
    }:
        raise OrderError("order_not_pending")

    row = (
        await session.execute(
            select(Master, MasterService.price)
            .join(User, User.id == Master.user_id)
            .join(MasterService, MasterService.master_id == Master.id)
            .where(
                Master.id == new_master_id,
                MasterService.service_id == order.service_id,
                MasterService.is_active.is_(True),
                Master.status == MasterStatus.APPROVED.value,
                Master.is_active.is_(True),
                User.is_blocked.is_(False),
            )
        )
    ).first()
    if row is None:
        raise OrderError("no_other_masters")

    master, price = row
    if master.id == order.master_id:
        raise OrderError("order_not_pending")

    timeout = await get_accept_timeout_minutes(session)
    now = datetime.now(timezone.utc)
    order.master_id = master.id
    order.price = quantize(price)
    order.status = OrderStatus.PENDING_MASTER.value
    order.accept_deadline = now + timedelta(minutes=timeout)
    order.payment_deadline = None
    order.accepted_at = None
    order.cancelled_at = None
    order.cancel_reason = None
    add_history(
        session,
        order,
        OrderStatus.PENDING_MASTER.value,
        note=f"Reassigned to master {master.id}",
        actor_telegram_id=client.telegram_id,
    )
    logger.info("Order %s reassigned to master %s", order.public_id, master.id)
    return order, master, order.price


async def cancel_order(
    session: AsyncSession,
    order_id: int,
    actor_user: User,
    *,
    is_admin: bool = False,
    reason: str = "client_cancelled",
) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise OrderError("order_not_found")
    if not is_admin and order.client_id != actor_user.id:
        raise OrderError("not_your_order")
    if order.status == OrderStatus.AWAITING_PAYMENT.value and is_admin:
        pass
    elif order.status not in {
        OrderStatus.PENDING_MASTER.value,
        OrderStatus.REJECTED_BY_MASTER.value,
        OrderStatus.EXPIRED.value,
        OrderStatus.AWAITING_PAYMENT.value,
    }:
        raise OrderError("cannot_cancel")

    now = datetime.now(timezone.utc)
    order.status = OrderStatus.CANCELLED.value
    order.cancelled_at = now
    order.cancel_reason = reason
    add_history(
        session,
        order,
        OrderStatus.CANCELLED.value,
        note=reason,
        actor_telegram_id=actor_user.telegram_id,
    )
    pending_payments = (
        await session.scalars(
            select(Payment).where(
                Payment.order_id == order.id,
                Payment.status == PaymentStatus.PENDING.value,
            )
        )
    ).all()
    for payment in pending_payments:
        payment.status = PaymentStatus.CANCELLED.value
        payment.cancelled_at = now
    logger.info("Order %s cancelled by user %s", order.public_id, actor_user.telegram_id)
    return order


async def start_work(
    session: AsyncSession, order_id: int, master: Master, actor_telegram_id: int
) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise OrderError("order_not_found")
    if order.master_id != master.id:
        raise OrderError("only_master_can")
    if order.status == OrderStatus.IN_PROGRESS.value:
        raise OrderError("order_not_pending")
    if order.status != OrderStatus.PAID.value:
        raise OrderError("order_not_pending")

    order.status = OrderStatus.IN_PROGRESS.value
    order.work_started_at = datetime.now(timezone.utc)
    add_history(
        session,
        order,
        OrderStatus.IN_PROGRESS.value,
        note="Master started work",
        actor_telegram_id=actor_telegram_id,
    )
    return order


async def mark_done(
    session: AsyncSession, order_id: int, master: Master, actor_telegram_id: int
) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise OrderError("order_not_found")
    if order.master_id != master.id:
        raise OrderError("only_master_can")
    if order.status not in {OrderStatus.PAID.value, OrderStatus.IN_PROGRESS.value}:
        raise OrderError("done_not_allowed")

    order.status = OrderStatus.AWAITING_CONFIRMATION.value
    order.completed_marked_at = datetime.now(timezone.utc)
    add_history(
        session,
        order,
        OrderStatus.AWAITING_CONFIRMATION.value,
        note="Master marked work as finished",
        actor_telegram_id=actor_telegram_id,
    )
    return order


async def confirm_completion(
    session: AsyncSession, order_id: int, client: User
) -> tuple[Order, Decimal, Decimal]:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise OrderError("order_not_found")
    if order.client_id != client.id:
        raise OrderError("not_your_order")
    if order.status != OrderStatus.AWAITING_CONFIRMATION.value:
        raise OrderError("cannot_confirm")

    income, commission = await balance_service.release_escrow(session, order)
    add_history(
        session,
        order,
        OrderStatus.COMPLETED.value,
        note="Client confirmed completion, escrow released",
        actor_telegram_id=client.telegram_id,
    )

    complaint = await session.scalar(
        select(Complaint).where(
            Complaint.order_id == order.id,
            Complaint.status.in_([ComplaintStatus.OPEN.value, ComplaintStatus.IN_REVIEW.value]),
        )
    )
    if complaint is not None:
        complaint.status = ComplaintStatus.RESOLVED.value
        complaint.admin_comment = "Order completed and confirmed by client"
        complaint.resolved_at = datetime.now(timezone.utc)

    logger.info(
        "Order %s completed, income=%s commission=%s",
        order.public_id,
        income,
        commission,
    )
    return order, income, commission


async def create_dispute(
    session: AsyncSession, order_id: int, client: User, text: str
) -> tuple[Order, Complaint]:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise OrderError("order_not_found")
    if order.client_id != client.id:
        raise OrderError("not_your_order")
    if order.status == OrderStatus.DISPUTED.value:
        raise OrderError("already_disputed")
    if order.status not in {
        OrderStatus.PAID.value,
        OrderStatus.IN_PROGRESS.value,
        OrderStatus.AWAITING_CONFIRMATION.value,
    }:
        raise OrderError("order_not_pending")

    order.status = OrderStatus.DISPUTED.value
    order.dispute_reason = text
    add_history(
        session,
        order,
        OrderStatus.DISPUTED.value,
        note=f"Client reported a problem: {text[:200]}",
        actor_telegram_id=client.telegram_id,
    )
    complaint = Complaint(
        order_id=order.id,
        user_id=client.id,
        text=text,
        status=ComplaintStatus.OPEN.value,
    )
    session.add(complaint)
    await session.flush()
    logger.info("Order %s disputed by client %s", order.public_id, client.id)
    return order, complaint


async def resolve_dispute(
    session: AsyncSession,
    order_id: int,
    *,
    favor_master: bool,
    note: str,
    admin_telegram_id: int,
) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise OrderError("order_not_found")
    if order.status != OrderStatus.DISPUTED.value:
        raise OrderError("order_not_pending")

    if favor_master:
        if order.escrow_released:
            raise OrderError("order_not_pending")
        await balance_service.release_escrow(session, order)
        add_history(
            session,
            order,
            OrderStatus.COMPLETED.value,
            note=f"Admin resolved dispute in favor of master: {note}",
            actor_telegram_id=admin_telegram_id,
        )
    else:
        refunded = await balance_service.refund_escrow(
            session, order, reason=f"dispute_refund: {note}"
        )
        if not refunded and not order.escrow_released:
            raise OrderError("order_not_pending")
        add_history(
            session,
            order,
            OrderStatus.CANCELLED.value,
            note=f"Admin resolved dispute in favor of client: {note}",
            actor_telegram_id=admin_telegram_id,
        )

    complaints = (
        await session.scalars(
            select(Complaint).where(
                Complaint.order_id == order.id,
                Complaint.status.in_(
                    [ComplaintStatus.OPEN.value, ComplaintStatus.IN_REVIEW.value]
                ),
            )
        )
    ).all()
    for complaint in complaints:
        complaint.status = (
            ComplaintStatus.RESOLVED.value if favor_master else ComplaintStatus.RESOLVED.value
        )
        complaint.admin_comment = note
        complaint.resolved_at = datetime.now(timezone.utc)
        complaint.resolved_by = admin_telegram_id

    logger.info(
        "Dispute resolved: order=%s favor_master=%s note=%s",
        order.public_id,
        favor_master,
        note,
    )
    return order


async def expire_orders(session: AsyncSession) -> dict[str, list[Order]]:
    now = datetime.now(timezone.utc)
    expired_master_orders: list[Order] = []
    expired_payment_orders: list[Order] = []

    master_orders = (
        await session.scalars(
            select(Order)
            .where(
                Order.status == OrderStatus.PENDING_MASTER.value,
                Order.accept_deadline.is_not(None),
                Order.accept_deadline < now,
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    for order in master_orders:
        order.status = OrderStatus.EXPIRED.value
        add_history(session, order, OrderStatus.EXPIRED.value, note="Accept deadline expired")
        expired_master_orders.append(order)

    payment_orders = (
        await session.scalars(
            select(Order)
            .where(
                Order.status == OrderStatus.AWAITING_PAYMENT.value,
                Order.payment_deadline.is_not(None),
                Order.payment_deadline < now,
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    for order in payment_orders:
        order.status = OrderStatus.CANCELLED.value
        order.cancelled_at = now
        order.cancel_reason = "payment_timeout"
        add_history(session, order, OrderStatus.CANCELLED.value, note="Payment deadline expired")
        pending_payments = (
            await session.scalars(
                select(Payment).where(
                    Payment.order_id == order.id,
                    Payment.status == PaymentStatus.PENDING.value,
                )
            )
        ).all()
        for payment in pending_payments:
            payment.status = PaymentStatus.CANCELLED.value
            payment.cancelled_at = now
        expired_payment_orders.append(order)

    if expired_master_orders or expired_payment_orders:
        logger.info(
            "Expired orders: master_response=%s payment=%s",
            len(expired_master_orders),
            len(expired_payment_orders),
        )
    return {"master_expired": expired_master_orders, "payment_expired": expired_payment_orders}


async def list_client_orders(
    session: AsyncSession, client_id: int, page: int = 0, page_size: int = 8
) -> tuple[list[Order], int]:
    total = await session.scalar(select(func.count(Order.id)).where(Order.client_id == client_id)) or 0
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    orders = (
        await session.scalars(
            select(Order)
            .where(Order.client_id == client_id)
            .order_by(Order.id.desc())
            .offset(page * page_size)
            .limit(page_size)
        )
    ).all()
    return list(orders), total


async def list_master_active_orders(
    session: AsyncSession, master_id: int
) -> list[Order]:
    orders = (
        await session.scalars(
            select(Order)
            .where(
                Order.master_id == master_id,
                Order.status.in_(
                    [
                        OrderStatus.PENDING_MASTER.value,
                        OrderStatus.AWAITING_PAYMENT.value,
                        OrderStatus.PAID.value,
                        OrderStatus.IN_PROGRESS.value,
                        OrderStatus.AWAITING_CONFIRMATION.value,
                        OrderStatus.DISPUTED.value,
                    ]
                ),
            )
            .order_by(Order.id.desc())
        )
    ).all()
    return list(orders)


async def list_master_completed_orders(
    session: AsyncSession, master_id: int, page: int = 0, page_size: int = 8
) -> tuple[list[Order], int]:
    total = (
        await session.scalar(
            select(func.count(Order.id)).where(
                Order.master_id == master_id,
                Order.status == OrderStatus.COMPLETED.value,
            )
        )
        or 0
    )
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    orders = (
        await session.scalars(
            select(Order)
            .where(
                Order.master_id == master_id,
                Order.status == OrderStatus.COMPLETED.value,
            )
            .order_by(Order.id.desc())
            .offset(page * page_size)
            .limit(page_size)
        )
    ).all()
    return list(orders), total


async def earning_preview(
    session: AsyncSession, order: Order
) -> tuple[Decimal, Decimal]:
    percent = await get_commission_percent(session)
    if order.master is not None and order.master.commission_percent is not None:
        percent = Decimal(order.master.commission_percent)
    commission = quantize(order.price * percent / Decimal("100"))
    return quantize(order.price - commission), commission
