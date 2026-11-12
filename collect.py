#!/usr/bin/env python3
"""ION Media Monitor collector.

Runs on GitHub Actions each morning. Pulls IOV price and volume, market
indices, ASX releases (with the text of register notices), news search results,
Reddit and third-party rating pages, then writes data/latest.json (plus a dated
copy in data/archive/). HotCopper is never fetched.

Standard library apart from pypdf, which reads register notice PDFs and is
optional. Every source is best effort: a failure is logged in sources[] and the
rest still runs.
"""
import datetime as dt
import html
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

MEL = ZoneInfo("Australia/Melbourne")
UTC = dt.timezone.utc
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
SOURCES = []

NEWS_QUERIES = [
    '"ION Video"',
    '"ASX:IOV" OR "ASX: IOV" OR "IOV.AX"',
    '"ion.video"',
    '"Linius Technologies" OR "Linius Video"',
    '"Finbar O\'Hanlon"',
    '"programmable video" OR "virtual video" ION',
    '"Priority Impact" Swinburne video',
]
NEWS_EDITIONS = [("AU", "en-AU", "AU:en"), ("US", "en-US", "US:en")]
# Daily runs look back 3 days; a manual backfill run (workflow input) looks back 30.
NEWS_WINDOW = os.environ.get("NEWS_WINDOW") or "3d"
BACKFILL = NEWS_WINDOW != "3d"

RATINGS = {
    "Morningstar": "https://www.morningstar.com.au/investments/security/asx/IOV",
    "Market Index": "https://www.marketindex.com.au/asx/iov",
}
# TipRanks, Simply Wall St, Stockopedia and Investing.com block GitHub's servers;
# the daily run checks those by web search on Mondays.
RATING_WORDS = re.compile(
    r"(price target|target price|fair value|consensus|analyst|rating|stockrank|"
    r"snowflake|strong buy|strong sell|\bbuy\b|\bhold\b|\bsell\b|undervalued|"
    r"overvalued|momentum|broker|coverage|star rating|moat|\brank\b|stock score)", re.I)


def get(url, accept="*/*", timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": accept, "Accept-Language": "en-AU,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def attempt(name, fn):
    try:
        value = fn()
        SOURCES.append({"source": name, "ok": True})
        return value
    except Exception as e:  # noqa: BLE001 - every source is best effort
        SOURCES.append({"source": name, "ok": False,
                        "error": f"{type(e).__name__}: {e}"[:300]})
        return None


def mel_date(ts):
    return dt.datetime.fromtimestamp(ts, UTC).astimezone(MEL).date().isoformat()


def to_iso(s):
    try:
        return parsedate_to_datetime(s).astimezone(UTC).isoformat()
    except Exception:  # noqa: BLE001
        return s


def strip_html(raw):
    raw = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


# ---------- price ----------
def yahoo_chart(symbol, rng="3mo"):
    last_err = None
    for host in ("query1", "query2"):
        url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
               f"?range={rng}&interval=1d&includePrePost=false")
        try:
            _, body = get(url, "application/json")
            return json.loads(body)["chart"]["result"][0]
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise last_err


def yahoo():
    res = yahoo_chart("IOV.AX")
    q = res["indicators"]["quote"][0]
    rows = []
    for i, ts in enumerate(res.get("timestamp") or []):
        c = q["close"][i]
        if c is None:
            continue
        rows.append({"date": mel_date(ts), "open": q["open"][i], "high": q["high"][i],
                     "low": q["low"][i], "close": round(c, 4),
                     "volume": int(q["volume"][i] or 0)})
    meta = res.get("meta", {})
    return {"sessions": rows, "meta": {k: meta.get(k) for k in (
        "regularMarketPrice", "regularMarketTime", "previousClose",
        "chartPreviousClose", "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "currency")}}


# ASX first (as at IOV's last session), then the US (usually mid-session when this runs).
INDICES = [("asx200", "ASX 200", ["^AXJO"]),
           ("asx_tech", "ASX tech", ["^AXIJ", "^AXTJ"]),
           ("sp500", "S&P 500", ["^GSPC"]),
           ("nasdaq", "Nasdaq", ["^IXIC"])]


def index_series(symbols):
    last_err = None
    for sym in symbols:
        try:
            res = yahoo_chart(sym, "1mo")
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
        meta = res.get("meta", {})
        tz = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
        closes = res["indicators"]["quote"][0]["close"]
        rows = [{"date": dt.datetime.fromtimestamp(ts, UTC).astimezone(tz).date().isoformat(),
                 "close": round(c, 2)}
                for ts, c in zip(res.get("timestamp") or [], closes) if c is not None]
        regular = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
        now = dt.datetime.now(UTC).timestamp()
        live = bool(regular) and regular.get("start", 0) <= now < regular.get("end", 0)
        return {"symbol": sym, "sessions": rows, "price": meta.get("regularMarketPrice"),
                "market_time_utc": dt.datetime.fromtimestamp(meta.get("regularMarketTime") or 0, UTC)
                .isoformat(timespec="minutes"),
                "state": "open" if live else "closed", "exchange_tz": meta.get("exchangeTimezoneName")}
    raise last_err or ValueError("no symbol")


def markets():
    out = {}
    for key, name, syms in INDICES:
        val = attempt(f"index:{name}", lambda syms=syms: index_series(syms))
        if val:
            val["name"] = name
            out[key] = val
    return out


def stooq():
    _, body = get("https://stooq.com/q/d/l/?s=iov.au&i=d", "text/csv")
    lines = [line for line in body.strip().splitlines() if line]
    if not lines or not lines[0].lower().startswith("date"):
        raise ValueError(f"unexpected stooq response: {body[:120]}")
    rows = []
    for line in lines[1:][-70:]:
        d, o, h, lo, c, v = (line.split(",") + [""] * 6)[:6]
        rows.append({"date": d, "open": float(o), "high": float(h), "low": float(lo),
                     "close": float(c), "volume": int(float(v or 0))})
    return {"sessions": rows}


def asx_json(path):
    _, body = get(f"https://asx.api.markitdigital.com/asx-research/1.0/companies/iov/{path}",
                  "application/json")
    return json.loads(body).get("data")


def get_bytes(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/pdf,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


REGISTER_KINDS = [
    ("substantial", re.compile(r"substantial hold", re.I)),
    ("director", re.compile(r"director'?s? interest", re.I)),
    ("issue", re.compile(r"application for quotation|proposed issue of securities|issue of securities|"
                         r"notification regarding unquoted|cessation of securities|cleansing", re.I)),
]


def pdf_text(url, limit=3000):
    """First part of a notice's text. Register notices are short forms; the start holds who and how much."""
    try:
        from pypdf import PdfReader  # installed by the workflow; optional
    except ImportError:
        return None
    import io
    reader = PdfReader(io.BytesIO(get_bytes(url)))
    text = " ".join((page.extract_text() or "") for page in reader.pages[:3])
    return re.sub(r"\s+", " ", text).strip()[:limit]


def asx_announcements():
    data = asx_json("announcements?count=20&market_sensitive=false")
    items = data.get("items", []) if isinstance(data, dict) else []
    out = []
    cutoff = (dt.datetime.now(UTC) - dt.timedelta(days=35)).isoformat()
    for it in items:
        key = it.get("documentKey")
        url = (f"https://cdn-api.markitdigital.com/apiman-gateway/ASX/asx-research/1.0/file/{key}"
               "?access_token=83ff96335c2d45a094df02a206a39ff4") if key else \
            "https://www.asx.com.au/markets/trade-our-cash-market/announcements.iov"
        row = {"date": it.get("date"), "headline": it.get("headline"),
               "price_sensitive": it.get("isPriceSensitive"), "url": url}
        kind = next((k for k, rx in REGISTER_KINDS if rx.search(row["headline"] or "")), None)
        if kind:
            row["register_kind"] = kind
            if kind in ("substantial", "director") and key and str(row["date"]) >= cutoff:
                try:
                    row["notice_text"] = pdf_text(url)
                except Exception as e:  # noqa: BLE001
                    row["notice_error"] = f"{type(e).__name__}: {e}"[:120]
        out.append(row)
    return out


# ---------- news ----------
def google_news():
    found = {}
    for q in NEWS_QUERIES:
        for ed, hl, ceid in NEWS_EDITIONS:
            if ed == "US" and q != NEWS_QUERIES[0]:
                continue
            url = ("https://news.google.com/rss/search?q=" +
                   urllib.parse.quote(f"{q} when:{NEWS_WINDOW}") + f"&hl={hl}&gl={ed}&ceid={ceid}")
            _, body = get(url, "application/rss+xml")
            for item in ET.fromstring(body).iter("item"):
                title = (item.findtext("title") or "").strip()
                src = item.find("source")
                key = re.sub(r"\W+", "", title.lower())[:90]
                if not title or key in found:
                    continue
                found[key] = {"title": title, "link": item.findtext("link"),
                              "published": to_iso(item.findtext("pubDate") or ""),
                              "outlet": src.text if src is not None else None,
                              "outlet_url": src.get("url") if src is not None else None,
                              "query": q, "edition": ed, "via": "google_news"}
    return list(found.values())


RELEVANT = re.compile(r"\bion\b|\biov\b|linius|o'hanlon|priority impact|swinburne", re.I)


def gn_decode(link):
    """Resolve a Google News article link to the publisher URL (best effort)."""
    m = re.search(r"/articles/([^?/]+)", link or "")
    if not m:
        return None
    gid = m.group(1)
    sg = ts = None
    for page_url in (f"https://news.google.com/articles/{gid}", f"https://news.google.com/rss/articles/{gid}"):
        try:
            _, page = get(page_url, "text/html")
        except Exception:  # noqa: BLE001
            continue
        sg = re.search(r'data-n-a-sg="([^"]+)"', page)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page)
        if sg and ts:
            break
    if not (sg and ts):
        return None
    inner = ["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None,
                             None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
             gid, int(ts.group(1)), sg.group(1)]
    body = "f.req=" + urllib.parse.quote(json.dumps([[["Fbv4je", json.dumps(inner), None, "generic"]]]))
    req = urllib.request.Request(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute", data=body.encode(),
        headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    with urllib.request.urlopen(req, timeout=20) as r:
        txt = r.read().decode("utf-8", "replace")
    chunk = txt.split("\n\n", 1)[1] if "\n\n" in txt else txt
    url = json.loads(json.loads(chunk)[0][2])[1]
    return url if isinstance(url, str) and url.startswith("http") else None


def resolve_google_links(items):
    done = 0
    for it in items:
        if done >= (90 if BACKFILL else 30) or not RELEVANT.search(it.get("title") or ""):
            continue
        try:
            real = gn_decode(it.get("link"))
            if real:
                it["google_link"], it["link"] = it["link"], real
                done += 1
        except Exception as e:  # noqa: BLE001
            it["decode_error"] = f"{type(e).__name__}"[:60]
    return done


ERROR_TERMS = {
    "calls ION a compression company": r"compression (?:company|technology|tech)",
    "calls ION a platform": r"\b(?:its|the|ION's|Ion's) (?:video )?platform\b",
    "says six patent families": r"\b(?:six|6) patent families\b",
    "names Linius": r"\bLinius\b",
    "names Vattenfall": r"\bVattenfall\b",
    "spells Fossefall as Fossenfall": r"\bFossenfall\b",
    "gives the tender in US dollars": r"US\$\s?1 ?(?:million|m)\b",
}


def meta(page, *names):
    for n in names:
        m = re.search(r'<meta[^>]+(?:property|name)=["\']' + re.escape(n) +
                      r'["\'][^>]+content=["\']([^"\']+)', page, re.I) or \
            re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']' +
                      re.escape(n) + r'["\']', page, re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    return None


def enrich(items, cap=15):
    """Add the publisher's own description and any known error phrases, never article text."""
    n = 0
    for it in items:
        link = it.get("link") or ""
        if n >= cap or "news.google.com" in link or not RELEVANT.search(it.get("title") or ""):
            continue
        n += 1
        try:
            _, page = get(link, "text/html")
            text = strip_html(page)
            it["description"] = (meta(page, "og:description", "description", "twitter:description") or "")[:300]
            it["published_meta"] = meta(page, "article:published_time", "datePublished")
            it["author"] = meta(page, "author", "article:author")
            it["error_terms"] = [k for k, rx in ERROR_TERMS.items() if re.search(rx, text, re.I)]
        except Exception as e:  # noqa: BLE001
            it["enrich_error"] = f"{type(e).__name__}: {e}"[:80]
    return n


def bing_news():
    found = {}
    for q in ('"ION Video"', '"ASX: IOV"'):
        url = ("https://www.bing.com/news/search?format=rss&setlang=en-AU&cc=AU&q=" +
               urllib.parse.quote(q))
        _, body = get(url, "application/rss+xml")
        for item in ET.fromstring(body).iter("item"):
            title = (item.findtext("title") or "").strip()
            link = item.findtext("link") or ""
            real = urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get("url", [link])[0]
            key = re.sub(r"\W+", "", title.lower())[:90]
            if title and key not in found:
                found[key] = {"title": title, "link": real,
                              "published": to_iso(item.findtext("pubDate") or ""),
                              "snippet": strip_html(item.findtext("description") or "")[:300],
                              "query": q, "via": "bing_news"}
    return list(found.values())


def reddit():
    q = '"ION Video" OR "ASX:IOV" OR "ASX: IOV" OR "$IOV"'
    url = ("https://www.reddit.com/search.json?sort=new&t=week&limit=25&q=" +
           urllib.parse.quote(q))
    _, body = get(url, "application/json")
    out = []
    for ch in json.loads(body).get("data", {}).get("children", []):
        d = ch.get("data", {})
        text = f"{d.get('title', '')} {d.get('selftext', '')}"
        if not re.search(r"ion video|\bIOV\b", text, re.I):
            continue
        out.append({"title": d.get("title"), "subreddit": d.get("subreddit"),
                    "link": "https://www.reddit.com" + d.get("permalink", ""),
                    "published": dt.datetime.fromtimestamp(d.get("created_utc", 0), UTC).isoformat(),
                    "score": d.get("score"), "comments": d.get("num_comments"),
                    "text": (d.get("selftext") or "")[:500]})
    return out


# ---------- ratings ----------
def rating_page(name, url):
    status, body = get(url, "text/html")
    text = strip_html(body)
    windows, last = [], -400
    for m in RATING_WORDS.finditer(text):
        if m.start() - last < 250:
            continue
        last = m.start()
        windows.append(text[max(0, m.start() - 150): m.start() + 250])
        if len(windows) >= 8:
            break
    data_hits = re.findall(
        r'"(?:priceTarget|targetPrice|consensus\w*|fairValue\w*|stockRank\w*|'
        r'analystRating\w*|starRating|snowflake\w*|valueScore|qualityScore|momentumScore)"\s*:\s*'
        r'("[^"]{0,40}"|[-\d.]+|\{[^{}]{0,200}\})', body)
    return {"url": url, "status": status, "text_len": len(text),
            "title": strip_html(re.search(r"(?is)<title>(.*?)</title>", body).group(1))
            if re.search(r"(?is)<title>(.*?)</title>", body) else None,
            "snippets": windows, "data_hits": data_hits[:20]}


def main():
    now = dt.datetime.now(UTC)
    out = {"fetched_at_utc": now.isoformat(timespec="seconds"),
           "melbourne_date": now.astimezone(MEL).date().isoformat()}

    out["price_yahoo"] = attempt("yahoo", yahoo)
    out["price_stooq"] = attempt("stooq", stooq)
    out["markets"] = markets()
    out["asx_header"] = attempt("asx_header", lambda: asx_json("header"))
    out["asx_key_stats"] = attempt("asx_key_stats", lambda: asx_json("key-statistics"))
    out["asx_announcements"] = attempt("asx_announcements", asx_announcements)
    out["news_google"] = attempt("google_news", google_news)
    if out["news_google"]:
        out["google_links_resolved"] = attempt("google_link_resolve",
                                               lambda: resolve_google_links(out["news_google"]))
    out["news_bing"] = attempt("bing_news", bing_news)
    recent_cut = (now - dt.timedelta(days=31 if BACKFILL else 3)).isoformat()
    fresh = [it for it in (out["news_google"] or []) + (out["news_bing"] or [])
             if str(it.get("published", "")) >= recent_cut]
    out["articles_enriched"] = attempt("article_enrich", lambda: enrich(fresh, cap=40 if BACKFILL else 15))
    out["news_window"] = NEWS_WINDOW
    out["reddit"] = attempt("reddit", reddit)
    out["ratings"] = {n: attempt(f"rating:{n}", lambda n=n, u=u: rating_page(n, u))
                      for n, u in RATINGS.items()}
    out["sources"] = SOURCES

    os.makedirs("data/archive", exist_ok=True)
    blob = json.dumps(out, indent=1, ensure_ascii=False)
    with open("data/latest.json", "w") as f:
        f.write(blob)
    with open(f"data/archive/{out['melbourne_date']}.json", "w") as f:
        f.write(blob)
    keep = sorted(os.listdir("data/archive"))[-45:]
    for name in os.listdir("data/archive"):
        if name not in keep:
            os.remove(os.path.join("data/archive", name))
    print(json.dumps(SOURCES, indent=1))


if __name__ == "__main__":
    main()
