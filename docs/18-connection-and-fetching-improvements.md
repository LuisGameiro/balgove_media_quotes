# 18 — Connection and fetching improvement plan

This is a plan derived from the implementation reference in
[17](17-connection-and-fetching-reference.md). Each item states a concrete case from the current
code, the smallest fix, the files it touches and how to verify it. It is **not** a decision
register: adopting an item still needs a row in [04](04-risks-and-open-questions.md) and a check
in [08](08-traceability.md).

**Status: all items below were implemented on 2026-11-21.** Item 1.1 was delivered as the
compatibility option (deprecated flag kept) because `scripts/manual_validate.ps1` still passes it.

The "Case" paragraphs describe the state **before** the fix. Later rounds changed the same files
again (19 commands, migrations `0001..0006`, `adapters/` package), so read those counts as
historical; [17](17-connection-and-fetching-reference.md) is the current reference.

Scope: how a source is connected, fetched and controlled. The feature catalogue in
[13](13-code-review-and-feature-roadmap.md) (visuals, repetition, analytics, review UI) is
deliberately out of scope — those consume derived JSONL and do not change fetching.

---

## 1. Tier 1 — fix misleading or broken controls first

### 1.1 `--offline` is accepted but does nothing

- **Case:** `mq run --offline --date 2026-11-19` and `mq run --date 2026-11-19` behave
  identically. The flag is only printed in verbose mode (`cli/app.py:243-244`); no code reads it.
- **Fix (delivered):** keep `--offline` as a deprecated, accepted-for-compatibility flag, change
  its help text to say every `mq` command is offline, and remove the misleading verbose banner.
  `scripts/manual_validate.ps1` still passes the flag, so removing it would break that script.
- **Files:** `src/media_quotes/cli/app.py`, `scripts/manual_validate.ps1`, `docs/02`, `docs/12`,
  `docs/14-command-reference.md`, `README.md`.
- **Verify:** `mq run --help` shows the deprecated note; no other tests change because nothing
  consumes the flag.

### 1.2 `.env.example` omits every new workstream variable

- **Case:** an operator wants translation or a judge. `.env.example` has no `TRANSLATION_MODE`,
  `AI_JUDGE_MODE`, `AI_JUDGE_MODEL_SNAPSHOT`, `ACTIVE_TOPIC`, `SOURCES_DIR`, `SOURCE_TOPICS_CSV`,
  `TOPIC_RULES_PATH`, `TOPICS_DIR`, `JUDGE_PROMPT_VERSION` or `TRANSLATION_PROMPT_VERSION`, so the
  settings that gate these features are undiscoverable; `mq doctor` would reject a half-configured
  attempt with a validation error.
- **Fix:** add a clearly commented "Topic and optional workstreams" section to `.env.example`
  with all ten variables, their defaults and the OpenRouter preconditions.
- **Files:** `.env.example`.
- **Verify:** each of the ten names appears in `.env.example`; `Copy-Item .env.example .env`
  followed by `mq doctor` still passes with defaults.

### 1.3 Operational docs contradict the code

- **Case:** `docs/14` says "17 CLI subcommands" and migrations `0001..0003`; there are now 18
  commands (including `topics`) and migrations `0004`/`0005`. The root README says per-source
  behaviour lives in a single `adapters.py` and all source rows are disabled; in fact the adapters
  are a package and 10 sources are usable. `docs/13` still maps `adapters.py` and 16 commands.
- **Fix:** update the counts, command table, migration list and adapter/source-state wording in
  those three documents. Where a document repeats the flag tables from [17], replace them with a
  link rather than a second copy that can drift again.
- **Files:** `docs/14-command-reference.md`, `README.md`, `docs/13-code-review-and-feature-roadmap.md`.
- **Verify:** `python scripts/docs_qa.py` (41/41), plus a manual pass that the command count and
  migration ids match `cli/app.py` and `migrations/versions/`.

---

## 2. Tier 2 — close real capability gaps

### 2.1 There is no supported live-fetch command in `mq`

- **Case:** 10 sources are usable and licensed, but the only way to fetch a live feed is the
  diagnostic script `scripts/test_scraper.py`. That path produces a JSON report and a cached blob
  but no audit events, no digest and no Postgres rows, so a daily live run cannot be operated or
  reviewed through the one-command interface the project promises.
- **Fix:** add `mq fetch --source-id <id> [--date ...] [--limit N] [--dry-run]`, defaulting to
  dry-run. Smallest useful version: resolve the topic route's feed URL, run the existing T0 gate,
  call `fetch_bytes` with `FetchPolicy`, store the blob with `BlobCache`, run
  `inspect_html_pipeline`, and print the same report the script prints. Do not add DB writes in the
  first slice; `mq run` already owns persistence.
- **Files:** `src/media_quotes/cli/app.py`, new `src/media_quotes/cli/fetch.py`, unit tests with
  `httpx.MockTransport`.
- **Verify:** a blocked source is refused with exit 3 and no fetch; a dry-run with an approved
  source makes zero network calls; a mocked live feed produces a report with `topic_id`.

### 2.2 Translation can never run even when configured

- **Case:** `TRANSLATION_MODE=translate` with a pinned OpenRouter model still skips every quote,
  because `_translation_for_quote` requires `translation_rights_confirmed=true` on the route and
  all 28 routes have it `false`. The operator sees `translation_status=skipped` with no explanation.
- **Fix:** do **not** flip the rights flag without legal approval. Instead add a `mq validate`
  warning when translation mode is on but no route confirms translation rights, naming the
  precondition. Keep the fail-closed behaviour.
- **Files:** `src/media_quotes/validation/modular.py` (or `cli/common.py`), `docs/17`.
- **Verify:** with default settings no warning; with translation mode on and no confirming route
  the warning lists the missing right; no quote text is ever emitted by the validator.

### 2.3 `FetchPolicy` cannot be tuned without editing code

- **Case:** the script calls `FetchPolicy()` with defaults in five places; a slow source needing a
  20-second timeout or a 0.5 req/s rate requires a code change.
- **Fix:** add `FETCH_TIMEOUT_SECONDS`, `FETCH_REQUESTS_PER_SECOND`, `FETCH_MAX_BYTES` and
  `FETCH_MAX_CONNECTIONS` to `Settings`, and construct `FetchPolicy` from settings at the call
  sites. Keep the current defaults so behaviour is unchanged.
- **Files:** `src/media_quotes/config.py`, `scripts/test_scraper.py`, `src/media_quotes/fetch.py`
  (one helper `fetch_policy_from(settings)`), `.env.example`.
- **Verify:** a settings test asserts overrides flow into the policy; existing fetch tests pass
  unchanged; no network call in tests.

---

## 3. Tier 3 — polish, only after Tier 1 and 2

### 3.1 Local XML discovery ignores `--all-topics`

- **Case:** `--rss-file` and `--sitemap-file` pass `require_topic=False` unconditionally
  (`test_scraper.py:1721,1727`), so `--all-topics` has no effect there while it does for
  `--live-feed`.
- **Fix:** pass `require_topic=require_topic` in both calls. One-line change each.
- **Files:** `scripts/test_scraper.py`.
- **Verify:** an off-topic RSS item is included with `--all-topics` and excluded without it.

### 3.2 A new HTTP client is opened per fetch

- **Case:** `run_live_feed` fetches the feed and then each article with a separate
  `fetch_bytes` call; each call builds its own `httpx.AsyncClient`, so `max_connections=10` never
  applies across requests. At 1 req/s this is not a bottleneck today, but it becomes one if the
  rate limit is raised.
- **Fix:** let `fetch_bytes` accept an optional `client`, and create one client per `run_live_feed`
  invocation. Keep the existing constructor path for tests and other callers.
- **Files:** `src/media_quotes/fetch.py`, `scripts/test_scraper.py`.
- **Verify:** a mocked test asserts one client used for the feed and all articles; existing
  `MockTransport` tests still pass.

---

## 4. Rollout order and gates

1. Tier 1 (1.1–1.3) — small, docs/flag hygiene; no behaviour change beyond removing a no-op.
2. Tier 2 (2.1–2.3) — capability; ship each with a mocked test.
3. Tier 3 (3.1–3.2) — only if the live path is actually used regularly.

After each step run the full gate: `python scripts/docs_qa.py`, `ruff format --check .`,
`ruff check .`, `mypy --strict src/`, `pytest` with the coverage floor, `python -m build`, and the
CLI smoke check. [17](17-connection-and-fetching-reference.md) must be updated in the same change
as any flag or default it describes, so the reference stays truthful.

---

## 5. Explicitly not proposed

- Changing T0 rights policy or enabling a blocked source: that is a legal decision, not a code one.
- Making `mq` itself call the network without a dedicated command and dry-run default.
- The Tier A/B/C features in [13](13-code-review-and-feature-roadmap.md): they consume already
  verified quotes and do not affect connection or fetching.
