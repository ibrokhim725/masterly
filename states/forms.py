from aiogram.fsm.state import State, StatesGroup


class MasterForm(StatesGroup):
    full_name = State()
    phone = State()
    avatar = State()
    experience = State()
    area = State()
    about = State()
    services = State()
    prices = State()
    card_number = State()
    card_holder = State()
    card_bank = State()


class OrderForm(StatesGroup):
    description = State()
    photo = State()
    date = State()
    time = State()
    address = State()
    location = State()
    comment = State()
    confirm = State()


class ReviewForm(StatesGroup):
    comment = State()


class WithdrawForm(StatesGroup):
    amount = State()
    card_number = State()
    card_holder = State()
    card_bank = State()


class ComplaintForm(StatesGroup):
    text = State()


class AdminForm(StatesGroup):
    block_reason = State()
    master_reject_reason = State()
    service_name_uz = State()
    service_name_ru = State()
    service_price = State()
    broadcast_text = State()
    commission = State()
    min_withdrawal = State()
    withdrawal_reject_reason = State()
    withdrawal_txn = State()
    complaint_resolution = State()
    resolve_note = State()
