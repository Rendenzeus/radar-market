"""Headlines from credible publishers plus a lightweight sentiment score.

Sources (no API keys):
- Google News RSS, restricted with ``site:`` filters to established outlets
  (Reuters, Bloomberg, WSJ, FT, AP, CNBC, Kitco, MarketWatch, FXStreet, ...).
  Unfiltered Google News returns many low-quality sites, hence the whitelist.
- Direct RSS feeds from CoinDesk and The Block for crypto.

Sentiment is a finance-specific keyword model (no ML dependency, so it runs
inside the Android build). It is a rough directional read of headlines, not
an understanding of the articles.
"""

from __future__ import annotations

import math
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

from data import _get_text

TOP_TIER = {"reuters", "bloomberg", "bloomberg.com", "wsj", "the wall street journal", "financial times", "ap news", "cnbc"}
MAX_AGE_HOURS = 48
HALF_LIFE_HOURS = 6


@dataclass(frozen=True)
class Headline:
    title: str
    source: str
    url: str
    published: float  # epoch seconds
    sentiment: float  # -1 .. +1 for the asset's price


@dataclass(frozen=True)
class NewsSummary:
    headlines: list[Headline]
    score: float  # recency/source weighted average, -1 .. +1
    recent: float  # last 3 hours
    earlier: float  # 3-24 hours ago
    fetched_at: float

    @property
    def shift(self) -> float:
        """Positive when sentiment improved over the last hours."""
        return self.recent - self.earlier


def _google(query: str, sites: list[str]) -> str:
    q = f"{query} (" + " OR ".join(f"site:{s}" for s in sites) + ") when:2d"
    return "https://news.google.com/rss/search?" + urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})


GOLD_SITES = [
    "reuters.com", "bloomberg.com", "cnbc.com", "kitco.com", "wsj.com", "ft.com",
    "marketwatch.com", "apnews.com", "fxstreet.com", "investing.com",
]
CRYPTO_SITES = ["reuters.com", "bloomberg.com", "cnbc.com", "coindesk.com", "theblock.co", "wsj.com", "ft.com", "apnews.com"]

FEEDS = {
    "GC=F": {
        "sources": [(_google("gold price OR gold futures OR bullion", GOLD_SITES), None)],
        "keywords": re.compile(r"\b(gold|bullion|xau|precious metal)", re.I),
    },
    "BTCUSDT": {
        "sources": [
            (_google("bitcoin OR btc", CRYPTO_SITES), None),
            ("https://www.coindesk.com/arc/outboundfeeds/rss/", "CoinDesk"),
            ("https://www.theblock.co/rss.xml", "The Block"),
        ],
        "keywords": re.compile(r"\b(bitcoin|btc|crypto)", re.I),
    },
}

# ----------------------------------------------------------------- sentiment
_UP = {
    "rise", "rises", "rising", "rose", "gain", "gains", "gained", "jump", "jumps", "jumped", "climb", "climbs",
    "climbed", "rally", "rallies", "rallied", "surge", "surges", "surged", "soar", "soars", "soared", "rebound",
    "rebounds", "rebounded", "recover", "recovers", "recovered", "advance", "advances", "advanced", "higher",
    "bullish", "breaks", "bounce", "bounces", "bounced", "extends", "tops", "firms", "steadies", "up",
}
_DOWN = {
    "fall", "falls", "falling", "fell", "drop", "drops", "dropped", "slip", "slips", "slipped", "slide", "slides",
    "slid", "tumble", "tumbles", "tumbled", "plunge", "plunges", "plunged", "sink", "sinks", "sank", "retreat",
    "retreats", "decline", "declines", "declined", "slump", "slumps", "lower", "bearish", "crash", "crashes",
    "crashed", "dips", "dipped", "eases", "eased", "loses", "lost", "down", "weakens", "hit", "hits",
}
_ASSET = {
    "GC=F": {"gold", "gold's", "bullion", "xau", "xau/usd", "precious", "metals"},
    "BTCUSDT": {"bitcoin", "bitcoin's", "btc", "crypto", "crypto's", "cryptocurrency", "cryptocurrencies", "btc/usd"},
}
_WINDOW = 6  # tokens after the asset name that a price verb may refer to
_CLAUSE = re.compile(r"[,;:]|\s(?:as|while|after|amid|but|despite|though|even as|with|on)\s")

# Phrases that carry direction wherever they appear (applied to every market).
_PHRASES = [
    (r"record high|all-time high|best (week|month|day)|(\d+|multi)[- ](week|month|year) high|weekly gain", +0.8),
    (r"sell[- ]?off|worst (week|month|day)|(\d+|multi|one|three|two)[- ](week|month|year) low|lowest|weekly loss(es)?|steep loss", -0.8),
    (r"(rebound|recover|bounce)\w* from", +0.8),  # outweighs the "x-month low" it bounced from
    (r"raises? .{0,30}target|upgrade", +0.5),
    (r"cuts? .{0,30}target|downgrade", -0.5),
]

DRIVERS = {
    "GC=F": [
        (r"rate cuts?|cuts? rates|dovish|easing", +0.6),
        (r"safe[- ]haven|geopolitic|war|conflict|tensions?|missile|attack", +0.5),
        (r"(weak|weaker|softer|falling|slipping) dollar|dollar (falls|slips|weakens|eases)", +0.6),
        (r"yields? (fall|drop|slip|ease)", +0.4),
        (r"central bank (buying|purchases)|etf inflows?", +0.5),
        (r"rate[- ]hikes?|hawkish|higher for longer|tightening", -0.6),
        (r"(strong|stronger|firm|firmer) dollar|dollar (rises|gains|firms|strengthens|jumps)|usd strength", -0.6),
        (r"yields? (rise|jump|climb|surge)|hot inflation|inflation (heats|beats|sours)", -0.4),
        (r"profit[- ]taking|outflows?", -0.4),
    ],
    "BTCUSDT": [
        (r"etf inflows?|inflows?|approval|approves|adoption|strategic reserve|accumulat", +0.6),
        (r"deep value|oversold|bottom(ing)?|buy the dip|short squeeze", +0.5),
        (r"rate cuts?|dovish|easing", +0.4),
        (r"outflows?|lose \$[\d.]+ ?(b|billion|m|million)|liquidations?|hack|exploit|ban|crackdown|lawsuit|sues|fraud|bankrupt|insolven|money leaves", -0.6),
        (r"rate[- ]hikes?|hawkish|risk[- ]off", -0.4),
        (r"capitulat|panic|fear|hopeless|weary", -0.4),
    ],
}


def headline_sentiment(symbol: str, title: str) -> float:
    text = title.lower()
    raw = 0.0
    # Price verbs only count inside the clause that names the asset
    # ("Gold steady while yields rise": "rise" belongs to yields).
    for clause in _CLAUSE.split(text):
        tokens = re.findall(r"[a-z$'/-]+|\d[\d.]*", clause)
        assets = [i for i, t in enumerate(tokens) if t in _ASSET.get(symbol, set())]
        for i, tok in enumerate(tokens):
            if not any(a < i <= a + _WINDOW for a in assets):
                continue
            if tok in _UP:
                raw += 0.8
            elif tok in _DOWN:
                raw -= 0.8
    for pattern, weight in _PHRASES + DRIVERS.get(symbol, []):
        if re.search(pattern, text):
            raw += weight
    return math.tanh(raw / 1.5)


# -------------------------------------------------------------------- fetch
def _parse(xml_text: str, fixed_source: str | None) -> list[tuple[str, str, str, float]]:
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = item.findtext("pubDate")
        try:
            ts = parsedate_to_datetime(pub).timestamp() if pub else time.time()
        except Exception:
            ts = time.time()
        source = fixed_source or (item.findtext("source") or "").strip()
        if not fixed_source and source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        if title:
            out.append((title, source or "?", link, ts))
    return out


def _weighted(items: list[Headline], now: float, lo_h: float, hi_h: float) -> float:
    num = den = 0.0
    for h in items:
        age = (now - h.published) / 3600
        if not lo_h <= age < hi_h:
            continue
        w = (1.0 if h.source.lower() in TOP_TIER else 0.7) * 0.5 ** (age / HALF_LIFE_HOURS)
        num += w * h.sentiment
        den += w
    return num / den if den else 0.0


def fetch_news(symbol: str) -> NewsSummary:
    cfg = FEEDS[symbol]
    now = time.time()
    seen: set[str] = set()
    headlines: list[Headline] = []
    errors = 0
    for url, fixed in cfg["sources"]:
        try:
            items = _parse(_get_text(url), fixed)
        except Exception:
            errors += 1
            continue
        for title, source, link, ts in items:
            key = re.sub(r"\W+", " ", title.lower()).strip()[:80]
            if key in seen or now - ts > MAX_AGE_HOURS * 3600 or not cfg["keywords"].search(title):
                continue
            seen.add(key)
            headlines.append(Headline(title, source, link, ts, headline_sentiment(symbol, title)))
    if errors == len(cfg["sources"]):
        raise ConnectionError("Sumber berita tidak dapat dihubungi")
    headlines.sort(key=lambda h: h.published, reverse=True)
    return NewsSummary(
        headlines=headlines[:40],
        score=_weighted(headlines, now, 0, MAX_AGE_HOURS),
        recent=_weighted(headlines, now, 0, 3),
        earlier=_weighted(headlines, now, 3, 24),
        fetched_at=now,
    )
