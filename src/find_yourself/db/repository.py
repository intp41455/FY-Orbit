"""Repository helpers with optimistic concurrency control.

Every mutable row carries a ``version`` column. Writes must go through
``update_versioned`` which conditions the UPDATE on the expected version; if
another writer changed the row in between the rowcount is 0 and a
``StaleState`` error is raised. This replaces the previous global write lock.
"""

from typing import Any, Generic, TypeVar

from sqlalchemy import update
from sqlalchemy.orm import Session

from .types import utcnow

T = TypeVar("T")


class StaleState(Exception):
    """Raised when an optimistic-version update matched 0 rows."""

    def __init__(self, model: str, row_id: str, expected: int):
        self.model, self.row_id, self.expected = model, row_id, expected
        super().__init__(f"{model} {row_id} changed; expected version {expected}")


class Repository(Generic[T]):
    model: type[T]

    def get(self, s: Session, row_id: str) -> T | None:
        return s.get(self.model, row_id)

    def add(self, s: Session, instance: T) -> T:
        s.add(instance)
        s.flush()
        return instance

    def update_versioned(self, s: Session, row_id: str, expected_version: int, changes: dict[str, Any]) -> None:
        changes = {**changes, "version": expected_version + 1}
        result = s.execute(
            update(self.model)
            .where(self.model.id == row_id, self.model.version == expected_version)  # type: ignore[attr-defined]
            .values(**changes)
        )
        if result.rowcount == 0:
            raise StaleState(self.model.__tablename__, row_id, expected_version)
        s.flush()


def now_iso() -> str:
    return utcnow().isoformat()
