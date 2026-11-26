# Synthetic gold sets

These files are **synthetic evaluation data**, not collected reporting. They are used only to
measure extraction, relevance and section-assignment behavior.

| File | Purpose | Used by |
| --- | --- | --- |
| `synthetic-v1.json` | 70 labelled articles with a 60/20/20 train/dev/test-style split across the six canonical war sections. | Default `gold_set_version` in `Settings`; `mq eval --gold-set synthetic-v1`. |
| `synthetic-v2.json` | 176 labelled fixtures spanning all sources and sections; used for the wider source×section matrix. | `scripts/generate_topic_matrix.py`, `tests/unit/test_all_sources_matrix.py`, `scripts/run_all.ps1`. |

Every `article_ref` points to a fixture under `data/fixtures/`. Validation checks those references;
never hand-edit a label without also updating the referenced fixture and regenerating any derived
evaluation report under `var/output/audit/`.

The acceptance target for obsolete precision is not established by these synthetic sets alone; it
requires held-out human labels from real reporting.
