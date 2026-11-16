from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import (
    Complaint,
    Master,
    Notification,
    Order,
    User,
    WithdrawalRequest,
)
from keyboards.inline import (
    choose_other_master_kb,
    new_order_master_kb,
    payment_offer_kb,
    review_shortcut_kb,
)
from utils.formatting import format_money, format_phone
from utils.i18n import t

logger = logging.getLogger(__name__)


async def _send(
    bot: Bot,
    session: AsyncSession,
    user: User | None,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    kind: str = "system",
) -> bool:
    if user is None:
        return False
    session.add(
        Notification(
            user_id=user.id,
            notification_type=kind,
            text=text[:4000],
        )
    )
    await session.flush()
    if user.is_blocked:
        return False
    try:
        await bot.send_message(user.telegram_id, text, reply_markup=reply_markup)
        return True
    except TelegramAPIError as exc:
        logger.warning("Telegram send failed for user %s: %s", user.telegram_id, exc)
        return False


async def notify_admins(
    bot: Bot,
    session: AsyncSession,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    kind: str = "admin",
) -> int:
    sent = 0
    for admin_id in settings.admin_ids:
        admin_user = await session.scalar(select(User).where(User.telegram_id == admin_id))
        if admin_user is not None:
            if await _send(bot, session, admin_user, text, reply_markup=reply_markup, kind=kind):
                sent += 1
            continue
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup)
            sent += 1
        except TelegramAPIError as exc:
            logger.warning("Telegram send failed for admin %s: %s", admin_id, exc)
    return sent


def _master_lang(order: Order) -> str:
    if order.master and order.master.user and order.master.user.language:
        return order.master.user.language
    return settings.DEFAULT_LANGUAGE


def _client_lang(order: Order) -> str:
    return order.client.language if order.client and order.client.language else settings.DEFAULT_LANGUAGE


def _contacts(order: Order, lang: str, for_master: bool) -> str:
    if for_master:
        client = order.client
        return t(
            lang,
            "contacts_title_master",
            client=client.full_name,
            phone=format_phone(client.phone),
            telegram=client.telegram_link or t(lang, "no_username"),
        )
    master = order.master
    if master is None:
        return ""
    return t(
        lang,
        "contacts_title_client",
        master=master.full_name,
        phone=format_phone(master.phone),
        telegram=master.user.telegram_link if master.user else t(lang, "no_username"),
    )


async def notify_new_order(bot: Bot, session: AsyncSession, order: Order) -> None:
    if order.master is None or order.master.user is None:
        return
    lang = _master_lang(order)
    text = t(
        lang,
        "new_order_for_master",
        order_id=order.public_id,
        service=order.service_name,
        client=order.client.full_name,
        date=order.scheduled_date.strftime("%d.%m.%Y") if order.scheduled_date else "—",
        time=order.scheduled_time or "—",
        address=order.address or "—",
        description=order.description or "—",
        price=format_money(order.price, lang),
    )
    await _send(
        bot,
        session,
        order.master.user,
        text,
        reply_markup=new_order_master_kb(lang, order.id),
        kind="new_order",
    )


async def notify_order_accepted(bot: Bot, session: AsyncSession, order: Order) -> None:
    lang = _client_lang(order)
    master_name = order.master.full_name if order.master else "—"
    text = t(
        lang,
        "order_accepted_client",
        master=master_name,
        service=order.service_name,
        price=format_money(order.price, lang),
    )
    await _send(
        bot,
        session,
        order.client,
        text,
        reply_markup=payment_offer_kb(lang, order.id),
        kind="order_accepted",
    )


async def notify_order_rejected(bot: Bot, session: AsyncSession, order: Order) -> None:
    lang = _client_lang(order)
    master_name = order.master.full_name if order.master else "—"
    text = t(lang, "order_rejected_client", master=master_name)
    await _send(
        bot,
        session,
        order.client,
        text,
        reply_markup=choose_other_master_kb(lang, order.id),
        kind="order_rejected",
    )


async def notify_order_expired(bot: Bot, session: AsyncSession, order: Order) -> None:
    lang = _client_lang(order)
    await _send(
        bot,
        session,
        order.client,
        t(lang, "order_expired_client"),
        reply_markup=choose_other_master_kb(lang, order.id),
        kind="order_expired",
    )


async def notify_payment_expired(bot: Bot, session: AsyncSession, order: Order) -> None:
    client_lang = _client_lang(order)
    await _send(
        bot,
        session,
        order.client,
        t(client_lang, "notif_order_cancelled", order_id=order.public_id),
        kind="order_cancelled",
    )
    if order.master and order.master.user:
        master_lang = _master_lang(order)
        await _send(
            bot,
            session,
            order.master.user,
            t(master_lang, "order_cancelled_by_client", order_id=order.public_id),
            kind="order_cancelled",
        )


async def notify_order_cancelled_to_master(
    bot: Bot, session: AsyncSession, order: Order
) -> None:
    if order.master is None or order.master.user is None:
        return
    lang = _master_lang(order)
    await _send(
        bot,
        session,
        order.master.user,
        t(lang, "order_cancelled_by_client", order_id=order.public_id),
        kind="order_cancelled",
    )


async def notify_payment_success(bot: Bot, session: AsyncSession, order: Order) -> None:
    client_lang = _client_lang(order)
    await _send(
        bot,
        session,
        order.client,
        t(
            client_lang,
            "payment_success_client",
            order_id=order.public_id,
            amount=format_money(order.price, client_lang),
        ),
        kind="payment_success",
    )
    await _send(
        bot,
        session,
        order.client,
        _contacts(order, client_lang, for_master=False),
        kind="contacts",
    )

    if order.master and order.master.user:
        master_lang = _master_lang(order)
        await _send(
            bot,
            session,
            order.master.user,
            t(
                master_lang,
                "payment_success_master",
                order_id=order.public_id,
                amount=format_money(order.price, master_lang),
            ),
            kind="payment_success",
        )
        await _send(
            bot,
            session,
            order.master.user,
            _contacts(order, master_lang, for_master=True),
            kind="contacts",
        )


async def notify_work_started(bot: Bot, session: AsyncSession, order: Order) -> None:
    lang = _client_lang(order)
    await _send(
        bot,
        session,
        order.client,
        t(
            lang,
            "master_work_started",
            master=order.master.full_name if order.master else "—",
            order_id=order.public_id,
        ),
        kind="work_started",
    )


async def notify_order_done(bot: Bot, session: AsyncSession, order: Order) -> None:
    lang = _client_lang(order)
    from keyboards.inline import order_actions_kb

    await _send(
        bot,
        session,
        order.client,
        t(
            lang,
            "master_done_client",
            order_id=order.public_id,
            service=order.service_name,
        ),
        reply_markup=order_actions_kb(
            lang,
            order.id,
            order.status,
            can_confirm=True,
        ),
        kind="order_done",
    )


async def notify_order_completed(
    bot: Bot, session: AsyncSession, order: Order, income: Decimal, commission: Decimal
) -> None:
    client_lang = _client_lang(order)
    await _send(
        bot,
        session,
        order.client,
        t(client_lang, "order_completed_client"),
        reply_markup=review_shortcut_kb(client_lang, order.id),
        kind="order_completed",
    )
    if order.master and order.master.user:
        master_lang = _master_lang(order)
        await _send(
            bot,
            session,
            order.master.user,
            t(
                master_lang,
                "order_completed_master",
                order_id=order.public_id,
                income=format_money(income, master_lang),
                commission=format_money(commission, master_lang),
            ),
            kind="order_completed",
        )


async def notify_dispute(
    bot: Bot, session: AsyncSession, order: Order, complaint: Complaint
) -> None:
    if order.master and order.master.user:
        master_lang = _master_lang(order)
        await _send(
            bot,
            session,
            order.master.user,
            t(master_lang, "dispute_master_ack"),
            kind="dispute",
        )
    admin_lang = settings.DEFAULT_LANGUAGE
    text = t(
        admin_lang,
        "notif_dispute_admin",
        order_id=order.public_id,
        client=order.client.full_name,
        master=order.master.full_name if order.master else "—",
        price=format_money(order.price, admin_lang),
        reason=order.dispute_reason or complaint.text,
    )
    await notify_admins(bot, session, text, kind="dispute")


async def notify_master_application(
    bot: Bot, session: AsyncSession, master: Master
) -> None:
    admin_lang = settings.DEFAULT_LANGUAGE
    text = t(
        admin_lang,
        "notif_master_application_admin",
        name=master.full_name,
        phone=format_phone(master.phone),
        experience=master.experience_years,
        area=master.service_area,
    )
    await notify_admins(bot, session, text, kind="master_application")


async def notify_master_decision(
    bot: Bot, session: AsyncSession, master: Master, approved: bool, reason: str = ""
) -> None:
    if master.user is None:
        return
    lang = master.user.language or settings.DEFAULT_LANGUAGE
    if approved:
        text = t(lang, "notif_master_approved")
    else:
        text = t(lang, "notif_master_rejected", reason=reason)
    await _send(bot, session, master.user, text, kind="master_decision")


async def notify_withdrawal_created(
    bot: Bot, session: AsyncSession, request: WithdrawalRequest
) -> None:
    admin_lang = settings.DEFAULT_LANGUAGE
    master_name = "—"
    if request.master_id:
        master_name = (
            await session.scalar(
                select(Master.full_name).where(Master.id == request.master_id)
            )
            or "—"
        )
    text = t(
        admin_lang,
        "notif_withdrawal_created_admin",
        request_id=request.id,
        master=master_name,
        amount=format_money(request.amount, admin_lang),
        card=request.card_masked or "—",
    )
    await notify_admins(bot, session, text, kind="withdrawal_request")


async def notify_withdrawal_approved(
    bot: Bot, session: AsyncSession, request: WithdrawalRequest
) -> None:
    if request.master and request.master.user:
        lang = request.master.user.language or settings.DEFAULT_LANGUAGE
        await _send(
            bot,
            session,
            request.master.user,
            t(lang, "notif_withdrawal_approved", amount=format_money(request.amount, lang)),
            kind="withdrawal",
        )


async def notify_withdrawal_paid(
    bot: Bot, session: AsyncSession, request: WithdrawalRequest
) -> None:
    if request.master and request.master.user:
        lang = request.master.user.language or settings.DEFAULT_LANGUAGE
        await _send(
            bot,
            session,
            request.master.user,
            t(
                lang,
                "notif_withdrawal_paid",
                amount=format_money(request.amount, lang),
                txn=request.provider_txn_id or "—",
            ),
            kind="withdrawal",
        )


async def notify_withdrawal_rejected(
    bot: Bot, session: AsyncSession, request: WithdrawalRequest
) -> None:
    if request.master and request.master.user:
        lang = request.master.user.language or settings.DEFAULT_LANGUAGE
        await _send(
            bot,
            session,
            request.master.user,
            t(
                lang,
                "notif_withdrawal_rejected",
                amount=format_money(request.amount, lang),
                reason=request.admin_comment or "—",
            ),
            kind="withdrawal",
        )


async def notify_block(
    bot: Bot, session: AsyncSession, user: User, blocked: bool, reason: str = ""
) -> None:
    lang = user.language or settings.DEFAULT_LANGUAGE
    if blocked:
        text = t(lang, "blocked_notify", reason=reason)
    else:
        text = t(lang, "unblocked_notify")
    await _send(bot, session, user, text, kind="block")


async def notify_complaint_resolved(
    bot: Bot, session: AsyncSession, complaint: Complaint, decision: str
) -> None:
    if complaint.user is None:
        return
    lang = complaint.user.language or settings.DEFAULT_LANGUAGE
    await _send(
        bot,
        session,
        complaint.user,
        t(lang, "notif_complaint_resolved", decision=decision),
        kind="complaint",
    )


async def notify_dispute_resolved(
    bot: Bot,
    session: AsyncSession,
    order: Order,
    *,
    favor_master: bool,
    note: str,
) -> None:
    client_lang = _client_lang(order)
    await _send(
        bot,
        session,
        order.client,
        t(
            client_lang,
            "dispute_won_client_notify"
            if not favor_master
            else "dispute_lost_client_notify",
        ),
        kind="dispute",
    )
    if order.master and order.master.user:
        master_lang = _master_lang(order)
        await _send(
            bot,
            session,
            order.master.user,
            t(
                master_lang,
                "dispute_resolved_master_notify"
                if favor_master
                else "dispute_resolved_client_notify",
            ),
            kind="dispute",
        )


async def notify_review(
    bot: Bot,
    session: AsyncSession,
    master_user: User,
    rating: int,
    comment: str | None,
) -> None:
    lang = master_user.language or settings.DEFAULT_LANGUAGE
    await _send(
        bot,
        session,
        master_user,
        t(
            lang,
            "review_thanks_master",
            rating=rating,
            comment=comment or t(lang, "no_comment"),
        ),
        kind="review",
    )
