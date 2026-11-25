"""Validation for modular file-per-topic and file-per-source JSON registries."""

from __future__ import annotations

import json
from pathlib import Path

from ..enums import APPROVED_RIGHTS, AVAILABILITY_STATUSES
from ..source_configs import SourceConfig, load_source_config_file
from ..source_topics import load_source_topic_routes
from ..topics import TopicPack, load_topic_pack_file

__all__ = ["check_source_configs", "check_topic_packs", "check_topic_rule_fallback"]


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


def check_topic_rule_fallback(topics_dir: Path, fallback_path: Path, errors: list[str]) -> None:
    """The generated fallback rule file must match its authoritative topic pack."""
    if not fallback_path.is_file():
        return
    try:
        fallback = json.loads(fallback_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{fallback_path.name}: {exc}")
        return
    rule_id = str(fallback.get("rule_id") or "")
    for path in sorted(topics_dir.glob("*.json")):
        if path.name.endswith(".schema.json"):
            continue
        try:
            pack = load_topic_pack_file(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if pack.relevance.rule_id == rule_id:
            if pack.relevance.to_rule_dict() != fallback:
                errors.append(
                    f"{fallback_path.name}: differs from topics/{path.name} relevance; "
                    "regenerate the fallback from the topic pack"
                )
            return
    errors.append(f"{fallback_path.name}: no topic pack declares rule_id {rule_id!r}")


def check_source_configs(
    sources_dir: Path,
    csv_by_id: dict[str, dict[str, str]],
    topic_packs: dict[str, TopicPack],
    errors: list[str],
    routes_csv: Path | None = None,
) -> dict[str, SourceConfig]:
    """Validate JSON source configs and their registry/route CSV parity."""
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
        if str(cfg.availability_status) not in AVAILABILITY_STATUSES:
            errors.append(f"{where}: unknown availability_status {cfg.availability_status!r}")
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
            json_row = cfg.to_csv_row()
            for field, json_val in json_row.items():
                csv_val = csv_row.get(field, "")
                if json_val != csv_val:
                    errors.append(
                        f"sources/{sid}.json: {field}={json_val!r} differs from "
                        f"sources.csv ({csv_val!r}); run `mq sources --action sync-csv`"
                    )

    if routes_csv is not None:
        try:
            csv_routes = load_source_topic_routes(routes_csv)
        except (OSError, ValueError) as exc:
            errors.append(f"source_topic_routes.csv: {exc}")
        else:
            csv_by_key = {
                (route.source_id, route.route_id): route
                for routes in csv_routes.values()
                for route in routes
            }
            json_by_key = {
                (cfg.source_id, route.route_id): route.to_source_topic_route(cfg.source_id)
                for cfg in configs.values()
                for route in cfg.routes
            }
            for key in sorted(json_by_key.keys() - csv_by_key.keys()):
                errors.append(f"sources/{key[0]}.json: route {key[1]!r} missing from routes CSV")
            for key in sorted(csv_by_key.keys() - json_by_key.keys()):
                errors.append(f"source_topic_routes.csv: route {key!r} missing from source JSON")
            comparable = (
                "discovery_url",
                "topic_rule_id",
                "route_type",
                "priority",
                "fallback_route_id",
                "approved",
                "license_reference",
                "translation_rights_confirmed",
                "notes",
            )
            for key in sorted(json_by_key.keys() & csv_by_key.keys()):
                json_route = json_by_key[key]
                csv_route = csv_by_key[key]
                for field in comparable:
                    json_val = getattr(json_route, field)
                    csv_val = getattr(csv_route, field)
                    if json_val != csv_val:
                        errors.append(
                            f"sources/{key[0]}.json: route {key[1]!r} {field}={json_val!r} "
                            f"differs from source_topic_routes.csv ({csv_val!r}); "
                            "run `mq sources --action sync-csv`"
                        )
    return configs
