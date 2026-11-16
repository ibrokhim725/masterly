from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.enums import MasterStatus, OrderStatus
from database.models import Master, MasterService, Order, Review, Service, User
from keyboards.callbacks import (
    DraftCB,
    MasterPickCB,
    OrderActionCB,
    PageCB,
    ReassignCB,
    ReviewCB,
    ServiceCB,
)
from keyboards.inline import (
    confirm_order_kb,
    masters_kb,
    my_orders_kb,
    order_actions_kb,
    reassign_masters_kb,
    review_comment_kb,
    review_stars_kb,
    services_kb,
)
from keyboards.reply import cancel_only, client_menu, location_request, optional_form
from services import notification_service, order_service
from services.order_service import OrderError
from states.forms import ComplaintForm, OrderForm, ReviewForm
from utils.formatting import format_date, format_money, quantize
from utils.i18n import button_variants, t
from utils.rendering import render_order
from utils.validators import (
    is_future_schedule,
    parse_date,
    parse_description,
    parse_time,
)

router = Router(name="customer")

PAGE_SIZE = 8
SKIP_VARIANTS = button_variants("btn_skip")


def _pages(total: int, size: int = PAGE_SIZE) -> int:
    return max(1, math.ceil(total / size))


def _ensure_client(message: Message, user: User, lang: str, _) -> bool:
    if not user.phone or not user.role:
        return False
    return user.role == "client"


async def _load_client_orders_page(
    session: AsyncSession, user: User, page: int
):
    orders, total = await order_service.list_client_orders(
        session, user.id, page=page, page_size=PAGE_SIZE
    )
    return orders, total, _pages(total)


async def send_client_orders(
    target: Message, session: AsyncSession, user: User, lang: str, page: int = 0
) -> None:
    orders, total, pages = await _load_client_orders_page(session, user, page)
    if total == 0:
        await target.answer(
            t(lang, "no_orders"), reply_markup=client_menu(lang, False)
        )
        return
    text = t(lang, "my_orders_title")
    if pages > 1:
        text += "\n" + t(lang, "page_of", current=page + 1, total=pages)
    await target.answer(
        text, reply_markup=my_orders_kb(lang, orders, page, pages, "client_orders")
    )


@router.message(F.text.in_(button_variants("btn_client_profile")))
async def client_profile(
    message: Message, session: AsyncSession, db_user: User, lang: str, _
) -> None:
    from keyboards.inline import profile_kb
    from utils.rendering import render_profile

    count = (
        await session.scalar(
            select(func.count())
            .select_from(Order)
            .where(Order.client_id == db_user.id)
        )
        or 0
    )
    await message.answer(
        render_profile(db_user, lang, count), reply_markup=profile_kb(lang)
    )


@router.message(F.text.in_(button_variants("btn_client_orders")))
async def client_orders(
    message: Message, session: AsyncSession, db_user: User, lang: str, _
) -> None:
    if not _ensure_client(message, db_user, lang, _):
        await message.answer(t(lang, "client_role_required"))
        return
    await send_client_orders(message, session, db_user, lang, 0)


@router.callback_query(PageCB.filter(F.scope == "client_orders"))
async def client_orders_page(
    callback: CallbackQuery,
    callback_data: PageCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
) -> None:
    orders, total, pages = await _load_client_orders_page(
        session, db_user, max(0, callback_data.page)
    )
    if total == 0:
        await callback.answer(t(lang, "no_orders"), show_alert=True)
        return
    text = t(lang, "my_orders_title")
    if pages > 1:
        text += "\n" + t(lang, "page_of", current=min(callback_data.page, pages - 1) + 1, total=pages)
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=my_orders_kb(
                lang, orders, callback_data.page, pages, "client_orders"
            ),
        )
    await callback.answer()


@router.message(F.text.in_(button_variants("btn_client_order")))
async def start_new_order(
    message: Message,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    state,
) -> None:
    if not db_user.phone or not db_user.role:
        await message.answer(t(lang, "please_register"))
        return
    if db_user.role != "client":
        await message.answer(t(lang, "client_role_required"))
        return
    await state.clear()
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
    await message.answer(
        t(lang, "services_title") + "\n" + t(lang, "choose_service_hint"),
        reply_markup=services_kb(lang, list(services), 0),
    )


@router.callback_query(ServiceCB.filter())
async def service_callback(
    callback: CallbackQuery,
    callback_data: ServiceCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    state,
) -> None:
    if not callback.message:
        return
    if callback_data.action == "cancel":
        await state.clear()
        await callback.message.delete()
        await callback.message.answer(t(lang, "cancelled"))
        await callback.answer()
        return

    services = (
        await session.scalars(
            select(Service)
            .where(Service.is_active.is_(True))
            .order_by(Service.sort_order, Service.id)
        )
    ).all()

    if callback_data.action == "list":
        await state.clear()
        if not services:
            await callback.answer(t(lang, "no_services"), show_alert=True)
            return
        await callback.message.edit_text(
            t(lang, "services_title") + "\n" + t(lang, "choose_service_hint"),
            reply_markup=services_kb(lang, list(services), 0),
        )
        await callback.answer()
        return

    if callback_data.action == "page":
        await callback.message.edit_text(
            t(lang, "services_title") + "\n" + t(lang, "choose_service_hint"),
            reply_markup=services_kb(lang, list(services), callback_data.page),
        )
        await callback.answer()
        return

    if callback_data.action != "pick":
        await callback.answer(t(lang, "unknown_callback"), show_alert=True)
        return

    if not _ensure_client(callback.message, db_user, lang, _):
        await callback.answer(t(lang, "client_role_required"), show_alert=True)
        return

    service = await session.get(Service, callback_data.service_id)
    if service is None or not service.is_active:
        await callback.answer(t(lang, "service_not_found"), show_alert=True)
        return

    masters = await order_service.list_masters_for_service(session, service.id)
    if not masters:
        await callback.answer(t(lang, "no_masters_available"), show_alert=True)
        return

    await state.clear()
    await state.update_data(
        service_id=service.id,
        service_name=service.name(lang),
    )
    await callback.message.edit_text(
        t(lang, "available_masters_title"),
        reply_markup=masters_kb(lang, masters),
    )
    await callback.answer()


@router.callback_query(MasterPickCB.filter())
async def master_pick(
    callback: CallbackQuery,
    callback_data,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    state,
) -> None:
    if not callback.message:
        return
    data = await state.get_data()
    service_id = data.get("service_id")
    if not service_id:
        await callback.answer(t(lang, "session_expired"), show_alert=True)
        return
    row = (
        await session.execute(
            select(Master, MasterService.price)
            .join(
                MasterService,
                MasterService.master_id == Master.id,
            )
            .join(User, User.id == Master.user_id)
            .where(
                Master.id == callback_data.master_id,
                MasterService.service_id == service_id,
                MasterService.is_active.is_(True),
                Master.status == MasterStatus.APPROVED.value,
                Master.is_active.is_(True),
                User.is_blocked.is_(False),
            )
        )
    ).first()
    if row is None:
        await callback.answer(t(lang, "no_masters_available"), show_alert=True)
        return
    master, price = row
    await state.update_data(master_id=master.id, price=str(price))
    await state.set_state(OrderForm.description)
    await callback.message.edit_text(
        t(lang, "master_selected", name=master.full_name)
    )
    await callback.message.answer(
        t(lang, "description_prompt"), reply_markup=cancel_only(lang)
    )
    await callback.answer()


@router.message(OrderForm.description, F.text.in_(button_variants("btn_skip")))
async def description_skip(message: Message, lang: str, _) -> None:
    await message.answer(t(lang, "description_prompt"), reply_markup=cancel_only(lang))


@router.message(OrderForm.description, F.text)
async def order_description(message: Message, lang: str, _, state) -> None:
    value = parse_description(message.text or "", min_len=5, max_len=2000)
    if value is None:
        await message.answer(t(lang, "problem_too_short"))
        return
    await state.update_data(description=value)
    await state.set_state(OrderForm.photo)
    await message.answer(
        t(lang, "photo_prompt"), reply_markup=optional_form(lang)
    )


@router.message(OrderForm.photo, F.photo)
async def order_photo(message: Message, lang: str, _, state) -> None:
    file_id = message.photo[-1].file_id if message.photo else None
    await state.update_data(photo_file_id=file_id)
    await state.set_state(OrderForm.date)
    await message.answer(t(lang, "enter_date"), reply_markup=cancel_only(lang))


@router.message(OrderForm.photo, F.text.in_(button_variants("btn_skip")))
async def order_photo_skip(message: Message, lang: str, _, state) -> None:
    await state.update_data(photo_file_id=None)
    await state.set_state(OrderForm.date)
    await message.answer(t(lang, "enter_date"), reply_markup=cancel_only(lang))


@router.message(OrderForm.photo)
async def order_photo_invalid(message: Message, lang: str, _) -> None:
    await message.answer(t(lang, "photo_or_skip"), reply_markup=optional_form(lang))


@router.message(OrderForm.date, F.text.in_(button_variants("btn_skip")))
async def order_date_skip(message: Message, lang: str, _) -> None:
    await message.answer(t(lang, "enter_date"), reply_markup=cancel_only(lang))


@router.message(OrderForm.date, F.text)
async def order_date(message: Message, lang: str, _, state) -> None:
    value = parse_date(message.text or "")
    if value is None:
        await message.answer(t(lang, "date_invalid"))
        return
    await state.update_data(scheduled_date=value.isoformat())
    await state.set_state(OrderForm.time)
    await message.answer(t(lang, "enter_time"), reply_markup=cancel_only(lang))


@router.message(OrderForm.time, F.text)
async def order_time(message: Message, lang: str, _, state) -> None:
    value = parse_time(message.text or "")
    if value is None:
        await message.answer(t(lang, "time_invalid"))
        return
    data = await state.get_data()
    raw_date = data.get("scheduled_date")
    if not raw_date:
        await state.clear()
        await message.answer(t(lang, "session_expired"), reply_markup=client_menu(lang))
        return
    scheduled_date = dt.date.fromisoformat(raw_date)
    if not is_future_schedule(scheduled_date, value):
        await message.answer(t(lang, "schedule_past"))
        return
    await state.update_data(scheduled_time=value)
    await state.set_state(OrderForm.address)
    await message.answer(t(lang, "address_prompt"), reply_markup=cancel_only(lang))


@router.message(OrderForm.address, F.text.in_(button_variants("btn_skip")))
async def order_address_skip(message: Message, lang: str, _) -> None:
    await message.answer(t(lang, "address_prompt"), reply_markup=cancel_only(lang))


@router.message(OrderForm.address, F.text)
async def order_address(message: Message, lang: str, _, state) -> None:
    value = parse_description(message.text or "", min_len=8, max_len=500)
    if value is None:
        await message.answer(t(lang, "address_invalid"))
        return
    await state.update_data(address=value)
    await state.set_state(OrderForm.location)
    await message.answer(
        t(lang, "location_prompt"), reply_markup=location_request(lang)
    )


@router.message(OrderForm.location, F.location)
async def order_location(message: Message, lang: str, _, state) -> None:
    loc = message.location
    await state.update_data(
        latitude=loc.latitude if loc else None,
        longitude=loc.longitude if loc else None,
    )
    await state.set_state(OrderForm.comment)
    await message.answer(
        t(lang, "comment_prompt"), reply_markup=optional_form(lang)
    )


@router.message(OrderForm.location, F.text.in_(button_variants("btn_skip")))
async def order_location_skip(message: Message, lang: str, _, state) -> None:
    await state.update_data(latitude=None, longitude=None)
    await state.set_state(OrderForm.comment)
    await message.answer(
        t(lang, "comment_prompt"), reply_markup=optional_form(lang)
    )


@router.message(OrderForm.location)
async def order_location_invalid(message: Message, lang: str, _) -> None:
    await message.answer(
        t(lang, "location_or_skip"), reply_markup=location_request(lang)
    )


@router.message(OrderForm.comment, F.text.in_(button_variants("btn_skip")))
async def order_comment_skip(message: Message, lang: str, _, state) -> None:
    await state.update_data(comment=None)
    await _show_order_confirm(message, lang, state)


@router.message(OrderForm.comment, F.text)
async def order_comment(message: Message, lang: str, _, state) -> None:
    value = (message.text or "").strip()
    await state.update_data(comment=value[:1000] if value else None)
    await _show_order_confirm(message, lang, state)


async def _show_order_confirm(message: Message, lang: str, state) -> None:
    data = await state.get_data()
    if not data.get("master_id") or not data.get("price"):
        await state.clear()
        await message.answer(t(lang, "session_expired"), reply_markup=client_menu(lang))
        return
    text_lines = [
        t(lang, "order_confirm_title"),
        "",
        f"{t(lang, 'label_service')}: {data.get('service_name', '—')}",
        f"{t(lang, 'label_price')}: {format_money(Decimal(data['price']), lang)}",
        f"{t(lang, 'label_schedule')}: "
        f"{t(lang, 'schedule_title', date=format_date(dt.date.fromisoformat(data['scheduled_date'])), time=data.get('scheduled_time', '—'))}",
        f"{t(lang, 'label_address')}: {data.get('address', '—')}",
        f"{t(lang, 'label_description')}: {data.get('description', '—')}",
    ]
    if data.get("comment"):
        text_lines.append(f"{t(lang, 'label_comment')}: {data['comment']}")
    if data.get("latitude") is not None:
        text_lines.append(f"{t(lang, 'label_location')}: {data['latitude']}, {data['longitude']}")
    if data.get("photo_file_id"):
        text_lines.append(f"{t(lang, 'label_photo')}: ✅")
    await state.set_state(OrderForm.confirm)
    await message.answer("\n".join(text_lines), reply_markup=confirm_order_kb(lang))


@router.callback_query(DraftCB.filter(F.action == "cancel"))
async def order_cancel_draft(
    callback: CallbackQuery, lang: str, _, state
) -> None:
    await state.clear()
    if callback.message:
        await callback.message.edit_text(t(lang, "cancelled"))
    await callback.answer()


@router.callback_query(DraftCB.filter(F.action == "send"))
async def order_send(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    state,
    bot,
) -> None:
    if not callback.message:
        return
    data = await state.get_data()
    required = ("service_id", "master_id", "price", "description", "scheduled_date", "scheduled_time", "address")
    if any(key not in data for key in required):
        await state.clear()
        await callback.message.answer(t(lang, "session_expired"))
        await callback.answer()
        return

    service = await session.get(Service, data["service_id"])
    if service is None or not service.is_active:
        await state.clear()
        await callback.answer(t(lang, "service_not_found"), show_alert=True)
        return

    master = await session.get(Master, data["master_id"])
    if master is None or not master.is_active:
        await state.clear()
        await callback.answer(t(lang, "no_masters_available"), show_alert=True)
        return

    try:
        order = await order_service.create_order(
            session,
            client=db_user,
            service=service,
            master=master,
            price=Decimal(data["price"]),
            description=data.get("description"),
            photo_file_id=data.get("photo_file_id"),
            address=data.get("address") or "",
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
            scheduled_date=dt.date.fromisoformat(data["scheduled_date"]),
            scheduled_time=data.get("scheduled_time") or "00:00",
            comment=data.get("comment"),
        )
        await session.flush()
        order = await order_service.load_order(session, order.id)
        await session.commit()
        if order is not None:
            await notification_service.notify_new_order(bot, session, order)
            await callback.message.answer(
                t(lang, "order_created", order_id=order.public_id),
                reply_markup=client_menu(lang, False),
            )
    except OrderError as exc:
        await callback.message.answer(t(lang, exc.key))
    except Exception:
        await callback.message.answer(t(lang, "unexpected_error"))
        raise
    finally:
        await state.clear()
    await callback.answer()


async def _send_client_list(
    message: Message, session: AsyncSession, user: User, lang: str
) -> None:
    await send_client_orders(message, session, user, lang, 0)


async def _show_order_detail(
    message: Message,
    session: AsyncSession,
    db_user: User,
    lang: str,
    order_id: int,
    *,
    is_admin: bool = False,
) -> None:
    order = await order_service.load_order(session, order_id)
    if order is None:
        await message.answer(t(lang, "order_not_found"))
        return

    viewer = None
    if is_admin:
        viewer = "admin"
    elif order.client_id == db_user.id:
        viewer = "client"
    elif order.master is not None and order.master.user_id == db_user.id:
        viewer = "master"
    if viewer is None:
        await message.answer(t(lang, "not_your_order"))
        return

    text = render_order(order, lang, viewer)
    if viewer == "client":
        can_cancel = order.status in {
            OrderStatus.PENDING_MASTER.value,
            OrderStatus.REJECTED_BY_MASTER.value,
            OrderStatus.EXPIRED.value,
            OrderStatus.AWAITING_PAYMENT.value,
        }
        can_reassign = order.status in {
            OrderStatus.REJECTED_BY_MASTER.value,
            OrderStatus.EXPIRED.value,
        }
        markup = order_actions_kb(
            lang,
            order.id,
            order.status,
            has_review=order.review is not None,
            can_cancel=can_cancel,
            can_reassign=can_reassign,
            can_pay=order.status == OrderStatus.AWAITING_PAYMENT.value,
            can_confirm=order.status == OrderStatus.AWAITING_CONFIRMATION.value,
            can_review=order.status == OrderStatus.COMPLETED.value,
        )
    elif viewer == "master":
        from keyboards.inline import master_order_actions_kb

        markup = master_order_actions_kb(lang, order.id, order.status)
    else:
        from keyboards.inline import admin_order_kb

        markup = admin_order_kb(lang, order, 0)
    await message.answer(text, reply_markup=markup)


@router.callback_query(OrderActionCB.filter(F.action == "detail"))
async def order_detail(
    callback: CallbackQuery,
    callback_data: OrderActionCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    is_admin: bool,
) -> None:
    if not callback.message:
        return
    await _show_order_detail(
        callback.message,
        session,
        db_user,
        lang,
        callback_data.order_id,
        is_admin=is_admin,
    )
    await callback.answer()


@router.callback_query(OrderActionCB.filter(F.action == "list"))
async def order_list_from_detail(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    is_admin: bool,
) -> None:
    if not callback.message:
        return
    if db_user.role == "master":
        from handlers.master import send_master_incoming

        await send_master_incoming(callback.message, session, db_user, lang, is_admin)
    else:
        await send_client_orders(callback.message, session, db_user, lang, 0)
    await callback.answer()


@router.callback_query(OrderActionCB.filter(F.action == "cancel"))
async def order_cancel(
    callback: CallbackQuery,
    callback_data: OrderActionCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    bot,
) -> None:
    if not callback.message:
        return
    try:
        order = await order_service.load_order(session, callback_data.order_id)
        if order is None:
            raise OrderError("order_not_found")
        await order_service.cancel_order(
            session, order.id, db_user, reason=t(lang, "cancel_reason_client")
        )
        await session.flush()
        if order.master and order.master.user:
            await notification_service.notify_order_cancelled_to_master(
                bot, session, order
            )
        await callback.message.answer(t(lang, "order_cancelled_to_client"))
        await session.commit()
    except OrderError as exc:
        await callback.answer(t(lang, exc.key), show_alert=True)
        return
    await callback.answer()


@router.callback_query(OrderActionCB.filter(F.action == "confirm"))
async def order_confirm(
    callback: CallbackQuery,
    callback_data: OrderActionCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    bot,
) -> None:
    if not callback.message:
        return
    try:
        order = await order_service.load_order(session, callback_data.order_id)
        if order is None:
            raise OrderError("order_not_found")
        order, income, commission = await order_service.confirm_completion(
            session, order.id, db_user
        )
        await session.flush()
        await session.commit()
        await notification_service.notify_order_completed(
            bot, session, order, income, commission
        )
        await callback.message.answer(t(lang, "order_completed_client"))
    except OrderError as exc:
        await callback.answer(t(lang, exc.key), show_alert=True)
        return
    await callback.answer()


@router.callback_query(OrderActionCB.filter(F.action == "dispute"))
async def order_dispute(
    callback: CallbackQuery,
    callback_data: OrderActionCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    state,
) -> None:
    order = await order_service.load_order(session, callback_data.order_id)
    if order is None or order.client_id != db_user.id:
        await callback.answer(t(lang, "not_your_order"), show_alert=True)
        return
    if order.status not in {
        OrderStatus.PAID.value,
        OrderStatus.IN_PROGRESS.value,
        OrderStatus.AWAITING_CONFIRMATION.value,
        OrderStatus.DISPUTED.value,
    }:
        await callback.answer(t(lang, "order_not_pending"), show_alert=True)
        return
    await state.clear()
    await state.update_data(complaint_order_id=order.id)
    await state.set_state(ComplaintForm.text)
    if callback.message:
        await callback.message.answer(
            t(lang, "enter_problem"), reply_markup=cancel_only(lang)
        )
    await callback.answer()


@router.message(ComplaintForm.text, F.text.in_(button_variants("btn_skip")))
async def complaint_skip(message: Message, lang: str, _) -> None:
    await message.answer(t(lang, "enter_problem"), reply_markup=cancel_only(lang))


@router.message(ComplaintForm.text, F.text)
async def complaint_text(
    message: Message,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    state,
    bot,
) -> None:
    value = parse_description(message.text or "", min_len=5, max_len=2000)
    if value is None:
        await message.answer(t(lang, "problem_too_short"))
        return
    data = await state.get_data()
    order_id = data.get("complaint_order_id")
    if not order_id:
        await state.clear()
        await message.answer(t(lang, "session_expired"))
        return
    try:
        order, complaint = await order_service.create_dispute(
            session, order_id, db_user, value
        )
        await session.flush()
        await session.commit()
        order = await order_service.load_order(session, order.id)
        if order is not None:
            await notification_service.notify_dispute(bot, session, order, complaint)
        await message.answer(
            t(lang, "complaint_created"), reply_markup=client_menu(lang, False)
        )
    except OrderError as exc:
        await message.answer(t(lang, exc.key))
    finally:
        await state.clear()


@router.callback_query(OrderActionCB.filter(F.action == "review"))
async def review_start(
    callback: CallbackQuery,
    callback_data: OrderActionCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    state,
) -> None:
    order = await order_service.load_order(session, callback_data.order_id)
    if order is None or order.client_id != db_user.id:
        await callback.answer(t(lang, "not_your_order"), show_alert=True)
        return
    if order.status != OrderStatus.COMPLETED.value:
        await callback.answer(t(lang, "review_not_allowed"), show_alert=True)
        return
    if order.review is not None:
        await callback.answer(t(lang, "already_reviewed"), show_alert=True)
        return
    await state.clear()
    await state.update_data(review_order_id=order.id)
    if callback.message:
        await callback.message.answer(
            t(lang, "review_prompt") + "\n" + t(lang, "review_stars_hint"),
            reply_markup=review_stars_kb(lang, order.id),
        )
    await callback.answer()


@router.callback_query(ReviewCB.filter(F.action == "rate"))
async def review_rate(
    callback: CallbackQuery,
    callback_data: ReviewCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    state,
) -> None:
    if not (1 <= callback_data.rating <= 5):
        await callback.answer(t(lang, "invalid_rating"), show_alert=True)
        return
    order = await order_service.load_order(session, callback_data.order_id)
    if order is None or order.client_id != db_user.id:
        await callback.answer(t(lang, "not_your_order"), show_alert=True)
        return
    if order.status != OrderStatus.COMPLETED.value:
        await callback.answer(t(lang, "review_not_allowed"), show_alert=True)
        return
    if order.review is not None:
        await callback.answer(t(lang, "already_reviewed"), show_alert=True)
        return
    await state.update_data(review_order_id=order.id, review_rating=callback_data.rating)
    await state.set_state(ReviewForm.comment)
    if callback.message:
        await callback.message.edit_text(t(lang, "review_stars_hint"))
        await callback.message.answer(
            t(lang, "review_comment_prompt"),
            reply_markup=review_comment_kb(lang, order.id),
        )
    await callback.answer()


async def _save_review(
    session: AsyncSession,
    db_user: User,
    lang: str,
    state,
    bot,
    comment: str | None,
    *,
    target: Message | None = None,
) -> bool:
    data = await state.get_data()
    order_id = data.get("review_order_id")
    rating = data.get("review_rating")
    if not order_id or not rating:
        if target:
            await target.answer(t(lang, "session_expired"))
        await state.clear()
        return False
    order = await order_service.load_order(session, order_id)
    if order is None or order.client_id != db_user.id or order.master is None:
        if target:
            await target.answer(t(lang, "review_not_allowed"))
        await state.clear()
        return False
    if order.status != OrderStatus.COMPLETED.value:
        if target:
            await target.answer(t(lang, "review_not_allowed"))
        await state.clear()
        return False
    if order.review is not None:
        if target:
            await target.answer(t(lang, "already_reviewed"))
        await state.clear()
        return False

    review = Review(
        order_id=order.id,
        master_id=order.master_id,
        client_id=db_user.id,
        rating=int(rating),
        comment=(comment or None),
    )
    session.add(review)
    master = order.master
    old_count = master.rating_count or 0
    old_rating = Decimal(master.rating or 0)
    new_count = old_count + 1
    master.rating = quantize(
        ((old_rating * old_count) + Decimal(int(rating))) / Decimal(new_count)
    )
    master.rating_count = new_count
    await session.flush()
    await session.commit()
    if target:
        await target.answer(t(lang, "review_thanks"), reply_markup=client_menu(lang, False))
    if master.user:
        await notification_service.notify_review(
            bot,
            session,
            master.user,
            int(rating),
            comment,
        )
    await state.clear()
    return True


@router.message(ReviewForm.comment, F.text)
async def review_comment(
    message: Message,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    state,
    bot,
) -> None:
    value = " ".join((message.text or "").split())
    if len(value) > 1000:
        await message.answer(t(lang, "value_too_long"))
        return
    await _save_review(
        session, db_user, lang, state, bot, value or None, target=message
    )


@router.callback_query(ReviewCB.filter(F.action == "skip_comment"))
async def review_skip_comment(
    callback: CallbackQuery,
    callback_data: ReviewCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    state,
    bot,
) -> None:
    if not callback.message:
        return
    ok = await _save_review(
        session, db_user, lang, state, bot, None, target=None
    )
    if not ok:
        await callback.answer(t(lang, "session_expired"), show_alert=True)
        return
    try:
        await callback.message.edit_text(t(lang, "review_thanks"))
    except Exception:
        pass
    await callback.answer()


@router.callback_query(ReassignCB.filter(F.action == "list"))
async def reassign_list(
    callback: CallbackQuery,
    callback_data: ReassignCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
) -> None:
    order = await order_service.load_order(session, callback_data.order_id)
    if order is None or order.client_id != db_user.id:
        await callback.answer(t(lang, "not_your_order"), show_alert=True)
        return
    if order.status not in {
        OrderStatus.REJECTED_BY_MASTER.value,
        OrderStatus.EXPIRED.value,
    }:
        await callback.answer(t(lang, "order_not_pending"), show_alert=True)
        return
    masters = await order_service.list_masters_for_service(
        session, order.service_id, exclude_master_id=order.master_id
    )
    if not masters:
        await callback.answer(t(lang, "no_other_masters"), show_alert=True)
        return
    if callback.message:
        await callback.message.answer(
            t(lang, "reassign_title"),
            reply_markup=reassign_masters_kb(lang, order.id, masters),
        )
    await callback.answer()


@router.callback_query(ReassignCB.filter(F.action == "pick"))
async def reassign_pick(
    callback: CallbackQuery,
    callback_data: ReassignCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    bot,
) -> None:
    try:
        order, master, price = await order_service.reassign_order(
            session, callback_data.order_id, callback_data.master_id, db_user
        )
        await session.flush()
        await session.commit()
        order = await order_service.load_order(session, order.id)
        if order is not None:
            await notification_service.notify_new_order(bot, session, order)
        if callback.message:
            await callback.message.answer(
                t(lang, "order_reassigned_client", master=master.full_name)
            )
    except OrderError as exc:
        await callback.answer(t(lang, exc.key), show_alert=True)
        return
    await callback.answer()
