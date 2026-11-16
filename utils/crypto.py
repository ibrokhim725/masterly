from __future__ import annotations

import base64
import hashlib
import hmac
import logging

from cryptography.fernet import Fernet, InvalidToken

from config import settings
from utils.validators import mask_card_number

logger = logging.getLogger(__name__)


def _build_fernet() -> Fernet:
    if settings.CARD_ENCRYPTION_KEY:
        key = settings.CARD_ENCRYPTION_KEY.encode()
    else:
        digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


_fernet = _build_fernet()


def encrypt_card(number: str) -> str:
    return _fernet.encrypt(number.encode("utf-8")).decode("utf-8")


def decrypt_card(token: str) -> str | None:
    try:
        return _fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.error("Unable to decrypt stored card number")
        return None


def card_last4(number: str) -> str:
    return number[-4:]


def masked_card(number: str) -> str:
    return mask_card_number(number)


def sign_mock_payment(payment_id: int, amount: str) -> str:
    payload = f"{payment_id}:{amount}".encode("utf-8")
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_mock_signature(payment_id: int, amount: str, signature: str) -> bool:
    expected = sign_mock_payment(payment_id, amount)
    return hmac.compare_digest(expected, signature)


def md5_hex(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()
