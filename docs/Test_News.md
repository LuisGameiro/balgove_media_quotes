**Test**  
Media Quotes Collection System

> Status note (2026-11-23): a written blanket licence has been accepted for Reuters and BBC.
> `data/sources.csv`, `data/sources/reuters.json`, `data/sources/bbc.json` and
> `data/source_topic_routes.csv` now reflect the approved live routes. The risk discussion below
> is the original pre-licence assessment.

***1\. Task***

Develop an automated system for detecting, collecting and presenting media quotes from the sources specified below, where such media quotes are relevant to developing substantial understanding of the *current trends and expectations about Russia's war on Ukraine*.  

**T0 — legal rights gate (hard prerequisite).**  
No source may be retrieved, stored, embedded in a vector store, or quoted until the rights recorded for it in `data/sources.csv` are approved by counsel. Approval must explicitly cover: machine retrieval; storage in a quote database; LLM/RAG grounding and contextualization; verbatim republication of quotes in the daily report; and the historical backfill from 1 October 2026. A source is enabled only when `rights_status` is `license_held` or `api_permitted`; `pending_counsel_review` and `blocked` keep it disabled.

RSS is **not** an exemption. Automated retrieval of a feed is still automated collection. Reuters' robots.txt sets `User-agent: * / Disallow: /` (apart from `/plus/`) and requires prior written consent for any automated collection, in any format. BBC's Terms and robots comments forbid scraping/systematic extraction, dataset creation, LLM training, RAG, AI grounding and business use without permission; the fact that the BBC feed host permits a fetch does not grant those rights. Feeds also expose headlines, links, timestamps and short summaries rather than full article bodies or a backfillable archive, so they cannot satisfy the “all material quotes” or backfill requirements. This is an engineering risk assessment, not legal advice; counsel must review licences.

Sources — tiered by role, all subject to T0.

**Tier 1 — mandatory only once T0 is satisfied (monitor continuously):**
• Reuters — https://www.reuters.com — `rights_status=license_held`; written licence accepted 2026-11-23. Live sitemap discovery is an approved route.
• BBC News — https://www.bbc.co.uk/ — `rights_status=license_held`; written licence accepted 2026-11-23. Live RSS discovery is an approved route.

If T0 cannot be met for those two outlets, replace them with compliant discovery/fallback sources (still subject to their own terms review):
• GDELT — https://www.gdeltproject.org/ — discovery metadata and links only; rights to the underlying publisher's text are still required.
• The Guardian Open Platform — https://open-platform.theguardian.com/ — official API.
• The New York Times Article Search / Archive APIs — https://developer.nytimes.com/ — metadata and abstracts by default; full-text rights are separate.

**Tier 2 — additional authoritative outlets (plus; monitor daily):**
• Associated Press (AP) — https://apnews.com
• Bloomberg — https://www.bloomberg.com
• Financial Times — https://www.ft.com
• The Economist — https://www.economist.com
• The Guardian — https://www.theguardian.com
• Deutsche Welle — https://www.dw.com
• Le Monde — https://www.lemonde.fr

**Tier 3 — non-Western outlets with their own original reporting (plus; monitor daily). These are included precisely to avoid an all-Western, wire-copy-only picture:**
• Al Jazeera (Qatar) — https://www.aljazeera.com
• Kyiv Independent (Ukraine) — https://kyivindependent.com
• Ukrainska Pravda (Ukraine) — https://www.pravda.com.ua
• Suspilne / Ukrainian Public Broadcasting (Ukraine) — https://suspilne.media
• Interfax-Ukraine (Ukraine) — https://en.interfax.com.ua
• Anadolu Agency (Turkey) — https://www.aa.com.tr
• The Hindu (India) — https://www.thehindu.com
• The Indian Express (India) — https://indianexpress.com
• South China Morning Post (Hong Kong) — https://www.scmp.com
• Kyodo News (Japan) — https://english.kyodonews.net
• Yonhap News Agency (South Korea) — https://en.yna.co.kr

**Excluded by default (include only as explicitly labelled *perspective/claim* material):** RT, TASS, RIA Novosti, Xinhua, Global Times. They are non-Western and produce original output, but are state-controlled; treating them as neutral factual sources would corrupt the “substantial understanding” goal. If the user wants to study the belligerents’ narratives, add them behind a flag and never use their claims to establish or overwrite a fact in §2.4.

*1.1. Source selection criteria*  
A named outlet is admitted only when all of the following hold:
• Original reporting — first-hand bylines and directly attributed quotes. Verify a sample of articles against wire copy so the outlet is not merely relaying or translating Reuters/BBC/AP material. Per-article verification still applies even to the Tier 3 outlets above.
• Sustained topical coverage of the war since 1 October 2026, with a dated, stable archive covering the requested backfill period.
• Stable canonical article URL, publication date, and author/byline for auditability.
• Rights cleared under T0: `rights_status` is `license_held` or `api_permitted`, with a `license_reference` recorded; otherwise the source stays disabled.
• Machine-accessible content via the approved `access_mode` only (HTML, feed or API), with robots.txt and Terms checked for the actual path. Using RSS does not waive the rights check.
• Quotes attributable to a named person and role.
• Source type recorded with the data: independent, publicly funded, or state-controlled, plus language and country/region.

***2\. Functional Requirements***

*2.1. Database creation*  
• A database for storing relevant quotes should be stood up, with the following metadata:  
Quote ID  
Quote text  
Source ID  
Source name  
Source country/region  
Source type (independent | publicly funded | state-controlled)  
Original-reporting flag (first-hand | relayed wire copy)  
Original language  
Publication date  
Article name  
Article author/byline  
Article weblink (canonical)  
Extraction date  
Rights status (license_held | api_permitted | pending_counsel_review | blocked)  
Access mode (licensed_database | official_api | public_rss_permitted | pending_review | scraped_prohibited)  
License reference / consent ID  
Discovery method (sitemap | rss | api | gdelt)  
\[other if necessary\]

• A storage of acquired data and the chosen stack should be optimized for further efficient use of these data in LLM contextualization (for instance, storing data in json format, using Obsidian, Neo4j or any better auditable solutions). 

*2.2. Relevant articles and quotes detection*  
• The system must automatically and deterministically (not probabilistically) check each required source each day, and detect relevant articles.  
• Monitoring cadence by tier: Tier 1 at least every 6 hours; Tier 2 and Tier 3 at least once per day. Every check is logged.  
• A source is checked only while `enabled=true`, which requires T0 approval. Feeds and sitemaps may be used for discovery where permitted, but full-text extraction is allowed only from the approved `access_mode`.  
• The system should ‘read’ the relevant selected articles, then reason about whether there are relevant quotes (quotes that allow developing substantial understanding of the current trends and expectations about Russia's war on Ukraine).

*2.3. Quotes collection*  
• For each detected relevant quote:  
• Extract and save quotes and their metadata from the article’s webpage.  
• The extracted quote and metadata should be verified using deterministic tools (no ‘frankenquotes’, no incorrect/hallucinated sources, no empty metadata etc.)  
• Maintain the original formatting structure of the content.

• Relevant quotes for the period from October 1, 2026 until the current date should be collected and stored in the database.

• It would be a plus if the user can select the specific period from the past for which he wants to detect and collect relevant quotes to append the database (for instance, for August-September 2026), and the system will do that on request. 

• It would be an additional plus if the user can name another media (or ‘think-tank’) source, and the system automatically builds a pipeline for this source: the source is first checked against T0 and the admission criteria in §1.1, its source type, country/region and rights status are recorded, and then relevant quotes are detected and collected from this source, appending the database with the quotes from this source for the period from October 1, 2026.

*2.4. Reasoning about non-obsolete quotes and presenting them*  
• Each day the system should deliver an MD file where only non-obsolete material quotes (all recent, not outdated) for the rolling past 14-day period will be presented:  
• There should be a title of the file, where the user will see a period for which the quotes are presented.  
• The quotes should be grouped by separate sections, for instance: War Intensity, International Support, Peace Initiatives, Sanctions, Conflicting Parties’ Resources, etc.  
• The system should reason about what quotes to extract from the database into each of the sections, and then do that. *Obsolete quotes (with outdated facts that are superseded by known new facts) should not be shown.*   
• The quotes should be extracted into relevant sections deterministically (no change in the text of the quotes nor into their metadata), with the text of the quote, source name, publication name, article name and hyperlink to the original page.   
• Under the name of each section there should be a brief non-deterministic inference from LLM, accurately describing in 1-2 sentences the meaning resulting from the selected quotes. For instance: “*Commentators highlight facts indicating that the war in Ukraine is intensifying*"

***3\. Output Requirements***  
• Auditable database with quotes and relevant metadata.  
• Dashboard for the user where he can access latest MD files, see logs, access the database etc.  
• Dashboard view of per-source coverage (quotes collected per source per day) so under-covered or failed sources are visible at a glance.  
• GitHub with relevant repository.

***4\. Quality Requirements***  
• No source is ingested without recorded T0 approval; logs include the `license_reference`.  
• All material articles from mandatory sources should be detected.  
• Tier 3 non-Western outlets are represented in the output, and each stored quote is verified as first-hand reporting rather than a relay of Western wire copy.  
• Source type and attribution are stored for every quote; state-controlled sources (if enabled) are labelled and their claims are never treated as established facts.  
• All material quotes from those articles should be detected.  
• The quotes should be relevant, substantial for the selected domain, self-explanatory, cleansed (e.g., no contamination with text from ads/page numbers/table symbols etc).  
• All material quotes are extracted and saved (i.e. no important articles and quotes were left out).  
• Accurate extraction of quotes and metadata from the primary source is made.  
• Accurate load for the MD file (verbatim and categorisation into specific sections) is made. Obsolete quotes (with outdated facts that are superseded by known new facts) should not be shown.   
• Relevant interpretation of quotes is made for the description of their meaning in the MD file.  
• The database of quotes is auditable, easily integratable with frontier labs’ LLMs.   
• Logs are saved for auditability/traceability.  
• Processing should handle various potential errors (network issues, unavailable content, etc.)  
• The system should be able to process media articles regardless of their size or content type.

