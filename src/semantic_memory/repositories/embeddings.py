"""Persistence for optional derived embeddings."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from semantic_memory.models.embeddings import Embedding
from semantic_memory.models.enums import EmbeddingObjectType


class EmbeddingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(
        self,
        *,
        object_type: EmbeddingObjectType | str,
        object_id: uuid.UUID,
        model_key: str,
    ) -> Embedding | None:
        return self._session.scalar(
            select(Embedding).where(
                Embedding.object_type == str(object_type),
                Embedding.object_id == object_id,
                Embedding.model_key == model_key,
            )
        )

    def upsert(
        self,
        *,
        object_type: EmbeddingObjectType | str,
        object_id: uuid.UUID,
        model_key: str,
        dimensions: int,
        vector: list[float],
        content_hash: str,
        created_by_actor_id: uuid.UUID | None = None,
    ) -> Embedding:
        existing = self.get(
            object_type=object_type, object_id=object_id, model_key=model_key
        )
        if existing is not None:
            existing.dimensions = dimensions
            existing.vector = list(vector)
            existing.content_hash = content_hash
            self._session.flush()
            return existing
        row = Embedding(
            id=uuid.uuid4(),
            object_type=str(object_type),
            object_id=object_id,
            model_key=model_key,
            dimensions=dimensions,
            vector=list(vector),
            content_hash=content_hash,
            created_by_actor_id=created_by_actor_id,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def list_by_type(
        self, *, object_type: EmbeddingObjectType | str, model_key: str
    ) -> list[Embedding]:
        return list(
            self._session.scalars(
                select(Embedding).where(
                    Embedding.object_type == str(object_type),
                    Embedding.model_key == model_key,
                )
            ).all()
        )

    def delete_for_object(
        self,
        *,
        object_type: EmbeddingObjectType | str,
        object_id: uuid.UUID,
        model_key: str | None = None,
    ) -> int:
        stmt = delete(Embedding).where(
            Embedding.object_type == str(object_type),
            Embedding.object_id == object_id,
        )
        if model_key is not None:
            stmt = stmt.where(Embedding.model_key == model_key)
        result = self._session.execute(stmt)
        self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    def delete_all(self, *, model_key: str | None = None) -> int:
        stmt = delete(Embedding)
        if model_key is not None:
            stmt = stmt.where(Embedding.model_key == model_key)
        result = self._session.execute(stmt)
        self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    def count(self) -> int:
        from sqlalchemy import func

        return int(self._session.scalar(select(func.count()).select_from(Embedding)) or 0)
