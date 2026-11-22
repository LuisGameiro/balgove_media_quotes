# 16 — Topic, language, translation and AI-judge plan

This plan consolidates the decisions discussed after the source topic study. It covers four linked
workstreams: article topic validation, quote language classification, opt-in English translation,
and an AI judge that can replace operational human review when explicitly enabled.

The rights gate remains first. No route, model or flag can enable an unlicensed source, waive the
licence-scope check, or turn a derivative translation into an implied right.

---

## 1. Current gaps

| Area | Today | Gap |
|---|---|---|
| Article topic | Live diagnostic uses a keyword gate; `mq run` does not check topic before saving | Off-topic articles with valid quotes would be persisted |
| Quote language | `sources.csv` has source-level `language` / `requires_translation`; model has `source_language` | No quote-level language detection or confidence |
| Translation | `quote_text_en` exists in schema, fixtures and digest renderer; it is hand-filled in fixtures | No automatic translation step; no translation provenance or faithfulness check |
| Human review | `mq review --action decide` requires a reviewer; gold labels are human-authored; topic uncertainty has no reviewer | No replaceable reviewer interface; `judge_kappa` is a deterministic stand-in |
| Evaluation | Gold set has 70 articles, all `relevant=true` | No false-positive or language/translation metrics |

---

## 2. Principles

1. **Deterministic first.** Topic, span, attribution, word-limit, rights and date gates stay
   deterministic. An AI judge may resolve explicitly uncertain cases only when enabled and recorded.
2. **Rights first.** Translation is a derivative use. It requires the source licence to cover it;
   otherwise `translation_rights_confirmed=false` and translation never runs.
3. **Original text is immutable.** `quote_text_original` and `content_hash` never change because of
   translation. `quote_text_en` is an additional field with its own provenance hash.
4. **Fail closed.** Unknown rule id, unsupported language, low-confidence language detection, judge
   refusal, malformed model output or missing licence scope produce escalation or a coverage gap —
   never a guessed publication.
5. **One reviewer interface.** Every review step uses the same decision contract, so switching from
   human to AI reviewer is one environment-controlled policy, not scattered conditionals.
6. **Explainable.** Each decision records actor/model, prompt version, model snapshot, rationale,
   confidence, input hash and output hash.

---

## 3. Configuration to add

| File | Purpose | Key fields |
|---|---|---|
| `data/source_topic_routes.csv` | Approved discovery route per source | `source_id`, `route_id`, `route_type`, `discovery_url`, `topic_rule_id`, `priority`, `fallback_route_id`, `approved`, `license_reference`, `translation_rights_confirmed`, `notes` |
| `data/topic_rules.json` | Versioned multilingual relevance rules | `rule_id`, `version`, `locales`, `anchors`, `supporting_terms`, `exclusions`, `weights`, `accept_threshold`, `uncertain_band` |
| `data/topic_rules.schema.json` | Frozen schema for rule files | Types, allowed fields, term constraints |
| `.env` / `Settings` | Runtime switches | `TRANSLATION_MODE`, `AI_JUDGE_MODE`, `AI_JUDGE_MODEL_SNAPSHOT`, `JUDGE_PROMPT_VERSION`, existing OpenRouter settings |

### Runtime switches

| Variable | Values | Meaning |
|---|---|---|
| `TRANSLATION_MODE` | `off` (default), `detect_only`, `translate` | `off` still classifies language for audit; `translate` also generates `quote_text_en` |
| `AI_JUDGE_MODE` | `off` (default), `shadow`, `advisory`, `decision` | Controls whether the judge logs, recommends, queues or decides |
| `AI_JUDGE_MODEL_SNAPSHOT` | pinned model id | Required when mode is not `off`; no `latest` |
| `JUDGE_PROMPT_VERSION` | semantic version | Stored on every judge decision |

A CLI `--translate` override may be added for ad-hoc manual validation, but normal runs must use the
environment so a report can be reproduced. The effective mode belongs in the version manifest.

---

## 4. Workstream A — article topic gate

### Flow

```text
parse article metadata + cleaned body
  -> evaluate_topic(rule, article)
       accepted  -> extract quotes -> verify -> persist
       uncertain -> AI judge if enabled, else review queue -> no publish until decided
       rejected  -> coverage gap + audit event -> no publish
```

### Implementation

- Add `src/media_quotes/relevance.py` with pure `evaluate_topic()`; no network, no DB, no LLM.
- Call it in `src/media_quotes/cli/run.py::_process_source` immediately after
  `adapter.parse_metadata(raw)`, before any `QuoteCandidate` is built.
- Add `topic_status`, `topic_rule_version`, `topic_score` and `matched_terms` to quote records,
  coverage rows and audit events.
- Add a defence-in-depth persistence guard: quote rows require `topic_status="accepted"`.
- `mq validate` / `mq doctor` check that every approved route references an existing rule, every
  fallback route is approved, and every enabled source has at least one usable route.
- Upgrade `mq eval`: count non-relevant articles too, and publish precision, recall and false
  positive rate. The existing 70 all-relevant gold articles cannot measure false positives.

### Acceptance

- Off-topic fixture produces zero quote records and zero DB rows.
- Relevant-but-uncertain article is not published without a recorded decision.
- Replaying the same date with the same rule version yields the same decisions.

---

## 5. Workstream B — language classification and translation

### Language detection

Detection is always on and deterministic; translation is optional.

| Step | Rule |
|---|---|
| Prior | Use source-level `language` and `requires_translation` as a hint only |
| Detection | Unicode script plus locale stopwords/diacritics over the **quote text**, not the whole article |
| Supported codes | ISO 639-1 two-letter codes already allowed by `ExportQuoteRecord.source_language` (`uk`, `ru`, `en`, `fr`, `de`, `zh`, …) |
| Confidence | Store score; below threshold means `unknown`, never a guessed translation |
| Mixed language | If article language differs from quote language, quote language wins for translation |

Add `src/media_quotes/language.py` with `detect_language(text) -> LanguageDecision`.

### Translation

| Step | Rule |
|---|---|
| Gate | `TRANSLATION_MODE=translate` **and** source route `translation_rights_confirmed=true` |
| Provider | Reuse the pinned OpenAI-compatible client and OpenRouter boundary in `llm_live.py` / `llm_openrouter.py` |
| Output | Strict JSON: `{"translation": "...", "source_language": "...", "confidence": 0.0..1.0}` |
| Prompt | Pinned `TRANSLATION_PROMPT_VERSION`; no `latest`; quote text treated as untrusted data |
| Cache | SHA-256 of `(quote_text_original, source_lang, target_lang, model_snapshot, prompt_version)` |
| Determinism | Temperature 0, max tokens, retries, backoff, refusal/incomplete => no translation |
| Validation | Non-empty, no URLs/emails, no instruction-override patterns, sane length ratio, no added facts |
| Storage | `quote_text_en`, `quote_language`, `language_confidence`, `translation_model_snapshot`, `translation_prompt_version`, `translation_hash`, `translation_status` |
| Rendering | `src/media_quotes/digest.py` already shows translation when `quote_text_en` exists; switch its condition from `source_language != "en"` to detected `quote_language != "en"` |
| Integrity | `content_hash` stays based on original fields; restore drill semantics are unchanged |

Schema changes go through the normal frozen-schema process: update `data/quotes.schema.json`,
snapshot tests and the version manifest together. Database changes use a new Alembic migration.

### Optional flag behaviour

```dotenv
TRANSLATION_MODE=detect_only   # classify, store confidence, no LLM call
TRANSLATION_MODE=translate     # classify + generate translation where rights permit
```

If `TRANSLATION_MODE=translate` but the provider is `local`, fail configuration with a clear
message; do not silently emit machine translations from a template stand-in.

---

## 6. Workstream C — AI judge for review steps

### Is this sensible?

Yes for operational triage and consistency checks. No for legal accountability. The judge can
replace the *reviewer actor* for defined tasks, but it cannot approve a licence, create rights, or
override deterministic gates.

| Human step today | AI-judge behaviour when enabled | Hard boundary |
|---|---|---|
| Obsolescence `review --action decide` | Judge proposes `confirmed` / `rejected` / `escalated`, stores rationale and confidence | `obsoletion_allowed()` remains deterministic; low confidence escalates |
| Topic uncertain triage | Judge may decide the uncertain band in `decision` mode | It cannot promote an article the deterministic rule explicitly rejected; changing this needs a recorded decision because the brief requires deterministic article detection |
| Quote relevance / substantialness | Advisory pre-review before the deterministic verifier | Span, attribution, word limit and junk checks remain final |
| Gold-set labelling | Judge can pre-label and produce a real kappa against human labels | Human labels remain ground truth; never auto-rewrite the gold set |
| Translation quality | Judge scores faithfulness and completeness | Structural validation stays deterministic; bad output is dropped |
| Source admission / wire-copy sample | Judge flags likely relayed copy for review | Counsel/human signs rights; no auto-enable |
| Rights approval | Not delegated | Remains an authorised human decision |

### Unified judge contract

```python
JudgeDecision(
    task,
    input_hash,
    verdict,
    confidence,
    rationale,
    model_snapshot,
    prompt_version,
    schema_hash,
    actor,
    created_at,
    escalation_reason,
)
```

- `actor` is `human:<name>` or `openrouter:<model>@<prompt_version>`.
- Use the same `DecisionIn` provenance record and `llm_decisions` table already used by inference.
- On refusal, incomplete JSON, parse failure or confidence below threshold, the judge escalates.
- `AI_JUDGE_MODE=shadow` logs decisions without changing state; `advisory` fills the queue;
  `decision` writes verdicts for enabled tasks only.
- Every mode records cost and latency against a budget guard.

### Optional flag behaviour

```dotenv
AI_JUDGE_MODE=decision
AI_JUDGE_MODEL_SNAPSHOT=openai/gpt-4o-mini-2024-07-18
JUDGE_PROMPT_VERSION=judge-v1
LLM_PROVIDER=openrouter
```

The provider remains environment-selected with no CLI flag, matching the existing OpenRouter design.
If `AI_JUDGE_MODE != off` and the model snapshot or OpenRouter key is missing, startup fails.

---

## 7. Test strategy — every source and every section

The earlier plan checked correctness only where fixtures already existed. Today only 6 of 28
sources have fixtures; 22 have none. The test matrix below closes that gap without contacting any
rights-blocked source.

### 7.1 Four layers

| Layer | Applies to | What it checks | Network |
|---|---|---|---|
| Config conformance | all 28 sources | Registry row, approved routes, rule ids, fallback chains, translation-rights flag, category taxonomy | none |
| Gate enforcement | all 28 sources | An unapproved fetch probe is refused for every source, approved or not | none; `example.invalid` only |
| Fixture pipeline | all 28 sources | Topic decision, extraction, verification, language detection, category assignment, digest grouping | none |
| Live route probe | approved sources only | Feed/API reachability and parse health on an approved route | opt-in, rights permitting |

A blocked or unapproved source must return `blocked_not_probed` in layer 4. If a live request is
attempted for one, the test matrix fails.

### 7.2 All-source matrix

- Add clearly labelled synthetic fixtures for every `source_id` in `data/sources.csv`. Use
  `.invalid` URLs, “SYNTHETIC FIXTURE” notices and invented speakers; never scrape or store real
  article bodies from blocked outlets.
- Give each fixture set one article per canonical section from `data/sections.csv`, so all six
  categories are exercised for every source.
- Extend `validate_single_source()` / `validate_all_sources()` in `scripts/test_scraper.py` to report
  `topic_gate`, `language_detection`, `categories`, `translation_readiness` and `gate_enforcement`
  per source.
- `validate_all_sources()` exits non-zero on any config, gate, fixture or category failure.
- Add `tests/unit/test_all_sources_matrix.py`: parametrise over all registry rows and assert the
  same contract for approved, pending, blocked and state-controlled sources.
- Source-tier and `source_type` checks are included: `claim_only` must survive extraction and be
  rendered as an attributed claim for state-controlled rows.

### 7.3 Category tests

“Categories” means the canonical report sections in `data/sections.csv`. Add these cases for every
source:

| Case | Input | Expected result |
|---|---|---|
| Correct category | synthetic quote labelled `peace_initiatives` | stored and rendered under Peace Initiatives |
| No leakage | two quotes with different section labels | each appears only in its own section |
| Full coverage | one fixture per canonical section | source × section matrix has no empty cells |
| Wrong label | label says `sanctions` but quote text is clearly `resources` | deterministic section check records mismatch, not silent acceptance |
| Off-topic wins | off-topic article with a valid section label | rejected by the topic gate; not persisted |
| Language pair | Ukrainian and Russian fixtures with translations | original verbatim, `quote_text_en` separate, language code correct |
| Claim-only | state-controlled fixture | rendered as perspective/claim, never as established fact |

Add a `source × section` coverage report to `mq eval` and audit artifacts, and show gaps in the
dashboard. Golden labels must include at least one non-relevant article and one deliberately wrong
section label per source family.

### 7.4 Exit criteria

- 28/28 config and gate-enforcement checks pass.
- 28/28 fixture pipelines replay without contacting a network host.
- Every source has a fixture for each canonical section.
- Blocked sources record zero live requests.
- Section accuracy stays at or above the existing threshold, and per-source category coverage has
  no missing cells.

## 8. Evaluation before enabling

| Metric | Why |
|---|---|
| Topic precision / recall / false positive rate | Proves off-topic articles are rejected and relevant ones kept |
| Language detection accuracy by locale | Catches Ukrainian/Russian misclassification |
| Translation hallucination rate | No added names, dates, numbers or claims |
| Translation faithfulness score | Meaning preserved against human reference translations |
| Judge agreement (kappa) per task | Replaces the current stand-in with measured agreement |
| Judge escalation rate | Detects overconfidence or broad uncertainty |
| Cost / latency per decision | Guards API spend and daily runtime |

Add human-labelled negative articles, mixed-language quotes and human reference translations to the
next gold-set version. Do not tune rules or prompts on the test split.

---

## 9. Rollout phases

1. **Shadow foundation** — config schemas, pure evaluators, unit tests, no enabled flags.
2. **Topic gate** — reject/uncertain flow, coverage gaps, precision/recall eval.
3. **Language + translation** — detection always on; translation on one rights-cleared non-English source in shadow mode.
4. **AI judge advisory** — judge logs recommendations; compare against human decisions.
5. **AI judge decision** — only for tasks whose deterministic boundary is unchanged; requires recorded approval.
6. **Canary** — Guardian first, then one Ukrainian source; monitor drift and false positives daily.

Blocked outlets remain blocked throughout. No phase enables Reuters/BBC or state-controlled outlets.

---

## 10. Files to change

| Area | Files |
|---|---|
| Config | `src/media_quotes/config.py`, `.env.example` |
| New logic | `src/media_quotes/relevance.py`, `language.py`, `translation.py`, `judge.py` |
| Pipeline | `src/media_quotes/cli/run.py`, `src/media_quotes/pipeline.py` |
| Models/schema | `src/media_quotes/models.py`, `data/quotes.schema.json`, `migrations/versions/0004_*.py` |
| Evaluation | `src/media_quotes/evaluation.py`, `src/media_quotes/cli/eval.py`, `data/gold_set/` |
| Queue | `src/media_quotes/risk_controls/obsolescence.py`, `src/media_quotes/cli/maintenance.py` |
| Orchestration | `src/media_quotes/orchestration.py` (asset checks for topic gate and judge provenance) |
| Test matrix | `scripts/test_scraper.py`, `tests/unit/test_all_sources_matrix.py`, `data/fixtures/` for all 28 source ids |
| Docs | `docs/14-command-reference.md`, `docs/15-source-topic-study.md`, `docs/README.md` |

---

## 11. Decisions to adopt before implementation

- Approve that quote-level language detection is always on but translation is off by default.
- Confirm which licences permit derivative translations; otherwise the field stays empty.
- Decide whether the AI judge may decide topic uncertainty. The current brief requires deterministic
  article detection, so this must be an explicit recorded decision, not an implementation detail.
- Agree the gold-set expansion and acceptance thresholds before enabling any judge in `decision` mode.

Implementation should proceed in the phase order above; each phase ends with the full quality gate
and a measured eval report.
