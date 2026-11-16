from __future__ import annotations

from database.models import User


def is_fully_registered(user: User | None) -> bool:
    return bool(user and user.language and user.phone and user.role)


def is_master_ready(user: User | None) -> bool:
    return bool(
        user
        and user.master
        and user.master.status == "approved"
        and user.role == "master"
    )
