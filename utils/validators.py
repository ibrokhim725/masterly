from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from utils.formatting import combine_schedule, local_now

DATE_RE = re.compile(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$")
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
CARD_RE = re.compile(r"^\d{16,19}$")
PHONE_RE = re.compile(r"^\+998\d{9}$")


def normalize_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("998") and len(digits) == 12:
        candidate = f"+{digits}"
    elif len(digits) == 9:
        candidate = f"+998{digits}"
    elif len(digits) == 10 and digits.startswith("8"):
        candidate = f"+998{digits[1:]}"
    else:
        return None
    return candidate if PHONE_RE.match(candidate) else None


def parse_date(raw: str) -> date | None:
    match = DATE_RE.match(raw.strip())
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        value = date(year, month, day)
    except ValueError:
        return None
    today = local_now().date()
    if value < today or value > today + timedelta(days=365):
        return None
    return value


def parse_time(raw: str) -> str | None:
    match = TIME_RE.match(raw.strip())
    if not match:
        return None
    hour, minute = (int(part) for part in match.groups())
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def is_future_schedule(scheduled_date: date, scheduled_time: str) -> bool:
    try:
        value = combine_schedule(scheduled_date, scheduled_time)
    except Exception:
        return False
    return value > local_now() + timedelta(minutes=10)


def normalize_card_number(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if not CARD_RE.match(digits):
        return None
    if not luhn_valid(digits):
        return None
    return digits


def luhn_valid(number: str) -> bool:
    total = 0
    reverse = number[::-1]
    for index, char in enumerate(reverse):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def mask_card_number(number: str) -> str:
    digits = re.sub(r"\D", "", number)
    if len(digits) < 8:
        return "****"
    return f"{digits[:4]} **** **** {digits[-4:]}"


def is_valid_rating(value: int) -> bool:
    return 1 <= value <= 5


def parse_experience(raw: str) -> int | None:
    value = raw.strip()
    if not value.isdigit():
        return None
    years = int(value)
    return years if 0 <= years <= 60 else None


def parse_description(raw: str, min_len: int = 5, max_len: int = 2000) -> str | None:
    value = " ".join(raw.strip().split())
    if not (min_len <= len(value) <= max_len):
        return None
    return value


def looks_like_amount(raw: str) -> bool:
    cleaned = re.sub(r"[\s'`]", "", raw.lower())
    for token in ("so'm", "сўм", "сум", "uzs"):
        cleaned = cleaned.replace(token, "")
    cleaned = cleaned.replace(",", ".")
    try:
        Decimal(cleaned)
        return True
    except Exception:
        return False
