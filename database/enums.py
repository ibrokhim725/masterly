from __future__ import annotations

from enum import Enum


class Language(str, Enum):
    UZ = "uz"
    RU = "ru"


class UserRole(str, Enum):
    CLIENT = "client"
    MASTER = "master"
    ADMIN = "admin"


class PriceType(str, Enum):
    FIXED = "fixed"
    FROM = "from"
    INSPECTION = "inspection"


class MasterStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class OrderStatus(str, Enum):
    PENDING_MASTER = "pending_master"
    REJECTED_BY_MASTER = "rejected_by_master"
    EXPIRED = "expired"
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    IN_PROGRESS = "in_progress"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    DISPUTED = "disputed"

    @property
    def is_active(self) -> bool:
        return self in {
            OrderStatus.PENDING_MASTER,
            OrderStatus.AWAITING_PAYMENT,
            OrderStatus.PAID,
            OrderStatus.IN_PROGRESS,
            OrderStatus.AWAITING_CONFIRMATION,
            OrderStatus.DISPUTED,
        }


class PaymentProvider(str, Enum):
    CLICK = "click"
    PAYME = "payme"
    MOCK = "mock"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    CANCELLED = "cancelled"
    FAILED = "failed"
    REFUNDED = "refunded"


class WithdrawalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    PAID = "paid"
    REJECTED = "rejected"


class BalanceTxType(str, Enum):
    ESCROW_HOLD = "escrow_hold"
    RELEASE = "release"
    COMMISSION = "commission"
    WITHDRAWAL_HOLD = "withdrawal_hold"
    WITHDRAWAL_PAID = "withdrawal_paid"
    WITHDRAWAL_REFUND = "withdrawal_refund"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"


class ComplaintStatus(str, Enum):
    OPEN = "open"
    IN_REVIEW = "in_review"
    RESOLVED = "resolved"
    REJECTED = "rejected"
