from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from utils.i18n import t


def _btn(lang: str, key: str) -> KeyboardButton:
    return KeyboardButton(text=t(lang, key))


def client_menu(lang: str, is_admin: bool = False) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    if is_admin:
        builder.row(
            _btn(lang, "btn_admin_panel"),
            _btn(lang, "btn_client_order"),
            _btn(lang, "btn_client_orders"),
        )
    else:
        builder.row(_btn(lang, "btn_client_order"), _btn(lang, "btn_client_orders"))
    builder.row(_btn(lang, "btn_client_profile"), _btn(lang, "btn_client_help"))
    return builder.as_markup(resize_keyboard=True, is_persistent=True)


def master_menu(lang: str, is_admin: bool = False) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    if is_admin:
        builder.row(
            _btn(lang, "btn_admin_panel"),
            _btn(lang, "btn_master_incoming"),
            _btn(lang, "btn_master_completed"),
        )
        builder.row(
            _btn(lang, "btn_master_balance"),
            _btn(lang, "btn_master_withdraw"),
            _btn(lang, "btn_master_withdrawals"),
        )
        builder.row(_btn(lang, "btn_master_profile"), _btn(lang, "btn_master_help"))
    else:
        builder.row(
            _btn(lang, "btn_master_incoming"),
            _btn(lang, "btn_master_completed"),
            _btn(lang, "btn_master_balance"),
        )
        builder.row(
            _btn(lang, "btn_master_withdraw"),
            _btn(lang, "btn_master_withdrawals"),
            _btn(lang, "btn_master_profile"),
        )
        builder.row(_btn(lang, "btn_master_help"))
    return builder.as_markup(resize_keyboard=True, is_persistent=True)


def admin_menu(lang: str) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        _btn(lang, "btn_admin_panel"),
        _btn(lang, "btn_client_order"),
        _btn(lang, "btn_client_orders"),
    )
    builder.row(_btn(lang, "btn_client_profile"), _btn(lang, "btn_client_help"))
    return builder.as_markup(resize_keyboard=True, is_persistent=True)


def phone_request(lang: str) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=t(lang, "btn_share_phone"), request_contact=True))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def location_request(lang: str) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=t(lang, "btn_send_location"), request_location=True))
    builder.row(_btn(lang, "btn_skip"))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def optional_form(lang: str) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(_btn(lang, "btn_skip"), _btn(lang, "btn_cancel"))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def cancel_only(lang: str) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(_btn(lang, "btn_cancel"))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def skip_only(lang: str) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(_btn(lang, "btn_skip"))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)
