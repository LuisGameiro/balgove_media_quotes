"""Standard XML Sitemap and Google News Sitemap discovery parser."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from typing import TYPE_CHECKING

from .quote_extractor import is_topic_relevant
from .rss import _local_tag, _parse_feed_date

if TYPE_CHECKING:
    from ..topics import TopicPack

__all__ = ["parse_sitemap_xml"]


def parse_sitemap_xml(
    raw_xml: bytes | str,
    *,
    start: date | None = None,
    end: date | None = None,
    require_ukraine_topic: bool = False,
    topic_pack: TopicPack | None = None,
    require_topic: bool = False,
) -> list[dict[str, str]]:
    """Parse standard XML Sitemap (``<urlset>``) and Google News Sitemap (``<news:news>``).

    Extracts ``url`` (``<loc>``), ``publication_date`` (``<news:publication_date>`` or
    ``<lastmod>``), and optional ``title`` (``<news:title>``). Filters by ``[start, end]``
    when dates are present.
    """
    enforce_topic = require_topic or require_ukraine_topic
    xml_text = raw_xml.decode("utf-8") if isinstance(raw_xml, bytes) else raw_xml
    root = ET.fromstring(xml_text)  # noqa: S314
    entries: list[dict[str, str]] = []

    for elem in root.iter():
        if _local_tag(elem.tag).lower() != "url":
            continue
        loc = ""
        lastmod = ""
        news_pub = ""
        news_title = ""
        for desc in elem.iter():
            dtag = _local_tag(desc.tag).lower()
            if dtag == "loc" and not loc:
                loc = (desc.text or "").strip()
            elif dtag == "lastmod" and not lastmod:
                lastmod = (desc.text or "").strip()
            elif dtag == "publication_date" and not news_pub:
                news_pub = (desc.text or "").strip()
            elif dtag == "title" and not news_title:
                news_title = (desc.text or "").strip()
        if not loc:
            continue
        pub_date = _parse_feed_date(news_pub or lastmod)
        if start and pub_date and pub_date < start:
            continue
        if end and pub_date and pub_date > end:
            continue
        if enforce_topic and not is_topic_relevant(
            news_title, topic_pack=topic_pack, title=news_title, url=loc
        ):
            continue
        entries.append(
            {
                "url": loc,
                "title": news_title,
                "publication_date": pub_date.isoformat() if pub_date else "",
            }
        )
    return entries
