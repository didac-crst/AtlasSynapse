"""Shared HTTP transaction commit rules for audited mutations."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from semantic_memory.exceptions import DomainError


def run_audited_mutation[T](session: Session, operation: Callable[[], T]) -> T:
    """Commit success and rejection audits; roll back unexpected failures."""
    try:
        result = operation()
        session.commit()
        return result
    except DomainError:
        session.commit()
        raise
    except Exception:
        session.rollback()
        raise
