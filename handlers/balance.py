from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.enums import MasterStatus
from database.models import BalanceTransaction, MasterCard, User, WithdrawalRequest
from keyboards.callbacks import AdminCB, WithdrawCB
from keyboards.inline import (
    balance_kb,
    withdraw_cards_kb,
    withdraw_confirm_kb,
)
from keyboards.reply import cancel_only, master_menu
from services import balance_service, notification_service
from services.balance_service import BalanceError
from services.settings_service import get_min_withdrawal
from states.forms import WithdrawForm
from utils.crypto import card_last4, encrypt_card, masked_card
from utils.formatting import format_money, parse_decimal
from utils.i18n import button_variants, t
from utils.rendering import render_balance, render_tx, render_withdrawal
from utils.validators import normalize_card_number

router = Router(name="balance")


def _master_or_none(user: User):
    master = user.master
    if master is None or user.role != "master":
        return None
    return master


@router.message(F.text.in_(button_variants("btn_master_balance")))
async def master_balance(
    message: Message, session: AsyncSession, db_user: User, lang: str, _
) -> None:
    master = _master_or_none(db_user)
    if master is None:
        await message.answer(t(lang, "master_role_not_selected"))
        return
    balance = await balance_service.get_or_create_balance(session, master.id)
    await session.flush()
    await message.answer(render_balance(balance, lang), reply_markup=balance_kb(lang))


@router.callback_query(AdminCB.filter((F.section == "self") & (F.action == "tx")))
async def master_transactions(
    callback: CallbackQuery, session: AsyncSession, db_user: User, lang: str, _
) -> None:
    master = _master_or_none(db_user)
    if master is None:
        await callback.answer(t(lang, "master_role_not_selected"), show_alert=True)
        return
    transactions = (
        await session.scalars(
            select(BalanceTransaction)
            .where(BalanceTransaction.master_id == master.id)
            .order_by(BalanceTransaction.id.desc())
            .limit(20)
        )
    ).all()
    text = t(lang, "tx_title")
    if transactions:
        text += "\n\n" + "\n\n".join(render_tx(tx, lang) for tx in transactions)
    else:
        text += "\n\n" + t(lang, "no_tx")
    if callback.message:
        await callback.message.answer(text)
    await callback.answer()


@router.message(F.text.in_(button_variants("btn_master_withdrawals")))
async def master_withdrawals(
    message: Message, session: AsyncSession, db_user: User, lang: str, _, is_admin: bool
) -> None:
    master = _master_or_none(db_user)
    if master is None:
        await message.answer(t(lang, "master_role_not_selected"))
        return
    requests = (
        await session.scalars(
            select(WithdrawalRequest)
            .where(WithdrawalRequest.master_id == master.id)
            .order_by(WithdrawalRequest.id.desc())
            .limit(10)
        )
    ).all()
    if not requests:
        await message.answer(
            t(lang, "no_withdrawals"), reply_markup=master_menu(lang, is_admin)
        )
        return
    text = t(lang, "withdrawals_title") + "\n\n" + "\n\n".join(
        render_withdrawal(request, lang) for request in requests
    )
    await message.answer(text, reply_markup=master_menu(lang, is_admin))


@router.message(F.text.in_(button_variants("btn_master_withdraw")))
async def withdraw_start_message(
    message: Message, session: AsyncSession, db_user: User, lang: str, _
) -> None:
    await _show_withdraw_cards(message, session, db_user, lang)


async def _show_withdraw_cards(
    message: Message, session: AsyncSession, user: User, lang: str
) -> None:
    master = _master_or_none(user)
    if master is None:
        await message.answer(t(lang, "master_role_not_selected"))
        return
    if master.status != MasterStatus.APPROVED.value:
        await message.answer(t(lang, "master_not_approved"))
        return
    cards = (
        await session.scalars(
            select(MasterCard)
            .where(MasterCard.master_id == master.id, MasterCard.is_active.is_(True))
            .order_by(MasterCard.id.desc())
        )
    ).all()
    text = t(lang, "cards_title")
    if not cards:
        text += "\n" + t(lang, "withdraw_no_card")
    await message.answer(
        text, reply_markup=withdraw_cards_kb(lang, list(cards))
    )


@router.callback_query(WithdrawCB.filter(F.action == "start"))
async def withdraw_start_callback(
    callback: CallbackQuery, session: AsyncSession, db_user: User, lang: str, _
) -> None:
    if callback.message:
        await _show_withdraw_cards(callback.message, session, db_user, lang)
    await callback.answer()


@router.callback_query(WithdrawCB.filter(F.action == "add_card"))
async def withdraw_add_card(
    callback: CallbackQuery, lang: str, _, state
) -> None:
    await state.clear()
    await state.update_data(add_card_for_withdraw=True)
    await state.set_state(WithdrawForm.card_number)
    if callback.message:
        await callback.message.answer(
            t(lang, "card_number_prompt"), reply_markup=cancel_only(lang)
        )
    await callback.answer()


@router.callback_query(WithdrawCB.filter(F.action == "card"))
async def withdraw_card_selected(
    callback: CallbackQuery,
    callback_data: WithdrawCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    state,
) -> None:
    master = _master_or_none(db_user)
    if master is None:
        await callback.answer(t(lang, "master_role_not_selected"), show_alert=True)
        return
    card = await session.get(MasterCard, callback_data.card_id)
    if card is None or card.master_id != master.id or not card.is_active:
        await callback.answer(t(lang, "card_required"), show_alert=True)
        return
    balance = await balance_service.get_or_create_balance(session, master.id)
    min_amount = await get_min_withdrawal(session)
    await state.update_data(withdraw_card_id=card.id)
    await state.set_state(WithdrawForm.amount)
    if callback.message:
        await callback.message.answer(
            t(
                lang,
                "withdraw_prompt",
                available=format_money(balance.available, lang),
                min=format_money(min_amount, lang),
            ),
            reply_markup=cancel_only(lang),
        )
    await callback.answer()


@router.callback_query(WithdrawCB.filter(F.action == "cancel"))
async def withdraw_cancel(callback: CallbackQuery, lang: str, _, state) -> None:
    await state.clear()
    if callback.message:
        try:
            await callback.message.edit_text(t(lang, "cancelled"))
        except Exception:
            await callback.message.answer(t(lang, "cancelled"))
    await callback.answer()


@router.message(WithdrawForm.card_number, F.text)
async def withdraw_card_number(message: Message, lang: str, _, state) -> None:
    value = normalize_card_number(message.text or "")
    if value is None:
        await message.answer(t(lang, "card_invalid"))
        return
    await state.update_data(new_card_number=value)
    await state.set_state(WithdrawForm.card_holder)
    await message.answer(
        t(lang, "card_holder_prompt"), reply_markup=cancel_only(lang)
    )


@router.message(WithdrawForm.card_holder, F.text)
async def withdraw_card_holder(message: Message, lang: str, _, state) -> None:
    value = " ".join((message.text or "").split())
    if len(value) < 3 or len(value) > 120:
        await message.answer(t(lang, "value_too_long"))
        return
    await state.update_data(new_card_holder=value)
    await state.set_state(WithdrawForm.card_bank)
    await message.answer(t(lang, "card_bank_prompt"), reply_markup=cancel_only(lang))


@router.message(WithdrawForm.card_bank, F.text)
async def withdraw_card_bank(
    message: Message,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    state,
    is_admin: bool,
) -> None:
    value = " ".join((message.text or "").split())
    if len(value) < 2 or len(value) > 120:
        await message.answer(t(lang, "value_too_long"))
        return
    master = _master_or_none(db_user)
    if master is None:
        await state.clear()
        await message.answer(t(lang, "master_role_not_selected"))
        return
    data = await state.get_data()
    number = data.get("new_card_number")
    holder = data.get("new_card_holder")
    if not number or not holder:
        await state.clear()
        await message.answer(t(lang, "session_expired"))
        return
    existing = await session.scalar(
        select(MasterCard.id).where(MasterCard.master_id == master.id)
    )
    card = MasterCard(
        master_id=master.id,
        card_number_encrypted=encrypt_card(number),
        card_last4=card_last4(number),
        card_masked=masked_card(number),
        card_holder=holder,
        bank_name=value,
        is_default=existing is None,
    )
    session.add(card)
    await session.flush()
    await message.answer(t(lang, "card_saved", card=card.card_masked))

    if data.get("add_card_for_withdraw"):
        min_amount = await get_min_withdrawal(session)
        balance = await balance_service.get_or_create_balance(session, master.id)
        await state.update_data(withdraw_card_id=card.id)
        await state.set_state(WithdrawForm.amount)
        await message.answer(
            t(
                lang,
                "withdraw_prompt",
                available=format_money(balance.available, lang),
                min=format_money(min_amount, lang),
            ),
            reply_markup=cancel_only(lang),
        )
    else:
        await state.clear()
        await message.answer(t(lang, "card_added"), reply_markup=master_menu(lang, is_admin))


@router.message(WithdrawForm.amount, F.text)
async def withdraw_amount(
    message: Message, session: AsyncSession, db_user: User, lang: str, _, state
) -> None:
    master = _master_or_none(db_user)
    if master is None:
        await state.clear()
        await message.answer(t(lang, "master_role_not_selected"))
        return
    data = await state.get_data()
    card_id = data.get("withdraw_card_id")
    if not card_id:
        await state.clear()
        await message.answer(t(lang, "card_required"))
        return
    amount = parse_decimal(message.text or "")
    if amount is None:
        await message.answer(t(lang, "amount_required"))
        return
    balance = await balance_service.get_or_create_balance(session, master.id)
    min_amount = await get_min_withdrawal(session)
    if amount < min_amount:
        await message.answer(
            t(lang, "withdraw_min_error", min=format_money(min_amount, lang))
        )
        return
    if amount > balance.available:
        await message.answer(
            t(
                lang,
                "withdraw_insufficient",
                available=format_money(balance.available, lang),
            )
        )
        return
    card = await session.get(MasterCard, card_id)
    if card is None:
        await message.answer(t(lang, "card_required"))
        return
    await state.update_data(withdraw_amount=str(amount))
    await message.answer(
        t(
            lang,
            "withdraw_confirm",
            amount=format_money(amount, lang),
            card=card.card_masked,
            holder=card.card_holder,
        ),
        reply_markup=withdraw_confirm_kb(lang),
    )


@router.callback_query(WithdrawCB.filter(F.action == "confirm"))
async def withdraw_confirm(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    state,
    bot,
) -> None:
    master = _master_or_none(db_user)
    data = await state.get_data()
    amount_raw = data.get("withdraw_amount")
    card_id = data.get("withdraw_card_id")
    if master is None or not amount_raw or not card_id:
        await callback.answer(t(lang, "session_expired"), show_alert=True)
        await state.clear()
        return
    card = await session.get(MasterCard, card_id)
    if card is None or card.master_id != master.id:
        await callback.answer(t(lang, "card_required"), show_alert=True)
        await state.clear()
        return
    min_amount = await get_min_withdrawal(session)
    try:
        request = await balance_service.hold_withdrawal(
            session, master, Decimal(amount_raw), card, min_amount
        )
        await session.flush()
        await session.commit()
        await notification_service.notify_withdrawal_created(bot, session, request)
        await state.clear()
        if callback.message:
            await callback.message.edit_text(
                t(
                    lang,
                    "withdraw_created",
                    request_id=request.id,
                    amount=format_money(request.amount, lang),
                    card=request.card_masked or "—",
                )
            )
    except BalanceError as exc:
        await callback.answer(t(lang, exc.key), show_alert=True)
        return
    await callback.answer()
