#!/usr/bin/env python3
"""ION Media Monitor collector.

Runs on GitHub Actions each morning. Pulls IOV price and volume, ASX releases,
news search results, social and forum signals, and third-party rating pages,
then writes data/latest.json (plus a dated copy in data/archive/).

Standard library only, so the workflow needs no installs. Every source is
optional: a failure is logged in sources[] and the rest still runs.
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
def yahoo():
    last_err = None
    for host in ("query1", "query2"):
        url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/IOV.AX"
               "?range=3mo&interval=1d&includePrePost=false")
        try:
            _, body = get(url, "application/json")
            res = json.loads(body)["chart"]["result"][0]
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
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise last_err


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


def asx_announcements():
    data = asx_json("announcements?count=20&market_sensitive=false")
    items = data.get("items", []) if isinstance(data, dict) else []
    out = []
    for it in items:
        key = it.get("documentKey")
        url = (f"https://cdn-api.markitdigital.com/apiman-gateway/ASX/asx-research/1.0/file/{key}"
               "?access_token=83ff96335c2d45a094df02a206a39ff4") if key else \
            "https://www.asx.com.au/markets/trade-our-cash-market/announcements.iov"
        out.append({"date": it.get("date"), "headline": it.get("headline"),
                    "price_sensitive": it.get("isPriceSensitive"), "url": url})
    return out


# ---------- news ----------
def google_news():
    found = {}
    for q in NEWS_QUERIES:
        for ed, hl, ceid in NEWS_EDITIONS:
            if ed == "US" and q != NEWS_QUERIES[0]:
                continue
            url = ("https://news.google.com/rss/search?q=" +
                   urllib.parse.quote(f"{q} when:3d") + f"&hl={hl}&gl={ed}&ceid={ceid}")
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


def hotcopper():
    status, body = get("https://hotcopper.com.au/asx/iov/", "text/html")
    text = strip_html(body)
    i = text.find("Latest Threads")
    return {"status": status, "latest_threads_text": text[i:i + 2500] if i >= 0 else text[:1500]}


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
    out["asx_header"] = attempt("asx_header", lambda: asx_json("header"))
    out["asx_key_stats"] = attempt("asx_key_stats", lambda: asx_json("key-statistics"))
    out["asx_announcements"] = attempt("asx_announcements", asx_announcements)
    out["news_google"] = attempt("google_news", google_news)
    out["news_bing"] = attempt("bing_news", bing_news)
    out["reddit"] = attempt("reddit", reddit)
    out["hotcopper"] = attempt("hotcopper", hotcopper)
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
