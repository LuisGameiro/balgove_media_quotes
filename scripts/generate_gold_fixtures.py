"""Script to generate rich synthetic fixtures and gold set."""

import json
from pathlib import Path
from typing import Any

from media_quotes.enums import SECTION_IDS
from media_quotes.gold_set import split_for

ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = ROOT / "data" / "fixtures"
GOLD_SET_FILE = ROOT / "data" / "gold_set" / "synthetic-v1.json"

SECTIONS = list(SECTION_IDS)

SOURCES_INFO = {
    "reuters": {
        "pub_name": "Reuters (synthetic fixture)",
        "author": "Fixture Reuters Correspondent",
        "lang": "en",
        "url_base": "https://www.reuters.com/fixture/synthetic-2026-11-",
    },
    "bbc": {
        "pub_name": "BBC News (synthetic fixture)",
        "author": "Fixture BBC Correspondent",
        "lang": "en",
        "url_base": "https://www.bbc.co.uk/fixture/synthetic-2026-11-",
    },
    "scmp": {
        "pub_name": "South China Morning Post (synthetic fixture)",
        "author": "Fixture SCMP Analyst",
        "lang": "en",
        "url_base": "https://www.scmp.com/fixture/synthetic-2026-11-",
    },
    "ukrainskapravda": {
        "pub_name": "Ukrayinska Pravda (synthetic fixture)",
        "author": "Fixture UP Reporter",
        "lang": "uk",
        "url_base": "https://www.pravda.com.ua/fixture/synthetic-2026-11-",
    },
    "tass": {
        "pub_name": "TASS (synthetic fixture)",
        "author": "Fixture TASS Desk",
        "lang": "ru",
        "url_base": "https://tass.com/fixture/synthetic-2026-11-",
    },
    "dw": {
        "pub_name": "Deutsche Welle (synthetic fixture)",
        "author": "Fixture DW Analyst",
        "lang": "en",
        "url_base": "https://www.dw.com/fixture/synthetic-2026-11-",
    },
}

QUOTE_TEMPLATES = {
    "war_intensity": [
        (
            "Frontline engagements have intensified across eastern axes with sustained "
            "artillery duels over key transit nodes,",
            "General Marcus Vance, military spokesperson",
        ),
        (
            "Defensive fortifications around the northern sector continue to absorb repeated "
            "tactical reconnaissance probes,",
            "Colonel Elena Rostova, operations officer",
        ),
        (
            "Air defence units intercepted multiple cruise missiles aimed at critical "
            "regional power infrastructure early Wednesday,",
            "Commander David Sterling, air defense command",
        ),
    ],
    "international_support": [
        (
            "Allied partners finalized a bilateral security protocol guaranteeing expedited "
            "delivery of heavy ammunition supplies,",
            "Minister Claire Dubois, defense liaison",
        ),
        (
            "Technical training cohorts for advanced radar systems have graduated ahead of "
            "scheduled field deployment dates,",
            "Ambassador Lars Lindqvist, military cooperation envoy",
        ),
        (
            "Logistical corridors through central Europe remain fully operational under "
            "collective security transit arrangements,",
            "Coordinator Hans Weber, logistics taskforce",
        ),
    ],
    "peace_initiatives": [
        (
            "Diplomatic channels remain receptive to verifiable de-escalation benchmarks "
            "mediated by neutral partner states,",
            "Special Envoy Roberto Gomez, mediation council",
        ),
        (
            "Any durable cessation of hostilities requires reciprocal observation posts and "
            "legally binding arbitration guarantees,",
            "Dr. Sarah Jenkins, conflict resolution fellow",
        ),
        (
            "Preliminary discussions in Geneva focused on humanitarian corridors and civilian "
            "infrastructure protection agreements,",
            "Undersecretary Maria Rossi, diplomatic mission",
        ),
    ],
    "sanctions": [
        (
            "Secondary enforcement mechanisms targeting offshore transshipment hubs have "
            "significantly curtailed illicit component flows,",
            "Director Thomas Cole, sanctions compliance bureau",
        ),
        (
            "Multilateral export controls on dual-use microelectronics are compelling "
            "alternative component re-engineering,",
            "Analyst Sophia Chen, trade intelligence unit",
        ),
        (
            "Banking oversight measures have constricted cross-border settlement channels for "
            "embargoed industrial equipment,",
            "Commissioner Arthur Pendelton, economic security desk",
        ),
    ],
    "resources": [
        (
            "Domestic ammunition manufacturing output has doubled following the commissioning "
            "of modernized production lines,",
            "Director Kevin Albright, ordnance board",
        ),
        (
            "Maintenance turnaround times for armored personnel carriers have dropped by "
            "thirty percent at forward depots,",
            "Engineer Viktor Petrenko, logistics corps",
        ),
        (
            "Industrial metallurgy reserves remain sufficient to sustain planned replacement "
            "rates throughout the upcoming quarter,",
            "Analyst James Holloway, defence economics institute",
        ),
    ],
    "other": [
        (
            "Environmental rehabilitation teams are assessing agricultural soil contamination "
            "following infrastructure strikes,",
            "Ecologist Anna Lindemann, reconstruction monitoring team",
        ),
        (
            "Civilian municipal utilities restored district heating to eighty percent of "
            "residential zones before nightfall,",
            "Mayor Oksana Boyko, regional administration",
        ),
        (
            "Border management agencies recorded orderly freight clearance following the "
            "implementation of digitized customs clearance,",
            "Inspector Richard Davies, customs authority",
        ),
    ],
}

UKRAINIAN_QUOTES = [
    (
        "Відновлення енергетичної інфраструктури триває цілодобово за підтримки міжнародних "
        "інженерних команд,",
        "Олександр Коваленко, речник енергетичного штабу",
        "Restoration of energy infrastructure continues around the clock with support from "
        "international engineering teams,",
    ),
    (
        "Українські підрозділи тримають визначені оборонні рубежі та стримують спроби "
        "просування противника,",
        "Полковник Сергій Череватий, пресофіцер оперативного командування",
        "Ukrainian units hold designated defensive lines and contain enemy advance attempts,",
    ),
    (
        "Гуманітарні місії доставили зимову допомогу до прифронтових громад за координації "
        "міжнародних партнерів,",
        "Марія Ткаченко, координаторка гуманітарного штабу",
        "Humanitarian missions delivered winter aid to frontline communities under "
        "coordination of international partners,",
    ),
]

RUSSIAN_QUOTES = [
    (
        "Дипломатические контакты продолжаются по линии профильных ведомств для "
        "урегулирования гуманитарных вопросов,",
        "Алексей Смирнов, представитель внешнеполитического ведомства",
        "Diplomatic contacts continue through line agencies to resolve humanitarian issues,",
    ),
    (
        "Торгово-экономические связи с региональными партнерами демонстрируют стабильность в "  # noqa: RUF001 - Cyrillic preposition is intentional in Russian text
        "условиях внешних рестрикций,",
        "Михаил Васильев, аналитик экономического департамента",
        "Trade and economic ties with regional partners demonstrate stability amid external "
        "restrictions,",
    ),
    (
        "Морские перевозки в акватории осуществляются в штатном режиме под контролем службы "
        "безопасности судоходства,",
        "Дмитрий Орлов, дежурный координационного центра",
        "Maritime shipping in the water basin operates as normal under control of the "
        "maritime safety service,",
    ),
]


def main() -> None:
    print("Generating synthetic fixtures and gold set...")
    articles: list[dict[str, Any]] = []
    source_names = list(SOURCES_INFO.keys())

    for i in range(70):
        source_id = source_names[i % len(source_names)]
        source_meta = SOURCES_INFO[source_id]
        day = 10 + (i % 10)
        pub_date = f"2026-11-{day:02d}"
        art_ref = f"fixture://{source_id}/synthetic-{source_id}-2026-11-{day:02d}-art{i:03d}.json"

        s1 = SECTIONS[i % len(SECTIONS)]
        s2 = SECTIONS[(i + 2) % len(SECTIONS)]

        quotes_data: list[tuple[str, str, str, str]] = []
        if source_id == "ukrainskapravda":
            q_raw, speaker, q_en = UKRAINIAN_QUOTES[i % len(UKRAINIAN_QUOTES)]
            quotes_data.append((q_raw, speaker, s1, q_en))
        elif source_id == "tass":
            q_raw, speaker, q_en = RUSSIAN_QUOTES[i % len(RUSSIAN_QUOTES)]
            quotes_data.append((q_raw, speaker, s1, q_en))
        else:
            t1 = QUOTE_TEMPLATES[s1][(i // len(SECTIONS)) % len(QUOTE_TEMPLATES[s1])]
            quotes_data.append((t1[0], t1[1], s1, ""))
            if i % 3 == 0:
                t2 = QUOTE_TEMPLATES[s2][((i + 1) // len(SECTIONS)) % len(QUOTE_TEMPLATES[s2])]
                quotes_data.append((t2[0], t2[1], s2, ""))

        text_parts = [f"Synthetic report for {source_meta['pub_name']}. Date: {pub_date}."]
        quote_spans: list[dict[str, Any]] = []

        for q_text, speaker, sec, q_en in quotes_data:
            sentence = f"“{q_text}” said {speaker}."
            text_parts.append(sentence)
            span_item: dict[str, Any] = {
                "quote_text": q_text,
                "char_start": 0,
                "char_end": 0,
                "speaker": speaker.split(",")[0].strip(),
                "attribution_span": f"said {speaker}",
                "section_id": sec,
                "dom_path": f"json/quote_spans/{len(quote_spans)}",
            }
            if q_en:
                span_item["quote_text_en"] = q_en
            quote_spans.append(span_item)

        full_text = " ".join(text_parts)
        for span in quote_spans:
            q_str = str(span["quote_text"])
            pos = full_text.find(q_str)
            span["char_start"] = pos
            span["char_end"] = pos + len(q_str)

        fixture_payload = {
            "fixture": True,
            "notice": f"SYNTHETIC FIXTURE for {source_id} replay.",
            "source_id": source_id,
            "canonical_url": f"{source_meta['url_base']}{day:02d}-art{i:03d}/",
            "publication_date": pub_date,
            "title": f"SYNTHETIC FIXTURE - {s1.replace('_', ' ').title()} developments",
            "byline": source_meta["author"],
            "publication_name": source_meta["pub_name"],
            "language": source_meta["lang"],
            "article_text": full_text,
            "quote_spans": quote_spans,
        }

        fix_folder = FIXTURES_DIR / source_id
        fix_folder.mkdir(parents=True, exist_ok=True)
        fix_file = fix_folder / f"synthetic-{source_id}-2026-11-{day:02d}-art{i:03d}.json"
        fix_file.write_text(
            json.dumps(fixture_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        gold_quotes = [
            {
                "quote_text": span["quote_text"],
                "section_id": span["section_id"],
                "human_label": True,
            }
            for span in quote_spans
        ]
        articles.append(
            {
                "article_ref": art_ref,
                "source_id": source_id,
                "publication_date": pub_date,
                "relevant": True,
                "quotes": gold_quotes,
            }
        )

    negatives = [
        {
            "quote_text": "Advertisement | Page 4 | Subscribe now | all rights reserved",
            "human_label": False,
            "reason": "boilerplate and table symbols, must be rejected by the junk filter",
        },
        {
            "quote_text": "Click here to accept cookie notice and privacy policy settings.",
            "human_label": False,
            "reason": "cookie banner boilerplate",
        },
        {
            "quote_text": "All rights reserved. No part of this publication may be reproduced "
            "without prior permission.",
            "human_label": False,
            "reason": "copyright boilerplate",
        },
        {
            "quote_text": "Sponsored content: Discover premier commercial real estate "
            "opportunities in Zurich.",
            "human_label": False,
            "reason": "advertorial banner",
        },
        {
            "quote_text": "Sign up for our daily morning briefing delivered straight to your "
            "inbox.",
            "human_label": False,
            "reason": "newsletter subscription prompt",
        },
        {
            "quote_text": "Read more about breaking international developments on page 12.",
            "human_label": False,
            "reason": "pager link boilerplate",
        },
        {
            "quote_text": "Photo: Staff / Getty Images. Table 1: Macroeconomic indicators 2026.",
            "human_label": False,
            "reason": "photo credit and table junk",
        },
        {
            "quote_text": "Special advertorial feature produced independently of editorial staff.",
            "human_label": False,
            "reason": "commercial promotion disclaimer",
        },
        {
            "quote_text": "Subscribe to our digital edition for unlimited access across "
            "mobile and tablet.",
            "human_label": False,
            "reason": "paywall prompt",
        },
        {
            "quote_text": "Cookie Notice: We use tracking technologies to analyze site usage "
            "and deliver personalized ads.",
            "human_label": False,
            "reason": "cookie consent notice",
        },
        {
            "quote_text": "For syndication rights and licensing requests contact "
            "syndication@example.com.",
            "human_label": False,
            "reason": "licensing contact boilerplate",
        },
        {
            "quote_text": "Follow our live coverage updates on social channels and wire feeds.",
            "human_label": False,
            "reason": "social link promo",
        },
    ]

    gold_data = {
        "version": "synthetic-v1",
        "notice": "SYNTHETIC GOLD SET: 70 articles, 60/20/20 train/dev/test split across 6 "
        "sections and multiple sources.",
        "split": "train: ~60%, dev: ~20%, test: ~20%",
        "articles": articles,
        "negative_candidates": negatives,
    }

    splits_count = {"train": 0, "dev": 0, "test": 0}
    for art in articles:
        s = split_for(str(art["article_ref"]), "synthetic-v1")
        splits_count[s] += 1

    print(f"Total articles: {len(articles)}")
    print(f"Splits distribution: {splits_count}")
    total_quotes = sum(len(list(a["quotes"])) for a in articles)
    print(f"Total quotes: {total_quotes}")
    print(f"Total negative candidates: {len(negatives)}")

    GOLD_SET_FILE.write_text(json.dumps(gold_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Gold set saved successfully.")


if __name__ == "__main__":
    main()
