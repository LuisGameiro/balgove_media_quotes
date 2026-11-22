# 15 — Source topic study: how outlets organise the Russia–Ukraine war

This study answers one narrow question: **how do the outlets in the source registry organise the
Russia–Ukraine war topic for article discovery?** It is a structural study only: topic hubs, tags,
feeds, sitemaps and API parameters. It does not reproduce article bodies and does not change the
rights gate. Observation of a public feed is not permission to ingest, store or republish it.

Evidence labels: **VERIFIED** means the URL was fetched during this study; **KNOWN** means
documented or visible in a fetched page but not fully verified; **NOT VERIFIED** means a 403/404,
block, or no stable route found. Topic slugs are outlet-specific and must not be guessed from
another outlet's slug.

---

## 1. Patterns observed

| Pattern | How it works | Best use | Limitation |
|---|---|---|---|
| Dedicated topic hub + topic RSS | A curated hub page has a matching feed, e.g. Guardian `/world/ukraine` and `/world/ukraine/rss` | High-precision daily monitoring of one outlet | Curated, not exhaustive; feed may mix video/live/audio |
| Tag topic page | A tag slug collects war articles: Al Jazeera `/tag/ukraine-russia-crisis/`, Indian Express `/tag/russia-ukraine-war/`, SCMP `/topics/ukraine` | Human navigation and page-level classification | Slugs differ per outlet; topic RSS often absent |
| Section/event page | Suspilne event page `news/event/rf-ukraine-war-topnews/` with sub-blocks and war tag `vtorgnenna-rosii-v-ukrainu` | Rich taxonomy; Ukrainian-language coverage | Needs translation-aware rules; not a portable API |
| General RSS with topic metadata | Guardian feed items carry tags such as `Ukraine`, `Russia`, `World news` | Good secondary signal when tags exist | Many outlets expose only title, URL and date |
| General RSS / sitemap only | Most outlets expose broad feed metadata; war relevance is inferred from title/URL/date | Discovery baseline | Weak topic precision; no archive depth guarantee |
| API query | Guardian Open Platform `tag`/`q`/`from-date`; NYT `q`/`fq`/`begin_date`; GDELT `query`/`startdatetime` | Most deterministic, auditable and backfillable route | Requires keys, quotas and rights to the underlying text |

**Conclusion:** no cross-outlet universal topic slug exists. Deterministic discovery should be
configured **per source**, using the richest route that source actually exposes, and should record
which route and topic rule selected each candidate.

---

## 2. Tier 1 and Tier 2 sources

| Source | Topic arrangement | Discovery routes | Topic signals / caveat |
|---|---|---|---|
| Reuters | NOT VERIFIED; no article body fetched | NOT VERIFIED (sitemap 401) | Robots and terms block automated collection; structural observation only |
| BBC News | General Europe hub KNOWN; attempted topic ID 404 | `https://feeds.bbci.co.uk/news/world/europe/rss.xml` VERIFIED; general world RSS KNOWN; sitemap from robots | RSS has title/URL/time but no topic tags; terms forbid scraping, datasets and grounding — observation only |
| AP | Hub `/hub/russia-ukraine-war` KNOWN but 403 | `apnews.com/sitemap.xml` NOT VERIFIED (403) | Structural observation only |
| Bloomberg | NOT VERIFIED | `https://feeds.bloomberg.com/markets/news.rss` VERIFIED; news sitemap index KNOWN | Feed gives article ID/URL/date; no war tag; automated AI use restricted |
| Financial Times | NOT VERIFIED | `https://www.ft.com/rss/home` VERIFIED; `https://www.ft.com/sitemaps/index.xml` KNOWN | RSS is broad homepage; robots prohibits ML/AI use |
| The Economist | NOT VERIFIED | RSS NOT VERIFIED (403); `sitemap.xml` and `googlenews.xml` KNOWN from robots | Broad AI/training restrictions |
| The Guardian | `https://www.theguardian.com/world/ukraine` VERIFIED | `https://www.theguardian.com/world/ukraine/rss` VERIFIED; news sitemap KNOWN | Feed items carry topic tags: `Ukraine`, `Russia`, `World news`, `Europe`; best-tier example |
| Deutsche Welle | “Ukraine war” in hub navigation; exact URL NOT VERIFIED | `https://rss.dw.com/rdf/rss-en-all` VERIFIED; `https://www.dw.com/sitemap.xml` KNOWN | Feed has section labels; no topic-specific RSS verified |
| Le Monde (EN) | NOT VERIFIED | `https://www.lemonde.fr/en/rss/une.xml` VERIFIED; EN news sitemap KNOWN | Feed is section/path oriented; no verified war-topic route |

### Findings

- Only the Guardian showed a fully verified, high-precision topic hub **and** matching topic feed.
- Several outlets expose usable general feeds/sitemaps but no verified war-specific discovery route.
- Guardian's feed also demonstrates why tag metadata matters: a URL can be a video or live blog,
  yet its item tags still establish war-topic membership.
- Rights restrictions remain independent of route: an accessible feed is not a licence.

---

## 3. Tier 3 non-Western sources

| Source | Topic arrangement | Discovery routes | Topic signals / caveat |
|---|---|---|---|
| Al Jazeera | `https://www.aljazeera.com/tag/ukraine-russia-crisis/` VERIFIED | `https://www.aljazeera.com/xml/rss/all.xml` VERIFIED; sitemap VERIFIED | Rich tags/topics/locations; topic-specific RSS NOT VERIFIED |
| Kyiv Independent | War/Russia sections KNOWN | `/feed/` NOT VERIFIED (404) | EN outlet with licensing route; no stable topic node verified |
| Ukrainska Pravda | NOT VERIFIED | `https://www.pravda.com.ua/eng/rss/` VERIFIED | English feed; translation attribution matters |
| Suspilne | `https://suspilne.media/news/event/rf-ukraine-war-topnews/` VERIFIED | `https://suspilne.media/rss/all.rss` VERIFIED; war tag slug NOT VERIFIED | Ukrainian labels `Війна`, `Злочини РФ`; reuse requires source link |
| Interfax-Ukraine | “War” navigation KNOWN | `/rss/` NOT VERIFIED (404); feeds are a paid product | Internal-use notice; permission needed before reproduction |
| Anadolu Agency | NOT VERIFIED | Topic RSS NOT VERIFIED (404) | EN edition; agency rights review needed |
| The Hindu | NOT VERIFIED | General RSS link KNOWN but exact URL NOT VERIFIED | Some member-only content; mostly section-level taxonomy |
| The Indian Express | `https://indianexpress.com/tag/russia-ukraine-war/` VERIFIED | `https://indianexpress.com/feed/` VERIFIED; topic RSS NOT VERIFIED | Clear tag page; EN plus regional editions |
| SCMP | `https://www.scmp.com/topics/ukraine` VERIFIED | `https://www.scmp.com/rss/2/feed` VERIFIED; topic RSS NOT VERIFIED | Item label “Ukraine war”; paywall |
| Kyodo News | NOT VERIFIED (homepage sections only) | RSS/sitemap NOT VERIFIED | Agency copyright; no verified war node |
| Yonhap | Issue keywords KNOWN, no Ukraine hub verified | RSS index and `https://en.yna.co.kr/RSS/news.xml` VERIFIED | Six section feeds; keyword taxonomy mostly Korean-centric |

### Findings

- Topic slugs are outlet-specific: `ukraine-russia-crisis`, `russia-ukraine-war`,
  `rf-ukraine-war-topnews`, `topics/ukraine`. They cannot be generated from a shared template.
- No topic-specific RSS was verified for the non-Western set; general feeds plus tag/section pages
  are the realistic discovery pattern.
- Suspilne has the richest war taxonomy but needs Ukrainian-language relevance rules and attribution.
- Rights and translation review remain separate from topic discovery.

---

## 4. API-based discovery

| Service | Mechanism | Content returned | Verified endpoint/notes |
|---|---|---|---|
| GDELT 2.0 DOC API | `query` with phrases, OR, `domain:`, `sourcecountry:`, `sourcelang:`, `theme:`, `nearN:`; `mode=artlist`; date bounds | Title, URL, source country/language, publication date — links/metadata only | `https://api.gdeltproject.org/api/v2/doc/doc?query=...&mode=artlist&format=json` VERIFIED via official docs; live calls rate-limited |
| Guardian Open Platform | `/search` with `q`, `tag`, `section`, `from-date`, `to-date`, `page-size`; `show-fields=body` is opt-in | Minimal metadata by default; body only when explicitly requested | `https://content.guardianapis.com/search` VERIFIED via docs |
| NYT Article Search / Archive | `q`, `fq`, `begin_date`, `end_date`, `page`, `sort` | Snippet + metadata, not full body; archive by month | `/svc/search/v2/articlesearch.json` and `/svc/archive/v1/{year}/{month}.json` VERIFIED via docs |

APIs are the strongest deterministic discovery surface because queries, date bounds, pagination and
stable identifiers are explicit. They still require rights to the underlying article text; GDELT and
NYT metadata do not grant full-text rights.

---

## 5. Implication for this project

The current project has no topic configuration in `data/sources.csv`. The live diagnostic uses a
hardcoded Guardian Ukraine feed plus a hardcoded multilingual keyword list, and the daily pipeline
replays fixtures by date only. The study supports this target design:

1. Keep the rights and access columns in `data/sources.csv` unchanged in purpose.
2. Add a separate source-topic configuration (for example `data/source_topic_routes.csv`) with one row per
   source: `source_id`, `route` (`topic_rss`, `tag_page`, `general_rss`, `api`, `sitemap`),
   `discovery_url` or `api_query`, `topic_rule_id`, `language`, `notes`.
3. Add one versioned `data/topic_rules.json` / `src/media_quotes/relevance.py` rule: required terms,
   supporting terms, exclusions, per-language variants, scoring threshold.
4. Prefer per-source topic routes in this order: official query API with dates > topic RSS >
   general RSS with tags > sitemap > generic keyword scan.
5. Write the selected route, `topic_rule_version`, `matched_terms` and `topic_status` into coverage
   and audit logs so every inclusion/exclusion is explainable.
6. Never treat a public topic page or feed as a rights grant; the rights gate stays first.

This turns “is this article about the war?” from an invisible hardcoded substring check into a
source-specific, auditable decision.

## 6. Hardened design (recommended upgrade)

The basic `source_topics.csv` + `relevance.py` shape is a good start, but it is not enough for
production. The following controls make the design fail-closed, explainable and safe to change.

### 6.1 Non-negotiable principles

- **Rights gate first.** No topic route is consulted unless the source and that exact route are
  approved; a public topic page is never treated as a licence.
- **Route-level approval.** A source may be approved for one route and not another (official API,
  topic RSS and general RSS have different scopes). Store approval per route, not only per source.
- **Fail closed.** Invalid config, unresolved rule id, unapproved fallback or unparseable feed must
  produce a coverage gap, never a silent best-effort guess.
- **Version everything.** Rules and route config are immutable; changes create a new version. Each
  decision records the rule version and config hash so an old digest can be re-derived.
- **Evidence for every decision.** Store status, score, matched terms, source tag, rule version and
  raw blob reference for accepted, rejected and uncertain articles.
- **Deterministic selection.** No LLM in the topic decision. The LLM remains limited to section
  summaries, as the brief requires.
- **No silent fallback.** Every fallback hop is explicit, approved and logged with its reason.

### 6.2 Configuration surface

| File | Role | Key fields |
|---|---|---|
| `data/source_topic_routes.csv` | One row per allowed discovery route per source | `source_id`, `route_id`, `route_type`, `discovery_url` or `query_template`, `topic_rule_id`, `priority`, `fallback_route_id`, `approved`, `license_reference`, `timezone`, `lookback_days`, `notes` |
| `data/topic_rules.json` | Versioned, nested rule definitions (not CSV — multilingual rules do not fit flat tables) | `rule_id`, `version`, `locales`, `anchors`, `supporting_terms`, `exclusions`, `weights`, `accept_threshold`, `uncertain_band`, `evidence_fields` |
| `data/topic_rules.schema.json` | Frozen schema for rule files, snapshot-tested like `quotes.schema.json` | Allowed fields, types, term constraints |
| Version manifest / audit report | Provenance | `topic_rule_version`, `topic_rules_sha256`, `source_topics_sha256` |

Reference resolution is mandatory: every `topic_rule_id` in the route table must exist in the rule
file, every fallback route must point to an approved row, and every enabled source must have at least
one approved route. `mq validate` and `mq doctor` should check this before any network call.

### 6.3 Rule evaluator contract

The evaluator should be a pure function, for example:

```python
evaluate(topic_rule, article) -> TopicDecision(status, score, matched_terms, rule_version)
```

It must weigh evidence by trustworthiness:

| Signal | Weight | Notes |
|---|---|---|
| Publisher topic tag (e.g. Guardian `Ukraine`) | strongest | Only when the tag is machine-readable and from the publisher, not inferred |
| Title / headline | strong | Apply Unicode normalisation, casefold and word boundaries |
| Cleaned lead paragraph | strong | Use article body only, not navigation, related links or cookie text |
| Full cleaned body | medium | Word-boundary phrases; avoid raw substring matches such as `ros` matching `Russian` |
| URL slug | weakest | SEO/stale-slug risk; never sufficient on its own |

Rules should require at least one subject anchor (Ukraine/Russia/place names) and one war-context
term (war, invasion, strikes, ceasefire, sanctions and their local-language equivalents), then add
supporting terms to the score. Exclusion patterns handle other wars, sport, entertainment and
“war on drugs” style false positives. Uncertain cases go to a review queue, not straight to the
digest.

### 6.4 Hardening controls

| Risk | Control |
|---|---|
| Publisher taxonomy drifts or tag disappears | Per-route health metrics: match rate, parse rate and tag coverage by day; alert on sharp change |
| False positives from generic words or other wars | Word boundaries, exclusion terms, negative gold-set examples and per-source review samples |
| Multilingual gaps | Per-locale anchors; NFC + casefold + diacritic folding; explicit `Київ`/`Киев`, `Україна`/`Украина` style variants |
| Feed content treated as instructions | Treat all feed text as untrusted data; strip HTML before any downstream use; no prompt text from feeds |
| SSRF or URL injection | HTTPS only; host allowlist derived from registry homepage/feed host; redirects disabled; timeout and max-byte limits |
| Malformed or hostile XML | Strict parser limits; reject oversized entities/documents; record parse failure as a coverage gap |
| Partial route failure | Route-level `last_success_at`, retry/backoff and explicit coverage gap; never silently switch to an unapproved route |
| API quota/cost overrun | Date bounds, cache, pagination caps, `--max-cost`-style guard and redacted keys in env only |
| Rule change breaks history | Immutable rule versions; backfill records the rule version used; never tune on the test split |
| Manual exceptions | Review queue with reason codes, reviewer, timestamp and rule version; overrides are auditable, never silent |

### 6.5 Validation before enabling any live route

1. **Shadow mode:** run the new evaluator alongside the current keyword filter; log disagreements,
   do not publish from them.
2. **Gold set:** add article-level labels `relevant`, `not_relevant` and `uncertain`, including other
   wars, sport and Ukrainian/Russian-language examples. Extend the existing eval contract with
   article precision / false-positive rate, not only recall.
3. **Failure injection:** 403, 404, timeout, malformed XML, oversized response, unknown rule id,
   missing fallback and rate-limit responses must all exit closed with actionable gaps.
4. **Canary:** enable one source first (Guardian is the best candidate because its topic RSS and
   tags are verified), then one non-Western source, before considering broader rollout.
5. **Replay:** rebuild a past date using the recorded rule version and config hash; the decision set
   must be identical.

A practical first release is deliberately small: versioned JSON rules, the route CSV, evidence
logging, negative gold-set evaluation and a Guardian-only shadow canary. Human review and richer
drift monitoring can be added without changing the core contract.
