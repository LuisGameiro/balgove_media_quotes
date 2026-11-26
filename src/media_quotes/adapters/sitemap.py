"""Standard XML Sitemap and Google News Sitemap discovery parser."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import defusedxml.ElementTree as ET

from ..text_utils import local_xml_tag as _local_tag
from .quote_extractor import is_topic_relevant
from .rss import _parse_feed_date

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
    max_bytes: int = 10_000_000,
    max_items: int | None = None,
) -> list[dict[str, str]]:
    """Parse standard XML Sitemap (``<urlset>``) and Google News Sitemap (``<news:news>``).

    Extracts ``url`` (``<loc>``), ``publication_date`` (``<news:publication_date>`` or
    ``<lastmod>``), and optional ``title`` (``<news:title>``). Filters by ``[start, end]``
    when dates are present.
    """
    raw_len = len(raw_xml) if isinstance(raw_xml, bytes) else len(raw_xml.encode("utf-8"))
    if raw_len > max_bytes:
        raise ValueError(f"Sitemap XML payload exceeds size limit ({raw_len} > {max_bytes} bytes)")

    enforce_topic = require_topic or require_ukraine_topic
    xml_text = raw_xml.decode("utf-8") if isinstance(raw_xml, bytes) else raw_xml
    root = ET.fromstring(xml_text)
    entries: list[dict[str, str]] = []

    for elem in root.iter():
        if max_items is not None and len(entries) >= max_items:
            break
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
