from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from database.enums import (
    BalanceTxType,
    ComplaintStatus,
    Language,
    MasterStatus,
    OrderStatus,
    PaymentProvider,
    PaymentStatus,
    PriceType,
    UserRole,
    WithdrawalStatus,
)

BigIntPK = BigInteger().with_variant(Integer, "sqlite")

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def enum_column(enum_cls: type, length: int = 32) -> SAEnum:
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


def money_column() -> Mapped[Decimal]:
    return mapped_column(
        Numeric(14, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0"),
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), index=True)
    language: Mapped[str | None] = mapped_column(enum_column(Language, 8))
    role: Mapped[str | None] = mapped_column(enum_column(UserRole, 16))
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    blocked_reason: Mapped[str | None] = mapped_column(String(255))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped[UserProfile | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan", lazy="selectin"
    )
    master: Mapped[Master | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def display_name(self) -> str:
        return self.full_name or (f"@{self.username}" if self.username else str(self.telegram_id))

    @property
    def telegram_link(self) -> str | None:
        if self.username:
            return f"https://t.me/{self.username}"
        return f"tg://user?id={self.telegram_id}"


class UserProfile(TimestampMixin, Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    avatar_file_id: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(120))
    address: Mapped[str | None] = mapped_column(String(500))
    about: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="profile", lazy="selectin")


class Service(TimestampMixin, Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name_uz: Mapped[str] = mapped_column(String(255), nullable=False)
    name_ru: Mapped[str] = mapped_column(String(255), nullable=False)
    description_uz: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description_ru: Mapped[str] = mapped_column(Text, nullable=False, default="")
    base_price: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0.00"), server_default=text("0")
    )
    price_type: Mapped[str] = mapped_column(
        enum_column(PriceType, 16), nullable=False, default=PriceType.FROM.value
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("1"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    master_services: Mapped[list[MasterService]] = relationship(
        back_populates="service", cascade="all, delete-orphan"
    )

    def name(self, lang: str) -> str:
        return self.name_ru if lang == "ru" else self.name_uz

    def description(self, lang: str) -> str:
        return self.description_ru if lang == "ru" else self.description_uz


class Master(TimestampMixin, Base):
    __tablename__ = "masters"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    avatar_file_id: Mapped[str | None] = mapped_column(String(255))
    experience_years: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    service_area: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    about: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        enum_column(MasterStatus, 16), nullable=False, default=MasterStatus.PENDING.value, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("1"))
    rating: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, default=Decimal("0.00"), server_default=text("0")
    )
    rating_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    completed_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    commission_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[int | None] = mapped_column(BigInteger)

    user: Mapped[User] = relationship(back_populates="master", lazy="selectin")
    services: Mapped[list[MasterService]] = relationship(
        back_populates="master", cascade="all, delete-orphan", lazy="selectin"
    )
    cards: Mapped[list[MasterCard]] = relationship(
        back_populates="master", cascade="all, delete-orphan", lazy="selectin"
    )
    balance: Mapped[MasterBalance | None] = relationship(
        back_populates="master", uselist=False, cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def is_approved(self) -> bool:
        return self.status == MasterStatus.APPROVED.value and self.is_active

    def price_for(self, service: Service) -> Decimal:
        for item in self.services:
            if item.service_id == service.id and item.is_active:
                return item.price
        return service.base_price


class MasterService(Base):
    __tablename__ = "master_services"
    __table_args__ = (
        UniqueConstraint("master_id", "service_id", name="uq_master_services_master_service"),
        Index("ix_master_services_service_active", "service_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        ForeignKey("masters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("1"))

    master: Mapped[Master] = relationship(back_populates="services")
    service: Mapped[Service] = relationship(back_populates="master_services", lazy="selectin")


class MasterCard(TimestampMixin, Base):
    __tablename__ = "master_cards"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        ForeignKey("masters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    card_number_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    card_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    card_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    card_holder: Mapped[str] = mapped_column(String(120), nullable=False)
    bank_name: Mapped[str | None] = mapped_column(String(120))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("1"))

    master: Mapped[Master] = relationship(back_populates="cards")


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_client_status", "client_id", "status"),
        Index("ix_orders_master_status", "master_id", "status"),
        Index("ix_orders_status_deadline", "status", "accept_deadline"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    master_id: Mapped[int | None] = mapped_column(
        ForeignKey("masters.id", ondelete="SET NULL"), index=True
    )
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT"), nullable=False
    )
    service_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        enum_column(OrderStatus, 32), nullable=False, default=OrderStatus.PENDING_MASTER.value, index=True
    )
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    description: Mapped[str | None] = mapped_column(Text)
    photo_file_id: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[float | None] = mapped_column(Numeric(10, 7))
    scheduled_date: Mapped[date | None] = mapped_column(Date)
    scheduled_time: Mapped[str | None] = mapped_column(String(5))
    client_comment: Mapped[str | None] = mapped_column(Text)
    accept_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    work_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    dispute_reason: Mapped[str | None] = mapped_column(Text)
    commission_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    master_earning: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    escrow_released: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())

    client: Mapped[User] = relationship(foreign_keys=[client_id], lazy="selectin")
    master: Mapped[Master | None] = relationship(foreign_keys=[master_id], lazy="selectin")
    service: Mapped[Service] = relationship(lazy="selectin")
    history: Mapped[list[OrderStatusHistory]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderStatusHistory.id"
    )
    payments: Mapped[list[Payment]] = relationship(back_populates="order", cascade="all, delete-orphan")
    review: Mapped[Review | None] = relationship(
        back_populates="order", uselist=False, cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def is_paid(self) -> bool:
        return self.paid_at is not None and self.status != OrderStatus.CANCELLED.value


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(enum_column(OrderStatus, 32), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    changed_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    order: Mapped[Order] = relationship(back_populates="history")


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("provider", "provider_txn_id", name="uq_payments_provider_txn"),
        Index("ix_payments_order_status", "order_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(enum_column(PaymentProvider, 16), nullable=False)
    provider_txn_id: Mapped[str | None] = mapped_column(String(128))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="UZS", server_default="UZS")
    status: Mapped[str] = mapped_column(
        enum_column(PaymentStatus, 16), nullable=False, default=PaymentStatus.PENDING.value, index=True
    )
    pay_url: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped[Order] = relationship(back_populates="payments", lazy="selectin")
    user: Mapped[User] = relationship(lazy="selectin")


class MasterBalance(TimestampMixin, Base):
    __tablename__ = "master_balances"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        ForeignKey("masters.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    available: Mapped[Decimal] = money_column()
    pending: Mapped[Decimal] = money_column()
    locked: Mapped[Decimal] = money_column()
    total_earned: Mapped[Decimal] = money_column()
    total_withdrawn: Mapped[Decimal] = money_column()
    commission_total: Mapped[Decimal] = money_column()

    master: Mapped[Master] = relationship(back_populates="balance")


class BalanceTransaction(Base):
    __tablename__ = "balance_transactions"
    __table_args__ = (
        Index("ix_balance_transactions_master_created", "master_id", "created_at"),
        Index("ix_balance_transactions_order", "order_id"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        ForeignKey("masters.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    withdrawal_id: Mapped[int | None] = mapped_column(
        ForeignKey("withdrawal_requests.id", ondelete="SET NULL")
    )
    tx_type: Mapped[str] = mapped_column(enum_column(BalanceTxType, 32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    available_after: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    pending_after: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    locked_after: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WithdrawalRequest(TimestampMixin, Base):
    __tablename__ = "withdrawal_requests"
    __table_args__ = (Index("ix_withdrawal_requests_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(
        ForeignKey("masters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    card_id: Mapped[int | None] = mapped_column(ForeignKey("master_cards.id", ondelete="SET NULL"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    card_last4: Mapped[str | None] = mapped_column(String(4))
    card_masked: Mapped[str | None] = mapped_column(String(32))
    card_holder: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(
        enum_column(WithdrawalStatus, 16),
        nullable=False,
        default=WithdrawalStatus.PENDING.value,
        index=True,
    )
    admin_comment: Mapped[str | None] = mapped_column(String(500))
    provider_txn_id: Mapped[str | None] = mapped_column(String(128))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_by: Mapped[int | None] = mapped_column(BigInteger)

    master: Mapped[Master] = relationship(lazy="selectin")
    card: Mapped[MasterCard | None] = relationship(lazy="selectin")


class Review(TimestampMixin, Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    master_id: Mapped[int] = mapped_column(
        ForeignKey("masters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)

    order: Mapped[Order] = relationship(back_populates="review", lazy="selectin")
    master: Mapped[Master] = relationship(lazy="selectin")
    client: Mapped[User] = relationship(lazy="selectin")


class Complaint(TimestampMixin, Base):
    __tablename__ = "complaints"
    __table_args__ = (Index("ix_complaints_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        enum_column(ComplaintStatus, 16),
        nullable=False,
        default=ComplaintStatus.OPEN.value,
        index=True,
    )
    admin_comment: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[int | None] = mapped_column(BigInteger)

    order: Mapped[Order] = relationship(lazy="selectin")
    user: Mapped[User] = relationship(lazy="selectin")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str | None] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(lazy="selectin")


class AdminSetting(TimestampMixin, Base):
    __tablename__ = "admin_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
