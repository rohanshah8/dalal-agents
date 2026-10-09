"""Text agents: concall transcript analysis and news/web intelligence."""
from __future__ import annotations

import json

from ..analytics.text import forward_looking, split_transcript, tag_events, tone
from ..models import Source
from .base import Agent

CONCALL_SYSTEM = """You are a senior Indian equity research analyst reading an earnings-call transcript.
Extract ONLY what is explicitly stated. Every item must include a short verbatim quote (≤ 25 words)
copied exactly from the transcript. Never invent numbers. If something is not discussed, return an empty list.
At most 6 items per list; prefer items with numbers (growth, margins, capex, deal values, headcount)."""

CONCALL_USER = """Company: {name}
Transcript ({date}) — management remarks and Q&A (truncated):
<<<
{text}
>>>

Return JSON:
{{
  "guidance": [{{"topic": "revenue|margin|capex|demand|other", "statement": "...", "quote": "..."}}],
  "growth_drivers": [{{"statement": "...", "quote": "..."}}],
  "new_initiatives": [{{"statement": "...", "quote": "..."}}],
  "risks": [{{"statement": "...", "quote": "..."}}],
  "analyst_concerns": [{{"statement": "...", "quote": "..."}}],
  "capex_or_investment": [{{"statement": "...", "quote": "..."}}],
  "management_tone": "confident|cautious|mixed|defensive",
  "one_line_takeaway": "..."
}}"""


class ConcallAgent(Agent):
    name = "concall"
    description = "Earnings-call transcripts: guidance, plans, risks, tone shift"

    def execute(self) -> None:
        if self.ctx.settings.n_concalls == 0:
            self.finding.data = {"calls": []}
            self.finding.summary = "Transcript analysis disabled"
            return
        d = self.data()
        calls = [x for x in d.documents if x.kind == "concall"]
        if not calls:
            raise LookupError("no concall transcripts listed")
        n = self.ctx.settings.n_concalls
        # Download candidates in parallel (a couple of spares in case some PDFs fail), keep newest-first order.
        from concurrent.futures import ThreadPoolExecutor
        cands = calls[: n + 3]

        def fetch(doc):
            try:
                text = self.ctx.docs.pdf_text(doc.url)
            except Exception as e:
                return doc, None, f"{doc.date}: {e}"
            if len(text) < 2000:
                return doc, None, f"{doc.date}: transcript text too short (scanned PDF?)"
            return doc, text, None

        with ThreadPoolExecutor(max_workers=3) as pool:
            fetched = list(pool.map(fetch, cands))
        texts, seen_dates = [], set()
        for doc, text, err in fetched:
            if err:
                self.finding.errors.append(err)
            elif doc.date not in seen_dates:  # same quarter listed twice (BSE + company site)
                seen_dates.add(doc.date)
                texts.append((doc, text))
        texts = texts[:n]
        with ThreadPoolExecutor(max_workers=max(1, len(texts))) as pool:
            results = list(pool.map(lambda dt: self._analyse(*dt), texts))
        if not results:
            raise LookupError("could not read any transcript")
        if len(results) >= 2:
            a, b = results[0]["tone"]["net_tone"], results[1]["tone"]["net_tone"]
            shift = a - b
            self.finding.data["tone_shift"] = shift
            self.fact("concall_tone_shift", "Concall net-tone change vs previous call", shift * 100,
                      Source(provider="dalal-agents lexicon", title="Tone computed from transcripts"), "pp",
                      results[0]["date"])
        self.finding.data["calls"] = results
        if self.finding.errors:
            self.finding.status = "partial"
        self.finding.summary = f"{len(results)} transcript(s) analysed"

    def _analyse(self, doc, text: str) -> dict:
        src = Source(provider="bse/company filing", url=doc.url, title=f"Concall transcript {doc.date}")
        mgmt, qa = split_transcript(text)
        t_all, t_mgmt, t_qa = tone(text), tone(mgmt), tone(qa) if qa else None
        out: dict = {"date": doc.date, "url": doc.url, "n_chars": len(text), "tone": t_all,
                     "tone_management": t_mgmt, "tone_qa": t_qa}
        self.fact(f"concall_tone_{doc.date}", f"Concall net tone ({doc.date}), −100..+100",
                  t_all["net_tone"] * 100, Source(provider="dalal-agents lexicon", url=doc.url,
                                                  title=f"Tone computed from transcript {doc.date}"), "", doc.date)
        if self.ctx.llm.enabled:
            budget = 60000
            body = (mgmt[: int(budget * 0.55)] + "\n\n[Q&A]\n" + qa[: int(budget * 0.45)]) if qa else text[:budget]
            try:
                j = self.ctx.llm.complete_json(CONCALL_SYSTEM, CONCALL_USER.format(
                    name=self.data().profile.name, date=doc.date, text=body), max_tokens=6000)
                out["extraction"] = self._ground(j, text, src, doc.date)
                out["method"] = "llm"
                return out
            except Exception as e:
                self.finding.errors.append(f"LLM extraction {doc.date}: {e}")
        out["extraction"] = {"forward_looking": []}
        for s in forward_looking(text, max_items=10):
            eid = self.excerpt(s, src, kind="transcript", date=doc.date)
            out["extraction"]["forward_looking"].append({"statement": s, "quote": s, "excerpt_id": eid})
        out["method"] = "keyword"
        return out

    def _ground(self, j: dict, text: str, src: Source, date: str) -> dict:
        """Keep only items whose quote actually appears in the transcript (anti-hallucination)."""
        norm_text = _norm(text)
        clean: dict = {}
        dropped = 0
        for key, items in j.items():
            if not isinstance(items, list):
                clean[key] = items
                continue
            kept = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                q = str(it.get("quote", ""))
                if q and _quote_found(_norm(q), norm_text):
                    it["excerpt_id"] = self.excerpt(f"{it.get('statement', '')} — “{q}”", src,
                                                    kind="transcript", date=date)
                    kept.append(it)
                else:
                    dropped += 1
            clean[key] = kept
        clean["_dropped_ungrounded"] = dropped
        return clean


def _norm(s: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in s).split())


def _quote_found(q: str, text: str) -> bool:
    if not q:
        return False
    if q in text:
        return True
    words = q.split()
    if len(words) < 6:
        return False
    # tolerate PDF line-break artefacts: require a long contiguous chunk to match
    chunk = max(6, int(len(words) * 0.6))
    return any(" ".join(words[i:i + chunk]) in text for i in range(0, len(words) - chunk + 1))


NEWS_SYSTEM = """You are an equity research analyst. Summarise the news and web results about a listed
Indian company into themes. Use ONLY the provided items and cite each claim with its [E#] id.
Do not give investment advice."""

NEWS_USER = """Company: {name}
Items:
{items}

Return JSON:
{{
  "themes": [{{"theme": "...", "summary": "... [E#]", "sentiment": "positive|negative|neutral",
               "evidence": ["E#", ...]}}],
  "future_plans": [{{"plan": "... [E#]", "evidence": ["E#"]}}],
  "red_flags": [{{"flag": "... [E#]", "evidence": ["E#"]}}]
}}"""


class NewsWebAgent(Agent):
    name = "news"
    description = "News flow, sentiment, event tags and forward-looking plans from the open web"

    WEB_QUERIES = [
        "{name} capex expansion plan",
        "{name} management guidance outlook FY27",
        "{name} order book new contracts",
        "{name} strategy future plans",
    ]

    def execute(self) -> None:
        d = self.data()
        name = _short_name(d.profile.name)
        items = []
        try:
            items = self.ctx.news.search(f'"{name}"', days=self.ctx.settings.news_days)
        except Exception as e:
            self.finding.errors.append(f"news: {e}")
        if len(items) < 5:
            try:
                items += self.ctx.news.search(f"{self.symbol} share", days=self.ctx.settings.news_days)
            except Exception:
                pass
        news_out = []
        for it in items[:30]:
            src = Source(provider=f"google-news/{it.get('publisher') or 'unknown'}", url=it["url"],
                         title=it.get("publisher"))
            eid = self.excerpt(it["title"], src, kind="news", date=it.get("date"))
            t = tone(it["title"])
            news_out.append(it | {"excerpt_id": eid, "tone": t["net_tone"], "tags": tag_events(it["title"])})

        web_out = []
        for q in self.WEB_QUERIES:
            try:
                res = self.ctx.web.search(q.format(name=name), limit=5)
            except Exception as e:
                self.finding.errors.append(f"web '{q}': {e}")
                continue
            for r in res:
                text = f"{r.get('title', '')}: {r.get('snippet', '')}".strip()
                if len(text) < 40 or any(w["url"] == r.get("url") for w in web_out):
                    continue
                eid = self.excerpt(text, Source(provider=self.ctx.web.name, url=r.get("url"), title=r.get("title")),
                                   kind="web")
                web_out.append(r | {"excerpt_id": eid, "query": q.format(name=name)})

        if not news_out and not web_out:
            raise LookupError("no news or web results")
        # Average over ALL headlines (neutral = 0) so a single positive headline doesn't read as +100.
        tones = [n["tone"] for n in news_out]
        avg = sum(tones) / len(tones) if tones else 0.0
        pos = sum(1 for n in news_out if n["tone"] > 0)
        neg = sum(1 for n in news_out if n["tone"] < 0)
        lex = Source(provider="dalal-agents lexicon", title="Headline tone (finance lexicon)")
        self.fact("news_count", f"News headlines (last {self.ctx.settings.news_days} days)", len(news_out), lex)
        self.fact("news_tone", "Average headline net tone (−100..+100)", avg * 100, lex)
        self.fact("news_pos", "Positive-tone headlines", pos, lex)
        self.fact("news_neg", "Negative-tone headlines", neg, lex)
        tags: dict[str, int] = {}
        for n in news_out:
            for t in n["tags"]:
                tags[t] = tags.get(t, 0) + 1
        self.finding.data = {"news": news_out, "web": web_out, "avg_tone": avg, "pos": pos, "neg": neg,
                             "event_counts": tags}
        if self.ctx.llm.enabled:
            listing = "\n".join(
                f"[{x['excerpt_id']}] ({x.get('date') or 'web'}) {x.get('title')}"
                + (f" — {x.get('snippet', '')[:300]}" if x.get("snippet") else "")
                for x in news_out[:25] + web_out[:15])
            try:
                self.finding.data["llm"] = self.ctx.llm.complete_json(
                    NEWS_SYSTEM, NEWS_USER.format(name=d.profile.name, items=listing), max_tokens=2500)
            except Exception as e:
                self.finding.errors.append(f"LLM news synthesis: {e}")
        if self.finding.errors:
            self.finding.status = "partial"
        self.finding.summary = f"{len(news_out)} headlines (net tone {avg:+.2f}), {len(web_out)} web results"


def _short_name(name: str) -> str:
    for suf in (" Ltd", " Limited", " Ltd."):
        if name.endswith(suf):
            name = name[: -len(suf)]
    return name.strip()


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)
