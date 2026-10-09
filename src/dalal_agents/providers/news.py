"""News (Google News RSS) and open-web search (Tavily if keyed, else Bing RSS)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

import feedparser

from ..http import HttpClient


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


class NewsProvider:
    name = "google-news"
    URL = "https://news.google.com/rss/search"

    def __init__(self, http: HttpClient):
        self.http = http

    def search(self, query: str, days: int = 60, limit: int = 40) -> list[dict]:
        q = f"{query} when:{days}d"
        xml = self.http.get_bytes(self.URL, params={"q": q, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
                                  ttl_s=3 * 3600)
        return parse_news(xml, days=days, limit=limit)


def parse_news(xml: bytes | str, days: int = 60, limit: int = 40) -> list[dict]:
    feed = feedparser.parse(xml)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out, seen = [], set()
    for e in feed.entries:
        title = _strip_html(e.get("title", ""))
        publisher = (e.get("source") or {}).get("title")
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(" - " + publisher)]
        key = title.lower()[:80]
        if not title or key in seen:
            continue
        seen.add(key)
        try:
            dt = parsedate_to_datetime(e.get("published"))
        except Exception:
            dt = None
        if dt and dt < cutoff:
            continue
        out.append({"title": title, "url": e.get("link"), "publisher": publisher,
                    "published_at": dt.isoformat() if dt else None,
                    "date": dt.date().isoformat() if dt else None})
    out.sort(key=lambda x: x["date"] or "", reverse=True)
    return out[:limit]


class WebSearchProvider:
    """Open-web search for forward-looking information (capex, guidance, expansion…)."""

    def __init__(self, http: HttpClient, tavily_api_key: str | None = None):
        self.http = http
        self.tavily_api_key = tavily_api_key
        self.name = "tavily" if tavily_api_key else "bing"

    def search(self, query: str, limit: int = 8) -> list[dict]:
        if self.tavily_api_key:
            try:
                return self._tavily(query, limit)
            except Exception:
                pass
        return self._bing(query, limit)

    def _tavily(self, query: str, limit: int) -> list[dict]:
        res = self.http.post_json("https://api.tavily.com/search", {
            "api_key": self.tavily_api_key, "query": query, "max_results": limit,
            "search_depth": "advanced", "include_answer": False,
        })
        return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content", "")[:800]}
                for r in res.get("results", [])]

    def _bing(self, query: str, limit: int) -> list[dict]:
        xml = self.http.get_bytes("https://www.bing.com/search",
                                  params={"format": "rss", "q": query, "cc": "IN"}, ttl_s=24 * 3600)
        return parse_bing(xml, limit)


def parse_bing(xml: bytes | str, limit: int = 8) -> list[dict]:
    feed = feedparser.parse(xml)
    return [{"title": _strip_html(e.get("title", "")), "url": e.get("link"),
             "snippet": _strip_html(e.get("summary", ""))} for e in feed.entries[:limit]]
