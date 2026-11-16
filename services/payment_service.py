from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.enums import OrderStatus, PaymentProvider, PaymentStatus
from database.models import Order, Payment
from services import balance_service, order_service
from utils.crypto import md5_hex, sign_mock_payment
from utils.formatting import quantize

logger = logging.getLogger(__name__)


class PaymentError(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


class PaymeError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def money_str(value: Decimal | int | float | str) -> str:
    return f"{quantize(value):.2f}"


def active_provider() -> PaymentProvider:
    return PaymentProvider(settings.PAYMENT_PROVIDER)


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _load_payload(payment: Payment | None) -> dict[str, Any]:
    if payment is None or not payment.raw_payload:
        return {}
    try:
        data = json.loads(payment.raw_payload)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _save_payload(payment: Payment, **values: Any) -> None:
    data = _load_payload(payment)
    data.update(values)
    payment.raw_payload = json.dumps(data, ensure_ascii=False)


def _build_pay_url(payment: Payment, order: Order) -> str:
    provider = PaymentProvider(payment.provider)
    if provider == PaymentProvider.MOCK:
        if not settings.ALLOW_MOCK_PAYMENTS:
            raise PaymentError("payment_create_error")
        signature = sign_mock_payment(payment.id, money_str(payment.amount))
        return (
            f"{settings.public_base_url}/payments/mock/pay/{payment.id}"
            f"?sig={signature}"
        )
    if provider == PaymentProvider.CLICK:
        if not (settings.CLICK_SERVICE_ID and settings.CLICK_MERCHANT_ID and settings.CLICK_SECRET_KEY):
            raise PaymentError("payment_create_error")
        amount = str(int(payment.amount)) if payment.amount == payment.amount.to_integral_value() else money_str(payment.amount)
        return (
            "https://my.click.uz/services/pay"
            f"?service_id={settings.CLICK_SERVICE_ID}"
            f"&merchant_id={settings.CLICK_MERCHANT_ID}"
            f"&amount={amount}"
            f"&transaction_param={order.public_id}"
            f"&return_url={settings.public_base_url}/payments/return"
        )
    if provider == PaymentProvider.PAYME:
        if not (settings.PAYME_MERCHANT_ID and settings.PAYME_KEY):
            raise PaymentError("payment_create_error")
        tiyin = int((quantize(order.price) * Decimal("100")).to_integral_value())
        raw = (
            f"m={settings.PAYME_MERCHANT_ID}"
            f";ac.order_id={order.public_id}"
            f";a={tiyin}"
            f";l=uz"
            f";c={settings.public_base_url}/payments/return"
        )
        encoded = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
        return f"https://checkout.paycom.uz/{encoded}"
    raise PaymentError("payment_create_error")


async def create_payment(
    session: AsyncSession,
    order: Order,
    *,
    provider: PaymentProvider | None = None,
) -> Payment:
    provider = provider or active_provider()
    if order.status != OrderStatus.AWAITING_PAYMENT.value:
        raise PaymentError("order_not_payable")
    deadline = order.payment_deadline
    if deadline is not None:
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if deadline < datetime.now(timezone.utc):
            raise PaymentError("order_not_payable")

    payment = await session.scalar(
        select(Payment)
        .where(
            Payment.order_id == order.id,
            Payment.provider == provider.value,
            Payment.status == PaymentStatus.PENDING.value,
        )
        .order_by(Payment.id.desc())
        .with_for_update()
    )
    if payment is None:
        payment = Payment(
            order_id=order.id,
            user_id=order.client_id,
            provider=provider.value,
            amount=quantize(order.price),
            status=PaymentStatus.PENDING.value,
            currency="UZS",
        )
        session.add(payment)
        await session.flush()

    payment.pay_url = _build_pay_url(payment, order)
    return payment


async def mark_paid(
    session: AsyncSession,
    payment_id: int,
    *,
    provider_txn_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Order | None:
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if payment is None:
        raise PaymentError("not_found")
    if payment.status == PaymentStatus.PAID.value:
        return None
    if payment.status in {
        PaymentStatus.CANCELLED.value,
        PaymentStatus.REFUNDED.value,
        PaymentStatus.FAILED.value,
    }:
        raise PaymentError("payment_cancelled")

    if provider_txn_id:
        duplicate = await session.scalar(
            select(Payment.id).where(
                Payment.provider == payment.provider,
                Payment.provider_txn_id == provider_txn_id,
                Payment.id != payment.id,
            )
        )
        if duplicate:
            raise PaymentError("payment_already_paid")

    order = await session.scalar(
        select(Order).where(Order.id == payment.order_id).with_for_update()
    )
    if order is None:
        raise PaymentError("order_not_found")
    if order.status != OrderStatus.AWAITING_PAYMENT.value:
        if order.paid_at is not None:
            raise PaymentError("payment_already_paid")
        raise PaymentError("order_not_payable")

    now = datetime.now(timezone.utc)
    payment.status = PaymentStatus.PAID.value
    payment.provider_txn_id = provider_txn_id
    payment.paid_at = now
    if payload is not None:
        payment.raw_payload = json.dumps(payload, ensure_ascii=False)

    order.status = OrderStatus.PAID.value
    order.paid_at = now
    order_service.add_history(
        session,
        order,
        OrderStatus.PAID.value,
        note=f"Payment received via {payment.provider}",
    )
    await balance_service.hold_escrow(session, order)
    await session.flush()
    logger.info(
        "Payment %s marked paid: order=%s amount=%s provider=%s",
        payment.id,
        order.public_id,
        payment.amount,
        payment.provider,
    )
    return order


async def cancel_pending_payment(
    session: AsyncSession, payment: Payment, reason: str = "cancelled"
) -> None:
    if payment.status == PaymentStatus.PENDING.value:
        payment.status = PaymentStatus.CANCELLED.value
        payment.cancelled_at = datetime.now(timezone.utc)
        _save_payload(payment, cancel_reason=reason)


# ---------------------------------------------------------------------------
# Click SHOP API
# ---------------------------------------------------------------------------


def _click_response(
    data: dict[str, Any],
    error: int,
    note: str,
    prepare_id: int | str = 0,
) -> dict[str, Any]:
    return {
        "click_trans_id": data.get("click_trans_id", ""),
        "merchant_trans_id": data.get("merchant_trans_id", ""),
        "merchant_prepare_id": prepare_id,
        "error": error,
        "error_note": note,
    }


def _click_sign_valid(data: dict[str, Any], secret: str) -> bool:
    parts = [
        str(data.get("click_trans_id", "")),
        str(data.get("service_id", "")),
        secret,
        str(data.get("merchant_trans_id", "")),
        str(data.get("amount", "")),
        str(data.get("action", "")),
        str(data.get("sign_time", "")),
    ]
    expected = md5_hex("".join(parts))
    return expected.lower() == str(data.get("sign_string", "")).lower()


async def handle_click(
    session: AsyncSession, data: dict[str, Any], action: str
) -> dict[str, Any]:
    if not settings.CLICK_SECRET_KEY:
        return _click_response(data, -8, "Click is not configured")
    if not _click_sign_valid(data, settings.CLICK_SECRET_KEY):
        return _click_response(data, -1, "Sign check failed")
    if settings.CLICK_SERVICE_ID and str(data.get("service_id")) != str(settings.CLICK_SERVICE_ID):
        return _click_response(data, -2, "Invalid service_id")

    try:
        amount = Decimal(str(data.get("amount")))
    except (InvalidOperation, TypeError):
        return _click_response(data, -2, "Invalid amount")

    order = await order_service.load_order_by_public_id(
        session, str(data.get("merchant_trans_id", ""))
    )
    if order is None:
        return _click_response(data, -5, "Order not found")

    if action == "0":
        if order.status != OrderStatus.AWAITING_PAYMENT.value:
            if order.paid_at:
                return _click_response(data, -4, "Already paid")
            return _click_response(data, -6, "Order is not awaiting payment")
        if quantize(amount) != quantize(order.price):
            return _click_response(data, -2, "Amount mismatch")
        try:
            payment = await create_payment(session, order, provider=PaymentProvider.CLICK)
        except PaymentError as exc:
            return _click_response(data, -8, str(exc.key))
        await session.flush()
        return _click_response(data, 0, "Success", prepare_id=payment.id)

    if action == "1":
        prepare_id = data.get("merchant_prepare_id")
        payment: Payment | None = None
        if prepare_id not in (None, "", 0, "0"):
            payment = await session.scalar(
                select(Payment).where(Payment.id == int(prepare_id)).with_for_update()
            )
        if payment is None:
            payment = await session.scalar(
                select(Payment)
                .where(
                    Payment.order_id == order.id,
                    Payment.provider == PaymentProvider.CLICK.value,
                    Payment.status == PaymentStatus.PENDING.value,
                )
                .order_by(Payment.id.desc())
                .with_for_update()
            )
        elif payment.order_id != order.id:
            return _click_response(data, -6, "Transaction not found")
        if payment is None:
            if order.paid_at:
                return _click_response(data, -4, "Already paid")
            return _click_response(data, -6, "Transaction not found")
        if payment.status == PaymentStatus.PAID.value:
            return _click_response(data, -4, "Already paid", prepare_id=payment.id)
        if payment.status != PaymentStatus.PENDING.value:
            return _click_response(data, -6, "Transaction is not pending", prepare_id=payment.id)
        if quantize(amount) != quantize(payment.amount):
            return _click_response(data, -2, "Amount mismatch", prepare_id=payment.id)

        click_error = str(data.get("error", "0"))
        if click_error not in {"0", "None", ""}:
            await cancel_pending_payment(session, payment, "click_error")
            return _click_response(data, -9, "Transaction cancelled by Click", prepare_id=payment.id)

        try:
            await mark_paid(
                session,
                payment.id,
                provider_txn_id=str(data.get("click_trans_id", "")),
                payload=data,
            )
        except PaymentError as exc:
            return _click_response(data, -4, str(exc.key), prepare_id=payment.id)
        return _click_response(data, 0, "Success", prepare_id=payment.id)

    return _click_response(data, -3, "Unknown action")


# ---------------------------------------------------------------------------
# Payme Merchant API
# ---------------------------------------------------------------------------


def _payme_error(rpc_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def _payme_result(rpc_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _dt_ms(value: datetime | None) -> int:
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


async def _payme_find_payment(
    session: AsyncSession, txn_id: str
) -> Payment | None:
    return await session.scalar(
        select(Payment)
        .where(
            Payment.provider == PaymentProvider.PAYME.value,
            Payment.provider_txn_id == txn_id,
        )
        .with_for_update()
    )


async def _payme_order_by_account(
    session: AsyncSession, account: dict[str, Any]
) -> Order | None:
    order_public_id = account.get("order_id") if isinstance(account, dict) else None
    if not order_public_id:
        return None
    order = await order_service.load_order_by_public_id(session, str(order_public_id))
    if order is None:
        return None
    return await session.scalar(select(Order).where(Order.id == order.id).with_for_update())


def _payme_state(payment: Payment) -> tuple[int, int, int, int, str | None]:
    payload = _load_payload(payment)
    state = int(payload.get("state", 1))
    if payment.status == PaymentStatus.PAID.value:
        state = 2
    elif payment.status in {PaymentStatus.CANCELLED.value, PaymentStatus.REFUNDED.value}:
        state = int(payload.get("cancel_state", -1))
    create_time = int(payload.get("create_time", _dt_ms(payment.created_at)))
    perform_time = int(payload.get("perform_time", 0))
    cancel_time = int(payload.get("cancel_time", 0))
    reason = payload.get("reason")
    return state, create_time, perform_time, cancel_time, reason


async def handle_payme(session: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    rpc_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return _payme_error(rpc_id, -32600, "Invalid params")

    try:
        if method == "CheckPerformTransaction":
            result = await _payme_check_perform(session, params)
        elif method == "CreateTransaction":
            result = await _payme_create(session, params)
        elif method == "PerformTransaction":
            result = await _payme_perform(session, params)
        elif method == "CancelTransaction":
            result = await _payme_cancel(session, params)
        elif method == "CheckTransaction":
            result = await _payme_check(session, params)
        elif method == "GetStatement":
            result = await _payme_statement(session, params)
        else:
            return _payme_error(rpc_id, -32601, "Method not found")
    except PaymeError as exc:
        return _payme_error(rpc_id, exc.code, exc.message)
    except PaymentError as exc:
        key_to_error = {
            "payment_already_paid": (-31051, "Order already paid"),
            "order_not_payable": (-31008, "Order is not payable"),
            "order_not_found": (-31050, "Order not found"),
            "payment_cancelled": (-31005, "Transaction cancelled"),
            "not_found": (-31003, "Transaction not found"),
        }
        code, message = key_to_error.get(exc.key, (-31008, "Unable to process payment"))
        return _payme_error(rpc_id, code, message)
    return _payme_result(rpc_id, result)


def _payme_validate_amount(order: Order, amount_raw: Any) -> int:
    try:
        amount = int(amount_raw)
    except (TypeError, ValueError):
        raise PaymeError(-31001, "Invalid amount")
    expected = int(quantize(order.price) * 100)
    if amount != expected:
        raise PaymeError(-31001, "Invalid amount")
    return amount


async def _payme_check_perform(session: AsyncSession, params: dict[str, Any]) -> dict[str, Any]:
    order = await _payme_order_by_account(session, params.get("account") or {})
    if order is None:
        raise PaymeError(-31050, "Order not found")
    if order.status != OrderStatus.AWAITING_PAYMENT.value:
        if order.paid_at:
            raise PaymeError(-31051, "Order already paid")
        raise PaymeError(-31008, "Order is not payable")
    _payme_validate_amount(order, params.get("amount"))
    return {"allow": True}


async def _payme_create(session: AsyncSession, params: dict[str, Any]) -> dict[str, Any]:
    txn_id = str(params.get("id", ""))
    if not txn_id:
        raise PaymeError(-31001, "Invalid transaction id")

    existing = await _payme_find_payment(session, txn_id)
    if existing is not None:
        state, create_time, _perform, _cancel, _reason = _payme_state(existing)
        return {
            "create_time": create_time,
            "transaction": str(existing.id),
            "state": state,
        }

    order = await _payme_order_by_account(session, params.get("account") or {})
    if order is None:
        raise PaymeError(-31050, "Order not found")
    _payme_validate_amount(order, params.get("amount"))
    if order.status != OrderStatus.AWAITING_PAYMENT.value:
        if order.paid_at:
            raise PaymeError(-31051, "Order already paid")
        raise PaymeError(-31008, "Order is not payable")

    paid_payment = await session.scalar(
        select(Payment.id).where(
            Payment.order_id == order.id,
            Payment.status == PaymentStatus.PAID.value,
        )
    )
    if paid_payment:
        raise PaymeError(-31051, "Order already paid")

    create_time = int(params.get("time", _now_ms()))
    payment = Payment(
        order_id=order.id,
        user_id=order.client_id,
        provider=PaymentProvider.PAYME.value,
        provider_txn_id=txn_id,
        amount=quantize(order.price),
        status=PaymentStatus.PENDING.value,
        currency="UZS",
        raw_payload=json.dumps(
            {"create_time": create_time, "state": 1}, ensure_ascii=False
        ),
    )
    session.add(payment)
    await session.flush()
    logger.info("Payme transaction created: payment=%s order=%s", payment.id, order.public_id)
    return {"create_time": create_time, "transaction": str(payment.id), "state": 1}


async def _payme_perform(session: AsyncSession, params: dict[str, Any]) -> dict[str, Any]:
    txn_id = str(params.get("id", ""))
    payment = await _payme_find_payment(session, txn_id)
    if payment is None:
        raise PaymeError(-31003, "Transaction not found")

    state, _create_time, perform_time, _cancel, _reason = _payme_state(payment)
    if state == 2:
        return {
            "perform_time": perform_time,
            "transaction": str(payment.id),
            "state": 2,
        }
    if state < 0:
        raise PaymeError(-31005, "Transaction cancelled")

    perform_ms = int(params.get("time", _now_ms()))
    try:
        await mark_paid(session, payment.id, provider_txn_id=txn_id)
    except PaymentError as exc:
        if exc.key == "payment_already_paid":
            payment.status = PaymentStatus.CANCELLED.value
            payment.cancelled_at = datetime.now(timezone.utc)
            _save_payload(
                payment,
                state=-1,
                cancel_state=-1,
                cancel_time=perform_ms,
                reason="duplicate_payment",
            )
            logger.warning(
                "Payme transaction %s rejected: order already paid by another payment",
                txn_id,
            )
            raise PaymeError(-31051, "Order already paid")
        raise
    _save_payload(payment, state=2, perform_time=perform_ms)
    logger.info("Payme transaction performed: payment=%s", payment.id)
    return {"perform_time": perform_ms, "transaction": str(payment.id), "state": 2}


async def _payme_cancel(session: AsyncSession, params: dict[str, Any]) -> dict[str, Any]:
    txn_id = str(params.get("id", ""))
    payment = await _payme_find_payment(session, txn_id)
    if payment is None:
        raise PaymeError(-31003, "Transaction not found")
    if payment.order is not None and payment.order.status == OrderStatus.COMPLETED.value:
        raise PaymeError(-31007, "Unable to cancel completed order")

    state, _create_time, _perform, cancel_time, _reason = _payme_state(payment)
    if state < 0:
        return {
            "cancel_time": cancel_time,
            "transaction": str(payment.id),
            "state": state,
        }

    now_ms = int(params.get("time", _now_ms()))
    reason_code = params.get("reason")
    cancel_state = -1
    if payment.status == PaymentStatus.PAID.value:
        order = await session.scalar(
            select(Order).where(Order.id == payment.order_id).with_for_update()
        )
        if order is not None:
            await balance_service.refund_escrow(
                session,
                order,
                reason=f"payme_cancelled:{reason_code}",
            )
            order.status = OrderStatus.CANCELLED.value
            order.cancelled_at = datetime.now(timezone.utc)
            order.cancel_reason = "payment_provider_cancelled"
            order_service.add_history(
                session,
                order,
                OrderStatus.CANCELLED.value,
                note="Payme cancelled the transaction",
            )
        payment.status = PaymentStatus.REFUNDED.value
        cancel_state = -2
    else:
        payment.status = PaymentStatus.CANCELLED.value
        payment.cancelled_at = datetime.now(timezone.utc)

    _save_payload(
        payment,
        state=cancel_state,
        cancel_state=cancel_state,
        cancel_time=now_ms,
        reason=reason_code,
    )
    logger.info("Payme transaction cancelled: payment=%s state=%s", payment.id, cancel_state)
    return {
        "cancel_time": now_ms,
        "transaction": str(payment.id),
        "state": cancel_state,
    }


async def _payme_check(session: AsyncSession, params: dict[str, Any]) -> dict[str, Any]:
    txn_id = str(params.get("id", ""))
    payment = await _payme_find_payment(session, txn_id)
    if payment is None:
        raise PaymeError(-31003, "Transaction not found")
    state, create_time, perform_time, cancel_time, reason = _payme_state(payment)
    return {
        "create_time": create_time,
        "perform_time": perform_time,
        "cancel_time": cancel_time,
        "transaction": str(payment.id),
        "state": state,
        "reason": reason,
    }


async def _payme_statement(session: AsyncSession, params: dict[str, Any]) -> dict[str, Any]:
    try:
        time_from = int(params.get("from", 0))
        time_to = int(params.get("to", _now_ms()))
    except (TypeError, ValueError):
        raise PaymeError(-31001, "Invalid time range")

    from_dt = datetime.fromtimestamp(time_from / 1000, tz=timezone.utc)
    to_dt = datetime.fromtimestamp(time_to / 1000, tz=timezone.utc)
    payments = (
        await session.scalars(
            select(Payment).where(
                Payment.provider == PaymentProvider.PAYME.value,
                Payment.created_at >= from_dt,
                Payment.created_at <= to_dt,
            )
        )
    ).all()
    transactions = []
    for payment in payments:
        state, create_time, perform_time, cancel_time, reason = _payme_state(payment)
        order = await session.scalar(select(Order).where(Order.id == payment.order_id))
        transactions.append(
            {
                "id": payment.provider_txn_id,
                "time": create_time,
                "amount": int(quantize(payment.amount) * 100),
                "account": {"order_id": order.public_id if order else ""},
                "create_time": create_time,
                "perform_time": perform_time,
                "cancel_time": cancel_time,
                "transaction": str(payment.id),
                "state": state,
                "reason": reason,
            }
        )
    return {"transactions": transactions}


async def mark_payment_failed(
    session: AsyncSession, payment_id: int, payload: dict[str, Any] | None = None
) -> None:
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if payment is None or payment.status != PaymentStatus.PENDING.value:
        return
    payment.status = PaymentStatus.FAILED.value
    if payload is not None:
        payment.raw_payload = json.dumps(payload, ensure_ascii=False)


def payment_timeout_deadline(minutes: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)
