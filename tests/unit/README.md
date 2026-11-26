# Unit tests

Unit tests are grouped by subject: `adapters/`, `cli/`, `collection/`, `config/`, `db/`,
`evaluation/`, `graph/`, `ingestion/`, `llm/`, `orchestration/`, `registry/`,
`risk_controls/`, `validation/` and `verification/`.

The review-bundle tests remain at this level (`test_audit_findings_fixes.py`,
`test_planned_features.py`, `test_round3_improvements.py`, `test_src_review_batch2.py`,
`test_track_a_features.py`). Each spans several subjects; splitting them would require
changing assertions or shared helpers, so they are deliberately left intact.

`tests/conftest.py` stays at `tests/` and is shared by all groups.
