# Intentional Ukrainian/Russian sample text is part of the synthetic multilingual matrix.
# ruff: noqa: RUF001
"""Regenerate the synthetic source x section topic matrix and gold set v2.

Stdlib only and byte deterministic: the same registry/section CSVs always produce the
same files. Every fixture is clearly labelled, uses invented speakers and quotes, and
points only at .invalid URLs. Nothing here contacts a network host or grants rights.
Run with: python scripts/generate_topic_matrix.py
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
FIXTURES_DIR = DATA_DIR / "fixtures"
GOLD_PATH = DATA_DIR / "gold_set" / "synthetic-v2.json"

RULE_ID = "ukraine-war-v1"
GOLD_VERSION = "synthetic-v2"
FIXTURE_NOTICE = (
    "SYNTHETIC FIXTURE - invented speakers and quotations; .invalid URLs only; "
    "not real reporting and never a rights grant"
)
LANG_FOR_CODE = {"uk": "uk", "ru": "ru", "fr": "fr"}
WAR_CONTEXT = {
    "en": (
        "Synthetic context: Ukraine, Russia, missile strikes and ceasefire "
        "monitoring in this fixture."
    ),
    "uk": "Синтетичний контекст: Україна, росія, обстріли та перемир'я у цій вигаданій історії.",
    "ru": (
        "Синтетический контекст: Украина, россия, обстрелы и перемирие в этой вымышленной истории."
    ),
    "fr": (
        "Synthetic context: Ukraine, Russia, missile strikes and ceasefire "
        "monitoring in this fixture."
    ),
}

QUOTES: dict[str, dict[str, str]] = {
    "war_intensity": {
        "en": (
            "Ukraine's eastern front saw synthetic shelling and drone strikes near "
            "imaginary checkpoints overnight, a fixture scenario said."
        ),
        "uk": (
            "На сході України у цій модельованій сцені фіксують обстріли та удари дронів "
            "поблизу умовних блокпостів."
        ),
        "ru": (
            "На востоке Украины в этой модельной сцене зафиксированы обстрелы и удары "
            "дронов возле условных блокпостов."
        ),
        "fr": (
            "Sur le front oriental de l'Ukraine, des tirs et des frappes de drones ont "
            "été simulés cette nuit près de points de contrôle fictifs."
        ),
    },
    "international_support": {
        "en": (
            "International support for Ukraine in this fixture includes a made-up air "
            "defence training package scheduled for next month."
        ),
        "uk": (
            "Міжнародна допомога Україні у цій фіктивній історії включає вигаданий пакет "
            "навчання з повітряної оборони наступного місяця."
        ),
        "ru": (
            "Международная помощь Украине в этой вымышленной истории включает условный "
            "пакет обучения по противовоздушной обороне в следующем месяце."
        ),
        "fr": (
            "Le soutien international à l'Ukraine dans ce scénario comprend un programme "
            "fictif de formation à la défense aérienne le mois prochain."
        ),
    },
    "peace_initiatives": {
        "en": (
            "A ceasefire plan for Ukraine needs monitoring and guarantees that cannot be "
            "withdrawn unilaterally, this synthetic scenario says."
        ),
        "uk": (
            "План перемир'я для України потребує моніторингу та гарантій, яких не можна "
            "скасувати в односторонньому порядку, йдеться у цій вигаданій сцені."
        ),
        "ru": (
            "План перемирия для Украины требует мониторинга и гарантий, которые нельзя "
            "отозвать в одностороннем порядке, говорится в этой вымышленной сцене."
        ),
        "fr": (
            "Un plan de cessez-le-feu pour l'Ukraine exige une surveillance et des "
            "garanties irrévocables, selon ce scénario fictif."
        ),
    },
    "sanctions": {
        "en": (
            "New sanctions on dual-use exports are changing procurement timelines inside "
            "this entirely invented sanctions scenario."
        ),
        "uk": (
            "Нові санкції на експорт товарів подвійного призначення змінюють строки "
            "закупівель у цій повністю вигаданій санкційній сцені."
        ),
        "ru": (
            "Новые санкции на экспорт товаров двойного назначения меняют сроки закупок "
            "в этой полностью вымышленной санкционной сцене."
        ),
        "fr": (
            "De nouvelles sanctions sur les exportations à double usage modifient les "
            "délais d'approvisionnement dans ce scénario de sanctions fictif."
        ),
    },
    "resources": {
        "en": (
            "Synthetic ammunition output in Ukraine has risen while trained personnel "
            "remain the binding constraint in this model."
        ),
        "uk": (
            "Умовний випуск боєприпасів в Україні зріс, але навчені фахівці залишаються "
            "головним обмеженням у цій моделі."
        ),
        "ru": (
            "Условный выпуск боеприпасов на Украине вырос, но подготовленные "
            "специалисты остаются главным ограничением в этой модели."
        ),
        "fr": (
            "La production fictive de munitions en Ukraine a augmenté, mais le manque "
            "de spécialistes formés reste la contrainte principale du modèle."
        ),
    },
    "other": {
        "en": (
            "Long-term accountability for Ukraine is the focus of this synthetic "
            "cross-cutting paragraph for offline replay."
        ),
        "uk": (
            "Довгострокова відповідальність за події в Україні є предметом цього "
            "синтетичного загального абзацу для автономного відтворення."
        ),
        "ru": (
            "Долгосрочная ответственность за события на Украине - тема этого "
            "синтетического общего абзаца для автономного воспроизведения."
        ),
        "fr": (
            "La responsabilité à long terme pour l'Ukraine est au coeur de ce paragraphe "
            "synthétique transversal destiné à un replay hors ligne."
        ),
    },
}

SPEAKERS: dict[str, tuple[str, ...]] = {
    "en": (
        "Fixture Alina Marchenko",
        "Fixture Petro Doroshenko",
        "Fixture Helen Sokolova",
        "Fixture Mykola Varenyk",
        "Fixture Ivan Lysenko",
        "Fixture Oksana Bondar",
        "Fixture Marko Tymchenko",
    ),
    "uk": (
        "Фіктивний Олександр Прикладовий",
        "Фіктивна Ірина Вигадайко",
        "Фіктивний Петро Синтетик",
        "Фіктивна Марія Макетна",
        "Фіктивний Дмитро Демонстраційний",
        "Фіктивна Олена Модельна",
        "Фіктивний Тарас Прикладний",
    ),
    "ru": (
        "Вымышленный Иван Примеров",
        "Вымышленная Анна Синтетикова",
        "Вымышленный Пётр Макетов",
        "Вымышленная Мария Демонстра",
        "Вымышленный Дмитрий Модельников",
        "Вымышленная Ольга Выдумкина",
        "Вымышленный Сергей Прикладной",
    ),
    "fr": (
        "Personnage Fictif Alina Marchenko",
        "Personnage Fictif Pierre Dorochenko",
        "Personnage Fictif Hélène Sokolova",
        "Personnage Fictif Nicolas Varenyk",
        "Personnage Fictif Ivan Lysenko",
        "Personnage Fictif Oxana Bondar",
        "Personnage Fictif Marc Tymchenko",
    ),
}

ATTRIBUTION_VERB = {
    "en": "said",
    "uk": "заявив",
    "ru": "заявил",
    "fr": "a déclaré",
}

ROLE = {
    "en": "synthetic analyst",
    "uk": "синтетичний аналітик",
    "ru": "синтетический аналитик",
    "fr": "analyste synthétique",
}

# Each source gets one deliberately mislabelled gold quote; we never alter the fixture label.
MISLABEL_CYCLE = (
    "international_support",
    "war_intensity",
    "other",
    "resources",
    "sanctions",
    "peace_initiatives",
)

OFFTOPIC: tuple[dict[str, str], ...] = (
    {
        "source_id": "ap",
        "slug": "football-final",
        "section_id": "other",
        "quote_text": (
            "The imaginary striker scored twice in a made-up cup final before a fictional "
            "crowd, a synthetic sports desk said."
        ),
    },
    {
        "source_id": "bbc",
        "slug": "other-war-conference",
        "section_id": "war_intensity",
        "quote_text": (
            "A fictional conference on a conflict in another region issued an invented "
            "statement about humanitarian corridors."
        ),
    },
    {
        "source_id": "guardian",
        "slug": "tv-awards",
        "section_id": "other",
        "quote_text": (
            "The fictional ensemble accepted an imaginary television award and thanked a "
            "make-believe studio audience."
        ),
    },
    {
        "source_id": "tass",
        "slug": "chess-event",
        "section_id": "resources",
        "quote_text": (
            "Вымышленная шахматная федерация объявила о наградах для двух воображаемых "
            "гроссмейстеров в этом сценарии."
        ),
    },
    {
        "source_id": "ukrainskapravda",
        "slug": "music-festival",
        "section_id": "peace_initiatives",
        "quote_text": (
            "Вигаданий музичний фестиваль у сусідній країні оголосив уявний склад "
            "виконавців на наступний сезон."
        ),
    },
    {
        "source_id": "ria",
        "slug": "weather-forecast",
        "section_id": "international_support",
        "quote_text": (
            "Вымышленная метеослужба пообещала воображаемое похолодание в соседнем "
            "регионе на следующей неделе."
        ),
    },
    {
        "source_id": "bloomberg",
        "slug": "coffee-prices",
        "section_id": "sanctions",
        "quote_text": (
            "A fictional commodity desk projected imaginary coffee prices for the next "
            "quarter in this fantasy market note."
        ),
    },
    {
        "source_id": "kyivindependent",
        "slug": "baking-club",
        "section_id": "war_intensity",
        "quote_text": (
            "An invented neighbourhood baking club shared a make-believe recipe for "
            "holiday bread in this synthetic lifestyle item."
        ),
    },
)

NEGATIVE_CANDIDATES: tuple[dict[str, str], ...] = (
    {
        "quote_text": (
            "Advertisement: subscribe to the synthetic newsletter for more fixture updates."
        ),
        "reason": "advertisement boilerplate",
    },
    {
        "quote_text": "Cookie notice: this fixture uses no tracking and stores no personal data.",
        "reason": "cookie notice",
    },
    {
        "quote_text": "All rights reserved by the imaginary publisher of this synthetic fixture.",
        "reason": "copyright boilerplate",
    },
    {
        "quote_text": "Click here to read more fictional headlines on page twelve of the fixture.",
        "reason": "pager link boilerplate",
    },
    {
        "quote_text": "Photo: Fixture Photography Desk; caption is invented for offline replay.",
        "reason": "photo credit and caption junk",
    },
    {
        "quote_text": "Sponsored message: a make-believe product appears here for layout testing.",
        "reason": "sponsored content",
    },
    {
        "quote_text": "Table 4: invented economic indicators for the synthetic topic scenario.",
        "reason": "table furniture",
    },
    {
        "quote_text": "Follow our fictional social account for imaginary breaking news updates.",
        "reason": "social promotion",
    },
)


def read_rows(filename: str) -> list[dict[str, str]]:
    with (DATA_DIR / filename).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def stable_int(*parts: str) -> int:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def publication_date(*key: str) -> str:
    # Keep all fixtures inside the default 14-day report window ending 2026-11-21.
    day = 10 + stable_int("date", *key) % 12
    return f"2026-11-{day:02d}"


def write_if_changed(path: Path, text: str) -> bool:
    payload = text.encode("utf-8")
    if path.exists() and path.read_bytes() == payload:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_bytes(payload)
    temp.replace(path)
    return True


def fixture_name(source_id: str, section_id: str) -> str:
    return f"synthetic-{source_id}-topic-{section_id}.html"


def build_fixture_html(
    *,
    source_id: str,
    source_name: str,
    section_id: str,
    section_name: str,
    publication: str,
    language: str,
    quote_text: str,
    speaker: str,
    file_name: str = "",
    title_override: str = "",
    context_text: str = "",
) -> str:
    name = file_name or fixture_name(source_id, section_id)
    canonical = f"https://fixture.invalid/{source_id}/{name}"
    title = title_override or f"SYNTHETIC FIXTURE - {section_name}"
    publisher = f"{source_name} (synthetic fixture)"
    json_ld = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": title,
        "datePublished": f"{publication}T09:00:00Z",
        "url": canonical,
        "description": FIXTURE_NOTICE,
        "author": [{"@type": "Person", "name": speaker}],
        "publisher": {"@type": "Organization", "name": publisher},
    }
    safe_title = html.escape(title)
    safe_notice = html.escape(FIXTURE_NOTICE, quote=True)
    safe_speaker = html.escape(speaker, quote=True)
    safe_quote = html.escape(quote_text)
    safe_publisher = html.escape(publisher, quote=True)
    lines = [
        "<!doctype html>",
        f"<!-- {FIXTURE_NOTICE} -->",
        f'<html lang="{language}">',
        "<head>",
        '  <meta charset="utf-8">',
        f"  <title>{safe_title}</title>",
        f'  <meta name="description" content="{safe_notice}">',
        f'  <meta name="publication_date" content="{publication}">',
        f'  <meta name="dc.date" content="{publication}">',
        f'  <meta name="article:published_time" content="{publication}T09:00:00Z">',
        f'  <meta name="author" content="{safe_speaker}">',
        f'  <meta name="publicationName" content="{safe_publisher}">',
        f'  <meta property="og:site_name" content="{safe_publisher}">',
        f'  <link rel="canonical" href="{canonical}">',
        '  <script type="application/ld+json">',
        json.dumps(json_ld, ensure_ascii=False, indent=2),
        "  </script>",
        "</head>",
        "<body>",
        "  <article>",
        f"    <h1>{safe_title}</h1>",
        '    <p class="byline">Fixture Desk - invented for offline replay</p>',
        '    <p class="fixture-notice">' + html.escape(FIXTURE_NOTICE) + ". "
        "Every speaker and quotation below is invented. Do not fetch this URL.</p>",
        *([f'    <p class="context">{html.escape(context_text)}</p>'] if context_text else []),
        '    <p><span class="quote" '
        f'data-speaker="{safe_speaker}" '
        f'data-role="{html.escape(ROLE[language], quote=True)}" '
        f'data-section="{section_id}">'
        f"“{safe_quote}” {ATTRIBUTION_VERB[language]} {safe_speaker}.</span></p>",
        "    <p>End of synthetic fixture. No live request or rights grant is implied.</p>",
        "  </article>",
        "</body>",
        "</html>",
    ]
    return "\n".join(lines) + "\n"


def split_bucket(article_ref: str) -> str:
    """Mirror src/media_quotes/gold_set.py: sha256(version:ref) mod 10, never tuned."""
    digest = hashlib.sha256(f"{GOLD_VERSION}:{article_ref}".encode()).digest()
    bucket = digest[0] % 10
    if bucket < 6:
        return "train"
    if bucket < 8:
        return "dev"
    return "test"


def main() -> None:
    source_rows = read_rows("sources.csv")
    section_rows = read_rows("sections.csv")
    section_ids = tuple(row["section_id"] for row in section_rows)
    section_names = {row["section_id"]: row["section_name"] for row in section_rows}

    articles: list[dict[str, Any]] = []
    written = 0
    mislabelled = 0

    for source_index, source in enumerate(source_rows):
        source_id = source["source_id"]
        language = LANG_FOR_CODE.get(source["language"], "en")
        wrong_section = MISLABEL_CYCLE[source_index % len(MISLABEL_CYCLE)]

        for section_id in section_ids:
            key = f"{source_id}:{section_id}"
            publication = publication_date(source_id, section_id)
            quote_text = QUOTES[section_id][language]
            speaker = SPEAKERS[language][stable_int("speaker", key) % len(SPEAKERS[language])]
            html_text = build_fixture_html(
                source_id=source_id,
                source_name=source["source_name"],
                section_id=section_id,
                section_name=section_names[section_id],
                publication=publication,
                language=language,
                quote_text=quote_text,
                speaker=speaker,
                context_text=WAR_CONTEXT[language],
            )
            path = FIXTURES_DIR / source_id / fixture_name(source_id, section_id)
            if write_if_changed(path, html_text):
                written += 1

            gold_section = section_id
            if section_id == wrong_section:
                position = section_ids.index(section_id)
                gold_section = section_ids[(position + 1) % len(section_ids)]
                mislabelled += 1

            articles.append(
                {
                    "article_ref": f"fixture://{source_id}/{fixture_name(source_id, section_id)}",
                    "source_id": source_id,
                    "publication_date": publication,
                    "relevant": True,
                    "quotes": [
                        {
                            "quote_text": quote_text,
                            "section_id": gold_section,
                            "human_label": True,
                        }
                    ],
                }
            )

    off_topic_count = 0
    for spec in OFFTOPIC:
        source_id = spec["source_id"]
        slug = spec["slug"]
        source_ref = f"synthetic-{source_id}-offtopic-{slug}.invalid.html"
        source = next(row for row in source_rows if row["source_id"] == source_id)
        language = LANG_FOR_CODE.get(source["language"], "en")
        publication = publication_date(source_id, slug)
        off_topic_html = build_fixture_html(
            source_id=source_id,
            source_name=source["source_name"],
            section_id=spec["section_id"],
            section_name="Off-topic control",
            publication=publication,
            language=language,
            quote_text=spec["quote_text"],
            speaker=ROLE[language][0],
            file_name=source_ref,
        )
        if write_if_changed(FIXTURES_DIR / source_id / source_ref, off_topic_html):
            written += 1
        articles.append(
            {
                "article_ref": f"fixture://{source_id}/{source_ref}",
                "source_id": source_id,
                "publication_date": publication_date(source_id, slug),
                "relevant": False,
                "quotes": [
                    {
                        "quote_text": spec["quote_text"],
                        "section_id": spec["section_id"],
                        "human_label": True,
                    }
                ],
            }
        )
        off_topic_count += 1

    gold_data = {
        "version": GOLD_VERSION,
        "notice": (
            f"SYNTHETIC GOLD SET v2: {len(source_rows)} sources x {len(section_ids)} sections "
            f"plus {off_topic_count} off-topic articles; {mislabelled} deliberately wrong "
            "section labels; Ukrainian and Russian invented quotes; .invalid URLs only."
        ),
        "split": "train: ~60%, dev: ~20%, test: ~20% by sha256(version:article_ref) hashing",
        "articles": articles,
        "negative_candidates": [
            {"quote_text": item["quote_text"], "reason": item["reason"], "human_label": False}
            for item in NEGATIVE_CANDIDATES
        ],
    }
    gold_text = json.dumps(gold_data, ensure_ascii=False, indent=2) + "\n"
    if write_if_changed(GOLD_PATH, gold_text):
        written += 1

    splits = {"train": 0, "dev": 0, "test": 0}
    for article in articles:
        splits[split_bucket(str(article["article_ref"]))] += 1

    print(f"Sources: {len(source_rows)}; sections: {len(section_ids)}")
    print(f"Fixtures + gold set written or refreshed: {written}")
    print(f"Gold articles: {len(articles)} (off-topic: {off_topic_count})")
    print(f"Deliberately wrong section labels: {mislabelled}")
    print(f"Deterministic splits: {splits}")


if __name__ == "__main__":
    main()
