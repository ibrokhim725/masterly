from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.enums import (
    MasterStatus,
    OrderStatus,
    PaymentStatus,
    WithdrawalStatus,
)
from keyboards.callbacks import (
    AdminCB,
    DraftCB,
    LangCB,
    MasterPickCB,
    MasterServiceCB,
    OrderActionCB,
    PageCB,
    PaymentCB,
    ReassignCB,
    ReviewCB,
    RoleCB,
    ServiceCB,
    WithdrawCB,
)
from utils.formatting import format_money, format_phone, human_status
from utils.i18n import t

PAGE_SIZE = 8


def _truncate(value: str, limit: int = 48) -> str:
    value = " ".join((value or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def language_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🇺🇿 O'zbek tili", callback_data=LangCB(code="uz").pack())
    builder.button(text="🇷🇺 Русский язык", callback_data=LangCB(code="ru").pack())
    builder.adjust(1)
    return builder.as_markup()


def intro_kb(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t(lang, "btn_continue"), callback_data=DraftCB(action="intro_done").pack())
    return builder.as_markup()


def role_kb(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t(lang, "btn_role_client"), callback_data=RoleCB(role="client").pack())
    builder.button(text=t(lang, "btn_role_master"), callback_data=RoleCB(role="master").pack())
    builder.adjust(1)
    return builder.as_markup()


def master_start_kb(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_continue"), callback_data=DraftCB(action="master_start").pack()
    )
    return builder.as_markup()


def master_resubmit_kb(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_resubmit_application"),
        callback_data=DraftCB(action="master_start").pack(),
    )
    return builder.as_markup()


def master_price_kb(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_default_prices"),
        callback_data=MasterServiceCB(action="defaults").pack(),
    )
    builder.button(
        text=t(lang, "btn_cancel"),
        callback_data=MasterServiceCB(action="cancel_prices").pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def master_completed_kb(
    lang: str, page: int, total_pages: int
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text=t(lang, "btn_prev"),
                    callback_data=PageCB(scope="master_completed", page=page - 1).pack(),
                )
            )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text=t(lang, "btn_next"),
                    callback_data=PageCB(scope="master_completed", page=page + 1).pack(),
                )
            )
        if nav:
            builder.row(*nav)
    return builder.as_markup()


def services_kb(lang: str, services: Sequence, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total = len(services)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    for service in services[start : start + PAGE_SIZE]:
        builder.row(
            InlineKeyboardButton(
                text=_truncate(service.name(lang), 55),
                callback_data=ServiceCB(action="pick", service_id=service.id, page=page).pack(),
            )
        )
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text=t(lang, "btn_prev"),
                    callback_data=ServiceCB(action="page", page=page - 1).pack(),
                )
            )
        if page < pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text=t(lang, "btn_next"),
                    callback_data=ServiceCB(action="page", page=page + 1).pack(),
                )
            )
        if nav:
            builder.row(*nav)
    builder.row(
        InlineKeyboardButton(text=t(lang, "btn_cancel"), callback_data=ServiceCB(action="cancel").pack())
    )
    return builder.as_markup()


def masters_kb(lang: str, items: Iterable[tuple]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for master, price in items:
        builder.row(
            InlineKeyboardButton(
                text=_truncate(
                    f"⭐ {float(master.rating):.1f} • {master.full_name} • {format_money(price, lang)}",
                    60,
                ),
                callback_data=MasterPickCB(master_id=master.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(text=t(lang, "btn_cancel"), callback_data=ServiceCB(action="cancel").pack())
    )
    return builder.as_markup()


def reassign_masters_kb(lang: str, order_id: int, items: Iterable[tuple]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for master, price in items:
        builder.row(
            InlineKeyboardButton(
                text=_truncate(
                    f"⭐ {float(master.rating):.1f} • {master.full_name} • {format_money(price, lang)}",
                    60,
                ),
                callback_data=ReassignCB(
                    action="pick", order_id=order_id, master_id=master.id
                ).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=t(lang, "btn_back"),
            callback_data=OrderActionCB(action="detail", order_id=order_id).pack(),
        )
    )
    return builder.as_markup()


def confirm_order_kb(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t(lang, "btn_send_order"), callback_data=DraftCB(action="send").pack())
    builder.button(text=t(lang, "btn_cancel"), callback_data=DraftCB(action="cancel").pack())
    builder.adjust(1)
    return builder.as_markup()


def new_order_master_kb(lang: str, order_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_accept_order"),
        callback_data=OrderActionCB(action="accept", order_id=order_id).pack(),
    )
    builder.button(
        text=t(lang, "btn_reject_order"),
        callback_data=OrderActionCB(action="reject", order_id=order_id).pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def choose_other_master_kb(lang: str, order_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_choose_other_master"),
        callback_data=ReassignCB(action="list", order_id=order_id).pack(),
    )
    return builder.as_markup()


def review_shortcut_kb(lang: str, order_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_leave_review"),
        callback_data=OrderActionCB(action="review", order_id=order_id).pack(),
    )
    return builder.as_markup()


def payment_offer_kb(lang: str, order_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_pay"),
        callback_data=PaymentCB(action="create", order_id=order_id).pack(),
    )
    builder.button(
        text=t(lang, "btn_reject_unpaid"),
        callback_data=OrderActionCB(action="cancel", order_id=order_id).pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def payment_link_kb(lang: str, order_id: int, pay_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t(lang, "btn_pay"), url=pay_url)
    builder.button(
        text=t(lang, "btn_check_payment"),
        callback_data=PaymentCB(action="check", order_id=order_id).pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def my_orders_kb(
    lang: str,
    orders: Sequence,
    page: int,
    total_pages: int,
    scope: str = "client_orders",
    show_new_order: bool = True,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for order in orders:
        builder.row(
            InlineKeyboardButton(
                text=_truncate(
                    f"#{order.public_id} • {order.service_name} • "
                    f"{human_status(order.status, lang)}",
                    60,
                ),
                callback_data=OrderActionCB(action="detail", order_id=order.id).pack(),
            )
        )
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text=t(lang, "btn_prev"),
                    callback_data=PageCB(scope=scope, page=page - 1).pack(),
                )
            )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text=t(lang, "btn_next"),
                    callback_data=PageCB(scope=scope, page=page + 1).pack(),
                )
            )
        if nav:
            builder.row(*nav)
    if show_new_order:
        builder.row(
            InlineKeyboardButton(
                text=t(lang, "btn_client_order"), callback_data=ServiceCB(action="list").pack()
            )
        )
    return builder.as_markup()


def order_actions_kb(
    lang: str,
    order_id: int,
    status: str,
    has_review: bool = False,
    can_cancel: bool = False,
    can_reassign: bool = False,
    can_pay: bool = False,
    can_confirm: bool = False,
    can_review: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    rows: list = []
    if can_pay:
        builder.button(
            text=t(lang, "btn_pay"),
            callback_data=PaymentCB(action="create", order_id=order_id).pack(),
        )
    if can_confirm:
        builder.button(
            text=t(lang, "btn_confirm_done"),
            callback_data=OrderActionCB(action="confirm", order_id=order_id).pack(),
        )
        builder.button(
            text=t(lang, "btn_dispute"),
            callback_data=OrderActionCB(action="dispute", order_id=order_id).pack(),
        )
    if can_review and not has_review:
        builder.button(
            text=t(lang, "btn_leave_review"),
            callback_data=OrderActionCB(action="review", order_id=order_id).pack(),
        )
    if can_reassign:
        builder.button(
            text=t(lang, "btn_choose_other_master"),
            callback_data=ReassignCB(action="list", order_id=order_id).pack(),
        )
    if can_cancel:
        builder.button(
            text=t(lang, "btn_cancel_order"),
            callback_data=OrderActionCB(action="cancel", order_id=order_id).pack(),
        )
    builder.button(
        text=t(lang, "btn_back"),
        callback_data=OrderActionCB(action="list", order_id=0).pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def master_order_actions_kb(lang: str, order_id: int, status: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if status == OrderStatus.PENDING_MASTER.value:
        builder.row(
            InlineKeyboardButton(
                text=t(lang, "btn_accept_order"),
                callback_data=OrderActionCB(action="accept", order_id=order_id).pack(),
            ),
            InlineKeyboardButton(
                text=t(lang, "btn_reject_order"),
                callback_data=OrderActionCB(action="reject", order_id=order_id).pack(),
            ),
        )
    if status == OrderStatus.PAID.value:
        builder.row(
            InlineKeyboardButton(
                text=t(lang, "btn_start_work"),
                callback_data=OrderActionCB(action="start", order_id=order_id).pack(),
            )
        )
    if status in {OrderStatus.PAID.value, OrderStatus.IN_PROGRESS.value}:
        builder.row(
            InlineKeyboardButton(
                text=t(lang, "btn_mark_done"),
                callback_data=OrderActionCB(action="done", order_id=order_id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=t(lang, "btn_back"),
            callback_data=OrderActionCB(action="master_list", order_id=0).pack(),
        )
    )
    return builder.as_markup()


def review_stars_kb(lang: str, order_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for rating in range(1, 6):
        builder.button(
            text="⭐" * rating,
            callback_data=ReviewCB(action="rate", order_id=order_id, rating=rating).pack(),
        )
    builder.adjust(5)
    return builder.as_markup()


def review_comment_kb(lang: str, order_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_skip"),
        callback_data=ReviewCB(action="skip_comment", order_id=order_id).pack(),
    )
    return builder.as_markup()


def profile_kb(lang: str, is_master: bool = False, is_paused: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_change_language"), callback_data=LangCB(code="show").pack()
    )
    if is_master:
        builder.button(
            text=t(lang, "btn_resume_master" if is_paused else "btn_pause_master"),
            callback_data=AdminCB(section="self", action="toggle_active").pack(),
        )
    builder.adjust(1)
    return builder.as_markup()


def balance_kb(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_master_withdraw"), callback_data=WithdrawCB(action="start").pack()
    )
    builder.button(
        text=t(lang, "btn_transactions"), callback_data=AdminCB(section="self", action="tx").pack()
    )
    builder.adjust(1)
    return builder.as_markup()


def withdraw_cards_kb(lang: str, cards: Sequence) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for card in cards:
        builder.row(
            InlineKeyboardButton(
                text=t(lang, "btn_use_card", card=card.card_masked),
                callback_data=WithdrawCB(action="card", card_id=card.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=t(lang, "btn_add_card"), callback_data=WithdrawCB(action="add_card").pack()
        )
    )
    builder.row(
        InlineKeyboardButton(text=t(lang, "btn_cancel"), callback_data=WithdrawCB(action="cancel").pack())
    )
    return builder.as_markup()


def withdraw_confirm_kb(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_withdraw_confirm"), callback_data=WithdrawCB(action="confirm").pack()
    )
    builder.button(
        text=t(lang, "btn_cancel"), callback_data=WithdrawCB(action="cancel").pack()
    )
    builder.adjust(1)
    return builder.as_markup()


def master_services_kb(lang: str, services: Sequence, selected: set[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for service in services:
        mark = "✅" if service.id in selected else "⬜"
        builder.row(
            InlineKeyboardButton(
                text=f"{mark} {_truncate(service.name(lang), 50)}",
                callback_data=MasterServiceCB(action="toggle", service_id=service.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=t(lang, "btn_default_prices"),
            callback_data=MasterServiceCB(action="defaults").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=t(lang, "btn_services_done"), callback_data=MasterServiceCB(action="done").pack()
        )
    )
    return builder.as_markup()


def admin_menu_kb(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    entries = [
        ("btn_admin_stats", "stats"),
        ("btn_admin_users", "users"),
        ("btn_admin_masters", "masters"),
        ("btn_admin_orders", "orders"),
        ("btn_admin_services", "services"),
        ("btn_admin_payments", "payments"),
        ("btn_admin_withdrawals", "withdrawals"),
        ("btn_admin_complaints", "complaints"),
        ("btn_admin_broadcast", "broadcast"),
        ("btn_admin_settings", "settings"),
        ("btn_admin_platform", "platform"),
    ]
    for key, section in entries:
        builder.button(
            text=t(lang, key), callback_data=AdminCB(section=section).pack()
        )
    builder.adjust(2)
    return builder.as_markup()


def admin_back_kb(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "admin_section_back"), callback_data=AdminCB(section="home").pack()
    )
    return builder.as_markup()


def _admin_pagination(
    builder: InlineKeyboardBuilder,
    lang: str,
    section: str,
    page: int,
    total_pages: int,
) -> None:
    if total_pages <= 1:
        return
    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text=t(lang, "btn_prev"),
                callback_data=AdminCB(section=section, page=page - 1).pack(),
            )
        )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text=t(lang, "btn_next"),
                callback_data=AdminCB(section=section, page=page + 1).pack(),
            )
        )
    if nav:
        builder.row(*nav)


def admin_users_kb(
    lang: str, users: Sequence, page: int, total_pages: int
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for user in users:
        role = t(lang, f"role_{user.role or 'client'}_label")
        builder.row(
            InlineKeyboardButton(
                text=_truncate(f"{user.full_name} • {role} • {format_phone(user.phone)}", 60),
                callback_data=AdminCB(section="user", obj_id=user.id, page=page).pack(),
            )
        )
    _admin_pagination(builder, lang, "users", page, total_pages)
    builder.row(
        InlineKeyboardButton(
            text=t(lang, "admin_section_back"), callback_data=AdminCB(section="home").pack()
        )
    )
    return builder.as_markup()


def admin_user_kb(lang: str, user, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_unblock_user" if user.is_blocked else "btn_block_user"),
        callback_data=AdminCB(
            section="user",
            action="unblock" if user.is_blocked else "block",
            obj_id=user.id,
            page=page,
        ).pack(),
    )
    builder.button(
        text=t(lang, "btn_back"),
        callback_data=AdminCB(section="users", page=page).pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_masters_kb(
    lang: str, masters: Sequence, page: int, total_pages: int
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for master in masters:
        status = t(lang, f"master_status_{master.status}")
        builder.row(
            InlineKeyboardButton(
                text=_truncate(f"{master.full_name} • {status}", 60),
                callback_data=AdminCB(section="master", obj_id=master.id, page=page).pack(),
            )
        )
    _admin_pagination(builder, lang, "masters", page, total_pages)
    builder.row(
        InlineKeyboardButton(
            text=t(lang, "admin_section_back"), callback_data=AdminCB(section="home").pack()
        )
    )
    return builder.as_markup()


def admin_master_kb(lang: str, master, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if master.status == MasterStatus.PENDING.value:
        builder.button(
            text=t(lang, "btn_approve_master"),
            callback_data=AdminCB(
                section="master", action="approve", obj_id=master.id, page=page
            ).pack(),
        )
        builder.button(
            text=t(lang, "btn_reject_master"),
            callback_data=AdminCB(
                section="master", action="reject", obj_id=master.id, page=page
            ).pack(),
        )
    else:
        builder.button(
            text=t(lang, "btn_toggle_master_active"),
            callback_data=AdminCB(
                section="master", action="toggle_active", obj_id=master.id, page=page
            ).pack(),
        )
    builder.button(
        text=t(lang, "btn_back"),
        callback_data=AdminCB(section="masters", page=page).pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_orders_kb(
    lang: str,
    orders: Sequence,
    page: int,
    total_pages: int,
    section: str = "orders",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for order in orders:
        builder.row(
            InlineKeyboardButton(
                text=_truncate(
                    f"#{order.public_id} • {order.service_name} • {human_status(order.status, lang)}",
                    60,
                ),
                callback_data=AdminCB(section="order", obj_id=order.id, page=page).pack(),
            )
        )
    _admin_pagination(builder, lang, section, page, total_pages)
    builder.row(
        InlineKeyboardButton(
            text=t(lang, "admin_section_back"), callback_data=AdminCB(section="home").pack()
        )
    )
    return builder.as_markup()


def admin_order_kb(lang: str, order, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if order.status == OrderStatus.DISPUTED.value:
        builder.button(
            text=t(lang, "btn_resolve_for_master"),
            callback_data=AdminCB(
                section="order", action="resolve_master", obj_id=order.id, page=page
            ).pack(),
        )
        builder.button(
            text=t(lang, "btn_resolve_for_client"),
            callback_data=AdminCB(
                section="order", action="resolve_client", obj_id=order.id, page=page
            ).pack(),
        )
    builder.button(
        text=t(lang, "btn_back"),
        callback_data=AdminCB(section="orders", page=page).pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_services_kb(
    lang: str, services: Sequence, page: int, total_pages: int
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for service in services:
        status = "🟢" if service.is_active else "🔴"
        builder.row(
            InlineKeyboardButton(
                text=_truncate(
                    f"{status} {service.name_uz} • {format_money(service.base_price, lang)}",
                    60,
                ),
                callback_data=AdminCB(section="service", obj_id=service.id, page=page).pack(),
            )
        )
    _admin_pagination(builder, lang, "services", page, total_pages)
    builder.row(
        InlineKeyboardButton(
            text=t(lang, "btn_add_service"),
            callback_data=AdminCB(section="service", action="add").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=t(lang, "admin_section_back"), callback_data=AdminCB(section="home").pack()
        )
    )
    return builder.as_markup()


def admin_service_kb(lang: str, service, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_edit_service_price"),
        callback_data=AdminCB(
            section="service", action="edit_price", obj_id=service.id, page=page
        ).pack(),
    )
    builder.button(
        text=t(lang, "btn_price_type"),
        callback_data=AdminCB(
            section="service", action="price_type", obj_id=service.id, page=page
        ).pack(),
    )
    builder.button(
        text=t(lang, "btn_toggle_service"),
        callback_data=AdminCB(
            section="service", action="toggle", obj_id=service.id, page=page
        ).pack(),
    )
    builder.button(
        text=t(lang, "btn_back"),
        callback_data=AdminCB(section="services", page=page).pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_price_type_kb(
    lang: str, service_id: int, page: int = 0
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, action in (
        ("btn_price_fixed", "ptype_fixed"),
        ("btn_price_from", "ptype_from"),
        ("btn_price_inspection", "ptype_inspection"),
    ):
        builder.row(
            InlineKeyboardButton(
                text=t(lang, key),
                callback_data=AdminCB(
                    section="service", action=action, obj_id=service_id, page=page
                ).pack(),
            )
        )
    if service_id:
        back = AdminCB(section="service", obj_id=service_id, page=page).pack()
    else:
        back = AdminCB(section="services").pack()
    builder.row(InlineKeyboardButton(text=t(lang, "btn_back"), callback_data=back))
    return builder.as_markup()


def admin_withdrawals_kb(
    lang: str, requests: Sequence, page: int, total_pages: int
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for request in requests:
        status = t(lang, f"withdrawal_status_{request.status}")
        builder.row(
            InlineKeyboardButton(
                text=_truncate(
                    f"#{request.id} • {request.master.full_name if request.master else '—'} • "
                    f"{format_money(request.amount, lang)} • {status}",
                    60,
                ),
                callback_data=AdminCB(
                    section="withdrawal", obj_id=request.id, page=page
                ).pack(),
            )
        )
    _admin_pagination(builder, lang, "withdrawals", page, total_pages)
    builder.row(
        InlineKeyboardButton(
            text=t(lang, "admin_section_back"), callback_data=AdminCB(section="home").pack()
        )
    )
    return builder.as_markup()


def admin_withdrawal_kb(lang: str, request) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if request.status == WithdrawalStatus.PENDING.value:
        builder.button(
            text=t(lang, "btn_approve_withdrawal"),
            callback_data=AdminCB(
                section="withdrawal", action="approve", obj_id=request.id
            ).pack(),
        )
        builder.button(
            text=t(lang, "btn_reject_withdrawal"),
            callback_data=AdminCB(
                section="withdrawal", action="reject", obj_id=request.id
            ).pack(),
        )
    if request.status == WithdrawalStatus.APPROVED.value:
        builder.button(
            text=t(lang, "btn_mark_paid"),
            callback_data=AdminCB(
                section="withdrawal", action="paid", obj_id=request.id
            ).pack(),
        )
        builder.button(
            text=t(lang, "btn_reject_withdrawal"),
            callback_data=AdminCB(
                section="withdrawal", action="reject", obj_id=request.id
            ).pack(),
        )
    if request.card_id:
        builder.button(
            text=t(lang, "btn_reveal_card"),
            callback_data=AdminCB(
                section="withdrawal", action="reveal", obj_id=request.id
            ).pack(),
        )
    builder.button(
        text=t(lang, "btn_back"),
        callback_data=AdminCB(section="withdrawals").pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_payments_kb(
    lang: str, payments: Sequence, page: int, total_pages: int
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for payment in payments:
        builder.row(
            InlineKeyboardButton(
                text=_truncate(
                    f"#{payment.id} • {format_money(payment.amount, lang)} • "
                    f"{t(lang, f'payment_status_{payment.status}')}",
                    60,
                ),
                callback_data=AdminCB(
                    section="payment", obj_id=payment.id, page=page
                ).pack(),
            )
        )
    _admin_pagination(builder, lang, "payments", page, total_pages)
    builder.row(
        InlineKeyboardButton(
            text=t(lang, "admin_section_back"), callback_data=AdminCB(section="home").pack()
        )
    )
    return builder.as_markup()


def admin_payment_kb(lang: str, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_back"),
        callback_data=AdminCB(section="payments", page=page).pack(),
    )
    return builder.as_markup()


def admin_skip_txn_kb(lang: str, request_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_skip"),
        callback_data=AdminCB(
            section="withdrawal", action="paid_now", obj_id=request_id
        ).pack(),
    )
    return builder.as_markup()


def admin_complaints_kb(
    lang: str, complaints: Sequence, page: int, total_pages: int
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for complaint in complaints:
        builder.row(
            InlineKeyboardButton(
                text=_truncate(
                    f"#{complaint.id} • {complaint.order.public_id if complaint.order else '—'} • "
                    f"{complaint.user.full_name if complaint.user else '—'}",
                    60,
                ),
                callback_data=AdminCB(
                    section="complaint", obj_id=complaint.id, page=page
                ).pack(),
            )
        )
    _admin_pagination(builder, lang, "complaints", page, total_pages)
    builder.row(
        InlineKeyboardButton(
            text=t(lang, "admin_section_back"), callback_data=AdminCB(section="home").pack()
        )
    )
    return builder.as_markup()


def admin_complaint_kb(lang: str, complaint) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_resolve_complaint"),
        callback_data=AdminCB(
            section="complaint", action="resolve", obj_id=complaint.id
        ).pack(),
    )
    builder.button(
        text=t(lang, "btn_back"),
        callback_data=AdminCB(section="complaints").pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_settings_kb(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_edit_commission"),
        callback_data=AdminCB(section="settings", action="commission").pack(),
    )
    builder.button(
        text=t(lang, "btn_edit_min_withdrawal"),
        callback_data=AdminCB(section="settings", action="min_withdrawal").pack(),
    )
    builder.row(
        InlineKeyboardButton(
            text=t(lang, "admin_section_back"), callback_data=AdminCB(section="home").pack()
        )
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_broadcast_confirm_kb(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "btn_broadcast_send"),
        callback_data=AdminCB(section="broadcast", action="send").pack(),
    )
    builder.button(
        text=t(lang, "btn_cancel"), callback_data=AdminCB(section="home").pack()
    )
    builder.adjust(1)
    return builder.as_markup()
