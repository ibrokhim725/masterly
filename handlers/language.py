from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User
from handlers.start import menu_for, send_status_home
from keyboards.callbacks import LangCB
from keyboards.inline import intro_kb, language_kb
from utils.i18n import t

router = Router(name="language")


@router.callback_query(LangCB.filter())
async def language_callback(
    callback: CallbackQuery,
    callback_data: LangCB,
    session: AsyncSession,
    db_user: User,
    lang: str,
    _,
    is_admin: bool,
) -> None:
    if callback_data.code == "show":
        if callback.message is not None:
            await callback.message.answer(t(lang, "choose_language"), reply_markup=language_kb())
        await callback.answer()
        return

    code = callback_data.code
    if code not in {"uz", "ru"}:
        await callback.answer(t(lang, "error_generic"), show_alert=True)
        return

    db_user.language = code
    await session.flush()
    new_lang = code
    await callback.answer(t(new_lang, "language_changed"))

    if callback.message is None:
        return
    try:
        await callback.message.edit_text(t(new_lang, "language_saved"))
    except Exception:
        pass

    if db_user.phone and db_user.role:
        await callback.message.answer(t(new_lang, "language_changed"))
        await send_status_home(callback.message, db_user, new_lang, is_admin)
    else:
        await callback.message.answer(t(new_lang, "intro"), reply_markup=intro_kb(new_lang))
