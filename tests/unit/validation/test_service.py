from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from media_quotes.validation import validate_data_dir

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
    reuters["enabled"] = "true"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    errors = validate_data_dir(data)
    assert any("T0 gate" in message for message in errors)


def test_blocked_source_cannot_supply_quote(tmp_path: Path) -> None:
    data = _copy_data(tmp_path)
    record = json.loads((data / "quotes.example.jsonl").read_text(encoding="utf-8").splitlines()[0])
    record["source_id"] = "reuters"
    (data / "quotes.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    errors = validate_data_dir(data)
    assert any("blocked source" in message for message in errors)
