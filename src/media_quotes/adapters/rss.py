"""RSS 2.0 and Atom XML feed discovery parser."""

from __future__ import annotations

import contextlib
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

from .quote_extractor import is_topic_relevant

if TYPE_CHECKING:
    from ..topics import TopicPack

__all__ = ["parse_rss_feed"]


def _parse_feed_date(raw_date: str) -> date | None:
    """Parse RFC-2822 (RSS ``<pubDate>``) or ISO-8601 (Atom/Sitemap) date strings into ``date``."""
    cleaned = raw_date.strip()
    if not cleaned:
        return None
    with contextlib.suppress(Exception):
        return date.fromisoformat(cleaned[:10])
    with contextlib.suppress(Exception):
        return parsedate_to_datetime(cleaned).date()
    with contextlib.suppress(Exception):
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).date()
    return None


def _local_tag(tag: str) -> str:
    """Strip XML namespace ``{http://...}name`` -> ``name``."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_rss_feed(
    raw_xml: bytes | str,
    *,
    start: date | None = None,
    end: date | None = None,
    require_ukraine_topic: bool = False,
    topic_pack: TopicPack | None = None,
    require_topic: bool = False,
) -> list[dict[str, str]]:
    """Parse RSS 2.0 (``<item>``) or Atom (``<entry>``) XML and return discovered article entries.

    Each entry contains ``url``, ``title``, and ``publication_date`` (ISO ``YYYY-MM-DD``).
    When ``start`` / ``end`` are provided, only articles within ``[start, end]`` are returned.
    """
    enforce_topic = require_topic or require_ukraine_topic
    xml_text = raw_xml.decode("utf-8") if isinstance(raw_xml, bytes) else raw_xml
    root = ET.fromstring(xml_text)  # noqa: S314
    items: list[dict[str, str]] = []

    for elem in root.iter():
        tag = _local_tag(elem.tag).lower()
        if tag not in {"item", "entry"}:
            continue
        url = ""
        title = ""
        pub_raw = ""
        for child in elem:
            ctag = _local_tag(child.tag).lower()
            if ctag == "link":
                href = child.attrib.get("href", "").strip()
                rel = child.attrib.get("rel", "alternate").strip()
                text_url = (child.text or "").strip()
                if href and (rel in {"alternate", ""} or not url):
                    url = href
                elif text_url and not url:
                    url = text_url
            elif ctag == "title" and not title:
                title = (child.text or "").strip()
            elif ctag in {"pubdate", "published", "updated", "date"} and not pub_raw:
                pub_raw = (child.text or "").strip()
        if not url:
            continue
        pub_date = _parse_feed_date(pub_raw)
        if start and pub_date and pub_date < start:
            continue
        if end and pub_date and pub_date > end:
            continue
        if enforce_topic and not is_topic_relevant(
            title, topic_pack=topic_pack, title=title, url=url
        ):
            continue
        items.append(
            {
                "url": url,
                "title": title,
                "publication_date": pub_date.isoformat() if pub_date else "",
            }
        )
    return items
