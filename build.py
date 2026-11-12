#!/usr/bin/env python3
"""ION Media Monitor: renders the daily email.

Usage: python3 build.py latest.json analysis.json out.html subject.txt

latest.json   the collector output (data/latest.json in the repo)
analysis.json written by the daily run:
  {
    "report_date": "YYYY-MM-DD",            Melbourne date of the report
    "summary": "Two or three plain sentences for the CEO.",
    "mentions_24h": 0, "mentions_7d_avg": 0.0,
    "sentiment": {"positive": 0, "neutral": 0, "negative": 0},
    "needs_look": [{"text": "...", "owner": "Anthony", "link": "https://..."}],
    "coverage": [{"outlet": "...", "headline": "...", "link": "https://...",
                  "sentiment": "positive|neutral|negative", "note": "..."}],
    "more_count": 0,
    "coverage_days": ["YYYY-MM-DD"],         dates with media coverage, for chart markers
    "ratings": [{"source": "...", "value": "...", "as_at": "...", "change": ""}],
    "notion_url": "https://..."
  }
"""
import datetime as dt
import json
import sys
from html import escape
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

MEL = ZoneInfo("Australia/Melbourne")
SHARES_ON_ISSUE = 141_472_364  # fallback only; ASX key statistics supply the live count

INK, DEEP, VIVID, ACCENT = "#0A1312", "#06231A", "#00E27B", "#007D4D"
MIST, LINE, SLATE, BODY = "#F3F6F5", "#E1E8E5", "#5B6B68", "#16201F"
NEG, GREY = "#B3261E", "#9AA6A3"
FONT = "font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;"
NUM = "font-variant-numeric:tabular-nums;"
SENT_COLOUR = {"positive": ACCENT, "neutral": GREY, "negative": NEG}

latest = json.load(open(sys.argv[1]))
a = json.load(open(sys.argv[2]))
report_date = dt.date.fromisoformat(a["report_date"])


# ---------- trading ----------
def sessions():
    for key in ("price_yahoo", "price_stooq"):
        src = latest.get(key) or {}
        rows = [r for r in (src.get("sessions") or []) if r.get("close")]
        if rows:
            rows = sorted({r["date"]: r for r in rows}.values(), key=lambda r: r["date"])
            # never show a session dated after the report date (a part-day bar)
            return [r for r in rows if r["date"] < report_date.isoformat()], key
    return [], None


rows, price_source = sessions()
releases = {}
for ann in latest.get("asx_announcements") or []:
    try:
        d = dt.datetime.fromisoformat(ann["date"].replace("Z", "+00:00")).astimezone(MEL).date()
        releases.setdefault(d.isoformat(), []).append(ann)
    except Exception:  # noqa: BLE001
        pass
coverage_days = set(a.get("coverage_days") or [])


def cents(p):
    c = p * 100
    return f"{c:.1f}c" if abs(c - round(c)) >= 0.05 else f"{round(c)}c"


def short_day(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d:%a} {d.day}"


trade = None
if len(rows) >= 2:
    last, prev = rows[-1], rows[-2]
    last_d = dt.date.fromisoformat(last["date"])
    month = [r for r in rows if dt.date.fromisoformat(r["date"]) > last_d - dt.timedelta(days=30)]
    ks = latest.get("asx_key_stats") or {}
    shares = ks.get("numOfShares") if isinstance(ks, dict) else None
    shares = shares if isinstance(shares, (int, float)) and shares > 1e7 else SHARES_ON_ISSUE
    trade = {
        "close": last["close"], "date": last["date"],
        "chg_pct": (last["close"] / prev["close"] - 1) * 100 if prev["close"] else 0.0,
        "volume": last["volume"],
        "avg_vol": sum(r["volume"] for r in month) / len(month),
        "avg_close": sum(r["close"] for r in month) / len(month),
        "mcap": last["close"] * shares,
        "seven": rows[-7:],
    }


# ---------- html helpers ----------
def p(text, size=15, colour=BODY, extra=""):
    return f'<p style="margin:0;{FONT}font-size:{size}px;line-height:1.5;color:{colour};{extra}">{text}</p>'


def section(title, inner, top=28):
    return (f'<tr><td style="padding:{top}px 28px 0 28px;">'
            f'<p style="margin:0 0 12px 0;{FONT}font-size:15px;font-weight:bold;color:{INK};">{title}</p>'
            f'{inner}</td></tr>')


def tile(label, value, sub, sub_colour=SLATE):
    return (f'<td width="50%" valign="top" style="padding:6px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'bgcolor="{MIST}" style="background:{MIST};border-radius:10px;"><tr>'
            f'<td style="padding:14px 16px;{FONT}">'
            f'<p style="margin:0;font-size:12px;color:{SLATE};">{label}</p>'
            f'<p style="margin:4px 0 0 0;font-size:24px;font-weight:bold;color:{INK};{NUM}">{value}</p>'
            f'<p style="margin:3px 0 0 0;font-size:12px;color:{sub_colour};{NUM}">{sub}</p>'
            f'</td></tr></table></td>')


def dot(colour, size=8):
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="display:inline-table;">'
            f'<tr><td width="{size}" height="{size}" bgcolor="{colour}" '
            f'style="width:{size}px;height:{size}px;background:{colour};border-radius:50%;font-size:0;line-height:0;">'
            f'&nbsp;</td></tr></table>')


# ---------- tiles ----------
sent = a.get("sentiment") or {}
pos, neu, neg = (int(sent.get(k, 0)) for k in ("positive", "neutral", "negative"))
total = pos + neu + neg
if total == 0:
    tone, tone_sub = "Quiet", "no new mentions"
else:
    if pos == neg and pos >= neu:
        tone = "Mixed"
    else:
        tone = max((("Positive", pos), ("Negative", neg), ("Neutral", neu)), key=lambda x: x[1])[0]
    order = sorted(((pos, "positive"), (neu, "neutral"), (neg, "negative")), key=lambda x: -x[0])
    tone_sub = ", ".join(f"{n} {w}" for n, w in order if n)

m24 = int(a.get("mentions_24h", total))
m7 = a.get("mentions_7d_avg")
m7_sub = "trend starts tomorrow" if m7 is None else f"7-day average {float(m7):.1f}"
mentions_label = a.get("mentions_label") or "Mentions, 24 hours"
if trade:
    up = trade["chg_pct"] >= 0
    price_tile = tile("IOV last close", cents(trade["close"]),
                      f"{'+' if up else ''}{trade['chg_pct']:.1f}% on {dt.date.fromisoformat(trade['date']):%a}",
                      ACCENT if up else NEG)
    ratio = trade["volume"] / trade["avg_vol"] if trade["avg_vol"] else 0
    vol_tile = tile("Volume", f"{trade['volume']:,}", f"{ratio:.1f}x 1-month average")
else:
    price_tile = tile("IOV last close", "n/a", "price feed unavailable", GREY)
    vol_tile = tile("Volume", "n/a", "price feed unavailable", GREY)
tiles = (f'<tr><td style="padding:20px 22px 0 22px;">'
         f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
         f'<tr>{price_tile}{vol_tile}</tr>'
         f'<tr>{tile(mentions_label, f"{m24}", m7_sub)}'
         f'{tile("Sentiment", tone, tone_sub)}</tr>'
         f'</table></td></tr>')

parts = []

# ---------- needs a look ----------
flags = a.get("needs_look") or []
if flags:
    rows_html = ""
    for f in flags:
        link = f' <a href="{escape(f["link"])}" style="color:{ACCENT};text-decoration:none;">Open</a>' if f.get("link") else ""
        owner = f' <span style="color:{SLATE};">({escape(f["owner"])})</span>' if f.get("owner") else ""
        rows_html += (f'<tr><td width="3" bgcolor="{VIVID}" style="width:3px;background:{VIVID};font-size:0;">&nbsp;</td>'
                      f'<td style="padding:10px 14px;{FONT}font-size:14px;line-height:1.45;color:{BODY};">'
                      f'{escape(f["text"])}{owner}{link}</td></tr>'
                      f'<tr><td colspan="2" style="height:8px;font-size:0;line-height:0;">&nbsp;</td></tr>')
    parts.append(section("Needs a look",
                         f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                         f'bgcolor="{MIST}" style="background:{MIST};">{rows_html}</table>'))

# ---------- coverage ----------
cov = (a.get("coverage") or [])[:5]
if cov:
    items = ""
    for i, c in enumerate(cov):
        border = f"border-top:1px solid {LINE};" if i else ""
        colour = SENT_COLOUR.get(str(c.get("sentiment", "")).lower(), GREY)
        items += (f'<tr><td width="18" valign="top" style="padding:16px 0 0 0;{border}">{dot(colour)}</td>'
                  f'<td style="padding:12px 0;{border}{FONT}">'
                  f'<p style="margin:0;font-size:12px;color:{SLATE};">{escape(c.get("outlet") or "")}</p>'
                  f'<p style="margin:2px 0 0 0;font-size:15px;line-height:1.4;">'
                  f'<a href="{escape(c.get("link") or "#")}" style="color:{INK};text-decoration:none;font-weight:bold;">'
                  f'{escape(c.get("headline") or "")}</a></p>'
                  f'<p style="margin:4px 0 0 0;font-size:13px;line-height:1.45;color:{SLATE};">{escape(c.get("note") or "")}</p>'
                  f'</td></tr>')
    more = int(a.get("more_count") or 0)
    tail = ""
    if more:
        log = a.get("notion_url")
        label = f"{more} more in the mentions log"
        tail = p(f'<a href="{escape(log)}" style="color:{ACCENT};text-decoration:none;">{label}</a>' if log else label,
                 13, SLATE, "padding-top:4px;")
    parts.append(section("Coverage",
                         f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{items}</table>{tail}'))
else:
    parts.append(section("Coverage", p("No new coverage in the last 24 hours.", 14, SLATE)))

# ---------- trading charts ----------
def kfmt(v):
    if v >= 1_000_000:
        return f"{v / 1e6:.1f}m"
    if v >= 10_000:
        return f"{round(v / 1000)}k"
    if v >= 1000:
        return f"{v / 1000:.1f}k"
    return f"{v}"


def chart_head(left, right):
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td style="{FONT}font-size:13px;font-weight:bold;color:{INK};padding-bottom:8px;">{left}</td>'
            f'<td align="right" style="{FONT}font-size:12px;color:{SLATE};padding-bottom:8px;{NUM}">{right}</td>'
            f'</tr></table>')


def axis(n):
    return (f'<tr><td colspan="{n}" bgcolor="{LINE}" style="height:1px;background:{LINE};'
            f'font-size:0;line-height:0;">&nbsp;</td></tr>')


def spacer(h):
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td height="{h}" style="height:{h}px;font-size:0;line-height:0;">&nbsp;</td></tr></table>') if h > 0 else ""


if trade:
    seven = trade["seven"]
    n = len(seven)
    closes = [r["close"] for r in seven]
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 0.01
    peak = max(r["volume"] for r in seven) or 1
    PH, VH = 52, 72
    pcells = vcells = labels = marks = ""
    for r in seven:
        is_last = r["date"] == trade["date"]
        colour = DEEP if is_last else "#9CC7B4"
        txt, wt = (INK, "bold") if is_last else (SLATE, "normal")
        lift = round((r["close"] - lo) / span * PH)
        pcells += (f'<td valign="bottom" align="center" style="height:{PH + 30}px;{FONT}">'
                   f'<p style="margin:0 0 4px 0;font-size:11px;color:{txt};font-weight:{wt};{NUM}">{cents(r["close"])}</p>'
                   f'{dot(colour, 9)}{spacer(lift)}</td>')
        h = max(2, round(r["volume"] / peak * VH)) if r["volume"] else 0
        bar = (f'<table role="presentation" width="62%" align="center" cellpadding="0" cellspacing="0" border="0">'
               f'<tr><td height="{h}" bgcolor="{colour}" style="height:{h}px;background:{colour};'
               f'border-radius:3px 3px 0 0;font-size:0;line-height:0;">&nbsp;</td></tr></table>') if h else ""
        vcells += (f'<td valign="bottom" align="center" style="height:{VH + 20}px;{FONT}">'
                   f'<p style="margin:0 0 4px 0;font-size:11px;color:{txt};font-weight:{wt};{NUM}">{kfmt(r["volume"])}</p>'
                   f'{bar}</td>')
        labels += (f'<td align="center" style="padding-top:6px;{FONT}font-size:11px;color:{txt};">'
                   f'{short_day(r["date"])}</td>')
        m = ""
        if r["date"] in releases:
            m += dot(VIVID, 7)
        if r["date"] in coverage_days:
            m += ("&nbsp;" if m else "") + dot(GREY, 7)
        marks += f'<td align="center" style="padding-top:5px;height:10px;font-size:0;line-height:0;">{m or "&nbsp;"}</td>'
    grid = f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="table-layout:fixed;">'
    price_chart = (chart_head("Close", f"1-month average {cents(trade['avg_close'])}")
                   + grid + f"<tr>{pcells}</tr>" + axis(n) + "</table>")
    volume_chart = (chart_head("Volume", f"1-month average {round(trade['avg_vol']):,}")
                    + grid + f"<tr>{vcells}</tr>" + axis(n) + f"<tr>{labels}</tr><tr>{marks}</tr></table>")
    any_marks = any(r["date"] in releases or r["date"] in coverage_days for r in seven)
    key = (f'<span style="color:{VIVID};">&#9679;</span> ASX release &nbsp; '
           f'<span style="color:{GREY};">&#9679;</span> Media coverage') if any_marks else ""
    foot = ((p(key, 12, SLATE, "padding-top:10px;") if key else "")
            + p(f"Market cap A${trade['mcap'] / 1e6:,.1f}m at the last close", 12, SLATE, f"padding-top:6px;{NUM}"))
    parts.append(section("Trading, last 7 sessions",
                         price_chart + spacer(18) + volume_chart + foot))
else:
    parts.append(section("Trading, last 7 sessions", p("Price data unavailable this morning.", 14, SLATE)))

# ---------- ratings ----------
ratings = a.get("ratings") or []
if ratings:
    r_rows = ""
    for i, r in enumerate(ratings):
        border = f"border-top:1px solid {LINE};" if i else ""
        chg = (f' <span style="color:{ACCENT};font-weight:bold;">{escape(r["change"])}</span>'
               if r.get("change") else "")
        r_rows += (f'<tr><td style="padding:9px 0;{border}{FONT}font-size:13px;color:{SLATE};width:38%;">{escape(r["source"])}</td>'
                   f'<td style="padding:9px 0;{border}{FONT}font-size:13px;color:{BODY};{NUM}">{escape(r["value"])}{chg}'
                   f'<span style="color:{GREY};"> &nbsp;{escape(r.get("as_at") or "")}</span></td></tr>')
    parts.append(section("Third-party ratings",
                         f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{r_rows}</table>'))

# ---------- assemble ----------
date_line = f"{report_date:%A} {report_date.day} {report_date:%B %Y}"
header = (f'<tr><td bgcolor="{INK}" style="background:{INK};padding:24px 28px;border-radius:14px 14px 0 0;{FONT}">'
          f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
          f'<td style="{FONT}font-size:22px;font-weight:bold;letter-spacing:3px;color:#FFFFFF;">ION</td>'
          f'<td align="right" style="{FONT}font-size:13px;color:#B9C6C2;">{date_line}</td></tr></table>'
          f'<p style="margin:14px 0 0 0;{FONT}font-size:20px;font-weight:bold;color:#FFFFFF;">Media and market monitor</p>'
          f'</td></tr>'
          f'<tr><td bgcolor="{VIVID}" style="height:3px;background:{VIVID};font-size:0;line-height:0;">&nbsp;</td></tr>')
summary = f'<tr><td style="padding:24px 28px 0 28px;">{p(escape(a["summary"]), 16, BODY)}</td></tr>'
log = a.get("notion_url")
footer_text = (a.get("window_label") or "Last 24 hours to 6:30am Melbourne time.") + " Prices are the last session close."
if log:
    footer_text += f' Full archive in the <a href="{escape(log)}" style="color:{SLATE};">mentions log</a>.'
footer = (f'<tr><td style="padding:28px 28px 26px 28px;">'
          f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
          f'<td style="border-top:1px solid {LINE};padding-top:14px;{FONT}font-size:11px;line-height:1.5;color:{GREY};">'
          f'{footer_text}</td></tr></table></td></tr>')

html = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#E9EEEC" '
        f'style="background:#E9EEEC;"><tr><td align="center" style="padding:24px 10px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#FFFFFF" '
        f'style="max-width:600px;background:#FFFFFF;border-radius:14px;">'
        + header + summary + tiles + "".join(parts) + footer +
        '</table></td></tr></table>')

assert "—" not in html and "–" not in html, "dash found"


class Nest(HTMLParser):
    VOID = {"img", "br", "meta", "hr"}

    def __init__(self):
        super().__init__()
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack and self.stack[-1] == tag, f"bad nesting at </{tag}>, open {self.stack[-3:]}"
        self.stack.pop()


n = Nest()
n.feed(html)
assert not n.stack, f"unclosed {n.stack}"

open(sys.argv[3], "w").write(html)
bits = [f"{report_date:%a} {report_date.day} {report_date:%b}"]
if trade:
    bits.append(f"IOV {cents(trade['close'])} {'+' if trade['chg_pct'] >= 0 else ''}{trade['chg_pct']:.1f}%")
bits.append(f"{m24} mention{'' if m24 == 1 else 's'}")
subject = "ION monitor, " + bits[0] + ": " + ", ".join(bits[1:])
open(sys.argv[4], "w").write(subject)
print(subject)
print(f"price source {price_source}; sessions {len(rows)}; releases on {sorted(releases)[-3:]}")
