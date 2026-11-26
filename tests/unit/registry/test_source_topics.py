"""Strict loading of approved discovery routes."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_quotes.source_topics import load_source_topic_routes

_HEADER = (
    "source_id,route_id,route_type,discovery_url,topic_rule_id,priority,"
    "fallback_route_id,approved,license_reference,translation_rights_confirmed,notes"
)


def _write(tmp_path: Path, header: str, rows: list[str]) -> Path:
    path = tmp_path / "routes.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def _routes_csv(tmp_path: Path) -> Path:
    return _write(
        tmp_path,
        _HEADER,
        [
            (
                "guardian,api,api,https://content.guardianapis.com/search,"
                "wwar,20,,true,LIC-1,false,API route"
            ),
            (
                "guardian,rss,topic_rss,https://www.theguardian.com/world/ukraine/rss,"
                "wwar,10,api,true,LIC-1,false,Topic feed"
            ),
            (
                "suspilne,tag,tag_page,"
                "https://suspilne.media/news/event/rf-ukraine-war-topnews/,"
                "wwar-uk,5,,true,LIC-9,true,Ukrainian labels"
            ),
        ],
    )


def test_routes_are_keyed_by_source_and_sorted_by_priority(tmp_path: Path) -> None:
    routes = load_source_topic_routes(_routes_csv(tmp_path))

    assert list(routes) == ["guardian", "suspilne"]
    assert [route.priority for route in routes["guardian"]] == [10, 20]
    assert isinstance(routes["guardian"][0].priority, int)
    assert routes["guardian"][0].fallback_route_id == "api"
    assert routes["guardian"][0].approved is True
    assert routes["suspilne"][0].translation_rights_confirmed is True
    assert routes["suspilne"][0].notes == "Ukrainian labels"


def test_duplicate_source_and_route_id_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _HEADER,
        [
            "guardian,rss,topic_rss,https://example.org/rss,wwar,10,,true,LIC-1,false,first",
            "guardian,rss,topic_rss,https://example.org/other,w war,11,,true,LIC-1,false,second",
        ],
    )
    with pytest.raises(ValueError, match="duplicate source_id\\+route_id"):
        load_source_topic_routes(path)


def test_missing_or_unexpected_columns_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing="):
        load_source_topic_routes(_write(tmp_path, _HEADER.replace(",approved,", ","), []))
    with pytest.raises(ValueError, match="unexpected="):
        load_source_topic_routes(_write(tmp_path, f"{_HEADER},surprise", []))


def test_invalid_numbers_and_booleans_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="priority must be an integer"):
        load_source_topic_routes(
            _write(
                tmp_path,
                _HEADER,
                ["guardian,rss,topic_rss,https://example.org,w war,high,,true,LIC-1,false,"],
            )
        )
    with pytest.raises(ValueError, match="approved must be"):
        load_source_topic_routes(
            _write(
                tmp_path,
                _HEADER,
                ["guardian,rss,topic_rss,https://example.org,w war,1,,yes,LIC-1,false,"],
            )
        )


def test_required_fields_and_empty_file_contract(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty required columns"):
        load_source_topic_routes(
            _write(
                tmp_path,
                _HEADER,
                ["guardian,,topic_rss,https://example.org,w war,1,,true,LIC-1,false,"],
            )
        )
    assert load_source_topic_routes(_write(tmp_path, _HEADER, [])) == {}
