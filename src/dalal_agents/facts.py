"""FactStore: the single source of truth the LLM is allowed to cite."""
from __future__ import annotations

import math
import threading

from .models import Excerpt, Fact, Source


class FactStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.facts: dict[str, Fact] = {}
        self.excerpts: dict[str, Excerpt] = {}
        self._by_key: dict[str, str] = {}

    def add(
        self,
        key: str,
        label: str,
        value,
        source: Source,
        unit: str = "",
        period: str | None = None,
        company: str | None = None,
    ) -> str | None:
        """Register a fact and return its ID. None/NaN values are skipped (returns None)."""
        if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
            return None
        if isinstance(value, float):
            value = round(value, 4)
        with self._lock:
            if key in self._by_key:  # idempotent re-adds update in place
                fid = self._by_key[key]
                self.facts[fid] = self.facts[fid].model_copy(update={"value": value})
                return fid
            fid = f"F{len(self.facts) + 1}"
            self.facts[fid] = Fact(
                id=fid, key=key, label=label, value=value, unit=unit, period=period,
                company=company, source=source,
            )
            self._by_key[key] = fid
            return fid

    def excerpt(self, text: str, source: Source, kind: str = "other", date: str | None = None,
                company: str | None = None) -> str:
        with self._lock:
            eid = f"E{len(self.excerpts) + 1}"
            self.excerpts[eid] = Excerpt(id=eid, kind=kind, text=text.strip(), date=date,
                                         company=company, source=source)
            return eid

    def get(self, ref: str) -> Fact | Excerpt | None:
        return self.facts.get(ref) or self.excerpts.get(ref)

    def by_key(self, key: str) -> Fact | None:
        fid = self._by_key.get(key)
        return self.facts.get(fid) if fid else None

    # ---- rendering for prompts -------------------------------------------------
    def render_facts(self, company: str | None = None, ids: list[str] | None = None) -> str:
        lines = []
        for f in self.facts.values():
            if company and f.company != company:
                continue
            if ids is not None and f.id not in ids:
                continue
            period = f" ({f.period})" if f.period else ""
            lines.append(f"[{f.id}] {f.company or ''} · {f.label}{period}: {fmt_value(f.value, f.unit)}")
        return "\n".join(lines)

    def render_excerpts(self, ids: list[str] | None = None, max_chars: int = 600) -> str:
        lines = []
        for e in self.excerpts.values():
            if ids is not None and e.id not in ids:
                continue
            date = f" {e.date}" if e.date else ""
            text = e.text if len(e.text) <= max_chars else e.text[:max_chars] + "…"
            lines.append(f"[{e.id}] ({e.kind}{date}, {e.source.title or e.source.provider}) {text}")
        return "\n".join(lines)


def fmt_value(value, unit: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, str):
        return value
    if unit == "%":
        return f"{value:.1f}%"
    if unit == "₹ cr":
        return f"₹{value:,.0f} cr"
    if unit == "₹":
        return f"₹{value:,.2f}"
    if unit == "x":
        return f"{value:.2f}x"
    if unit == "pp":
        return f"{value:+.2f} pp"
    if unit == "days":
        return f"{value:.0f} days"
    if float(value).is_integer() and not unit:
        return f"{int(value):,}"
    if abs(value) >= 1000:
        return f"{value:,.0f}{(' ' + unit) if unit else ''}"
    return f"{value:.2f}{(' ' + unit) if unit else ''}"
