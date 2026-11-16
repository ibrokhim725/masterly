from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import settings

TWO_PLACES = Decimal("0.01")
_DEFAULT_TZ = timezone(timedelta(hours=5))


def _tz():
    try:
        return ZoneInfo(settings.TIMEZONE)
    except ZoneInfoNotFoundError:
        return _DEFAULT_TZ


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def local_now() -> datetime:
    return datetime.now(_tz())


def to_local(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_tz())


def make_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def quantize(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def format_money(value: Decimal | int | float | str | None, lang: str = "uz") -> str:
    if value is None:
        value = 0
    amount = Decimal(str(value))
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount == amount.to_integral_value():
        rendered = f"{int(amount):,}".replace(",", " ")
    else:
        rendered = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    suffix = "so'm" if lang == "uz" else "сум"
    return f"{sign}{rendered} {suffix}"


def format_price_type(price_type: str, lang: str) -> str:
    from utils.i18n import t

    if price_type == "fixed":
        return t(lang, "price_type_fixed")
    if price_type == "inspection":
        return t(lang, "price_type_inspection")
    return t(lang, "price_type_from")


def format_date(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else "—"


def format_time(value: str | None) -> str:
    return value or "—"


def format_dt(value: datetime | None, lang: str = "uz") -> str:
    local = to_local(value)
    if not local:
        return "—"
    return local.strftime("%d.%m.%Y %H:%M")


def format_phone(phone: str | None) -> str:
    if not phone:
        return "—"
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 12 and digits.startswith("998"):
        return f"+998 {digits[3:5]} {digits[5:8]} {digits[8:10]} {digits[10:12]}"
    return phone


def mask_phone(phone: str | None) -> str:
    if not phone:
        return "—"
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 7:
        return "***"
    return f"+{digits[:3]} ** *** ** {digits[-2:]}"


def today_local() -> date:
    return local_now().date()


def arrow_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def time_to_str(value: time) -> str:
    return value.strftime("%H:%M")


def combine_schedule(scheduled_date: date, scheduled_time: str) -> datetime:
    hour, minute = (int(part) for part in scheduled_time.split(":"))
    return datetime(
        scheduled_date.year,
        scheduled_date.month,
        scheduled_date.day,
        hour,
        minute,
        tzinfo=_tz(),
    )


def parse_decimal(raw: str) -> Decimal | None:
    cleaned = raw.strip().lower()
    for token in ("so'm", "сўм", "сум", "uzs", " ", "\u00a0", "'", "`"):
        cleaned = cleaned.replace(token, "")
    cleaned = cleaned.replace(",", ".")
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if value <= 0 or value > Decimal("999999999"):
        return None
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def human_status(status: str, lang: str) -> str:
    from utils.i18n import t

    return t(lang, f"order_status_{status}")


def percent_of(amount: Decimal, percent: Decimal) -> Decimal:
    return quantize(amount * percent / Decimal("100"))


def split_safe(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit:
            if current:
                chunks.append(current)
            current = line
            while len(current) > limit:
                chunks.append(current[:limit])
                current = current[limit:]
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks
