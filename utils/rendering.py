from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from database.enums import OrderStatus, PaymentStatus
from database.models import (
    BalanceTransaction,
    Complaint,
    Master,
    MasterBalance,
    Order,
    Payment,
    User,
    WithdrawalRequest,
)
from utils.formatting import (
    format_date,
    format_dt,
    format_money,
    format_phone,
    format_price_type,
    human_status,
)
from utils.i18n import t


def render_service(service, lang: str) -> str:
    return t(
        lang,
        "service_line",
        name=service.name(lang),
        description=service.description(lang) or "—",
        price=format_money(service.base_price, lang),
        price_type=format_price_type(service.price_type, lang),
    )


def render_master_card(master: Master, price: Decimal, lang: str) -> str:
    return t(
        lang,
        "master_card",
        name=master.full_name,
        rating=f"{float(master.rating):.2f}",
        reviews=master.rating_count,
        orders=master.completed_orders,
        experience=master.experience_years,
        area=master.service_area or "—",
        price=format_money(price, lang),
        about=(master.about or "—")[:400],
    )


def _contacts_for(order: Order, lang: str, viewer: str) -> str | None:
    if order.paid_at is None:
        return None
    if order.status in {OrderStatus.CANCELLED.value, OrderStatus.EXPIRED.value}:
        return None
    lines: list[str] = []
    if viewer in {"client", "admin"} and order.master is not None:
        lines.append(
            f"📞 {t(lang, 'label_master')}: {order.master.full_name}\n"
            f"{t(lang, 'label_phone')}: {format_phone(order.master.phone)}\n"
            f"{t(lang, 'label_telegram')}: "
            f"{order.master.user.telegram_link if order.master.user else t(lang, 'no_username')}"
        )
    if viewer in {"master", "admin"} and order.client is not None:
        lines.append(
            f"📞 {t(lang, 'label_client')}: {order.client.full_name}\n"
            f"{t(lang, 'label_phone')}: {format_phone(order.client.phone)}\n"
            f"{t(lang, 'label_telegram')}: {order.client.telegram_link or t(lang, 'no_username')}"
        )
    return "\n\n".join(lines) if lines else None


def render_order(order: Order, lang: str, viewer: str = "client") -> str:
    lines = [
        f"{t(lang, 'order_detail_title')} #{order.public_id}",
        "",
        f"{t(lang, 'label_order_id')}: {order.public_id}",
        f"{t(lang, 'label_service')}: {order.service_name}",
        f"{t(lang, 'label_status')}: {human_status(order.status, lang)}",
        f"{t(lang, 'label_price')}: {format_money(order.price, lang)}",
        f"{t(lang, 'label_date')}: {format_date(order.scheduled_date)}",
        f"{t(lang, 'label_time')}: {order.scheduled_time or '—'}",
        f"{t(lang, 'label_address')}: {order.address or '—'}",
        f"{t(lang, 'label_description')}: {order.description or '—'}",
    ]
    if order.client_comment:
        lines.append(f"{t(lang, 'label_comment')}: {order.client_comment}")
    if viewer in {"master", "admin"} and order.client:
        lines.append(f"{t(lang, 'label_client')}: {order.client.full_name}")
    if viewer in {"client", "admin"} and order.master:
        lines.append(f"{t(lang, 'label_master')}: {order.master.full_name}")
    if order.created_at:
        lines.append(f"{t(lang, 'label_created')}: {format_dt(order.created_at, lang)}")
    if order.status == OrderStatus.COMPLETED.value and order.master_earning is not None:
        lines.append(f"{t(lang, 'label_income')}: {format_money(order.master_earning, lang)}")
    if order.cancel_reason:
        lines.append(f"{t(lang, 'label_reason')}: {order.cancel_reason}")
    if order.accept_deadline and order.status == OrderStatus.PENDING_MASTER.value:
        lines.append(
            t(lang, "master_response_deadline", deadline=format_dt(order.accept_deadline, lang))
        )

    contacts = _contacts_for(order, lang, viewer)
    if contacts:
        lines.append("")
        lines.append(contacts)
    return "\n".join(lines)


def render_master_order(order: Order, lang: str) -> str:
    return render_order(order, lang, viewer="master")


def render_completed_order(order: Order, lang: str) -> str:
    return t(
        lang,
        "completed_order_item",
        order_id=order.public_id,
        service=order.service_name,
        client=order.client.full_name if order.client else "—",
        date=format_date(order.scheduled_date),
        income=format_money(order.master_earning or Decimal("0"), lang),
        status=human_status(order.status, lang),
    )


def render_profile(user: User, lang: str, orders_count: int = 0) -> str:
    role = t(lang, f"role_{user.role or 'client'}_label")
    lines = [
        t(lang, "profile_title"),
        "",
        f"{t(lang, 'profile_name')}: {user.full_name}",
        f"{t(lang, 'profile_phone')}: {format_phone(user.phone)}",
        f"{t(lang, 'profile_username')}: @{user.username}" if user.username else f"{t(lang, 'profile_username')}: {t(lang, 'no_username')}",
        f"{t(lang, 'profile_role')}: {role}",
        f"{t(lang, 'profile_language')}: {'O‘zbekcha' if lang == 'uz' else 'Русский'}",
        f"{t(lang, 'profile_orders')}: {orders_count}",
    ]
    return "\n".join(lines)


def render_master_profile(master: Master, lang: str) -> str:
    lines = [
        t(lang, "master_profile_title"),
        "",
        f"{t(lang, 'profile_name')}: {master.full_name}",
        f"{t(lang, 'profile_phone')}: {format_phone(master.phone)}",
        f"{t(lang, 'profile_status')}: {t(lang, f'master_status_{master.status}')}",
        f"{t(lang, 'profile_rating')}: {float(master.rating):.2f} ({master.rating_count})",
        f"{t(lang, 'label_orders_done')}: {master.completed_orders}",
        f"{t(lang, 'profile_experience')}: {master.experience_years}",
        f"{t(lang, 'profile_area')}: {master.service_area or '—'}",
        f"{t(lang, 'profile_about')}: {master.about or '—'}",
    ]
    if master.services:
        lines.append("")
        lines.append(f"{t(lang, 'profile_services')}:")
        for item in master.services:
            if item.service is None:
                continue
            lines.append(
                f"• {item.service.name(lang)} — {format_money(item.price, lang)}"
            )
    return "\n".join(lines)


def render_balance(balance: MasterBalance | None, lang: str) -> str:
    available = balance.available if balance else Decimal("0")
    pending = balance.pending if balance else Decimal("0")
    locked = balance.locked if balance else Decimal("0")
    earned = balance.total_earned if balance else Decimal("0")
    withdrawn = balance.total_withdrawn if balance else Decimal("0")
    commission = balance.commission_total if balance else Decimal("0")
    return "\n".join(
        [
            t(lang, "balance_title"),
            "",
            f"💰 {t(lang, 'balance_available')}: {format_money(available, lang)}",
            f"⏳ {t(lang, 'balance_pending')}: {format_money(pending, lang)}",
            f"🔒 {t(lang, 'balance_locked')}: {format_money(locked, lang)}",
            f"📈 {t(lang, 'balance_earned')}: {format_money(earned, lang)}",
            f"📜 {t(lang, 'balance_withdrawn')}: {format_money(withdrawn, lang)}",
            f"🏦 {t(lang, 'balance_commission')}: {format_money(commission, lang)}",
        ]
    )


def render_tx(tx: BalanceTransaction, lang: str) -> str:
    sign = "+" if tx.amount >= 0 else "−"
    return t(
        lang,
        "tx_item",
        date=format_dt(tx.created_at, lang),
        type=t(lang, f"tx_type_{tx.tx_type}"),
        sign=sign,
        amount=format_money(abs(tx.amount), lang),
        available=format_money(tx.available_after, lang),
    )


def render_withdrawal(request: WithdrawalRequest, lang: str) -> str:
    return t(
        lang,
        "withdrawal_item",
        id=request.id,
        date=format_dt(request.created_at, lang),
        amount=format_money(request.amount, lang),
        card=request.card_masked or "—",
        txn=request.provider_txn_id or "—",
        status=t(lang, f"withdrawal_status_{request.status}"),
    )


def render_application(master: Master, lang: str) -> str:
    prices = ", ".join(
        f"{item.service.name(lang)}: {format_money(item.price, lang)}"
        for item in master.services
        if item.service is not None
    )
    return t(
        lang,
        "master_application_card",
        name=master.full_name,
        phone=format_phone(master.phone),
        username=f"@{master.user.username}" if master.user and master.user.username else t(lang, "no_username"),
        experience=master.experience_years,
        area=master.service_area or "—",
        prices=prices or "—",
        about=master.about or "—",
    )


def render_user(user: User, lang: str) -> str:
    return t(
        lang,
        "user_item",
        name=user.full_name,
        telegram_id=user.telegram_id,
        role=t(lang, f"role_{user.role or 'client'}_label"),
        phone=format_phone(user.phone),
        status=t(
            lang,
            "admin_user_blocked_status" if user.is_blocked else "admin_user_active_status",
        ),
    )


def render_payment(payment: Payment, lang: str) -> str:
    return t(
        lang,
        "payment_item",
        id=payment.id,
        status=t(lang, f"payment_status_{payment.status}"),
        order_id=payment.order.public_id if payment.order else "—",
        amount=format_money(payment.amount, lang),
        provider=payment.provider.capitalize(),
        date=format_dt(payment.created_at, lang),
    )


def render_complaint(complaint: Complaint, lang: str) -> str:
    return t(
        lang,
        "complaint_detail",
        id=complaint.id,
        order_id=complaint.order.public_id if complaint.order else "—",
        user=complaint.user.full_name if complaint.user else "—",
        status=t(lang, f"complaint_status_{complaint.status}"),
        text=complaint.text,
    )


def render_admin_order(order: Order, lang: str) -> str:
    lines = [render_order(order, lang, viewer="admin")]
    if order.master_earning is not None:
        lines.append(
            f"💰 {t(lang, 'label_income')}: {format_money(order.master_earning, lang)}"
        )
    if order.commission_amount is not None:
        lines.append(
            f"🏦 {t(lang, 'label_commission')}: {format_money(order.commission_amount, lang)}"
        )
    paid_payments = [p for p in order.payments if p.status == PaymentStatus.PAID.value]
    for payment in paid_payments:
        lines.append(
            f"💳 {t(lang, 'label_payment')}: #{payment.id} "
            f"{format_money(payment.amount, lang)} ({payment.provider})"
        )
    if order.review:
        lines.append(
            t(
                lang,
                "admin_order_review",
                rating=order.review.rating,
                comment=order.review.comment or t(lang, "no_comment"),
            )
        )
    return "\n".join(lines)


def render_admin_withdrawal(request: WithdrawalRequest, lang: str) -> str:
    return "\n".join(
        [
            f"💸 #{request.id}",
            f"{t(lang, 'label_master')}: {request.master.full_name if request.master else '—'}",
            f"{t(lang, 'label_amount')}: {format_money(request.amount, lang)}",
            f"{t(lang, 'label_card')}: {request.card_masked or '—'}",
            f"{t(lang, 'label_status')}: {t(lang, f'withdrawal_status_{request.status}')}",
            f"{t(lang, 'label_created')}: {format_dt(request.created_at, lang)}",
        ]
    )


def render_dashboard(stats: dict, lang: str) -> str:
    return t(
        lang,
        "admin_stats",
        users=stats["users"],
        clients=stats["clients"],
        masters=stats["masters"],
        pending_masters=stats["pending_masters"],
        orders=stats["orders"],
        active_orders=stats["active_orders"],
        completed_orders=stats["completed_orders"],
        cancelled_orders=stats["cancelled_orders"],
        paid_total=format_money(stats["paid_total"], lang),
        commission_total=format_money(stats["commission_total"], lang),
        pending_withdrawals=format_money(stats["pending_withdrawals"], lang),
        open_complaints=stats["open_complaints"],
    )


def render_platform_balance(stats: dict, lang: str) -> str:
    return "\n".join(
        [
            t(lang, "platform_balance_title"),
            "",
            f"{t(lang, 'platform_total_paid')}: {format_money(stats['paid_total'], lang)}",
            f"{t(lang, 'platform_income')}: {format_money(stats['commission_total'], lang)}",
            f"{t(lang, 'platform_escrow')}: {format_money(stats['escrow_held'], lang)}",
            f"{t(lang, 'platform_pending_withdrawals')}: "
            f"{format_money(stats['pending_withdrawals'], lang)}",
        ]
    )


def render_datetime(value: datetime | None, lang: str) -> str:
    return format_dt(value, lang)
