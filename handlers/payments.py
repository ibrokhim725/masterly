from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database.enums import OrderStatus
from database.models import Order, User
from keyboards.callbacks import PaymentCB
from keyboards.inline import payment_link_kb
from services import order_service, payment_service
from services.payment_service import PaymentError
from utils.formatting import format_money
from utils.i18n import t

router = Router(name="payments")


def _is_paid(order: Order) -> bool:
    return order.paid_at is not None or order.status in {
        OrderStatus.PAID.value,
        OrderStatus.IN_PROGRESS.value,
        OrderStatus.AWAITING_CONFIRMATION.value,
        OrderStatus.COMPLETED.value,
        OrderStatus.DISPUTED.value,
    }


@router.callback_query(PaymentCB.filter())
async def payment_callback(
    callback: CallbackQuery,
    callback_data: PaymentCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
) -> None:
    order = await order_service.load_order(session, callback_data.order_id)
    if order is None:
        await callback.answer(t(lang, "order_not_found"), show_alert=True)
        return
    if order.client_id != db_user.id:
        await callback.answer(t(lang, "not_your_order"), show_alert=True)
        return

    if _is_paid(order):
        if callback.message:
            await callback.message.answer(
                t(
                    lang,
                    "payment_success_client",
                    order_id=order.public_id,
                    amount=format_money(order.price, lang),
                )
            )
        await callback.answer()
        return

    if callback_data.action == "check":
        if order.status == OrderStatus.AWAITING_PAYMENT.value:
            await callback.answer(t(lang, "payment_check_pending"), show_alert=True)
        else:
            await callback.answer(t(lang, "order_not_payable"), show_alert=True)
        return

    if callback_data.action != "create":
        await callback.answer(t(lang, "unknown_callback"), show_alert=True)
        return

    try:
        payment = await payment_service.create_payment(session, order)
        if not payment.pay_url:
            raise PaymentError("payment_create_error")
        await session.commit()
    except PaymentError as exc:
        await callback.answer(t(lang, exc.key), show_alert=True)
        return

    if callback.message:
        await callback.message.answer(
            t(
                lang,
                "payment_waiting_title",
                order_id=order.public_id,
                amount=format_money(payment.amount, lang),
            ),
            reply_markup=payment_link_kb(lang, order.id, payment.pay_url),
        )
    await callback.answer()


@router.message(Command("pay"))
async def pay_command(
    message: Message, session: AsyncSession, db_user: User, lang: str, _
) -> None:
    if not message.text:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(t(lang, "order_not_found"))
        return
    order = await order_service.load_order_by_public_id(session, parts[1].strip())
    if order is None or order.client_id != db_user.id:
        await message.answer(t(lang, "order_not_found"))
        return
    if _is_paid(order):
        await message.answer(
            t(
                lang,
                "payment_success_client",
                order_id=order.public_id,
                amount=format_money(order.price, lang),
            )
        )
        return
    try:
        payment = await payment_service.create_payment(session, order)
        if not payment.pay_url:
            raise PaymentError("payment_create_error")
        await session.commit()
    except PaymentError as exc:
        await message.answer(t(lang, exc.key))
        return
    await message.answer(
        t(
            lang,
            "payment_waiting_title",
            order_id=order.public_id,
            amount=format_money(payment.amount, lang),
        ),
        reply_markup=payment_link_kb(lang, order.id, payment.pay_url),
    )
