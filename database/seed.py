from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.enums import PriceType
from database.models import AdminSetting, Service

SERVICES: list[dict] = [
    {
        "code": "faucet",
        "name_uz": "🚰 Kran buzilishi yoki suv oqishi",
        "name_ru": "🚰 Неисправность крана или протечка",
        "description_uz": "Kranlarni ta'mirlash, oqishni to'xtatish, yangisini o'rnatish.",
        "description_ru": "Ремонт кранов, устранение протечек, замена.",
        "base_price": "80000",
        "price_type": PriceType.FROM.value,
        "sort_order": 1,
    },
    {
        "code": "toilet",
        "name_uz": "🚽 Unitaz ta'mirlash",
        "name_ru": "🚽 Ремонт унитаза",
        "description_uz": "Unitaz, bak va armatura ta'mirlash yoki almashtirish.",
        "description_ru": "Ремонт или замена унитаза, бачка и арматуры.",
        "base_price": "120000",
        "price_type": PriceType.FROM.value,
        "sort_order": 2,
    },
    {
        "code": "shower_mixer",
        "name_uz": "🚿 Dush va smesitel o'rnatish",
        "name_ru": "🚿 Установка душа и смесителя",
        "description_uz": "Dush kabinasi, smesitel va dush to'plamlarini o'rnatish.",
        "description_ru": "Установка душевой кабины, смесителей и душевых наборов.",
        "base_price": "100000",
        "price_type": PriceType.FROM.value,
        "sort_order": 3,
    },
    {
        "code": "pipes_repair",
        "name_uz": "🔧 Quvurlarni ta'mirlash",
        "name_ru": "🔧 Ремонт труб",
        "description_uz": "Quvurlardagi buzilishlar, teshiklar va ulanishlarni ta'mirlash.",
        "description_ru": "Устранение повреждений, течей и неисправных соединений труб.",
        "base_price": "90000",
        "price_type": PriceType.FROM.value,
        "sort_order": 4,
    },
    {
        "code": "leak",
        "name_uz": "💧 Suv sizib chiqishini bartaraf etish",
        "name_ru": "💧 Устранение протечки воды",
        "description_uz": "Suv oqishining sababini aniqlash va bartaraf etish.",
        "description_ru": "Диагностика и устранение причины протечки.",
        "base_price": "70000",
        "price_type": PriceType.INSPECTION.value,
        "sort_order": 5,
    },
    {
        "code": "boiler",
        "name_uz": "🔥 Suv isitkich (boiler) o'rnatish",
        "name_ru": "🔥 Установка водонагревателя (бойлера)",
        "description_uz": "Boiler o'rnatish, ulash va sozlash.",
        "description_ru": "Установка, подключение и настройка бойлера.",
        "base_price": "150000",
        "price_type": PriceType.FROM.value,
        "sort_order": 6,
    },
    {
        "code": "sink",
        "name_uz": "🚿 Rakovina o'rnatish",
        "name_ru": "🚿 Установка раковины",
        "description_uz": "Rakovina, smesitel va sifon o'rnatish.",
        "description_ru": "Установка раковины, смесителя и сифона.",
        "base_price": "90000",
        "price_type": PriceType.FROM.value,
        "sort_order": 7,
    },
    {
        "code": "bath",
        "name_uz": "🛁 Vanna o'rnatish",
        "name_ru": "🛁 Установка ванны",
        "description_uz": "Vanna o'rnatish, ulash va germetiklash.",
        "description_ru": "Установка, подключение и герметизация ванны.",
        "base_price": "250000",
        "price_type": PriceType.FROM.value,
        "sort_order": 8,
    },
    {
        "code": "pipe_replace",
        "name_uz": "🔩 Quvurlarni almashtirish",
        "name_ru": "🔩 Замена труб",
        "description_uz": "Eski quvurlarni demontaj qilish va yangilarini o'rnatish.",
        "description_ru": "Демонтаж старых и установка новых труб.",
        "base_price": "120000",
        "price_type": PriceType.FROM.value,
        "sort_order": 9,
    },
    {
        "code": "other",
        "name_uz": "🛠 Boshqa santexnik xizmatlar",
        "name_ru": "🛠 Прочие сантехнические услуги",
        "description_uz": "Boshqa turdagi santexnika ishlari.",
        "description_ru": "Другие виды сантехнических работ.",
        "base_price": "60000",
        "price_type": PriceType.INSPECTION.value,
        "sort_order": 10,
    },
]

DEFAULT_SETTINGS = {
    "commission_percent": (str(settings.COMMISSION_PERCENT), "Platform commission percentage"),
    "min_withdrawal": (str(settings.MIN_WITHDRAWAL), "Minimum withdrawal amount"),
    "master_accept_timeout_minutes": (
        str(settings.MASTER_ACCEPT_TIMEOUT_MINUTES),
        "Minutes a master has to accept an order",
    ),
    "payment_timeout_minutes": (
        str(settings.PAYMENT_TIMEOUT_MINUTES),
        "Minutes a client has to pay for an accepted order",
    ),
}


async def seed_services(session: AsyncSession) -> None:
    existing = set((await session.scalars(select(Service.code))).all())
    for item in SERVICES:
        if item["code"] in existing:
            continue
        session.add(
            Service(
                code=item["code"],
                name_uz=item["name_uz"],
                name_ru=item["name_ru"],
                description_uz=item["description_uz"],
                description_ru=item["description_ru"],
                base_price=Decimal(item["base_price"]),
                price_type=item["price_type"],
                sort_order=item["sort_order"],
                is_active=True,
            )
        )


async def seed_settings(session: AsyncSession) -> None:
    existing = set((await session.scalars(select(AdminSetting.key))).all())
    for key, (value, description) in DEFAULT_SETTINGS.items():
        if key in existing:
            continue
        session.add(AdminSetting(key=key, value=value, description=description))


async def seed_all(session: AsyncSession) -> None:
    await seed_services(session)
    await seed_settings(session)
    await session.commit()
