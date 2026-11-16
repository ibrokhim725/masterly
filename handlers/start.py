from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import User
from keyboards.inline import (
    intro_kb,
    language_kb,
    master_resubmit_kb,
    master_start_kb,
    role_kb,
)
from keyboards.reply import client_menu, master_menu
from utils.i18n import button_variants, t

router = Router(name="start")


def menu_for(user: User, lang: str, is_admin: bool):
    if user.role == "master" and user.master is not None:
        return master_menu(lang, is_admin)
    return client_menu(lang, is_admin)


async def send_status_home(
    message: Message, user: User, lang: str, is_admin: bool
) -> None:
    from utils.rendering import render_master_profile

    if user.role == "master":
        master = user.master
        if master is None:
            await message.answer(t(lang, "master_intro"), reply_markup=master_start_kb(lang))
            return
        if master.status == "pending":
            await message.answer(
                t(lang, "application_pending"),
                reply_markup=master_menu(lang, is_admin),
            )
            return
        if master.status == "rejected":
            await message.answer(
                t(lang, "application_rejected", reason=master.rejection_reason or "—"),
                reply_markup=master_resubmit_kb(lang),
            )
            return
        await message.answer(
            render_master_profile(master, lang),
            reply_markup=master_menu(lang, is_admin),
        )
        return

    await message.answer(
        t(lang, "choose_from_menu"),
        reply_markup=client_menu(lang, is_admin),
    )


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    state,
    is_admin: bool,
) -> None:
    if message.chat.type != "private":
        await message.answer(t(lang, "no_access"))
        return

    await state.clear()

    if not db_user.language:
        await message.answer(t(lang, "welcome"), reply_markup=language_kb())
        return

    if not db_user.phone:
        await message.answer(t(lang, "intro"), reply_markup=intro_kb(lang))
        return

    if not db_user.role:
        await message.answer(t(lang, "choose_role"), reply_markup=role_kb(lang))
        return

    await send_status_home(message, db_user, lang, is_admin)


def _support_ref() -> str:
    value = (settings.SUPPORT_CONTACT or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://", "tg://")):
        return value
    if value.isdigit():
        return f"tg://user?id={value}"
    return f"@{value.lstrip('@')}"


@router.message(Command("help"))
async def cmd_help(message: Message, lang: str, _, is_admin: bool, db_user: User) -> None:
    support = _support_ref()
    if db_user.role == "master":
        text = t(lang, "help_master", support=support)
    else:
        text = t(lang, "help_client", support=support)
    await message.answer(text, reply_markup=menu_for(db_user, lang, is_admin))


@router.message(Command("myid"))
async def cmd_myid(message: Message, lang: str, _) -> None:
    if message.from_user is None:
        return
    await message.answer(t(lang, "myid_text", id=message.from_user.id))


@router.message(Command("cancel"))
async def cmd_cancel(
    message: Message,
    db_user: User,
    lang: str,
    _,
    state,
    is_admin: bool,
) -> None:
    await state.clear()
    await message.answer(t(lang, "cancelled"))
    if db_user.language and db_user.phone and db_user.role:
        await send_status_home(message, db_user, lang, is_admin)
    else:
        await message.answer(
            t(lang, "choose_from_menu"),
            reply_markup=menu_for(db_user, lang, is_admin),
        )


@router.message(F.text.in_(button_variants("btn_cancel")))
async def cancel_button(
    message: Message,
    db_user: User,
    lang: str,
    _,
    state,
    is_admin: bool,
) -> None:
    await state.clear()
    await message.answer(t(lang, "cancelled"))
    if db_user.language and db_user.phone and db_user.role:
        await send_status_home(message, db_user, lang, is_admin)
    else:
        await message.answer(t(lang, "choose_from_menu"))


@router.message(F.text.in_(button_variants("btn_help") + button_variants("btn_client_help") + button_variants("btn_master_help")))
async def help_button(
    message: Message, db_user: User, lang: str, _, is_admin: bool
) -> None:
    support = _support_ref()
    if db_user.role == "master":
        text = t(lang, "help_master", support=support)
    else:
        text = t(lang, "help_client", support=support)
    await message.answer(text, reply_markup=menu_for(db_user, lang, is_admin))
