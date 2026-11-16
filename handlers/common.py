from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery, Message

from database.models import User
from handlers.start import menu_for
from keyboards.reply import cancel_only
from utils.i18n import t

router = Router(name="common")


@router.callback_query()
async def unknown_callback(callback: CallbackQuery, lang: str, _) -> None:
    await callback.answer(t(lang, "unknown_callback"), show_alert=True)


@router.message()
async def unknown_message(
    message: Message,
    db_user: User,
    lang: str,
    _,
    is_admin: bool,
    state,
) -> None:
    current = await state.get_state()
    if current:
        await message.answer(t(lang, "unknown_message"), reply_markup=cancel_only(lang))
        return
    await message.answer(
        t(lang, "unknown_message"),
        reply_markup=menu_for(db_user, lang, is_admin),
    )
