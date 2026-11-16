from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.enums import MasterStatus, OrderStatus
from database.models import Master, MasterCard, MasterService, Service, User
from keyboards.callbacks import (
    AdminCB,
    DraftCB,
    MasterServiceCB,
    OrderActionCB,
    PageCB,
)
from keyboards.inline import (
    master_completed_kb,
    master_order_actions_kb,
    master_services_kb,
    master_start_kb,
    my_orders_kb,
    profile_kb,
)
from keyboards.reply import cancel_only, master_menu, optional_form, skip_only
from services import notification_service, order_service
from services.order_service import OrderError
from states.forms import MasterForm
from utils.crypto import card_last4, encrypt_card, masked_card
from utils.formatting import format_money, parse_decimal
from utils.i18n import button_variants, t
from utils.rendering import (
    render_completed_order,
    render_master_order,
    render_master_profile,
)
from utils.validators import (
    normalize_card_number,
    normalize_phone,
    parse_description,
    parse_experience,
)

router = Router(name="master")

SKIP_VARIANTS = button_variants("btn_skip")


async def send_master_incoming(
    target: Message, session: AsyncSession, user: User, lang: str, is_admin: bool = False
) -> None:
    if user.master is None:
        await target.answer(t(lang, "master_not_approved"))
        return
    orders = await order_service.list_master_active_orders(session, user.master.id)
    if not orders:
        await target.answer(
            t(lang, "no_incoming_orders"), reply_markup=master_menu(lang, is_admin)
        )
        return
    await target.answer(
        t(lang, "incoming_title"),
        reply_markup=my_orders_kb(
            lang, orders[:20], 0, 1, "master_incoming", show_new_order=False
        ),
    )


async def send_master_completed(
    target: Message,
    session: AsyncSession,
    user: User,
    lang: str,
    page: int = 0,
    is_admin: bool = False,
) -> None:
    if user.master is None:
        await target.answer(t(lang, "master_not_approved"))
        return
    orders, total = await order_service.list_master_completed_orders(
        session, user.master.id, page=page
    )
    if total == 0:
        await target.answer(
            t(lang, "no_completed_orders"), reply_markup=master_menu(lang, is_admin)
        )
        return
    pages = max(1, (total + 7) // 8)
    page = max(0, min(page, pages - 1))
    text = t(lang, "completed_title") + "\n\n" + "\n\n".join(
        render_completed_order(order, lang) for order in orders
    )
    if pages > 1:
        text += "\n\n" + t(lang, "page_of", current=page + 1, total=pages)
    await target.answer(text, reply_markup=master_completed_kb(lang, page, pages))


@router.message(F.text.in_(button_variants("btn_master_incoming")))
async def master_incoming(
    message: Message, session: AsyncSession, db_user: User, lang: str, _, is_admin: bool
) -> None:
    await send_master_incoming(message, session, db_user, lang, is_admin)


@router.message(F.text.in_(button_variants("btn_master_completed")))
async def master_completed(
    message: Message, session: AsyncSession, db_user: User, lang: str, _, is_admin: bool
) -> None:
    await send_master_completed(message, session, db_user, lang, 0, is_admin)


@router.callback_query(PageCB.filter(F.scope == "master_completed"))
async def master_completed_page(
    callback: CallbackQuery,
    callback_data: PageCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
) -> None:
    if not callback.message or db_user.master is None:
        await callback.answer(t(lang, "master_not_approved"), show_alert=True)
        return
    orders, total = await order_service.list_master_completed_orders(
        session, db_user.master.id, page=callback_data.page
    )
    if total == 0:
        await callback.answer(t(lang, "no_completed_orders"), show_alert=True)
        return
    pages = max(1, (total + 7) // 8)
    page = max(0, min(callback_data.page, pages - 1))
    text = t(lang, "completed_title") + "\n\n" + "\n\n".join(
        render_completed_order(order, lang) for order in orders
    )
    if pages > 1:
        text += "\n\n" + t(lang, "page_of", current=page + 1, total=pages)
    await callback.message.edit_text(
        text, reply_markup=master_completed_kb(lang, page, pages)
    )
    await callback.answer()


@router.message(F.text.in_(button_variants("btn_master_profile")))
async def master_profile(message: Message, db_user: User, lang: str, _) -> None:
    master = db_user.master
    if master is None:
        await message.answer(t(lang, "master_intro"), reply_markup=master_start_kb(lang))
        return
    await message.answer(
        render_master_profile(master, lang),
        reply_markup=profile_kb(lang, is_master=True, is_paused=not master.is_active),
    )


@router.callback_query(AdminCB.filter((F.section == "self") & (F.action == "toggle_active")))
async def toggle_master_active(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
) -> None:
    master = db_user.master
    if master is None or master.status != MasterStatus.APPROVED.value:
        await callback.answer(t(lang, "master_not_approved"), show_alert=True)
        return
    master.is_active = not master.is_active
    await session.flush()
    await callback.answer(
        t(lang, "master_resumed" if master.is_active else "master_paused")
    )
    if callback.message:
        try:
            await callback.message.edit_text(
                render_master_profile(master, lang),
                reply_markup=profile_kb(
                    lang, is_master=True, is_paused=not master.is_active
                ),
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Master application
# ---------------------------------------------------------------------------


@router.callback_query(DraftCB.filter(F.action == "master_start"))
async def master_start(
    callback: CallbackQuery, db_user: User, lang: str, _, state
) -> None:
    if not db_user.phone or not db_user.role:
        await callback.answer(t(lang, "please_register"), show_alert=True)
        return
    if db_user.role != "master":
        await callback.answer(t(lang, "master_role_not_selected"), show_alert=True)
        return
    master = db_user.master
    if master is not None and master.status == MasterStatus.APPROVED.value:
        await callback.answer(t(lang, "notif_master_approved"), show_alert=True)
        return
    if master is not None and master.status == MasterStatus.PENDING.value:
        await callback.answer(t(lang, "application_pending"), show_alert=True)
        return
    await state.clear()
    await state.set_state(MasterForm.full_name)
    if callback.message:
        await callback.message.answer(
            t(lang, "master_form_fullname"), reply_markup=cancel_only(lang)
        )
    await callback.answer()


@router.message(MasterForm.full_name, F.text)
async def master_full_name(message: Message, lang: str, _, state) -> None:
    value = " ".join((message.text or "").split())
    if len(value) < 3:
        await message.answer(t(lang, "value_too_short"))
        return
    if len(value) > 255:
        await message.answer(t(lang, "value_too_long"))
        return
    await state.update_data(full_name=value)
    await state.set_state(MasterForm.phone)
    await message.answer(t(lang, "master_form_phone"), reply_markup=cancel_only(lang))


@router.message(MasterForm.phone, F.text)
async def master_phone(message: Message, lang: str, _, state) -> None:
    value = normalize_phone(message.text)
    if value is None:
        await message.answer(t(lang, "phone_invalid"))
        return
    await state.update_data(phone=value)
    await state.set_state(MasterForm.avatar)
    await message.answer(t(lang, "master_form_avatar"), reply_markup=skip_only(lang))


@router.message(MasterForm.avatar, F.photo)
async def master_avatar(message: Message, lang: str, _, state) -> None:
    await state.update_data(avatar_file_id=message.photo[-1].file_id)
    await _ask_experience(message, lang, state)


@router.message(MasterForm.avatar, F.text.in_(button_variants("btn_skip")))
async def master_avatar_skip(message: Message, lang: str, _, state) -> None:
    await state.update_data(avatar_file_id=None)
    await _ask_experience(message, lang, state)


@router.message(MasterForm.avatar)
async def master_avatar_invalid(message: Message, lang: str, _) -> None:
    await message.answer(t(lang, "photo_or_skip"), reply_markup=skip_only(lang))


async def _ask_experience(message: Message, lang: str, state) -> None:
    await state.set_state(MasterForm.experience)
    await message.answer(
        t(lang, "master_form_experience"), reply_markup=cancel_only(lang)
    )


@router.message(MasterForm.experience, F.text)
async def master_experience(message: Message, lang: str, _, state) -> None:
    value = parse_experience(message.text or "")
    if value is None:
        await message.answer(t(lang, "experience_invalid"))
        return
    await state.update_data(experience_years=value)
    await state.set_state(MasterForm.area)
    await message.answer(t(lang, "master_form_area"), reply_markup=cancel_only(lang))


@router.message(MasterForm.area, F.text)
async def master_area(message: Message, lang: str, _, state) -> None:
    value = " ".join((message.text or "").split())
    if len(value) < 2 or len(value) > 255:
        await message.answer(t(lang, "value_too_long"))
        return
    await state.update_data(service_area=value)
    await state.set_state(MasterForm.about)
    await message.answer(t(lang, "master_form_about"), reply_markup=cancel_only(lang))


@router.message(MasterForm.about, F.text)
async def master_about(
    message: Message, session: AsyncSession, lang: str, _, state
) -> None:
    value = parse_description(message.text or "", min_len=20, max_len=1000)
    if value is None:
        await message.answer(t(lang, "about_invalid"))
        return
    await state.update_data(about=value, selected_services=[])
    await _show_services(message, session, lang, state)


async def _show_services(
    message: Message, session: AsyncSession, lang: str, state
) -> None:
    services = (
        await session.scalars(
            select(Service)
            .where(Service.is_active.is_(True))
            .order_by(Service.sort_order, Service.id)
        )
    ).all()
    if not services:
        await message.answer(t(lang, "no_services"))
        return
    selected = set((await state.get_data()).get("selected_services") or [])
    await state.set_state(MasterForm.services)
    await message.answer(
        t(lang, "master_form_services"),
        reply_markup=master_services_kb(lang, list(services), selected),
    )


@router.message(MasterForm.services)
async def master_services_hint(message: Message, lang: str, _) -> None:
    await message.answer(t(lang, "master_form_services"))


@router.callback_query(MasterServiceCB.filter())
async def master_service_callback(
    callback: CallbackQuery,
    callback_data: MasterServiceCB,
    session: AsyncSession,
    lang: str,
    _,
    state,
) -> None:
    if not callback.message:
        return
    data = await state.get_data()
    if not data.get("full_name"):
        await callback.answer(t(lang, "session_expired"), show_alert=True)
        return

    services = list(
        (
            await session.scalars(
                select(Service)
                .where(Service.is_active.is_(True))
                .order_by(Service.sort_order, Service.id)
            )
        ).all()
    )
    selected = set(data.get("selected_services") or [])

    if callback_data.action == "cancel_prices":
        await state.clear()
        await callback.message.edit_text(t(lang, "cancelled"))
        await callback.answer()
        return

    if callback_data.action == "toggle":
        if callback_data.service_id in selected:
            selected.discard(callback_data.service_id)
        else:
            selected.add(callback_data.service_id)
        await state.update_data(selected_services=sorted(selected))
        await callback.message.edit_reply_markup(
            reply_markup=master_services_kb(lang, services, selected)
        )
        await callback.answer()
        return

    if callback_data.action == "defaults":
        selected = {service.id for service in services}
        await state.update_data(selected_services=sorted(selected))
        await callback.message.edit_reply_markup(
            reply_markup=master_services_kb(lang, services, selected)
        )
        await callback.answer(t(lang, "default_prices_applied"))
        return

    if callback_data.action == "done":
        if not selected:
            await callback.answer(t(lang, "select_at_least_one"), show_alert=True)
            return
        queue = [service.id for service in services if service.id in selected]
        await state.update_data(
            price_queue=queue,
            price_index=0,
            prices={},
        )
        await callback.answer()
        await _ask_next_price(callback.message, session, lang, state)
        return

    await callback.answer(t(lang, "unknown_callback"), show_alert=True)


async def _ask_next_price(
    message: Message, session: AsyncSession, lang: str, state
) -> None:
    data = await state.get_data()
    queue: list[int] = data.get("price_queue") or []
    index: int = data.get("price_index") or 0
    if index >= len(queue):
        await state.update_data(card_skip_allowed=True)
        await state.set_state(MasterForm.card_number)
        await message.answer(
            t(lang, "master_form_card_intro"), reply_markup=optional_form(lang)
        )
        return
    service = await session.get(Service, queue[index])
    if service is None:
        await state.update_data(price_index=index + 1)
        await _ask_next_price(message, session, lang, state)
        return
    await state.set_state(MasterForm.prices)
    await message.answer(
        t(
            lang,
            "price_prompt",
            service=service.name(lang),
            default=format_money(service.base_price, lang),
        ),
        reply_markup=cancel_only(lang),
    )


@router.message(MasterForm.prices, F.text)
async def master_price_input(
    message: Message, session: AsyncSession, lang: str, _, state
) -> None:
    value = parse_decimal(message.text or "")
    if value is None or value < Decimal("1000") or value > Decimal("100000000"):
        await message.answer(t(lang, "enter_price_between"))
        return
    data = await state.get_data()
    queue: list[int] = data.get("price_queue") or []
    index: int = data.get("price_index") or 0
    if index >= len(queue):
        await state.clear()
        await message.answer(t(lang, "session_expired"))
        return
    prices: dict = data.get("prices") or {}
    prices[str(queue[index])] = str(value)
    await state.update_data(prices=prices, price_index=index + 1)
    await _ask_next_price(message, session, lang, state)


@router.message(MasterForm.card_number, F.text.in_(button_variants("btn_skip")))
async def master_card_skip(
    message: Message,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    state,
    bot,
    is_admin: bool,
) -> None:
    await _finalize_application(
        message,
        session,
        db_user,
        lang,
        state,
        bot,
        card_data=None,
        is_admin=is_admin,
    )


@router.message(MasterForm.card_number, F.text)
async def master_card_number(message: Message, lang: str, _, state) -> None:
    value = normalize_card_number(message.text or "")
    if value is None:
        await message.answer(t(lang, "card_invalid"))
        return
    await state.update_data(card_number=value)
    await state.set_state(MasterForm.card_holder)
    await message.answer(
        t(lang, "card_holder_prompt"), reply_markup=cancel_only(lang)
    )


@router.message(MasterForm.card_holder, F.text)
async def master_card_holder(message: Message, lang: str, _, state) -> None:
    value = " ".join((message.text or "").split())
    if len(value) < 3 or len(value) > 120:
        await message.answer(t(lang, "value_too_long"))
        return
    await state.update_data(card_holder=value)
    await state.set_state(MasterForm.card_bank)
    await message.answer(t(lang, "card_bank_prompt"), reply_markup=cancel_only(lang))


@router.message(MasterForm.card_bank, F.text)
async def master_card_bank(
    message: Message,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    state,
    bot,
    is_admin: bool,
) -> None:
    value = " ".join((message.text or "").split())
    if len(value) < 2 or len(value) > 120:
        await message.answer(t(lang, "value_too_long"))
        return
    data = await state.get_data()
    number = data.get("card_number")
    holder = data.get("card_holder")
    if not number or not holder:
        await state.clear()
        await message.answer(t(lang, "session_expired"))
        return
    await _finalize_application(
        message,
        session,
        db_user,
        lang,
        state,
        bot,
        card_data={"number": number, "holder": holder, "bank": value},
        is_admin=is_admin,
    )


async def _finalize_application(
    message: Message,
    session: AsyncSession,
    db_user: User,
    lang: str,
    state,
    bot,
    is_admin: bool = False,
    *,
    card_data: dict | None,
) -> None:
    data = await state.get_data()
    prices: dict = data.get("prices") or {}
    if (
        not data.get("full_name")
        or not data.get("phone")
        or not prices
        or data.get("experience_years") is None
        or not data.get("service_area")
        or not data.get("about")
    ):
        await state.clear()
        await message.answer(t(lang, "session_expired"))
        return

    service_ids = [int(key) for key in prices]
    services = (
        await session.scalars(
            select(Service).where(
                Service.id.in_(service_ids), Service.is_active.is_(True)
            )
        )
    ).all()
    if not services:
        await state.clear()
        await message.answer(t(lang, "select_at_least_one"))
        return
    service_map = {service.id: service for service in services}

    master = await session.scalar(select(Master).where(Master.user_id == db_user.id))
    if master is None:
        master = Master(
            user_id=db_user.id,
            full_name=data["full_name"],
            phone=data["phone"],
            experience_years=int(data["experience_years"]),
            service_area=data["service_area"],
            about=data.get("about"),
            avatar_file_id=data.get("avatar_file_id"),
            status=MasterStatus.PENDING.value,
        )
        session.add(master)
        await session.flush()
    else:
        master.full_name = data["full_name"]
        master.phone = data["phone"]
        master.experience_years = int(data["experience_years"])
        master.service_area = data["service_area"]
        master.about = data.get("about")
        master.avatar_file_id = data.get("avatar_file_id")
        master.status = MasterStatus.PENDING.value
        master.rejection_reason = None
        master.is_active = True

    db_user.role = "master"

    await session.execute(
        delete(MasterService).where(MasterService.master_id == master.id)
    )
    for service_id in service_ids:
        if service_id not in service_map:
            continue
        session.add(
            MasterService(
                master_id=master.id,
                service_id=service_id,
                price=Decimal(prices[str(service_id)]),
            )
        )

    if card_data:
        existing = await session.scalar(
            select(MasterCard.id).where(MasterCard.master_id == master.id)
        )
        session.add(
            MasterCard(
                master_id=master.id,
                card_number_encrypted=encrypt_card(card_data["number"]),
                card_last4=card_last4(card_data["number"]),
                card_masked=masked_card(card_data["number"]),
                card_holder=card_data["holder"],
                bank_name=card_data.get("bank"),
                is_default=existing is None,
            )
        )

    await session.flush()
    await session.commit()

    await notification_service.notify_master_application(bot, session, master)
    await state.clear()
    await message.answer(
        t(lang, "application_submitted") + "\n\n" + t(lang, "application_pending_hint"),
        reply_markup=master_menu(lang, is_admin),
    )


# ---------------------------------------------------------------------------
# Order actions
# ---------------------------------------------------------------------------


async def _notify_order_update(
    callback: CallbackQuery, session: AsyncSession, order, lang: str
) -> None:
    if not callback.message:
        return
    try:
        await callback.message.edit_text(
            render_master_order(order, lang),
            reply_markup=master_order_actions_kb(lang, order.id, order.status),
        )
    except Exception:
        pass


@router.callback_query(OrderActionCB.filter(F.action == "accept"))
async def accept_order(
    callback: CallbackQuery,
    callback_data: OrderActionCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    bot,
) -> None:
    if db_user.master is None:
        await callback.answer(t(lang, "master_not_approved"), show_alert=True)
        return
    try:
        await order_service.accept_order(
            session, callback_data.order_id, db_user.master, db_user.telegram_id
        )
        await session.flush()
        order = await order_service.load_order(session, callback_data.order_id)
        await session.commit()
        if order is not None:
            await notification_service.notify_order_accepted(bot, session, order)
        await callback.answer(t(lang, "notif_order_accepted"))
        if callback.message:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
    except OrderError as exc:
        await callback.answer(t(lang, exc.key), show_alert=True)


@router.callback_query(OrderActionCB.filter(F.action == "reject"))
async def reject_order(
    callback: CallbackQuery,
    callback_data: OrderActionCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    bot,
) -> None:
    if db_user.master is None:
        await callback.answer(t(lang, "master_not_approved"), show_alert=True)
        return
    try:
        await order_service.reject_order(
            session, callback_data.order_id, db_user.master, db_user.telegram_id
        )
        await session.flush()
        order = await order_service.load_order(session, callback_data.order_id)
        await session.commit()
        if order is not None:
            await notification_service.notify_order_rejected(bot, session, order)
        await callback.answer(t(lang, "cancelled"))
        if callback.message:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
    except OrderError as exc:
        await callback.answer(t(lang, exc.key), show_alert=True)


@router.callback_query(OrderActionCB.filter(F.action == "start"))
async def start_order(
    callback: CallbackQuery,
    callback_data: OrderActionCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    bot,
) -> None:
    if db_user.master is None:
        await callback.answer(t(lang, "master_not_approved"), show_alert=True)
        return
    try:
        await order_service.start_work(
            session, callback_data.order_id, db_user.master, db_user.telegram_id
        )
        await session.flush()
        order = await order_service.load_order(session, callback_data.order_id)
        await session.commit()
        if order is not None:
            await notification_service.notify_work_started(bot, session, order)
        await callback.answer(t(lang, "work_started"))
        if order is not None:
            await _notify_order_update(callback, session, order, lang)
    except OrderError as exc:
        await callback.answer(t(lang, exc.key), show_alert=True)


@router.callback_query(OrderActionCB.filter(F.action == "done"))
async def done_order(
    callback: CallbackQuery,
    callback_data: OrderActionCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    bot,
) -> None:
    if db_user.master is None:
        await callback.answer(t(lang, "master_not_approved"), show_alert=True)
        return
    try:
        await order_service.mark_done(
            session, callback_data.order_id, db_user.master, db_user.telegram_id
        )
        await session.flush()
        order = await order_service.load_order(session, callback_data.order_id)
        await session.commit()
        if order is not None:
            await notification_service.notify_order_done(bot, session, order)
        await callback.answer(t(lang, "done_marked"))
        if order is not None:
            await _notify_order_update(callback, session, order, lang)
    except OrderError as exc:
        await callback.answer(t(lang, exc.key), show_alert=True)


@router.callback_query(OrderActionCB.filter(F.action == "master_list"))
async def master_order_list(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    is_admin: bool,
) -> None:
    if callback.message:
        await send_master_incoming(callback.message, session, db_user, lang, is_admin)
    await callback.answer()
