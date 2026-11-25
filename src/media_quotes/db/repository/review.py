"""Obsolescence review-flag persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..tables import obsolescence_flags_table, quotes_table
from .common import _inserted_count, _run_uuid

if TYPE_CHECKING:
    from ...risk_controls.models import ObsolescenceFlag


def persist_obsolescence_flags(
    session: Session,
    flags: list[ObsolescenceFlag],
    *,
    commit: bool = True,
) -> int:
    """Sync human-in-the-loop obsolescence flags to PostgreSQL when matching quotes exist."""
    if not flags:
        return 0
    canonical_ids = {flag.evidence.old_quote_id for flag in flags} | {
        flag.evidence.new_quote_id for flag in flags if flag.evidence.new_quote_id
    }
    mapping = dict(
        session.execute(
            select(quotes_table.c.canonical_quote_id, quotes_table.c.quote_id).where(
                quotes_table.c.canonical_quote_id.in_(canonical_ids)
            )
        ).all()
    )
    rows: list[dict[str, Any]] = []
    for flag in flags:
        quote_uuid = mapping.get(flag.evidence.old_quote_id)
        if not quote_uuid:
            continue
        flag_id = _run_uuid(
            f"obs-flag:{flag.evidence.old_quote_id}:{flag.evidence.decision_id}:{flag.state}"
        )
        superseded_list = (
            [mapping[flag.evidence.new_quote_id]]
            if flag.evidence.new_quote_id and flag.evidence.new_quote_id in mapping
            else []
        )
        rows.append(
            {
                "flag_id": flag_id,
                "quote_id": quote_uuid,
                "is_obsolete": flag.may_hide_quote(now=datetime.now(UTC)),
                "reason": f"[{flag.state}] {flag.reason_code or flag.evidence.rationale}",
                "superseded_by": superseded_list,
            }
        )
    if not rows:
        return 0
    statement = pg_insert(obsolescence_flags_table).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=["flag_id"],
        set_={
            "is_obsolete": statement.excluded.is_obsolete,
            "reason": statement.excluded.reason,
            "superseded_by": statement.excluded.superseded_by,
        },
    )
    result = session.execute(statement)
    if commit:
        session.commit()
    return _inserted_count(result, len(rows))
