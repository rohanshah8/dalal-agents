"""Screener.in provider — 10+ years of Indian financials, shareholding, peers and documents.

Only robots.txt-allowed paths are fetched (company pages, peers API, announcements),
throttled and cached. All amounts are ₹ crore as published by Screener.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..http import HttpClient
from ..models import CompanyData, CompanyProfile, Document, Peer, Source, Table

BASE = "https://www.screener.in"


def parse_number(text: str | None) -> float | None:
    """'7,41,925' → 741925.0 · '26%' → 26.0 · '-3.5' → -3.5 · '' → None."""
    if text is None:
        return None
    t = text.strip().replace(",", "").replace("%", "").replace("₹", "").replace("Cr.", "").strip()
    if t in ("", "-", "—", "--"):
        return None
    m = re.match(r"^-?\d+(\.\d+)?", t)
    return float(m.group(0)) if m else None


def _clean_label(text: str) -> str:
    return text.replace("\xa0", " ").rstrip("+").strip()


def parse_table(section) -> Table | None:
    if section is None:
        return None
    table = section.select_one("table.data-table") or section.select_one("table")
    if table is None:
        return None
    periods = [th.get_text(strip=True) for th in table.select("thead th")][1:]
    rows: dict[str, list[float | None]] = {}
    for tr in table.select("tbody tr"):
        tds = tr.select("td")
        if not tds:
            continue
        label = _clean_label(tds[0].get_text(" ", strip=True))
        if not label or label.lower().startswith("raw pdf"):
            continue
        rows[label] = [parse_number(td.get_text(strip=True)) for td in tds[1:]]
    if not periods or not rows:
        return None
    return Table(periods=periods, rows=rows)


def parse_company_page(html: str, symbol: str, url: str, consolidated: bool) -> CompanyData:
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.select_one("h1")
    name = h1.get_text(strip=True) if h1 else symbol

    top: dict[str, float | None] = {}
    for li in soup.select("#top-ratios li"):
        n, nums = li.select_one(".name"), li.select(".number")
        if not n or not nums:
            continue
        label = n.get_text(strip=True)
        if label == "High / Low" and len(nums) >= 2:
            top["52W High"] = parse_number(nums[0].get_text(strip=True))
            top["52W Low"] = parse_number(nums[1].get_text(strip=True))
        else:
            top[label] = parse_number(nums[0].get_text(strip=True))

    about_el = soup.select_one(".company-profile .about") or soup.select_one(".about")
    about = about_el.get_text(" ", strip=True) if about_el else None

    website = None
    for a in soup.select(".company-links a, .company-info a"):
        if a.get_text(strip=True).lower() == "website":
            website = a.get("href")

    sector_path = [a.get_text(strip=True) for a in soup.select('#peers a[href^="/market/"]')]

    ids = soup.select_one("[data-company-id]")
    company_id = ids.get("data-company-id") if ids else None
    wh = soup.select_one("[data-warehouse-id]")
    warehouse_id = wh.get("data-warehouse-id") if wh else None

    growth: dict[str, dict[str, float | None]] = {}
    for rt in soup.select(".ranges-table"):
        th = rt.select_one("th")
        if not th:
            continue
        cells = [td.get_text(strip=True) for td in rt.select("td")]
        growth[th.get_text(strip=True)] = {
            cells[i].rstrip(":"): parse_number(cells[i + 1]) for i in range(0, len(cells) - 1, 2)
        }

    tables = {sid: parse_table(soup.select_one(f"section#{sid}"))
              for sid in ("quarters", "profit-loss", "balance-sheet", "cash-flow", "ratios")}
    shp = parse_table(soup.select_one("#quarterly-shp")) or parse_table(soup.select_one("#shareholding"))

    pl = tables["profit-loss"]
    bs = tables["balance-sheet"]
    is_fin = bool((pl and pl.get("Financing Profit")) or (bs and bs.get("Deposits")))

    docs: list[Document] = []
    seen_urls: set[str] = set()
    for li in soup.select("#documents .concalls li"):
        date_el = li.select_one("div")
        date = date_el.get_text(strip=True) if date_el else None
        for a in li.select("a[href]"):
            text = a.get_text(strip=True).lower()
            if a["href"] in seen_urls or text not in ("transcript", "ppt", "notes"):
                continue  # skip audio recordings, duplicates
            seen_urls.add(a["href"])
            kind = "concall" if text in ("transcript", "notes") else "presentation"
            docs.append(Document(kind=kind, title=f"{a.get_text(strip=True)} {date or ''}".strip(),
                                 url=a["href"], date=date))
    for a in soup.select("#documents .annual-reports a[href]"):
        docs.append(Document(kind="annual_report", title=a.get_text(" ", strip=True), url=a["href"],
                             date=re.sub(r"\D", "", a.get_text()) or None))
    for a in soup.select("#documents .credit-ratings a[href]"):
        docs.append(Document(kind="credit_rating", title=a.get_text(" ", strip=True), url=a["href"]))

    profile = CompanyProfile(
        symbol=symbol, name=name, about=about, website=website, sector_path=sector_path,
        is_financial=is_fin, consolidated=consolidated, screener_url=url,
        screener_company_id=company_id, screener_warehouse_id=warehouse_id, top_ratios=top,
        pros=[li.get_text(" ", strip=True) for li in soup.select(".pros li")],
        cons=[li.get_text(" ", strip=True) for li in soup.select(".cons li")],
        growth_ranges=growth,
    )
    return CompanyData(
        profile=profile, quarters=tables["quarters"], profit_loss=pl, balance_sheet=bs,
        cash_flow=tables["cash-flow"], ratios=tables["ratios"], shareholding=shp, documents=docs,
        source=Source(provider="screener.in", url=url, title=f"Screener.in — {name}"),
    )


def parse_peers(html: str) -> list[Peer]:
    soup = BeautifulSoup(html, "lxml")
    headers = [th.get_text(" ", strip=True) for th in soup.select("tr th")]
    col = {h: i for i, h in enumerate(headers)}

    def pick(cells, *names):
        for n in names:
            for h, i in col.items():
                if h.lower().startswith(n.lower()) and i < len(cells):
                    return parse_number(cells[i])
        return None

    peers = []
    for tr in soup.select("tr"):
        tds = tr.select("td")
        a = tr.select_one("a[href*='/company/']")
        if not tds or a is None:
            continue
        cells = [td.get_text(strip=True) for td in tds]
        href = a["href"]
        m = re.search(r"/company/([^/]+)/", href)
        peers.append(Peer(
            symbol=m.group(1) if m else a.get_text(strip=True), name=a.get_text(strip=True),
            url=BASE + href, rank=int(parse_number(cells[0]) or 0) or None,
            price=pick(cells, "CMP"), pe=pick(cells, "P/E"), market_cap=pick(cells, "Mar Cap"),
            div_yield=pick(cells, "Div Yld"), np_qtr=pick(cells, "NP Qtr"),
            qtr_profit_var=pick(cells, "Qtr Profit Var"), sales_qtr=pick(cells, "Sales Qtr"),
            qtr_sales_var=pick(cells, "Qtr Sales Var"), roce=pick(cells, "ROCE"),
        ))
    return peers


def parse_announcements(html: str) -> list[Document]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for li in soup.select("li"):
        a = li.select_one("a[href]")
        if not a:
            continue
        parts = [p.strip() for p in li.get_text(" | ", strip=True).split("|") if p.strip()]
        title = parts[0] if parts else a.get_text(strip=True)
        date = next((p for p in parts[1:] if re.match(r"^\d{1,2} \w{3}( \d{4})?$", p)), None)
        detail = " ".join(p for p in parts[1:] if p != date).lstrip("- ").strip()
        out.append(Document(kind="announcement", title=f"{title}{' — ' + detail if detail else ''}",
                            url=a["href"], date=date))
    return out


class ScreenerProvider:
    name = "screener.in"

    def __init__(self, http: HttpClient):
        self.http = http

    def search(self, query: str) -> list[dict]:
        import json
        txt = self.http.get_text(f"{BASE}/api/company/search/", params={"q": query, "v": "3"},
                                 ttl_s=7 * 86400)
        return json.loads(txt)

    def company(self, symbol: str, consolidated: bool = True) -> CompanyData:
        symbol = symbol.upper().strip()
        order = [True, False] if consolidated else [False]
        last_err: Exception | None = None
        for cons in order:
            url = f"{BASE}/company/{symbol}/" + ("consolidated/" if cons else "")
            try:
                html = self.http.get_text(url)
            except Exception as e:  # 404 etc.
                last_err = e
                continue
            data = parse_company_page(html, symbol, url, cons)
            # Some companies have an empty consolidated view: fall back to standalone.
            if cons and (data.profit_loss is None or not data.profit_loss.rows):
                last_err = ValueError("empty consolidated financials")
                continue
            # Banks/NBFCs: consolidated numbers mix in insurance/AMC subsidiaries, which distorts
            # financing margin and drops NPA ratios. Use the standalone (lender-only) view.
            if cons and data.profile.is_financial:
                try:
                    url_sa = f"{BASE}/company/{symbol}/"
                    sa = parse_company_page(self.http.get_text(url_sa), symbol, url_sa, False)
                    if sa.profit_loss is not None and sa.profit_loss.rows:
                        return sa
                except Exception:
                    pass
            return data
        raise LookupError(f"Screener.in has no page for '{symbol}': {last_err}")

    def peers(self, data: CompanyData) -> list[Peer]:
        wid = data.profile.screener_warehouse_id
        if not wid:
            return []
        html = self.http.get_text(f"{BASE}/api/company/{wid}/peers/",
                                  headers={"Referer": data.profile.screener_url or BASE})
        return parse_peers(html)

    def announcements(self, data: CompanyData) -> list[Document]:
        cid = data.profile.screener_company_id
        if not cid:
            return []
        out, seen = [], set()
        for kind in ("recent",):  # "important" requires login
            try:
                docs = parse_announcements(self.http.get_text(f"{BASE}/announcements/{kind}/{cid}/"))
            except Exception:
                continue
            for doc in docs:
                if doc.url not in seen:
                    seen.add(doc.url)
                    out.append(doc)
        return out
