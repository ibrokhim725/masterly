from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.enums import (
    ComplaintStatus,
    MasterStatus,
    OrderStatus,
    PaymentStatus,
    PriceType,
    WithdrawalStatus,
)
from database.models import (
    Complaint,
    Master,
    Order,
    Payment,
    Service,
    User,
    WithdrawalRequest,
)
from keyboards.callbacks import AdminCB
from keyboards.inline import (
    admin_back_kb,
    admin_broadcast_confirm_kb,
    admin_complaint_kb,
    admin_complaints_kb,
    admin_master_kb,
    admin_masters_kb,
    admin_menu_kb,
    admin_order_kb,
    admin_orders_kb,
    admin_payment_kb,
    admin_payments_kb,
    admin_price_type_kb,
    admin_service_kb,
    admin_services_kb,
    admin_settings_kb,
    admin_skip_txn_kb,
    admin_user_kb,
    admin_users_kb,
    admin_withdrawal_kb,
    admin_withdrawals_kb,
)
from keyboards.reply import admin_menu
from services import (
    balance_service,
    notification_service,
    order_service,
    settings_service,
)
from services.balance_service import BalanceError
from services.order_service import OrderError
from states.forms import AdminForm
from utils.crypto import decrypt_card
from utils.formatting import (
    format_dt,
    format_money,
    format_phone,
    parse_decimal,
    split_safe,
)
from utils.i18n import button_variants, t
from utils.rendering import (
    render_admin_order,
    render_admin_withdrawal,
    render_application,
    render_complaint,
    render_dashboard,
    render_payment,
    render_platform_balance,
    render_user,
)

router = Router(name="admin")

PAGE_SIZE = 8
SKIP_VARIANTS = button_variants("btn_skip")


def _pages(total: int) -> int:
    return max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)


async def _send_admin_panel(message: Message, lang: str) -> None:
    await message.answer(
        t(lang, "admin_panel_title"), reply_markup=admin_menu_kb(lang)
    )


async def _guard_message(message: Message, lang: str, is_admin: bool) -> bool:
    if not is_admin:
        await message.answer(t(lang, "admin_denied"))
        return False
    return True


async def _guard_callback(
    callback: CallbackQuery, lang: str, is_admin: bool
) -> bool:
    if not is_admin:
        await callback.answer(t(lang, "admin_denied"), show_alert=True)
        return False
    return True


async def _edit(
    callback: CallbackQuery, text: str, markup=None
) -> None:
    if not callback.message:
        return
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramAPIError:
        await callback.message.answer(text, reply_markup=markup)


@router.message(Command("admin"))
async def admin_command(
    message: Message, lang: str, is_admin: bool, state
) -> None:
    if not await _guard_message(message, lang, is_admin):
        return
    await state.clear()
    await _send_admin_panel(message, lang)


@router.message(F.text.in_(button_variants("btn_admin_panel")))
async def admin_panel_button(
    message: Message, lang: str, is_admin: bool, state
) -> None:
    if not await _guard_message(message, lang, is_admin):
        return
    await state.clear()
    await _send_admin_panel(message, lang)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


@router.callback_query(AdminCB.filter(F.section == "home"))
async def admin_home(
    callback: CallbackQuery, lang: str, is_admin: bool, state
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    await state.clear()
    await _edit(
        callback, t(lang, "admin_panel_title"), admin_menu_kb(lang)
    )
    await callback.answer()


@router.callback_query(AdminCB.filter(F.section == "stats"))
async def admin_stats(
    callback: CallbackQuery, session: AsyncSession, lang: str, is_admin: bool
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    stats = await settings_service.get_platform_stats(session)
    await _edit(callback, render_dashboard(stats, lang), admin_back_kb(lang))
    await callback.answer()


@router.callback_query(AdminCB.filter(F.section == "platform"))
async def admin_platform(
    callback: CallbackQuery, session: AsyncSession, lang: str, is_admin: bool
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    stats = await settings_service.get_platform_stats(session)
    await _edit(callback, render_platform_balance(stats, lang), admin_back_kb(lang))
    await callback.answer()


@router.callback_query(AdminCB.filter(F.section == "users"))
async def admin_users(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    total = await session.scalar(select(func.count(User.id))) or 0
    pages = _pages(total)
    page = max(0, min(callback_data.page, pages - 1))
    users = (
        await session.scalars(
            select(User)
            .order_by(User.id.desc())
            .offset(page * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
    ).all()
    text = t(lang, "btn_admin_users")
    if pages > 1:
        text += "\n" + t(lang, "page_of", current=page + 1, total=pages)
    await _edit(callback, text, admin_users_kb(lang, list(users), page, pages))
    await callback.answer()


@router.callback_query(
    AdminCB.filter((F.section == "user") & (F.obj_id > 0) & (F.action == ""))
)
async def admin_user_detail(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    user = await session.get(User, callback_data.obj_id)
    if user is None:
        await callback.answer(t(lang, "not_found"), show_alert=True)
        return
    text = render_user(user, lang)
    if user.blocked_reason:
        text += "\n" + t(lang, "label_reason") + f": {user.blocked_reason}"
    await _edit(callback, text, admin_user_kb(lang, user, callback_data.page))
    await callback.answer()


@router.callback_query(AdminCB.filter((F.section == "user") & (F.action == "unblock")))
async def admin_user_unblock(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    bot,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    user = await session.get(User, callback_data.obj_id)
    if user is None:
        await callback.answer(t(lang, "not_found"), show_alert=True)
        return
    user.is_blocked = False
    user.blocked_reason = None
    await session.flush()
    await notification_service.notify_block(bot, session, user, False)
    await session.commit()
    await callback.answer(t(lang, "unblocked_notify"))
    await _edit(
        callback, render_user(user, lang), admin_user_kb(lang, user, callback_data.page)
    )


@router.callback_query(AdminCB.filter((F.section == "user") & (F.action == "block")))
async def admin_user_block_prompt(
    callback: CallbackQuery,
    callback_data: AdminCB,
    lang: str,
    is_admin: bool,
    state,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    await state.clear()
    await state.update_data(
        admin_user_id=callback_data.obj_id, admin_page=callback_data.page
    )
    await state.set_state(AdminForm.block_reason)
    if callback.message:
        await callback.message.answer(t(lang, "ask_block_reason"))
    await callback.answer()


@router.message(AdminForm.block_reason, F.text)
async def admin_user_block_reason(
    message: Message,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    state,
    bot,
) -> None:
    if not await _guard_message(message, lang, is_admin):
        return
    reason = (message.text or "").strip()
    if len(reason) < 3:
        await message.answer(t(lang, "ask_block_reason"))
        return
    data = await state.get_data()
    user = await session.get(User, data.get("admin_user_id"))
    if user is None:
        await state.clear()
        await message.answer(t(lang, "not_found"))
        return
    user.is_blocked = True
    user.blocked_reason = reason[:255]
    await session.flush()
    await notification_service.notify_block(bot, session, user, True, reason)
    await session.commit()
    await state.clear()
    await message.answer(t(lang, "blocked_notify", reason=reason))


@router.callback_query(AdminCB.filter(F.section == "masters"))
async def admin_masters(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    total = await session.scalar(select(func.count(Master.id))) or 0
    pages = _pages(total)
    page = max(0, min(callback_data.page, pages - 1))
    masters = (
        await session.scalars(
            select(Master)
            .order_by(Master.id.desc())
            .offset(page * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
    ).all()
    text = t(lang, "btn_admin_masters")
    if pages > 1:
        text += "\n" + t(lang, "page_of", current=page + 1, total=pages)
    await _edit(callback, text, admin_masters_kb(lang, list(masters), page, pages))
    await callback.answer()


@router.callback_query(
    AdminCB.filter((F.section == "master") & (F.obj_id > 0) & (F.action == ""))
)
async def admin_master_detail(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    master = await session.get(Master, callback_data.obj_id)
    if master is None:
        await callback.answer(t(lang, "not_found"), show_alert=True)
        return
    text = render_application(master, lang)
    if master.status == MasterStatus.REJECTED.value and master.rejection_reason:
        text += "\n\n" + t(lang, "label_reason") + f": {master.rejection_reason}"
    cards = "—"
    if master.cards:
        cards = ", ".join(card.card_masked for card in master.cards)
    text += "\n" + t(lang, "label_card") + f": {cards}"
    await _edit(callback, text, admin_master_kb(lang, master, callback_data.page))
    await callback.answer()


@router.callback_query(AdminCB.filter((F.section == "master") & (F.action == "approve")))
async def admin_master_approve(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    bot,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    master = await session.get(Master, callback_data.obj_id)
    if master is None:
        await callback.answer(t(lang, "not_found"), show_alert=True)
        return
    master.status = MasterStatus.APPROVED.value
    master.approved_at = datetime.now(timezone.utc)
    master.approved_by = callback.from_user.id
    master.rejection_reason = None
    master.is_active = True
    if master.user is not None:
        master.user.role = "master"
    await session.flush()
    await notification_service.notify_master_decision(bot, session, master, True)
    await session.commit()
    await callback.answer(t(lang, "notif_master_approved"))
    await _edit(
        callback,
        render_application(master, lang),
        admin_master_kb(lang, master, callback_data.page),
    )


@router.callback_query(AdminCB.filter((F.section == "master") & (F.action == "reject")))
async def admin_master_reject_prompt(
    callback: CallbackQuery,
    callback_data: AdminCB,
    lang: str,
    is_admin: bool,
    state,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    await state.clear()
    await state.update_data(
        admin_master_id=callback_data.obj_id, admin_page=callback_data.page
    )
    await state.set_state(AdminForm.master_reject_reason)
    if callback.message:
        await callback.message.answer(t(lang, "ask_reject_reason"))
    await callback.answer()


@router.message(AdminForm.master_reject_reason, F.text)
async def admin_master_reject_reason(
    message: Message,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    state,
    bot,
) -> None:
    if not await _guard_message(message, lang, is_admin):
        return
    reason = (message.text or "").strip()
    if len(reason) < 3:
        await message.answer(t(lang, "ask_reject_reason"))
        return
    data = await state.get_data()
    master = await session.get(Master, data.get("admin_master_id"))
    if master is None:
        await state.clear()
        await message.answer(t(lang, "not_found"))
        return
    master.status = MasterStatus.REJECTED.value
    master.rejection_reason = reason[:500]
    master.is_active = False
    master.approved_at = None
    master.approved_by = None
    await session.flush()
    await notification_service.notify_master_decision(
        bot, session, master, False, reason
    )
    await session.commit()
    await state.clear()
    await message.answer(t(lang, "application_rejected", reason=reason))


@router.callback_query(AdminCB.filter((F.section == "master") & (F.action == "toggle_active")))
async def admin_master_toggle(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    master = await session.get(Master, callback_data.obj_id)
    if master is None:
        await callback.answer(t(lang, "not_found"), show_alert=True)
        return
    master.is_active = not master.is_active
    await session.flush()
    await callback.answer(
        t(lang, "master_resumed" if master.is_active else "master_paused")
    )
    await _edit(
        callback,
        render_application(master, lang),
        admin_master_kb(lang, master, callback_data.page),
    )


@router.callback_query(AdminCB.filter(F.section == "orders"))
async def admin_orders(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    total = await session.scalar(select(func.count(Order.id))) or 0
    pages = _pages(total)
    page = max(0, min(callback_data.page, pages - 1))
    orders = (
        await session.scalars(
            select(Order)
            .order_by(Order.id.desc())
            .offset(page * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
    ).all()
    text = t(lang, "admin_orders_title")
    if not orders:
        text += "\n" + t(lang, "no_admin_orders")
    elif pages > 1:
        text += "\n" + t(lang, "page_of", current=page + 1, total=pages)
    await _edit(
        callback, text, admin_orders_kb(lang, list(orders), page, pages)
    )
    await callback.answer()


@router.callback_query(
    AdminCB.filter((F.section == "order") & (F.obj_id > 0) & (F.action == ""))
)
async def admin_order_detail(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    order = await order_service.load_order(session, callback_data.obj_id)
    if order is None:
        await callback.answer(t(lang, "order_not_found"), show_alert=True)
        return
    await _edit(
        callback,
        render_admin_order(order, lang),
        admin_order_kb(lang, order, callback_data.page),
    )
    await callback.answer()


@router.callback_query(
    AdminCB.filter(
        (F.section == "order") & F.action.in_({"resolve_master", "resolve_client"})
    )
)
async def admin_order_resolve_prompt(
    callback: CallbackQuery,
    callback_data: AdminCB,
    lang: str,
    is_admin: bool,
    state,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    await state.clear()
    await state.update_data(
        resolve_order_id=callback_data.obj_id,
        resolve_favor_master=callback_data.action == "resolve_master",
        admin_page=callback_data.page,
    )
    await state.set_state(AdminForm.resolve_note)
    if callback.message:
        await callback.message.answer(t(lang, "ask_resolve_note"))
    await callback.answer()


@router.message(AdminForm.resolve_note, F.text)
async def admin_order_resolve_note(
    message: Message,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    state,
    bot,
) -> None:
    if not await _guard_message(message, lang, is_admin):
        return
    note = (message.text or "").strip()
    if len(note) < 3:
        await message.answer(t(lang, "ask_resolve_note"))
        return
    data = await state.get_data()
    order_id = data.get("resolve_order_id")
    favor_master = bool(data.get("resolve_favor_master"))
    if not order_id:
        await state.clear()
        await message.answer(t(lang, "session_expired"))
        return
    try:
        await order_service.resolve_dispute(
            session,
            order_id,
            favor_master=favor_master,
            note=note,
            admin_telegram_id=message.from_user.id if message.from_user else 0,
        )
        await session.flush()
        await session.commit()
        order = await order_service.load_order(session, order_id)
        if order is not None:
            if favor_master and order.master_earning is not None:
                await notification_service.notify_order_completed(
                    bot,
                    session,
                    order,
                    order.master_earning,
                    order.commission_amount or Decimal("0"),
                )
            await notification_service.notify_dispute_resolved(
                bot, session, order, favor_master=favor_master, note=note
            )
        await state.clear()
        await message.answer(t(lang, "complaint_resolved_toast"))
        if order is not None:
            await message.answer(
                render_admin_order(order, lang),
                reply_markup=admin_order_kb(lang, order, data.get("admin_page", 0)),
            )
    except (OrderError, BalanceError) as exc:
        await state.clear()
        await message.answer(t(lang, exc.key))


@router.callback_query(AdminCB.filter(F.section == "services"))
async def admin_services(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    state,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    if await state.get_state() in {
        AdminForm.service_price.state,
        AdminForm.service_name_uz.state,
        AdminForm.service_name_ru.state,
    }:
        await state.clear()
    total = await session.scalar(select(func.count(Service.id))) or 0
    pages = _pages(total)
    page = max(0, min(callback_data.page, pages - 1))
    services = (
        await session.scalars(
            select(Service)
            .order_by(Service.sort_order, Service.id)
            .offset(page * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
    ).all()
    text = t(lang, "services_admin_title")
    if not services:
        text += "\n" + t(lang, "no_services_admin")
    elif pages > 1:
        text += "\n" + t(lang, "page_of", current=page + 1, total=pages)
    await _edit(
        callback, text, admin_services_kb(lang, list(services), page, pages)
    )
    await callback.answer()


@router.callback_query(
    AdminCB.filter((F.section == "service") & (F.obj_id > 0) & (F.action == ""))
)
async def admin_service_detail(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    service = await session.get(Service, callback_data.obj_id)
    if service is None:
        await callback.answer(t(lang, "service_not_found"), show_alert=True)
        return
    status = "🟢" if service.is_active else "🔴"
    text = t(
        lang,
        "service_admin_item",
        status=status,
        name=service.name(lang),
        price=format_money(service.base_price, lang),
        price_type=t(lang, f"price_type_{service.price_type}"),
    )
    if service.description(lang):
        text += "\n" + service.description(lang)
    await _edit(callback, text, admin_service_kb(lang, service, callback_data.page))
    await callback.answer()


@router.callback_query(AdminCB.filter((F.section == "service") & (F.action == "toggle")))
async def admin_service_toggle(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    service = await session.get(Service, callback_data.obj_id)
    if service is None:
        await callback.answer(t(lang, "service_not_found"), show_alert=True)
        return
    service.is_active = not service.is_active
    await session.flush()
    await callback.answer(t(lang, "saved"))
    status = "🟢" if service.is_active else "🔴"
    text = t(
        lang,
        "service_admin_item",
        status=status,
        name=service.name(lang),
        price=format_money(service.base_price, lang),
        price_type=t(lang, f"price_type_{service.price_type}"),
    )
    await _edit(callback, text, admin_service_kb(lang, service, callback_data.page))


@router.callback_query(AdminCB.filter((F.section == "service") & (F.action == "price_type")))
async def admin_service_price_type_prompt(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    service = await session.get(Service, callback_data.obj_id)
    if service is None:
        await callback.answer(t(lang, "service_not_found"), show_alert=True)
        return
    await _edit(
        callback,
        t(lang, "ask_service_price_type"),
        admin_price_type_kb(lang, service.id, callback_data.page),
    )
    await callback.answer()


@router.callback_query(AdminCB.filter((F.section == "service") & (F.action == "edit_price")))
async def admin_service_edit_price_prompt(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    state,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    service = await session.get(Service, callback_data.obj_id)
    if service is None:
        await callback.answer(t(lang, "service_not_found"), show_alert=True)
        return
    await state.clear()
    await state.update_data(admin_service_id=service.id)
    await state.set_state(AdminForm.service_price)
    if callback.message:
        await callback.message.answer(
            t(lang, "ask_service_price", service=service.name(lang))
        )
    await callback.answer()


@router.message(AdminForm.service_price, F.text)
async def admin_service_price(
    message: Message,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    state,
) -> None:
    if not await _guard_message(message, lang, is_admin):
        return
    data = await state.get_data()
    adding = bool(data.get("service_name_uz") and not data.get("admin_service_id"))
    if (message.text or "") in SKIP_VARIANTS:
        if adding:
            await message.answer(
                t(
                    lang,
                    "ask_service_price",
                    service=data.get("service_name_uz") or "—",
                )
            )
        else:
            service = await session.get(Service, data.get("admin_service_id"))
            await message.answer(
                t(
                    lang,
                    "ask_service_price",
                    service=service.name(lang) if service else "—",
                )
            )
        return
    price = parse_decimal(message.text or "")
    if price is None:
        await message.answer(t(lang, "price_invalid"))
        return

    if adding:
        await state.update_data(service_price=str(price))
        await message.answer(
            t(lang, "ask_service_price_type"),
            reply_markup=admin_price_type_kb(lang, 0),
        )
        return

    service = await session.get(Service, data.get("admin_service_id"))
    if service is None:
        await state.clear()
        await message.answer(t(lang, "service_not_found"))
        return
    service.base_price = price
    await session.flush()
    await state.clear()
    await message.answer(
        t(lang, "service_price_updated", price=format_money(price, lang))
    )


@router.callback_query(AdminCB.filter((F.section == "service") & (F.action == "add")))
async def admin_service_add(
    callback: CallbackQuery, lang: str, is_admin: bool, state
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    await state.clear()
    await state.set_state(AdminForm.service_name_uz)
    if callback.message:
        await callback.message.answer(t(lang, "ask_service_name_uz"))
    await callback.answer()


@router.message(AdminForm.service_name_uz, F.text)
async def admin_service_name_uz(
    message: Message, lang: str, is_admin: bool, state
) -> None:
    if not await _guard_message(message, lang, is_admin):
        return
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer(t(lang, "ask_service_name_uz"))
        return
    await state.update_data(service_name_uz=value[:255])
    await state.set_state(AdminForm.service_name_ru)
    await message.answer(t(lang, "ask_service_name_ru"))


@router.message(AdminForm.service_name_ru, F.text)
async def admin_service_name_ru(
    message: Message, lang: str, is_admin: bool, state
) -> None:
    if not await _guard_message(message, lang, is_admin):
        return
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer(t(lang, "ask_service_name_ru"))
        return
    await state.update_data(service_name_ru=value[:255])
    await state.set_state(AdminForm.service_price)
    data = await state.get_data()
    await message.answer(
        t(
            lang,
            "ask_service_price",
            service=data.get("service_name_uz") or value,
        )
    )


@router.callback_query(AdminCB.filter((F.section == "service") & (F.action.startswith("ptype_"))))
async def admin_service_price_type(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    state,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    price_type = callback_data.action.removeprefix("ptype_")
    if price_type not in {item.value for item in PriceType}:
        await callback.answer(t(lang, "unknown_callback"), show_alert=True)
        return
    if callback_data.obj_id:
        service = await session.get(Service, callback_data.obj_id)
        if service is None:
            await callback.answer(t(lang, "service_not_found"), show_alert=True)
            return
        service.price_type = price_type
        await session.flush()
        status = "🟢" if service.is_active else "🔴"
        text = t(
            lang,
            "service_admin_item",
            status=status,
            name=service.name(lang),
            price=format_money(service.base_price, lang),
            price_type=t(lang, f"price_type_{service.price_type}"),
        )
        if service.description(lang):
            text += "\n" + service.description(lang)
        await _edit(callback, text, admin_service_kb(lang, service, callback_data.page))
        await callback.answer(t(lang, "service_price_type_updated"))
        return

    data = await state.get_data()
    name_uz = data.get("service_name_uz")
    name_ru = data.get("service_name_ru")
    price_raw = data.get("service_price")
    if not name_uz or not name_ru or not price_raw:
        await state.clear()
        await callback.answer(t(lang, "session_expired"), show_alert=True)
        return
    service = Service(
        code=f"custom_{uuid4().hex[:12]}",
        name_uz=name_uz,
        name_ru=name_ru,
        description_uz="",
        description_ru="",
        base_price=Decimal(price_raw),
        price_type=price_type,
        is_active=True,
        sort_order=100,
    )
    session.add(service)
    await session.flush()
    await session.commit()
    await state.clear()
    await callback.answer(t(lang, "service_added"))
    if callback.message:
        await callback.message.answer(t(lang, "service_added"))


@router.callback_query(AdminCB.filter(F.section == "payments"))
async def admin_payments(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    total = await session.scalar(select(func.count(Payment.id))) or 0
    pages = _pages(total)
    page = max(0, min(callback_data.page, pages - 1))
    payments = (
        await session.scalars(
            select(Payment)
            .order_by(Payment.id.desc())
            .offset(page * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
    ).all()
    text = t(lang, "payments_title")
    if not payments:
        text += "\n" + t(lang, "no_payments")
    elif pages > 1:
        text += "\n" + t(lang, "page_of", current=page + 1, total=pages)
    await _edit(
        callback, text, admin_payments_kb(lang, list(payments), page, pages)
    )
    await callback.answer()


@router.callback_query(AdminCB.filter((F.section == "payment") & (F.obj_id > 0)))
async def admin_payment_detail(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    payment = await session.get(Payment, callback_data.obj_id)
    if payment is None:
        await callback.answer(t(lang, "not_found"), show_alert=True)
        return
    text = render_payment(payment, lang)
    if payment.provider_txn_id:
        text += f"\nTXN: {payment.provider_txn_id}"
    if payment.paid_at:
        text += "\n" + t(lang, "label_created") + f": {format_dt(payment.paid_at, lang)}"
    await _edit(callback, text, admin_payment_kb(lang, callback_data.page))
    await callback.answer()


@router.callback_query(AdminCB.filter(F.section == "withdrawals"))
async def admin_withdrawals(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    total = await session.scalar(select(func.count(WithdrawalRequest.id))) or 0
    pages = _pages(total)
    page = max(0, min(callback_data.page, pages - 1))
    requests = (
        await session.scalars(
            select(WithdrawalRequest)
            .order_by(WithdrawalRequest.id.desc())
            .offset(page * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
    ).all()
    text = t(lang, "admin_withdrawals_title")
    if not requests:
        text += "\n" + t(lang, "no_withdrawals_admin")
    elif pages > 1:
        text += "\n" + t(lang, "page_of", current=page + 1, total=pages)
    await _edit(
        callback,
        text,
        admin_withdrawals_kb(lang, list(requests), page, pages),
    )
    await callback.answer()


@router.callback_query(
    AdminCB.filter((F.section == "withdrawal") & (F.obj_id > 0) & (F.action == ""))
)
async def admin_withdrawal_detail(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    request = await session.get(WithdrawalRequest, callback_data.obj_id)
    if request is None:
        await callback.answer(t(lang, "not_found"), show_alert=True)
        return
    await _edit(
        callback,
        render_admin_withdrawal(request, lang),
        admin_withdrawal_kb(lang, request),
    )
    await callback.answer()


@router.callback_query(AdminCB.filter((F.section == "withdrawal") & (F.action == "approve")))
async def admin_withdrawal_approve(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    bot,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    request = await session.get(WithdrawalRequest, callback_data.obj_id)
    if request is None:
        await callback.answer(t(lang, "not_found"), show_alert=True)
        return
    try:
        await balance_service.approve_withdrawal(
            session, request, callback.from_user.id
        )
        await session.flush()
        await session.commit()
        await notification_service.notify_withdrawal_approved(bot, session, request)
        await callback.answer(t(lang, "withdrawal_approved_toast"))
        await _edit(
            callback,
            render_admin_withdrawal(request, lang),
            admin_withdrawal_kb(lang, request),
        )
    except BalanceError as exc:
        await callback.answer(t(lang, exc.key), show_alert=True)


@router.callback_query(AdminCB.filter((F.section == "withdrawal") & (F.action == "reject")))
async def admin_withdrawal_reject_prompt(
    callback: CallbackQuery,
    callback_data: AdminCB,
    lang: str,
    is_admin: bool,
    state,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    await state.clear()
    await state.update_data(withdrawal_reject_id=callback_data.obj_id)
    await state.set_state(AdminForm.withdrawal_reject_reason)
    if callback.message:
        await callback.message.answer(t(lang, "ask_reject_reason"))
    await callback.answer()


@router.message(AdminForm.withdrawal_reject_reason, F.text)
async def admin_withdrawal_reject_reason(
    message: Message,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    state,
    bot,
) -> None:
    if not await _guard_message(message, lang, is_admin):
        return
    reason = (message.text or "").strip()
    if len(reason) < 3:
        await message.answer(t(lang, "ask_reject_reason"))
        return
    data = await state.get_data()
    request = await session.get(
        WithdrawalRequest, data.get("withdrawal_reject_id")
    )
    if request is None:
        await state.clear()
        await message.answer(t(lang, "not_found"))
        return
    try:
        await balance_service.reject_withdrawal(
            session, request, message.from_user.id if message.from_user else 0, reason
        )
        await session.flush()
        await session.commit()
        await notification_service.notify_withdrawal_rejected(
            bot, session, request
        )
        await state.clear()
        await message.answer(t(lang, "withdrawal_rejected_toast"))
    except BalanceError as exc:
        await state.clear()
        await message.answer(t(lang, exc.key))


@router.callback_query(AdminCB.filter((F.section == "withdrawal") & (F.action == "paid")))
async def admin_withdrawal_paid_prompt(
    callback: CallbackQuery,
    callback_data: AdminCB,
    lang: str,
    is_admin: bool,
    state,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    await state.clear()
    await state.update_data(withdrawal_paid_id=callback_data.obj_id)
    await state.set_state(AdminForm.withdrawal_txn)
    if callback.message:
        await callback.message.answer(
            t(lang, "ask_withdrawal_txn"),
            reply_markup=admin_skip_txn_kb(lang, callback_data.obj_id),
        )
    await callback.answer()


@router.message(AdminForm.withdrawal_txn, F.text.in_(SKIP_VARIANTS))
async def admin_withdrawal_paid_skip(
    message: Message,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    state,
    bot,
) -> None:
    if not await _guard_message(message, lang, is_admin):
        return
    await _finish_withdrawal_paid(
        message, session, lang, state, bot, txn_id=None
    )


@router.message(AdminForm.withdrawal_txn, F.text)
async def admin_withdrawal_txn(
    message: Message,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    state,
    bot,
) -> None:
    if not await _guard_message(message, lang, is_admin):
        return
    txn_id = (message.text or "").strip() or None
    await _finish_withdrawal_paid(
        message, session, lang, state, bot, txn_id=txn_id
    )


async def _finish_withdrawal_paid(
    message: Message,
    session: AsyncSession,
    lang: str,
    state,
    bot,
    *,
    txn_id: str | None,
) -> None:
    data = await state.get_data()
    request = await session.get(
        WithdrawalRequest, data.get("withdrawal_paid_id")
    )
    if request is None:
        await state.clear()
        await message.answer(t(lang, "not_found"))
        return
    try:
        await balance_service.mark_withdrawal_paid(
            session,
            request,
            message.from_user.id if message.from_user else 0,
            txn_id,
        )
        await session.flush()
        await session.commit()
        await notification_service.notify_withdrawal_paid(bot, session, request)
        await state.clear()
        await message.answer(t(lang, "withdrawal_paid_toast"))
    except BalanceError as exc:
        await state.clear()
        await message.answer(t(lang, exc.key))


@router.callback_query(AdminCB.filter((F.section == "withdrawal") & (F.action == "paid_now")))
async def admin_withdrawal_paid_now(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    state,
    bot,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    request = await session.get(WithdrawalRequest, callback_data.obj_id)
    if request is None:
        await callback.answer(t(lang, "not_found"), show_alert=True)
        return
    try:
        await balance_service.mark_withdrawal_paid(
            session, request, callback.from_user.id, None
        )
        await session.flush()
        await session.commit()
        await notification_service.notify_withdrawal_paid(bot, session, request)
        await state.clear()
        await callback.answer(t(lang, "withdrawal_paid_toast"))
        if callback.message:
            await callback.message.answer(
                render_admin_withdrawal(request, lang),
                reply_markup=admin_withdrawal_kb(lang, request),
            )
    except BalanceError as exc:
        await callback.answer(t(lang, exc.key), show_alert=True)


@router.callback_query(AdminCB.filter((F.section == "withdrawal") & (F.action == "reveal")))
async def admin_withdrawal_reveal(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    bot,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    request = await session.get(WithdrawalRequest, callback_data.obj_id)
    if request is None or request.card is None:
        await callback.answer(t(lang, "admin_no_card"), show_alert=True)
        return
    number = decrypt_card(request.card.card_number_encrypted)
    if number is None:
        await callback.answer(t(lang, "admin_withdrawal_card_error"), show_alert=True)
        return
    text = t(
        lang,
        "card_reveal_warning",
        holder=request.card.card_holder,
        number=number,
        bank=request.card.bank_name or "—",
    )
    target = callback.from_user.id
    try:
        await bot.send_message(target, text)
        await callback.answer()
    except TelegramAPIError:
        await callback.answer(t(lang, "admin_withdrawal_card_error"), show_alert=True)


@router.callback_query(AdminCB.filter(F.section == "complaints"))
async def admin_complaints(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    total = await session.scalar(select(func.count(Complaint.id))) or 0
    pages = _pages(total)
    page = max(0, min(callback_data.page, pages - 1))
    complaints = (
        await session.scalars(
            select(Complaint)
            .order_by(Complaint.id.desc())
            .offset(page * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
    ).all()
    text = t(lang, "btn_admin_complaints")
    if not complaints:
        text += "\n" + t(lang, "no_complaints")
    elif pages > 1:
        text += "\n" + t(lang, "page_of", current=page + 1, total=pages)
    await _edit(
        callback, text, admin_complaints_kb(lang, list(complaints), page, pages)
    )
    await callback.answer()


@router.callback_query(
    AdminCB.filter((F.section == "complaint") & (F.obj_id > 0) & (F.action == ""))
)
async def admin_complaint_detail(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    complaint = await session.get(Complaint, callback_data.obj_id)
    if complaint is None:
        await callback.answer(t(lang, "not_found"), show_alert=True)
        return
    await _edit(
        callback, render_complaint(complaint, lang), admin_complaint_kb(lang, complaint)
    )
    await callback.answer()


@router.callback_query(AdminCB.filter((F.section == "complaint") & (F.action == "resolve")))
async def admin_complaint_resolve_prompt(
    callback: CallbackQuery,
    callback_data: AdminCB,
    lang: str,
    is_admin: bool,
    state,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    await state.clear()
    await state.update_data(complaint_id=callback_data.obj_id)
    await state.set_state(AdminForm.complaint_resolution)
    if callback.message:
        await callback.message.answer(t(lang, "ask_complaint_note"))
    await callback.answer()


@router.message(AdminForm.complaint_resolution, F.text)
async def admin_complaint_resolution(
    message: Message,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    state,
    bot,
) -> None:
    if not await _guard_message(message, lang, is_admin):
        return
    note = (message.text or "").strip()
    if len(note) < 3:
        await message.answer(t(lang, "ask_complaint_note"))
        return
    data = await state.get_data()
    complaint = await session.get(Complaint, data.get("complaint_id"))
    if complaint is None:
        await state.clear()
        await message.answer(t(lang, "not_found"))
        return
    complaint.status = ComplaintStatus.RESOLVED.value
    complaint.admin_comment = note
    complaint.resolved_at = datetime.now(timezone.utc)
    complaint.resolved_by = message.from_user.id if message.from_user else 0
    await session.flush()
    await notification_service.notify_complaint_resolved(
        bot, session, complaint, note
    )
    await session.commit()
    await state.clear()
    await message.answer(t(lang, "complaint_resolved_toast"))


@router.callback_query(AdminCB.filter((F.section == "settings") & (F.action == "")))
async def admin_settings(
    callback: CallbackQuery, lang: str, is_admin: bool
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    await _edit(
        callback, t(lang, "btn_admin_settings"), admin_settings_kb(lang)
    )
    await callback.answer()


@router.callback_query(AdminCB.filter((F.section == "settings") & (F.action == "commission")))
async def admin_commission_prompt(
    callback: CallbackQuery, lang: str, is_admin: bool, state
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    await state.clear()
    await state.set_state(AdminForm.commission)
    if callback.message:
        await callback.message.answer(t(lang, "ask_commission"))
    await callback.answer()


@router.message(AdminForm.commission, F.text)
async def admin_commission(
    message: Message, session: AsyncSession, lang: str, is_admin: bool, state
) -> None:
    if not await _guard_message(message, lang, is_admin):
        return
    value = parse_decimal(message.text or "")
    if value is None or value > Decimal("50"):
        await message.answer(t(lang, "ask_commission"))
        return
    await settings_service.set_setting(session, "commission_percent", str(value))
    await session.commit()
    await state.clear()
    await message.answer(
        t(lang, "commission_updated", value=f"{value:.2f}")
    )


@router.callback_query(AdminCB.filter((F.section == "settings") & (F.action == "min_withdrawal")))
async def admin_min_withdrawal_prompt(
    callback: CallbackQuery, lang: str, is_admin: bool, state
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    await state.clear()
    await state.set_state(AdminForm.min_withdrawal)
    if callback.message:
        await callback.message.answer(t(lang, "ask_min_withdrawal"))
    await callback.answer()


@router.message(AdminForm.min_withdrawal, F.text)
async def admin_min_withdrawal(
    message: Message, session: AsyncSession, lang: str, is_admin: bool, state
) -> None:
    if not await _guard_message(message, lang, is_admin):
        return
    value = parse_decimal(message.text or "")
    if value is None:
        await message.answer(t(lang, "ask_min_withdrawal"))
        return
    await settings_service.set_setting(session, "min_withdrawal", str(value))
    await session.commit()
    await state.clear()
    await message.answer(
        t(lang, "min_withdrawal_updated", value=format_money(value, lang))
    )


@router.callback_query(AdminCB.filter((F.section == "broadcast") & (F.action == "")))
async def admin_broadcast_prompt(
    callback: CallbackQuery, lang: str, is_admin: bool, state
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    await state.clear()
    await state.set_state(AdminForm.broadcast_text)
    if callback.message:
        await callback.message.answer(t(lang, "broadcast_prompt"))
    await callback.answer()


@router.message(AdminForm.broadcast_text, F.text)
async def admin_broadcast_text(
    message: Message, lang: str, is_admin: bool, state
) -> None:
    if not await _guard_message(message, lang, is_admin):
        return
    text = message.text or ""
    if not text.strip():
        await message.answer(t(lang, "broadcast_prompt"))
        return
    await state.update_data(broadcast_text=text)
    await message.answer(
        t(lang, "broadcast_confirm_text", text=text),
        reply_markup=admin_broadcast_confirm_kb(lang),
    )


@router.callback_query(AdminCB.filter((F.section == "broadcast") & (F.action == "send")))
async def admin_broadcast_send(
    callback: CallbackQuery,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    state,
    bot,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    data = await state.get_data()
    text = data.get("broadcast_text")
    if not text:
        await callback.answer(t(lang, "session_expired"), show_alert=True)
        return
    await callback.answer(t(lang, "broadcast_started"))
    users = (
        await session.scalars(select(User).where(User.is_blocked.is_(False)))
    ).all()
    if not users:
        await state.clear()
        if callback.message:
            await callback.message.answer(t(lang, "admin_broadcast_no_users"))
        return
    sent = 0
    failed = 0
    for user in users:
        try:
            for chunk in split_safe(text):
                await bot.send_message(user.telegram_id, chunk)
            sent += 1
        except TelegramAPIError:
            failed += 1
    await state.clear()
    if callback.message:
        await callback.message.answer(
            t(lang, "broadcast_done", sent=sent, failed=failed)
        )


@router.callback_query(AdminCB)
async def admin_unknown(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
    lang: str,
    is_admin: bool,
    state,
) -> None:
    if not await _guard_callback(callback, lang, is_admin):
        return
    if callback_data.section == "masters":
        await admin_masters(callback, callback_data, session, lang, is_admin)
        return
    await callback.answer(t(lang, "unknown_callback"), show_alert=True)
