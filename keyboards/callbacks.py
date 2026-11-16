from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class LangCB(CallbackData, prefix="lang"):
    code: str


class IntroCB(CallbackData, prefix="intro"):
    action: str = "done"


class RoleCB(CallbackData, prefix="role"):
    role: str


class ServiceCB(CallbackData, prefix="svc"):
    action: str
    service_id: int = 0
    page: int = 0


class MasterPickCB(CallbackData, prefix="mpick"):
    master_id: int


class OrderActionCB(CallbackData, prefix="ord"):
    action: str
    order_id: int


class ReassignCB(CallbackData, prefix="reas"):
    action: str
    order_id: int
    master_id: int = 0


class PaymentCB(CallbackData, prefix="pay"):
    action: str
    order_id: int


class ReviewCB(CallbackData, prefix="rev"):
    action: str
    order_id: int
    rating: int = 0


class BalanceCB(CallbackData, prefix="bal"):
    action: str


class WithdrawCB(CallbackData, prefix="wd"):
    action: str
    request_id: int = 0
    card_id: int = 0


class CardCB(CallbackData, prefix="card"):
    action: str
    card_id: int = 0


class AdminCB(CallbackData, prefix="adm"):
    section: str
    action: str = ""
    obj_id: int = 0
    page: int = 0


class MasterServiceCB(CallbackData, prefix="msvc"):
    action: str
    service_id: int = 0


class DraftCB(CallbackData, prefix="draft"):
    action: str


class PageCB(CallbackData, prefix="pg"):
    scope: str
    page: int = 0
