from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from media_quotes.validation import check_topic_rule_fallback, validate_data_dir

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"


def _copy_data(tmp_path: Path) -> Path:
    target = tmp_path / "data"
    target.mkdir()
    for name in (
        "sources.csv",
        "sections.csv",
        "source_topic_routes.csv",
        "topic_rules.json",
        "quotes.schema.json",
        "quotes.example.jsonl",
    ):
        shutil.copyfile(DATA_DIR / name, target / name)
    for name in ("sources", "topics"):
        shutil.copytree(DATA_DIR / name, target / name)
    return target


def test_current_data_is_valid(tmp_path: Path) -> None:
    assert validate_data_dir(_copy_data(tmp_path)) == []


def test_topic_rule_fallback_drift_is_detected(tmp_path: Path) -> None:
    data = _copy_data(tmp_path)
    path = data / "topic_rules.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["accept_threshold"] = 99
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    errors = validate_data_dir(data)

    assert any("differs from topics/ukraine-war.json" in message for message in errors)


def _fallback_check_dir(tmp_path: Path) -> tuple[Path, Path]:
    """A real topic pack plus a fallback path that does not exist yet."""
    topics = tmp_path / "topics"
    topics.mkdir()
    shutil.copyfile(DATA_DIR / "topics" / "ukraine-war.json", topics / "ukraine-war.json")
    return topics, tmp_path / "topic_rules.json"


def test_fallback_rule_missing_file_is_tolerated(tmp_path: Path) -> None:
    topics, fallback = _fallback_check_dir(tmp_path)
    errors: list[str] = []
    check_topic_rule_fallback(topics, fallback, errors)
    assert errors == []


def test_fallback_rule_invalid_json_reports_error(tmp_path: Path) -> None:
    topics, fallback = _fallback_check_dir(tmp_path)
    fallback.write_text("{not valid json", encoding="utf-8")
    errors: list[str] = []
    check_topic_rule_fallback(topics, fallback, errors)
    assert any("topic_rules.json" in message for message in errors)


def test_fallback_rule_without_matching_rule_id_reports_error(tmp_path: Path) -> None:
    topics, fallback = _fallback_check_dir(tmp_path)
    fallback.write_text(json.dumps({"rule_id": "no-such-rule-v9"}), encoding="utf-8")
    errors: list[str] = []
    check_topic_rule_fallback(topics, fallback, errors)
    assert any("no topic pack declares rule_id" in message for message in errors)


def test_example_records_validate_against_store(tmp_path: Path) -> None:
    data = _copy_data(tmp_path)
    shutil.copyfile(data / "quotes.example.jsonl", data / "quotes.jsonl")
    assert validate_data_dir(data) == []


def test_t0_gate_blocks_enabled_unlicensed_source(tmp_path: Path) -> None:
    data = _copy_data(tmp_path)
    path = data / "sources.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0])
    reuters = next(row for row in rows if row["source_id"] == "reuters")
    reuters["rights_status"] = "blocked"
    reuters["license_reference"] = ""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    errors = validate_data_dir(data)
    assert any("T0 gate" in message for message in errors)


def test_blocked_source_cannot_supply_quote(tmp_path: Path) -> None:
    data = _copy_data(tmp_path)
    # Make the registry row unlicensed; the quote still claims it is licensed.
    path = data / "sources.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    fieldnames = list(rows[0])
    target = next(row for row in rows if row["source_id"] == "reuters")
    target["rights_status"] = "blocked"
    target["license_reference"] = ""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    record = json.loads((data / "quotes.example.jsonl").read_text(encoding="utf-8").splitlines()[0])
    record["source_id"] = "reuters"
    record["rights_status"] = "license_held"
    (data / "quotes.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    errors = validate_data_dir(data)
    assert any("blocked source" in message for message in errors)
