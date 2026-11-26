"""Validation for modular file-per-topic and file-per-source JSON registries."""

from __future__ import annotations

import json
from pathlib import Path

from ..enums import APPROVED_RIGHTS, PERMITTED_ACCESS
from ..source_configs import SourceConfig, load_source_config_file
from ..source_topics import load_source_topic_routes
from ..topics import TopicPack, load_topic_pack_file

__all__ = ["check_source_configs", "check_topic_packs"]


def check_topic_packs(topics_dir: Path, errors: list[str]) -> dict[str, TopicPack]:
    """Validate all JSON topic packs under ``data/topics/`` when the directory exists."""
    if not topics_dir.is_dir():
        return {}
    files = [
        path for path in sorted(topics_dir.glob("*.json")) if not path.name.endswith(".schema.json")
    ]
    if not files:
        errors.append(f"{topics_dir.name}: expected at least one topic pack JSON file")
        return {}
    packs: dict[str, TopicPack] = {}
    for path in files:
        where = f"topics/{path.name}"
        try:
            pack = load_topic_pack_file(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{where}: {exc}")
            continue
        if pack.topic_id in packs:
            errors.append(f"{where}: duplicate topic_id {pack.topic_id!r}")
            continue
        packs[pack.topic_id] = pack
    return packs


def check_source_configs(
    sources_dir: Path,
    csv_by_id: dict[str, dict[str, str]],
    topic_packs: dict[str, TopicPack],
    errors: list[str],
    routes_csv: Path | None = None,
) -> dict[str, SourceConfig]:
    """Validate all JSON source configs under ``data/sources/`` and check CSV parity."""
    if not sources_dir.is_dir():
        return {}
    files = [
        path
        for path in sorted(sources_dir.glob("*.json"))
        if not path.name.endswith(".schema.json")
    ]
    if not files:
        errors.append(f"{sources_dir.name}: expected at least one source config JSON file")
        return {}

    configs: dict[str, SourceConfig] = {}
    for path in files:
        where = f"sources/{path.name}"
        try:
            cfg = load_source_config_file(path)
            cfg.to_registry_row()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{where}: {exc}")
            continue

        if cfg.source_id in configs:
            errors.append(f"{where}: duplicate source_id {cfg.source_id!r}")
            continue
        configs[cfg.source_id] = cfg

        if cfg.enabled and str(cfg.rights_status) not in APPROVED_RIGHTS:
            errors.append(f"{where}: T0 gate - enabled sources need approved rights_status")
        if cfg.enabled and str(cfg.discovery_method) == "web":
            errors.append(f"{where}: T0 gate - enabled sources cannot use web discovery")
        if cfg.enabled and str(cfg.access_mode) not in PERMITTED_ACCESS:
            errors.append(f"{where}: T0 gate - enabled sources need a permitted access_mode")
        if cfg.tier_block and not cfg.blocker_reason.strip():
            errors.append(f"{where}: tier_block sources need a blocker_reason")
        if str(cfg.rights_status) in APPROVED_RIGHTS and not cfg.license_reference:
            errors.append(f"{where}: approved rights need a license_reference")
        if cfg.enabled and cfg.cadence_hours == 0:
            errors.append(f"{where}: enabled source needs a positive cadence_hours")
        if cfg.tier == 4 and not cfg.claim_only:
            errors.append(f"{where}: tier 4 sources must be claim_only")
        if str(cfg.source_type) == "state-controlled" and not cfg.claim_only:
            errors.append(f"{where}: state-controlled sources must be claim_only")
        if not cfg.homepage_url.startswith("https://"):
            errors.append(f"{where}: homepage_url must be an https URL")
        if cfg.feed_url and not cfg.feed_url.startswith("https://"):
            errors.append(f"{where}: feed_url must be an https URL")

        seen_routes: set[str] = set()
        for route in cfg.routes:
            if route.route_id in seen_routes:
                errors.append(f"{where}: duplicate route_id {route.route_id!r}")
            seen_routes.add(route.route_id)
            if route.discovery_url and not route.discovery_url.startswith("https://"):
                errors.append(
                    f"{where}: route {route.route_id!r} discovery_url must be an https URL"
                )
            if route.approved and not route.license_reference:
                errors.append(
                    f"{where}: approved route {route.route_id!r} needs a license_reference"
                )
            if topic_packs and route.topic_id and route.topic_id not in topic_packs:
                errors.append(
                    f"{where}: route {route.route_id!r} references unknown "
                    f"topic_id {route.topic_id!r}"
                )

    if csv_by_id:
        missing_in_json = sorted(set(csv_by_id) - set(configs))
        extra_in_json = sorted(set(configs) - set(csv_by_id))
        if missing_in_json:
            errors.append(
                "sources/: missing JSON configs for sources.csv entries "
                f"{missing_in_json} (run `mq sources --action sync-csv` after updating JSON)"
            )
        if extra_in_json:
            errors.append(
                "sources/: JSON configs not present in sources.csv "
                f"{extra_in_json} (run `mq sources --action sync-csv`)"
            )
        for sid in sorted(set(configs) & set(csv_by_id)):
            cfg = configs[sid]
            csv_row = csv_by_id[sid]
            checks = (
                ("rights_status", str(cfg.rights_status), csv_row["rights_status"]),
                ("enabled", "true" if cfg.enabled else "false", csv_row["enabled"]),
                ("tier_block", "true" if cfg.tier_block else "false", csv_row["tier_block"]),
                ("blocker_reason", cfg.blocker_reason, csv_row["blocker_reason"]),
                (
                    "availability_status",
                    str(cfg.availability_status),
                    csv_row["availability_status"],
                ),
                ("tier", str(cfg.tier), csv_row["tier"]),
                ("access_mode", str(cfg.access_mode), csv_row["access_mode"]),
                ("source_type", str(cfg.source_type), csv_row["source_type"]),
                ("claim_only", "true" if cfg.claim_only else "false", csv_row["claim_only"]),
                ("notes", cfg.notes, csv_row.get("notes", "")),
            )
            for field, json_val, csv_val in checks:
                if json_val != csv_val:
                    errors.append(
                        f"sources/{sid}.json: {field}={json_val!r} differs from "
                        f"sources.csv ({csv_val!r}); run `mq sources --action sync-csv`"
                    )
    if routes_csv is not None and routes_csv.is_file():
        csv_routes = load_source_topic_routes(routes_csv)
        json_routes = {
            source_id: {route.route_id for route in cfg.routes}
            for source_id, cfg in configs.items()
        }
        for source_id in sorted(set(csv_routes) | set(json_routes)):
            csv_ids = {route.route_id for route in csv_routes.get(source_id, [])}
            json_ids = json_routes.get(source_id, set())
            if csv_ids != json_ids:
                errors.append(
                    f"source_topic_routes.csv: {source_id} route ids "
                    f"{sorted(csv_ids)} differ from JSON {sorted(json_ids)}"
                )
    return configs
