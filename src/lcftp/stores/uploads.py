"""Database-backed upload batches, frames and outcomes."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lcftp_models.upload import FrameRecord, ItemOutcome

from ..db.schema import FrameRow, OutcomeRow, UploadBatchRow

__all__ = ["SqlUploadStore"]


class SqlUploadStore:
    """Persistence for uploads."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def batch_owner(self, batch_id: UUID) -> str | None:
        """Return the observatory that owns a batch, if it exists."""
        row = await self._session.get(UploadBatchRow, batch_id)
        return row.observatory_id if row else None

    async def ensure_batch(
        self, batch_id: UUID, observatory_id: str, now: datetime
    ) -> None:
        """Create the batch if it does not exist."""
        stmt = (
            insert(UploadBatchRow)
            .values(
                batch_id=batch_id,
                observatory_id=observatory_id,
                created_at=now,
                completed_at=None,
            )
            .on_conflict_do_nothing()
        )
        await self._session.execute(stmt)

    async def frames(self, frame_ids: Sequence[UUID]) -> dict[UUID, FrameRow]:
        """Return existing frame rows by id."""
        if not frame_ids:
            return {}
        stmt = select(FrameRow).where(FrameRow.frame_id.in_(frame_ids))
        rows = (await self._session.execute(stmt)).scalars()
        return {r.frame_id: r for r in rows}

    async def frames_in_batch(self, batch_id: UUID) -> list[FrameRow]:
        """Return a batch's frames."""
        stmt = (
            select(FrameRow)
            .where(FrameRow.batch_id == batch_id)
            .order_by(FrameRow.object_key)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def upsert_pending(
        self, record: FrameRecord, batch_id: UUID, key: str
    ) -> None:
        """Record a frame awaiting upload (moving it to this batch)."""
        values = {
            "frame_id": record.frame_id,
            "batch_id": batch_id,
            "plan_id": record.plan_id,
            "item_id": record.item_id,
            "observatory_id": record.observatory_id,
            "dia_object_id": record.dia_object_id,
            "object_key": key,
            "sha256": record.checksums.sha256,
            "size_bytes": record.size_bytes,
            "record": record.model_dump(mode="json"),
            "status": "pending",
            "received_at": None,
        }
        stmt = insert(FrameRow).values(**values)
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[FrameRow.frame_id],
                set_={k: v for k, v in values.items() if k != "frame_id"},
            )
        )

    async def set_status(
        self, frame_id: UUID, status: str, received_at: datetime | None
    ) -> None:
        """Update a frame's upload status."""
        stmt = (
            update(FrameRow)
            .where(FrameRow.frame_id == frame_id)
            .values(status=status, received_at=received_at)
        )
        await self._session.execute(stmt)

    async def complete_batch(self, batch_id: UUID, now: datetime) -> None:
        """Record that a batch was verified."""
        stmt = (
            update(UploadBatchRow)
            .where(UploadBatchRow.batch_id == batch_id)
            .values(completed_at=now)
        )
        await self._session.execute(stmt)

    async def add_outcome(
        self, outcome: ItemOutcome, observatory_id: str, now: datetime
    ) -> None:
        """Store an item outcome; repeats of the same item are ignored."""
        stmt = (
            insert(OutcomeRow)
            .values(
                plan_id=outcome.plan_id,
                item_id=outcome.item_id,
                observatory_id=observatory_id,
                status=outcome.status,
                reason=outcome.reason.value,
                record=outcome.model_dump(mode="json"),
                reported_at=now,
            )
            .on_conflict_do_nothing()
        )
        await self._session.execute(stmt)
