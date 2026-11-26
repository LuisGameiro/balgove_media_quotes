# Synthetic gold sets

These files are synthetic evaluation data, not collected reporting. They measure extraction,
relevance and section assignment against known labels.

| File | Contents | Used by |
| --- | --- | --- |
| `synthetic-v1.json` | 70 labelled articles split 60/20/20 across the six war sections | Default `gold_set_version`; `mq eval --gold-set synthetic-v1` |
| `synthetic-v2.json` | 176 labelled fixtures spanning all sources and sections | Topic matrix generation and the all-sources matrix test |

Every `article_ref` points to a file under `../fixtures/`. Validation checks those references. If you
change a label, update the referenced fixture and regenerate any derived report under
`../../var/output/audit/`.

These sets cannot establish the obsolete-precision or real-source completeness targets. Those need
held-out human labels from real reporting; see [`../../docs/06-whats-left.md`](../../docs/06-whats-left.md).
