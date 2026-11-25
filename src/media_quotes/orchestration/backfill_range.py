"""Manual historical range backfill helpers for Dagster."""

from datetime import date
from typing import Any

import dagster as dg

from ..backfill import BackfillRequest, plan_backfill
from ..backfill import execute_backfill as execute_backfill
from ..config import Settings
from ..db.resources import DatabaseResourceHandle
from ..exceptions import ConfigError
from ..graph import Neo4jResourceHandle
from .common import DailyPipelineConfig, _compat


def _sync_backfill_graph(
    context: dg.AssetExecutionContext,
    settings: Settings,
    topic_id: str,
    neo4j: Neo4jResourceHandle,
) -> dict[str, Any]:
    """Project the completed range from Postgres to Neo4j without failing the backfill."""
    if not settings.persist_to_postgres:
        return {"neo4j_sync_status": "skipped", "neo4j_skip_reason": "persist_to_postgres is false"}
    if not settings.sync_to_neo4j:
        return {"neo4j_sync_status": "skipped", "neo4j_skip_reason": "graph sync disabled"}
    from ..db.engine import create_db_engine
    from ..graph import graph_session, init_schema, sync_postgres_to_graph

    engine = None
    try:
        engine = create_db_engine(settings)
        with graph_session(neo4j.get_driver(), settings.neo4j_database) as session:
            init_schema(session)
            stats = sync_postgres_to_graph(engine, session, topic_id=topic_id)
        return {
            "neo4j_sync_status": "ok",
            "neo4j_quotes_projected": stats.quotes_projected,
            "neo4j_articles_projected": stats.articles_projected,
            "neo4j_speakers_projected": stats.speakers_projected,
            "neo4j_last_synced_at": stats.last_synced_at,
            "neo4j_errors": stats.errors,
        }
    except Exception as exc:
        context.log.warning(f"Neo4j sync failed after backfill: {exc}")
        return {"neo4j_sync_status": "error", "neo4j_sync_error": str(exc)}
    finally:
        if engine is not None:
            engine.dispose()


def _run_backfill_range(
    context: dg.AssetExecutionContext,
    config: DailyPipelineConfig,
    settings: Settings,
    database: DatabaseResourceHandle,
    neo4j: Neo4jResourceHandle,
    partition_range: dg.PartitionKeyRange,
) -> dict[str, Any]:
    """Execute one manual historical range through the shared backfill service."""
    if config.dry_run:
        raise ConfigError("Dagster range backfills do not support dry_run; use mq backfill")
    if config.tier is not None:
        raise ConfigError("Dagster range backfills do not support a tier filter")
    if config.source_mode != "blobs":
        raise ConfigError("Dagster range backfills require source_mode='blobs'")
    if config.max_cost is None:
        raise ConfigError("max_cost is required for a Dagster range backfill")
    from ..cli.common import _MAX_BACKFILL_DAYS

    requested_days = (
        date.fromisoformat(partition_range.end) - date.fromisoformat(partition_range.start)
    ).days + 1
    if requested_days > _MAX_BACKFILL_DAYS:
        raise ConfigError(f"Dagster range backfills are capped at {_MAX_BACKFILL_DAYS} days")

    app_settings = settings
    if config.persist_to_postgres is not None:
        app_settings = settings.model_copy(
            update={"persist_to_postgres": config.persist_to_postgres}
        )
    request = BackfillRequest(
        start=date.fromisoformat(partition_range.start),
        end=date.fromisoformat(partition_range.end),
        max_cost=config.max_cost,
        execute=True,
        topic_id=config.topic_id,
        source_mode="blobs",
        fetch_archive=config.fetch_archive,
        max_archive_articles=config.max_archive_articles,
        pipeline="dagster-backfill",
    )
    plan = plan_backfill(app_settings, request)
    context.log.info(
        "Historical backfill plan: "
        f"{request.start}..{plan.effective_end}, days={plan.span_days}, "
        f"articles~{plan.estimate.articles}, calls~{plan.estimate.calls}, "
        f"cost~${plan.estimate.cost:.2f}, cache_hit_rate={plan.cache_rate:.2%}, "
        f"budget={plan.budget_note}"
    )
    result = _compat("execute_backfill")(
        app_settings,
        request,
        plan=plan,
        session_context=database.context(),
    )
    for archive in result.archives:
        context.log.info(
            f"Archive {archive.source_id}: stored={archive.stored_articles}, "
            f"discovered={archive.discovered_urls}, errors={len(archive.errors)}"
        )
        for error in archive.errors:
            context.log.warning(f"Archive {archive.source_id}: {error}")
    for partition in result.partitions:
        context.log.info(
            f"Partition {partition.report_date}: quotes={partition.quotes}, "
            f"gaps={partition.gaps}, rejected={partition.rejected}, "
            f"persisted={partition.persisted_quotes}, status={partition.status}"
        )

    graph_metadata = _sync_backfill_graph(
        context, app_settings, config.topic_id or app_settings.active_topic, neo4j
    )
    summary = {
        "run_id": f"dagster-backfill-{request.start}-{request.end}",
        "backfill_range": f"{request.start}..{plan.effective_end}",
        "start": request.start.isoformat(),
        "end": plan.effective_end.isoformat(),
        "span_days": plan.span_days,
        "source_mode": "blobs",
        "sources": plan.sources,
        "estimated_articles": plan.estimate.articles,
        "estimated_calls": plan.estimate.calls,
        "estimated_cost": plan.estimate.cost,
        "estimated_duration_hours": plan.estimate.duration_hours,
        "cache_hit_rate": plan.cache_rate,
        "budget_note": plan.budget_note,
        "archive_stored_articles": result.archive_stored_articles,
        "archive_errors": result.archive_errors,
        "partitions": len(result.partitions),
        "quotes": result.total_quotes,
        "gaps": result.total_gaps,
        "rejected": result.total_rejected,
        "persisted_quotes": result.persisted_quotes,
        **graph_metadata,
    }
    context.add_output_metadata(
        {
            "backfill_summary": dg.MetadataValue.json(summary),
            "backfill_range": f"{request.start}..{plan.effective_end}",
            "estimated_cost": float(plan.estimate.cost),
            "archive_stored_articles": result.archive_stored_articles,
            "archive_errors": result.archive_errors,
            "partition_count": len(result.partitions),
            "verified_quotes": result.total_quotes,
            "coverage_gaps": result.total_gaps,
            "persisted_quotes": result.persisted_quotes,
            **graph_metadata,
        }
    )
    return summary
