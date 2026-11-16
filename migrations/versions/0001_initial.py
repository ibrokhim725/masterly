"""Initial marketplace schema.

This initial revision creates the schema directly from the SQLAlchemy models so
that the first deploy always matches the code base. Subsequent schema changes
must be created with ``alembic revision --autogenerate -m "change"``.

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from database.models import Base

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    op.execute(
        """
        INSERT INTO admin_settings (key, value, description)
        VALUES
            ('commission_percent', '15', 'Platform commission percentage'),
            ('min_withdrawal', '50000', 'Minimum withdrawal amount'),
            ('master_accept_timeout_minutes', '15', 'Minutes a master has to accept an order'),
            ('payment_timeout_minutes', '120', 'Minutes a client has to pay for an accepted order')
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
