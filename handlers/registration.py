from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User
from handlers.start import menu_for, send_status_home
from keyboards.callbacks import DraftCB, RoleCB
from keyboards.inline import intro_kb, master_start_kb, role_kb
from keyboards.reply import client_menu, phone_request
from utils.i18n import t
from utils.validators import normalize_phone

router = Router(name="registration")


@router.callback_query(DraftCB.filter(F.action == "intro_done"))
async def intro_done(
    callback: CallbackQuery, db_user: User, lang: str, _, is_admin: bool
) -> None:
    await callback.answer()
    if not callback.message:
        return
    if db_user.phone:
        await send_status_home(callback.message, db_user, lang, is_admin)
        return
    await callback.message.answer(
        t(lang, "share_phone_prompt"), reply_markup=phone_request(lang)
    )


@router.message(F.contact)
async def contact_received(
    message: Message,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    is_admin: bool,
) -> None:
    contact = message.contact
    if contact is None or message.from_user is None:
        return
    if contact.user_id != message.from_user.id:
        await message.answer(t(lang, "phone_invalid"), reply_markup=phone_request(lang))
        return

    phone = normalize_phone(contact.phone_number)
    if phone is None:
        await message.answer(t(lang, "phone_invalid"), reply_markup=phone_request(lang))
        return

    db_user.phone = phone
    await session.flush()
    await message.answer(t(lang, "phone_saved", phone=phone))

    if not db_user.role:
        await message.answer(t(lang, "choose_role"), reply_markup=role_kb(lang))
        return

    await send_status_home(message, db_user, lang, is_admin)


@router.callback_query(RoleCB.filter())
async def role_selected(
    callback: CallbackQuery,
    callback_data: RoleCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    is_admin: bool,
) -> None:
    role = callback_data.role
    if role not in {"client", "master"}:
        await callback.answer(t(lang, "error_generic"), show_alert=True)
        return
    if not db_user.phone:
        await callback.answer(t(lang, "please_register"), show_alert=True)
        return

    db_user.role = role
    await session.flush()
    await callback.answer()

    if not callback.message:
        return

    if role == "client":
        await callback.message.answer(t(lang, "role_saved_client"))
        await callback.message.answer(
            t(lang, "choose_from_menu"), reply_markup=client_menu(lang, is_admin)
        )
        return

    await callback.message.answer(t(lang, "role_saved_master"))
    if db_user.master is not None:
        await send_status_home(callback.message, db_user, lang, is_admin)
        return
    await callback.message.answer(
        t(lang, "master_intro"), reply_markup=master_start_kb(lang)
    )
