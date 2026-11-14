#!/usr/bin/env python3
"""ION Media Monitor: renders the email.

Usage: python3 build.py latest.json analysis.json out.html subject.txt

Writes, beside out.html:
  out.html           the email. Its trading chart is an inline image (cid:att0) when chart.png exists
  chart.png          the one-month price and volume chart (needs matplotlib)
  out_fallback.html  the same email with table-drawn charts and no image, for when an image can't be sent
  subject.txt
The last printed line is "chart: image" or "chart: tables" (matplotlib missing, out.html has no image).

latest.json   the collector output (data/latest.json in the repo)
analysis.json written by the daily run:
  {
    "report_date": "YYYY-MM-DD",            Melbourne date of the report
    "edition": "weekday" | "monday" | "weekly" | "weekend",   weekly = the Wednesday report; monday is the same layout
    "summary": "Two or three plain sentences for the CEO.",
    "mentions_24h": 0,                       count shown in the mentions tile
    "mentions_label": "Mentions, 24 hours",  optional tile label
    "mentions_7d_avg": 0.0,                  or null; "mentions_sub" overrides the line under the count
    "sentiment": {"positive": 0, "neutral": 0, "negative": 0},
    "needs_look": [{"text": "...", "owner": "Anthony", "link": "https://..."}],
    "flags_sub": "one short line under the Flags count when there are flags",
    "coverage": [{"outlet": "...", "headline": "...", "link": "https://...",
                  "sentiment": "positive|neutral|negative", "note": "..."}],
    "more_count": 0,
    "coverage_days": ["YYYY-MM-DD"],         dates with media coverage, for chart markers
    "register": [{"date": "YYYY-MM-DD", "text": "...", "link": "https://..."}],   "register_title" optional
    "ratings": [{"source": "...", "value": "...", "as_at": "...", "change": ""}],   Mondays
    "sentiment_30d": [{"date": "YYYY-MM-DD", "positive": 0, "neutral": 0, "negative": 0}],   Mondays
    "notion_url": "https://...",
    "window_label": "optional footer line",
    "image_mode": "local" | "collector" | "none",   collector = the chart the GitHub collector drew
    "top50": {                               Mondays: InvestorHub top-50 mover alerts
      "week_start": "YYYY-MM-DD", "week_end": "YYYY-MM-DD", "history_from": "YYYY-MM-DD",
      "moves":   [{"date": "YYYY-MM-DD", "holder": "AS IN THE ALERT", "shares": -7257, "value": 3629}],
      "history": [same shape, earlier alerts]
    }
  }
Also: python3 build.py --chart-only latest.json chart.png   draws only the chart (used by the collector).
"""
import datetime as dt
import json
import os
import sys
from html import escape
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

MEL = ZoneInfo("Australia/Melbourne")
SHARES_ON_ISSUE = 141_472_364  # fallback only; ASX key statistics supply the live count

INK, DEEP, VIVID, ACCENT = "#0A1312", "#06231A", "#00E27B", "#007D4D"
MIST, LINE, SLATE, BODY = "#F3F6F5", "#E1E8E5", "#5B6B68", "#16201F"
NEG, GREY, SOFT = "#B3261E", "#9AA6A3", "#9CC7B4"
FONT = "font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;"
NUM = "font-variant-numeric:tabular-nums;"
SENT_COLOUR = {"positive": ACCENT, "neutral": GREY, "negative": NEG}

CHART_ONLY = len(sys.argv) > 1 and sys.argv[1] == "--chart-only"
if CHART_ONLY:
    # python3 build.py --chart-only latest.json chart.png   (run by the GitHub collector)
    latest = json.load(open(sys.argv[2]))
    a = {"report_date": dt.datetime.now(MEL).date().isoformat(), "summary": ""}
    OUT_HTML, OUT_SUBJECT, CHART_PNG = None, None, sys.argv[3]
else:
    latest = json.load(open(sys.argv[1]))
    a = json.load(open(sys.argv[2]))
    OUT_HTML, OUT_SUBJECT = sys.argv[3], sys.argv[4]
    CHART_PNG = os.path.join(os.path.dirname(os.path.abspath(OUT_HTML)), "chart.png")
OUT_DIR = os.path.dirname(os.path.abspath(OUT_HTML)) if OUT_HTML else None


def _dedupe_sessions(series):
    """Yahoo sometimes returns the latest bar twice; keep one bar per date (the last seen), in date order."""
    if isinstance(series, dict) and isinstance(series.get("sessions"), list):
        by_date = {}
        for row in series["sessions"]:
            if isinstance(row, dict) and row.get("date"):
                by_date[row["date"]] = row
        series["sessions"] = [by_date[d] for d in sorted(by_date)]


for _key in ("price_yahoo", "price_stooq"):
    _dedupe_sessions(latest.get(_key))
for _ix in (latest.get("markets") or {}).values():
    _dedupe_sessions(_ix)
report_date = dt.date.fromisoformat(a["report_date"])
# "local": draw chart.png here and reference it as cid:att0.
# "collector": the email carries the chart the GitHub collector drew (ASX release markers only).
IMAGE_MODE = a.get("image_mode") or "local"
assert IMAGE_MODE in ("local", "collector", "none"), f"unknown image_mode {IMAGE_MODE}"
edition = a.get("edition") or "weekday"
assert edition in ("weekday", "monday", "weekly", "weekend"), f"unknown edition {edition}"
WEEKLY, WEEKEND = edition in ("monday", "weekly"), edition == "weekend"


# ---------- formatting ----------
def cents(p):
    c = p * 100
    return f"{c:.1f}c" if abs(c - round(c)) >= 0.05 else f"{round(c)}c"


def short_day(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d:%a} {d.day}"


def day_month(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d.day} {d:%b}"


def kfmt(v):
    if v >= 1_000_000:
        return f"{v / 1e6:.1f}m"
    if v >= 10_000:
        return f"{round(v / 1000)}k"
    if v >= 1000:
        return f"{v / 1000:.1f}k"
    return f"{round(v)}"


def pct(v, dp=1):
    return f"{'+' if v >= 0 else '-'}{abs(v):.{dp}f}%"


# ---------- trading ----------
def sessions():
    for key in ("price_yahoo", "price_stooq"):
        src = latest.get(key) or {}
        rows = [r for r in (src.get("sessions") or []) if r.get("close")]
        if rows:
            rows = sorted({r["date"]: r for r in rows}.values(), key=lambda r: r["date"])
            # never show a session dated on or after the report date (a part-day bar)
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


def window(days_from, days_to, last_d):
    """Sessions dated in (last_d - days_from, last_d - days_to]."""
    return [r for r in rows if last_d - dt.timedelta(days=days_from) < dt.date.fromisoformat(r["date"])
            <= last_d - dt.timedelta(days=days_to)]


def avg(xs):
    return sum(xs) / len(xs) if xs else 0.0


trade = None
if len(rows) >= 2:
    last, prev = rows[-1], rows[-2]
    last_d = dt.date.fromisoformat(last["date"])
    month = window(30, 0, last_d)
    prior = window(60, 30, last_d)
    ks = latest.get("asx_key_stats") or {}
    shares = ks.get("numOfShares") if isinstance(ks, dict) else None
    shares = shares if isinstance(shares, (int, float)) and shares > 1e7 else SHARES_ON_ISSUE
    trade = {
        "close": last["close"], "date": last["date"],
        "chg_pct": (last["close"] / prev["close"] - 1) * 100 if prev["close"] else 0.0,
        "volume": last["volume"],
        "avg_vol": avg([r["volume"] for r in month]),
        "avg_close": avg([r["close"] for r in month]),
        "value": last["close"] * last["volume"],
        "avg_value": avg([r["close"] * r["volume"] for r in month]),
        "prior_value": avg([r["close"] * r["volume"] for r in prior]) if len(prior) >= 5 else None,
        "mcap": last["close"] * shares,
        "month": month,
        "seven": rows[-7:],
    }
    if len(rows) >= 11:
        wk, wk0 = rows[-5:], rows[-10:-5]
        trade["week"] = {
            "volume": sum(r["volume"] for r in wk), "value": sum(r["close"] * r["volume"] for r in wk),
            "prev_volume": sum(r["volume"] for r in wk0), "prev_value": sum(r["close"] * r["volume"] for r in wk0),
            "chg_pct": (wk[-1]["close"] / rows[-6]["close"] - 1) * 100 if rows[-6]["close"] else 0.0,
        }


# ---------- markets line ----------
def index_change(ix, on_or_before):
    s = [r for r in ix.get("sessions") or [] if r["date"] <= on_or_before]
    if len(s) < 2 or not s[-2]["close"]:
        return None, None
    return (s[-1]["close"] / s[-2]["close"] - 1) * 100, s[-1]["date"]


def markets_line():
    mk = latest.get("markets") or {}
    asx_asof = trade["date"] if trade else (report_date - dt.timedelta(days=1)).isoformat()
    asx_bits, asx_day = [], None
    for key in ("asx200", "asx_tech"):
        ix = mk.get(key)
        if not ix:
            continue
        chg, d = index_change(ix, asx_asof)
        if chg is not None:
            asx_bits.append(f"{ix['name']} {pct(chg)}")
            asx_day = asx_day or d
    us_bits, us_label = [], None
    for key in ("sp500", "nasdaq"):
        ix = mk.get(key)
        if not ix or not ix.get("sessions"):
            continue
        s = ix["sessions"]
        if ix.get("state") == "open" and ix.get("price"):
            mt = dt.datetime.fromisoformat(ix["market_time_utc"])
            tz = ZoneInfo(ix.get("exchange_tz") or "America/New_York")
            mdate = mt.astimezone(tz).date().isoformat()
            base = [r for r in s if r["date"] < mdate]
            if not base:
                continue
            chg = (ix["price"] / base[-1]["close"] - 1) * 100
            t = mt.astimezone(MEL)
            us_label = us_label or f"US at {t:%-I:%M}{t:%p}".replace("AM", "am").replace("PM", "pm") + ", mid-session"
        else:
            chg, d = index_change(ix, "9999-12-31")
            if chg is None:
                continue
            us_label = us_label or f"US {dt.date.fromisoformat(d):%A} close"
        us_bits.append(f"{ix['name']} {pct(chg)}")
    out = []
    if asx_bits:
        day = f" on {dt.date.fromisoformat(asx_day):%A}" if asx_day else ""
        out.append(" and ".join(asx_bits) + day + ".")
    if us_bits:
        out.append(f"{us_label}: " + ", ".join(us_bits) + ".")
    return " ".join(out)


# ---------- html helpers ----------
def p(text, size=15, colour=BODY, extra=""):
    return f'<p style="margin:0;{FONT}font-size:{size}px;line-height:1.5;color:{colour};{extra}">{text}</p>'


def section(title, inner, top=28, right=""):
    head = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td style="{FONT}font-size:15px;font-weight:bold;color:{INK};padding-bottom:12px;">{title}</td>'
            f'<td align="right" style="{FONT}font-size:12px;color:{SLATE};padding-bottom:12px;">{right}</td>'
            f'</tr></table>') if right else \
        f'<p style="margin:0 0 12px 0;{FONT}font-size:15px;font-weight:bold;color:{INK};">{title}</p>'
    return f'<tr><td style="padding:{top}px 28px 0 28px;">{head}{inner}</td></tr>'


def tile(label, value, sub, sub_colour=SLATE):
    return (f'<td width="50%" valign="top" style="padding:6px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'bgcolor="{MIST}" style="background:{MIST};border-radius:10px;"><tr>'
            f'<td style="padding:14px 16px;{FONT}">'
            f'<p style="margin:0;font-size:12px;color:{SLATE};">{label}</p>'
            f'<p style="margin:4px 0 0 0;font-size:24px;font-weight:bold;color:{INK};">{value}</p>'
            f'<p style="margin:3px 0 0 0;font-size:12px;color:{sub_colour};{NUM}">{sub}</p>'
            f'</td></tr></table></td>')


def dot(colour, size=8):
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="display:inline-table;">'
            f'<tr><td width="{size}" height="{size}" bgcolor="{colour}" '
            f'style="width:{size}px;height:{size}px;background:{colour};border-radius:50%;font-size:0;line-height:0;">'
            f'&nbsp;</td></tr></table>')


def link(href, text, bold=True):
    return (f'<a href="{escape(href)}" style="color:{ACCENT};text-decoration:underline;'
            f'{"font-weight:bold;" if bold else ""}">{text}</a>')


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


def stat_rows(pairs):
    out = ""
    for i, (label, value) in enumerate(pairs):
        border = f"border-top:1px solid {LINE};" if i else ""
        out += (f'<tr><td style="padding:8px 0;{border}{FONT}font-size:13px;color:{SLATE};">{label}</td>'
                f'<td align="right" style="padding:8px 0;{border}{FONT}font-size:13px;color:{BODY};{NUM}">{value}</td></tr>')
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{out}</table>'


# ---------- chart image ----------
def panel_title(fig, ax, bold, note, y):
    """Panel label in ink with a grey note after it, both above the plot."""
    t = ax.text(0, y, bold, transform=ax.transAxes, fontsize=9, fontweight="bold", color="#0A1312", ha="left")
    fig.canvas.draw()
    box = t.get_window_extent(renderer=fig.canvas.get_renderer()).transformed(ax.transAxes.inverted())
    ax.text(box.x1 + 0.015, y, note, transform=ax.transAxes, fontsize=8, color="#5B6B68", ha="left")


def render_chart(month, path):
    """One-month close and volume, two panels on a shared date axis. Returns True if written."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        from matplotlib.ticker import FuncFormatter, MaxNLocator
    except ImportError:
        return False
    names = {f.name for f in font_manager.fontManager.ttflist}
    family = next((f for f in ("Inter", "Helvetica Neue", "Helvetica", "Arial", "Liberation Sans")
                   if f in names), "DejaVu Sans")
    plt.rcParams.update({"font.family": family, "font.size": 8, "axes.unicode_minus": False})

    n = len(month)
    xs = list(range(n))
    closes = [r["close"] * 100 for r in month]
    vols = [r["volume"] for r in month]
    avg_c, avg_v = avg(closes), avg(vols)

    fig = plt.figure(figsize=(4.6, 3.0), dpi=240, facecolor="white")
    gs = fig.add_gridspec(2, 1, height_ratios=[1.75, 1], hspace=0.36,
                          left=0.015, right=0.86, top=0.9, bottom=0.135)
    axp = fig.add_subplot(gs[0])
    axv = fig.add_subplot(gs[1], sharex=axp)
    for ax in (axp, axv):
        ax.set_facecolor("white")
        for side in ("top", "left", "right"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(LINE)
        ax.spines["bottom"].set_linewidth(0.7)
        ax.yaxis.tick_right()
        ax.yaxis.grid(True, color=LINE, linewidth=0.6)
        ax.set_axisbelow(True)
        ax.tick_params(length=0, colors=SLATE, labelsize=8, pad=4)

    # price
    lo, hi = min(closes + [avg_c]), max(closes + [avg_c])
    pad = (hi - lo) * 0.18 or 1
    axp.set_ylim(lo - pad, hi + pad * 1.3)
    axp.yaxis.set_major_locator(MaxNLocator(nbins=4, steps=[1, 2, 2.5, 5, 10]))
    axp.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}c"))
    axp.axhline(avg_c, color=GREY, linewidth=0.9, dashes=(3, 2.5), zorder=1)
    axp.plot(xs, closes, color=ACCENT, linewidth=1.6, solid_joinstyle="round", solid_capstyle="round", zorder=2)
    axp.scatter([n - 1], [closes[-1]], s=30, color=ACCENT, edgecolors="white", linewidths=1.3, zorder=3)
    axp.annotate(cents(month[-1]["close"]), (n - 1, closes[-1]), xytext=(-6, 6), textcoords="offset points",
                 ha="right", va="bottom", fontsize=9, fontweight="bold", color=INK)
    panel_title(fig, axp, "Close", f"dashed line is the 1-month average, {cents(avg_c / 100)}", 1.06)
    plt.setp(axp.get_xticklabels(), visible=False)

    # volume
    colours = [SOFT] * (n - 1) + [DEEP]
    axv.bar(xs, vols, width=0.62, color=colours, zorder=2)
    top_v = max(vols + [avg_v]) or 1
    axv.set_ylim(0, top_v * 1.18)
    axv.yaxis.set_major_locator(MaxNLocator(nbins=3, steps=[1, 2, 2.5, 5, 10]))
    axv.yaxis.set_major_formatter(FuncFormatter(lambda v, _: "0" if v == 0 else kfmt(v)))
    axv.axhline(avg_v, color=GREY, linewidth=0.9, dashes=(3, 2.5), zorder=3)
    panel_title(fig, axv, "Volume, shares", f"1-month average {kfmt(avg_v)}", 1.1)

    # release and coverage markers under the date axis
    ymark = -top_v * 0.13
    for i, r in enumerate(month):
        rel, cov = r["date"] in releases, r["date"] in coverage_days
        if rel:
            axv.scatter([i - (0.2 if cov else 0)], [ymark], s=14, color=VIVID, clip_on=False, zorder=4, linewidths=0)
        if cov:
            axv.scatter([i + (0.2 if rel else 0)], [ymark], s=14, color=GREY, clip_on=False, zorder=4, linewidths=0)

    ticks = list(range(n - 1, -1, -7))[::-1]
    axv.set_xticks(ticks)
    axv.set_xticklabels([day_month(month[i]["date"]) for i in ticks])
    axv.tick_params(axis="x", pad=13)
    axv.set_xlim(-0.7, n - 0.3)
    fig.savefig(path, dpi=240, facecolor="white")
    plt.close(fig)
    return True


if CHART_ONLY:
    ok = bool(trade) and len(trade["month"]) >= 5 and render_chart(trade["month"], CHART_PNG)
    print("chart written" if ok else "chart not written (no price data or matplotlib)")
    sys.exit(0 if ok else 1)

# ---------- tiles ----------
sent = a.get("sentiment") or {}
pos, neu, neg = (int(sent.get(k, 0)) for k in ("positive", "neutral", "negative"))
m24 = int(a.get("mentions_24h", pos + neu + neg))
m7 = a.get("mentions_7d_avg")
m_sub = a.get("mentions_sub") or ("trend starts tomorrow" if m7 is None else f"7-day average {float(m7):.1f}")
mentions_label = a.get("mentions_label") or "Mentions, 24 hours"
flags = a.get("needs_look") or []
if flags:
    flag_tile = tile("Flags", f"{len(flags)}", escape(a.get("flags_sub") or "see Needs a look"), NEG)
else:
    flag_tile = tile("Flags", "None", "nothing negative or inaccurate")
mention_tile = tile(mentions_label, f"{m24}", escape(m_sub))

if trade:
    if WEEKLY and trade.get("week"):
        chg, when = trade["week"]["chg_pct"], "over 5 sessions"
    else:
        chg, when = trade["chg_pct"], f"on {dt.date.fromisoformat(trade['date']):%a}"
    if abs(chg) < 0.05:
        price_sub, price_col = f"Unchanged {when}", SLATE
    else:
        price_sub, price_col = f"{pct(chg)} {when}", ACCENT if chg > 0 else NEG
    price_tile = tile("IOV last close", cents(trade["close"]), price_sub, price_col)
    ratio = trade["volume"] / trade["avg_vol"] if trade["avg_vol"] else 0
    vol_tile = tile("Volume, shares", f"{trade['volume']:,}",
                    f"A${kfmt(trade['value'])} traded, {ratio:.1f}x 1-month average")
else:
    price_tile = tile("IOV last close", "n/a", "price feed unavailable", GREY)
    vol_tile = tile("Volume", "n/a", "price feed unavailable", GREY)

grid_rows = f"<tr>{mention_tile}{flag_tile}</tr>" if WEEKEND else \
    f"<tr>{price_tile}{vol_tile}</tr><tr>{mention_tile}{flag_tile}</tr>"
tiles = (f'<tr><td style="padding:20px 22px 0 22px;">'
         f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
         f'{grid_rows}</table></td></tr>')

parts = []

# ---------- needs a look ----------
if flags:
    rows_html = ""
    for f in flags:
        lk = " " + link(f["link"], "Open link") if f.get("link") else ""
        owner = f' <span style="color:{SLATE};">({escape(f["owner"])})</span>' if f.get("owner") else ""
        rows_html += (f'<tr><td width="3" bgcolor="{VIVID}" style="width:3px;background:{VIVID};font-size:0;">&nbsp;</td>'
                      f'<td style="padding:10px 14px;{FONT}font-size:14px;line-height:1.45;color:{BODY};">'
                      f'{escape(f["text"])}{owner}{lk}</td></tr>'
                      f'<tr><td colspan="2" style="height:8px;font-size:0;line-height:0;">&nbsp;</td></tr>')
    parts.append(section("Needs a look",
                         f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                         f'bgcolor="{MIST}" style="background:{MIST};">{rows_html}</table>'))

# ---------- coverage ----------
cov = (a.get("coverage") or [])[:5]
split = ", ".join(f"{n} {w}" for n, w in ((pos, "positive"), (neu, "neutral"), (neg, "negative")) if n)
if cov:
    items = ""
    for i, c in enumerate(cov):
        border = f"border-top:1px solid {LINE};" if i else ""
        colour = SENT_COLOUR.get(str(c.get("sentiment", "")).lower(), GREY)
        items += (f'<tr><td width="18" valign="top" style="padding:16px 0 0 0;{border}">{dot(colour)}</td>'
                  f'<td style="padding:12px 0;{border}{FONT}">'
                  f'<p style="margin:0;font-size:12px;color:{SLATE};">{escape(c.get("outlet") or "")}</p>'
                  f'<p style="margin:2px 0 0 0;font-size:15px;line-height:1.4;">'
                  f'{link(c.get("link") or "#", escape(c.get("headline") or ""))}</p>'
                  f'<p style="margin:4px 0 0 0;font-size:13px;line-height:1.45;color:{SLATE};">{escape(c.get("note") or "")}</p>'
                  f'</td></tr>')
    more = int(a.get("more_count") or 0)
    tail = ""
    if more:
        log = a.get("notion_url")
        label = f"{more} more in the mentions log"
        tail = p(link(log, label, bold=False) if log else label, 13, SLATE, "padding-top:4px;")
    parts.append(section("Coverage",
                         f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{items}</table>{tail}',
                         right=split))
else:
    parts.append(section("Coverage", p("No new coverage since the last report.", 14, SLATE)))

# ---------- register ----------
reg = a.get("register") or []
if reg:
    r_html = ""
    for i, r in enumerate(reg):
        border = f"border-top:1px solid {LINE};" if i else ""
        lk = " " + link(r["link"], "Notice", bold=False) if r.get("link") else ""
        r_html += (f'<tr><td width="52" valign="top" style="padding:9px 0;{border}{FONT}font-size:12px;color:{SLATE};'
                   f'white-space:nowrap;{NUM}">{escape(day_month(r["date"]))}</td>'
                   f'<td style="padding:9px 0;{border}{FONT}font-size:14px;line-height:1.45;color:{BODY};">'
                   f'{escape(r["text"])}{lk}</td></tr>')
    parts.append(section(a.get("register_title") or ("Register, last 7 days" if WEEKLY else "Register"),
                         f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{r_html}</table>'))

# ---------- top 50 shareholders (weekly edition) ----------
KEEP_UPPER_SHORT = {"MR": "Mr", "MRS": "Mrs", "MS": "Ms", "DR": "Dr", "AND": "and", "THE": "The", "OF": "of", "FOR": "for",
                    "PTY": "Pty", "LTD": "Ltd", "LIMITED": "Limited", "INC": "Inc", "SUPER": "Super", "FUND": "Fund"}


def holder_name(raw):
    """Registry names arrive in capitals; keep short codes (PTY, LTD, BNP, A/C) and title-case the rest."""
    out = []
    for w in str(raw).split():
        core = w.strip("<>()")
        if core in KEEP_UPPER_SHORT:
            nw = w.replace(core, KEEP_UPPER_SHORT[core])
        elif len(core) <= 3 or "/" in core or any(ch.isdigit() for ch in core):
            nw = w
        else:
            nw = w.replace(core, core[:1] + core[1:].lower())
        out.append(nw)
    return " ".join(out)


def is_nominee(raw):
    u = str(raw).upper()
    return "NOMINEE" in u or "CUSTODIAN" in u


def share_short(n):
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1e6:.2f}m"
    if n >= 100_000:
        return f"{round(n / 1e3):,}k"
    if n >= 1_000:
        return f"{n / 1e3:.1f}k"
    return f"{n:,}"


def name_key(raw):
    """Match registry names across the alerts (capitals, A/C) and the InvestorHub list (title case)."""
    s = str(raw).upper().replace("A/C", " ")
    return " ".join("".join(ch if ch.isalnum() else " " for ch in s).split())


def make_holding_lookup(snapshot, moves_all, as_of):
    """Holding at as_of = snapshot balance + top-50 alert moves dated after the snapshot, up to as_of.
    Returns holder -> (shares, rank_in_snapshot) or None when the holder was not in the snapshot."""
    if not snapshot or not snapshot.get("rows"):
        return None, None
    snap_date = snapshot["as_at"]
    by_id = {str(r["id"]): r for r in snapshot["rows"] if r.get("id")}
    by_key = {}
    for r in snapshot["rows"]:
        by_key.setdefault(name_key(r["holder"]), r)

    def find(m):
        if m.get("id") and str(m["id"]) in by_id:
            return by_id[str(m["id"])]
        k = name_key(m["holder"])
        if k in by_key:
            return by_key[k]
        for sk, r in by_key.items():  # snapshot names can be cut short; accept a clean prefix match
            if len(sk) >= 20 and (k.startswith(sk) or sk.startswith(k)):
                return r
        return None

    def holding(holder, sample_move):
        r = find(sample_move)
        if r is None:
            return None
        later = sum(int(m["shares"]) for m in moves_all
                    if snap_date < m["date"] <= as_of and (find(m) is r))
        return int(r["shares"]) + later

    return holding, snap_date

t50 = a.get("top50") or {}
if WEEKLY and t50:
    shares_on_issue = trade["mcap"] / trade["close"] if trade and trade["close"] else SHARES_ON_ISSUE
    SIZEABLE = round(shares_on_issue * 0.001)  # 0.1% of issued capital: always flagged
    SIGNAL = round(shares_on_issue * 0.0002)   # 0.02%: the floor for any other flag
    wk_start, wk_end = t50["week_start"], t50["week_end"]
    moves = [m for m in t50.get("moves") or [] if wk_start <= m["date"] <= wk_end]
    hist = [m for m in t50.get("history") or [] if m["date"] < wk_start]
    hist_from = t50.get("history_from") or (min(m["date"] for m in hist) if hist else wk_start)
    sign = lambda m: 1 if int(m["shares"]) >= 0 else -1
    net = {}
    for m in moves:
        h = net.setdefault(m["holder"], {"shares": 0, "value": 0.0, "days": set(), "sell_days": set()})
        h["shares"] += int(m["shares"])
        h["value"] += sign(m) * abs(float(m.get("value") or 0))
        h["days"].add(m["date"])
        if int(m["shares"]) < 0:
            h["sell_days"].add(m["date"])
    sample = {}
    for m in moves:
        if m.get("id") or m["holder"] not in sample:
            sample[m["holder"]] = m
    holding_fn, snap_date = make_holding_lookup(t50.get("snapshot"), list(t50.get("history") or []) + moves, wk_end)
    holdings = {hd: (holding_fn(hd, sample[hd]) if holding_fn else None) for hd in net}

    def now_holds(hd):
        n = holdings.get(hd)
        if n is None:
            return ""
        return f" Now holds {n:,} ({n / shares_on_issue * 100:.2f}%)." if n > 0 else " No longer on the register."

    four_wk = (dt.date.fromisoformat(wk_start) - dt.timedelta(days=28)).isoformat()
    t50_flags = []
    for holder, h in net.items():
        n_before = len(t50_flags)
        name = holder_name(holder) + (" (nominee account)" if is_nominee(holder) else "")
        prior = [m for m in hist if m["holder"] == holder]
        prior_buys = [m for m in prior if int(m["shares"]) > 0 and m["date"] >= four_wk]
        prior_sells = [m for m in prior if int(m["shares"]) < 0 and m["date"] >= four_wk]
        qty, val = abs(h["shares"]), abs(h["value"])
        if h["shares"] > 0:
            if not prior and qty >= SIGNAL:
                t50_flags.append(("buy", qty, f"{name} bought {qty:,} shares (A${kfmt(val)}), its first move in the "
                                          f"top-50 alerts since {day_month(hist_from)}."))
            elif qty >= SIZEABLE:
                t50_flags.append(("buy", qty, f"{name} bought {qty:,} shares (A${kfmt(val)}), above 0.1% of issued capital."))
            elif prior_buys and qty >= SIGNAL and not is_nominee(holder):
                earlier = sum(int(m["shares"]) for m in prior_buys)
                t50_flags.append(("buy", qty, f"{name} is buying again: {qty:,} shares this week after {earlier:,} "
                                          f"in the previous four weeks."))
        elif h["shares"] < 0:
            if not prior_sells and qty >= SIGNAL:
                t50_flags.append(("sell", qty, f"{name} started selling: {qty:,} shares (A${kfmt(val)}), "
                                           f"with no sales in the previous four weeks."))
            elif qty >= SIZEABLE or len(h["sell_days"]) >= 3 or qty >= SIGNAL:
                earlier = -sum(int(m["shares"]) for m in prior_sells)
                t50_flags.append(("sell", qty, f"{name} is selling again: {qty:,} shares (A${kfmt(val)}) this week "
                                           f"after {earlier:,} in the previous four weeks."))
        if len(t50_flags) > n_before:
            kind_, qty_, text_ = t50_flags[-1]
            t50_flags[-1] = (kind_, qty_, text_ + now_holds(holder))
    t50_flags.sort(key=lambda f: -f[1])
    rows_sorted = sorted(net.items(), key=lambda kv: -abs(kv[1]["shares"]))
    n_buy = sum(1 for _, h in rows_sorted if h["shares"] > 0)
    n_sell = sum(1 for _, h in rows_sorted if h["shares"] < 0)
    head_right = f"{n_buy} bought, {n_sell} sold" if rows_sorted else ""
    body = ""
    if t50_flags:
        f_html = ""
        for kind, _, text in t50_flags[:4]:
            f_html += (f'<tr><td width="16" valign="top" style="padding:13px 0 0 0;">{dot(ACCENT if kind == "buy" else NEG)}</td>'
                       f'<td style="padding:8px 0;{FONT}font-size:14px;line-height:1.45;color:{BODY};">{escape(text)}</td></tr>')
        body += f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{f_html}</table>{spacer(8)}'
    if rows_sorted:
        cell = f"padding:8px 0;{FONT}font-size:13px;"
        show_hold = holding_fn is not None
        t_html = (f'<tr><td style="{cell}color:{SLATE};font-size:12px;">Holder</td>'
                  f'<td align="right" style="{cell}color:{SLATE};font-size:12px;padding-left:8px;">Change</td>'
                  f'<td align="right" style="{cell}color:{SLATE};font-size:12px;width:56px;">A$</td>'
                  + (f'<td align="right" style="{cell}color:{SLATE};font-size:12px;padding-left:8px;">Holding</td>' if show_hold else "")
                  + '</tr>')
        for i, (holder, h) in enumerate(rows_sorted):
            colour = ACCENT if h["shares"] > 0 else NEG
            wt = "font-weight:bold;" if i == 0 else ""
            n_hold = holdings.get(holder)
            hold_txt = "n/a" if n_hold is None else share_short(n_hold)
            nm = holder_name(holder)
            main, _, desig = nm.partition(" <")
            desig_html = (f'<br><span style="font-size:11px;color:{SLATE};font-weight:normal;">{escape(desig.rstrip(">").replace(" A/C", "").strip())}</span>'
                          if desig else "")
            t_html += (f'<tr><td style="{cell}border-top:1px solid {LINE};color:{BODY};{wt}">{escape(main)}{desig_html}</td>'
                       f'<td align="right" style="{cell}border-top:1px solid {LINE};color:{colour};white-space:nowrap;padding-left:8px;{NUM}{wt}">'
                       f'{"+" if h["shares"] > 0 else "-"}{abs(h["shares"]):,}</td>'
                       f'<td align="right" style="{cell}border-top:1px solid {LINE};color:{BODY};white-space:nowrap;{NUM}">'
                       f'{kfmt(abs(h["value"]))}</td>'
                       + (f'<td align="right" style="{cell}border-top:1px solid {LINE};color:{BODY if n_hold is not None else GREY};'
                          f'white-space:nowrap;padding-left:8px;{NUM}">{hold_txt}</td>' if show_hold else "")
                       + '</tr>')
        body += f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{t_html}</table>'
    else:
        body += p("No top-50 holder bought or sold last week.", 14, SLATE)
    body += p(f"From InvestorHub's daily top-50 mover alerts, {day_month(wk_start)} to {day_month(wk_end)}. "
              f"Dollar values use the market price on the day. Flags need at least 0.02% of issued capital "
              f"({SIGNAL:,} shares); anything above 0.1% ({SIZEABLE:,}) is always flagged. Nominee accounts hold shares "
              f"for underlying clients."
              + (f" Holding is the InvestorHub register balance on {day_month(snap_date)} plus the alert moves since; "
                 f"n/a means the holder was outside the top 100 on that date." if holding_fn else ""),
              11, GREY, "padding-top:10px;")
    parts.append(section("Top 50 shareholders, last week", body, right=head_right))

# ---------- 30-day sentiment wave (weekly edition) ----------
s30 = {r["date"]: r for r in (a.get("sentiment_30d") or [])}
if WEEKLY and s30:
    days = [report_date - dt.timedelta(days=i) for i in range(29, -1, -1)]
    raw = [(int(s30.get(d.isoformat(), {}).get("positive", 0)), int(s30.get(d.isoformat(), {}).get("neutral", 0)),
            int(s30.get(d.isoformat(), {}).get("negative", 0))) for d in days]
    WEIGHTS = (1, 2, 3, 2, 1)  # 5-day centred, triangular: smooths daily spikes into a wave

    def roll(i, k):
        num = den = 0
        for off, w in zip(range(-2, 3), WEIGHTS):
            j = i + off
            if 0 <= j < len(raw):
                num += raw[j][k] * w
                den += w
        return num / den

    up_s = [roll(i, 0) for i in range(len(raw))]
    down_s = [roll(i, 2) for i in range(len(raw))]
    top = max(up_s + down_s) or 1
    WH = 44
    DH = round(max(down_s) / top * WH)  # negative half only takes the room it needs
    up_row = dn_row = lab_row = ""
    for i, d in enumerate(days):
        hu = round(up_s[i] / top * WH)
        hd = round(down_s[i] / top * WH) if DH else 0
        up_bar = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
                  f'<td height="{hu}" bgcolor="{ACCENT}" style="height:{hu}px;background:{ACCENT};font-size:0;line-height:0;">&nbsp;</td>'
                  f'</tr></table>') if hu else ""
        dn_bar = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
                  f'<td height="{hd}" bgcolor="{NEG}" style="height:{hd}px;background:{NEG};font-size:0;line-height:0;">&nbsp;</td>'
                  f'</tr></table>') if hd else ""
        up_row += f'<td valign="bottom" style="height:{WH}px;padding:0;">{up_bar}</td>'
        dn_row += f'<td valign="top" style="height:{DH}px;padding:0;">{dn_bar}</td>'
        label = f"{d.day} {d:%b}" if i in (0, 14, 29) else ""
        align = "left" if i == 0 else "right" if i == 29 else "center"
        lab_row += (f'<td align="{align}" style="padding-top:6px;{FONT}font-size:11px;color:{SLATE};'
                    f'white-space:nowrap;overflow:visible;">{label}</td>') if label else '<td></td>'
    tp, tn, tg = (sum(x[k] for x in raw) for k in (0, 1, 2))
    tot = tp + tn + tg
    net = round((tp - tg) / tot * 100) if tot else 0
    wave_chart = (chart_head(f"{tot} mention{'' if tot == 1 else 's'}: {tp} positive, {tn} neutral, {tg} negative",
                             f"Net {'+' if net > 0 else ''}{net}")
                  + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="table-layout:fixed;">'
                  + f"<tr>{up_row}</tr>" + axis(len(days)) + (f"<tr>{dn_row}</tr>" if DH else "") + f"<tr>{lab_row}</tr></table>"
                  + p(f'<span style="color:{ACCENT};">&#9632;</span> Positive above the line &nbsp; '
                      f'<span style="color:{NEG};">&#9632;</span> Negative below the line &nbsp;|&nbsp; 5-day smoothing',
                      12, SLATE, "padding-top:8px;"))
    parts.append(section("Sentiment, last 30 days", wave_chart))


# ---------- trading ----------
def table_charts():
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
        colour = DEEP if is_last else SOFT
        txt, wt = (INK, "bold") if is_last else (SLATE, "normal")
        lift = round((r["close"] - lo) / span * PH)
        pcells += (f'<td valign="bottom" align="center" style="height:{PH + 30}px;{FONT}">'
                   f'<p style="margin:0 0 4px 0;font-size:11px;color:{txt};font-weight:{wt};{NUM}">{cents(r["close"])}</p>'
                   f'{dot(colour, 9)}{spacer(lift)}</td>')
        h = max(2, round(r["volume"] / peak * VH)) if r["volume"] else 0
        bar = (f'<table role="presentation" width="62%" align="center" cellpadding="0" cellspacing="0" border="0">'
               f'<tr><td height="{h}" bgcolor="{colour}" style="height:{h}px;background:{colour};'
               f'border-radius:3px 3px 0 0;font-size:0;line-height:0;">&nbsp;</td></tr></table>') if h else ""
        vcells += (f'<td valign="bottom" align="center" style="height:{VH + 34}px;{FONT}">'
                   f'<p style="margin:0;font-size:11px;color:{txt};font-weight:{wt};{NUM}">{kfmt(r["volume"])}</p>'
                   f'<p style="margin:0 0 4px 0;font-size:10px;color:{GREY};{NUM}">A${kfmt(r["close"] * r["volume"])}</p>'
                   f'{bar}</td>')
        labels += (f'<td align="center" style="padding-top:6px;{FONT}font-size:11px;color:{txt};">'
                   f'{short_day(r["date"])}</td>')
        m = ""
        if r["date"] in releases:
            m += dot(VIVID, 7)
        if r["date"] in coverage_days:
            m += ("&nbsp;" if m else "") + dot(GREY, 7)
        marks += f'<td align="center" style="padding-top:5px;height:10px;font-size:0;line-height:0;">{m or "&nbsp;"}</td>'
    grid = '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="table-layout:fixed;">'
    return (chart_head("Close", "")
            + grid + f"<tr>{pcells}</tr>" + axis(n) + "</table>" + spacer(18)
            + chart_head("Volume, shares and A$", "")
            + grid + f"<tr>{vcells}</tr>" + axis(n) + f"<tr>{labels}</tr><tr>{marks}</tr></table>")


def trading_section(image):
    if WEEKEND:
        if not trade:
            return ""
        return section("Trading", p(f"No trading since {dt.date.fromisoformat(trade['date']):%A}'s close of "
                                    f"{cents(trade['close'])}. Wednesday's weekly report covers the week.", 14, SLATE))
    if not trade:
        return section("Trading", p("Price data unavailable this morning.", 14, SLATE))
    if image:
        chart = (f'<img src="cid:att0" width="572" alt="IOV close and daily volume over the last month" '
                 f'style="display:block;width:100%;max-width:572px;height:auto;border:0;outline:none;">')
        marks_in = trade["month"]
    else:
        chart = table_charts()
        marks_in = trade["seven"]
    cov_marks = coverage_days if not (image and IMAGE_MODE == "collector") else set()
    any_rel = any(r["date"] in releases for r in marks_in)
    any_cov = any(r["date"] in cov_marks for r in marks_in)
    key_bits = ([f'<span style="color:{VIVID};">&#9679;</span> ASX release'] if any_rel else []) + \
               ([f'<span style="color:{GREY};">&#9679;</span> Media coverage'] if any_cov else [])
    key = p(" &nbsp; ".join(key_bits), 12, SLATE, "padding:8px 0 4px 0;") if key_bits else ""
    stats = [] if image else [("1-month average close", cents(trade["avg_close"]))]
    stats.append(("1-month average volume", f"{round(trade['avg_vol']):,} shares a day"))
    if trade.get("prior_value") is not None:
        stats.append(("Value traded per day, last 30 days",
                      f"A${kfmt(trade['avg_value'])} (previous 30 days A${kfmt(trade['prior_value'])})"))
    if WEEKLY and trade.get("week"):
        w = trade["week"]
        stats.append(("Last 5 sessions",
                      f"{kfmt(w['volume'])} shares, A${kfmt(w['value'])} (previous 5: {kfmt(w['prev_volume'])}, "
                      f"A${kfmt(w['prev_value'])})"))
    stats.append(("Market cap at the last close", f"A${trade['mcap'] / 1e6:,.1f}m"))
    if not image:
        return section("Trading, last 7 sessions", chart + key + spacer(6) + stat_rows(stats))
    # the image gets most of the card's width; the title, key and figures keep the usual margins
    return (section("Trading, last month", "")
            + f'<tr><td style="padding:0 14px;">{chart}</td></tr>'
            + f'<tr><td style="padding:0 28px;">{key}{spacer(6)}{stat_rows(stats)}</td></tr>')


# ---------- ratings (weekly edition) ----------
ratings = a.get("ratings") or []
ratings_html = ""
if ratings:
    r_rows = ""
    for i, r in enumerate(ratings):
        border = f"border-top:1px solid {LINE};" if i else ""
        chg = (f' <span style="color:{ACCENT};font-weight:bold;">{escape(r["change"])}</span>' if r.get("change") else "")
        r_rows += (f'<tr><td style="padding:9px 0;{border}{FONT}font-size:13px;color:{SLATE};width:38%;">{escape(r["source"])}</td>'
                   f'<td style="padding:9px 0;{border}{FONT}font-size:13px;color:{BODY};{NUM}">{escape(r["value"])}{chg}'
                   f'<span style="color:{GREY};"> &nbsp;{escape(r.get("as_at") or "")}</span></td></tr>')
    ratings_html = section("Third-party ratings",
                           f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{r_rows}</table>')

# ---------- assemble ----------
title = {"monday": "Weekly media and market monitor", "weekly": "Weekly media and market monitor",
         "weekend": "Weekend media monitor"}.get(
    edition, "Media and market monitor")
date_line = f"{report_date:%A} {report_date.day} {report_date:%B %Y}"
WORDMARK = f'{FONT}font-size:22px;font-weight:bold;letter-spacing:3px;color:#FFFFFF;'
# The ION send (image_mode collector or none) attaches assets/ion-logo-email.png as cid:logo; other routes use the wordmark.
LOGO_CID = IMAGE_MODE != "local"


def header_html(logo):
    mark = (f'<img src="cid:logo" width="96" height="36" alt="ION" style="display:block;width:96px;height:36px;'
            f'border:0;outline:none;{WORDMARK}">' if logo else "ION")
    return (f'<tr><td bgcolor="{INK}" style="background:{INK};padding:24px 28px;border-radius:14px 14px 0 0;{FONT}">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td valign="middle" style="{WORDMARK}">{mark}</td>'
            f'<td align="right" valign="middle" style="{FONT}font-size:13px;color:#B9C6C2;">{date_line}</td></tr></table>'
            f'<p style="margin:16px 0 0 0;{FONT}font-size:20px;font-weight:bold;color:#FFFFFF;">{title}</p>'
            f'</td></tr>'
            f'<tr><td bgcolor="{VIVID}" style="height:3px;background:{VIVID};font-size:0;line-height:0;">&nbsp;</td></tr>')
mline = markets_line()
summary = (f'<tr><td style="padding:24px 28px 0 28px;">{p(escape(a["summary"]), 16, BODY)}'
           + (p(escape(mline), 13, SLATE, f"padding-top:10px;{NUM}") if mline else "") + '</td></tr>')
log = a.get("notion_url")
footer_text = ((a.get("window_label") or "Covers the 24 hours to 6:30am Melbourne time.")
               + " Prices are the last session close; value traded is shares times the closing price."
               + " Index moves are from Yahoo Finance.")
if WEEKLY and s30:
    footer_text += " Net sentiment is positive minus negative mentions as a share of all mentions."
if log:
    footer_text += f' Full archive in the {link(log, "mentions log", bold=False)}.'
footer = (f'<tr><td style="padding:28px 28px 26px 28px;">'
          f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
          f'<td style="border-top:1px solid {LINE};padding-top:14px;{FONT}font-size:11px;line-height:1.5;color:{GREY};">'
          f'{footer_text}</td></tr></table></td></tr>')


def page(image, logo=False):
    body = (header_html(logo) + summary + tiles + "".join(parts) + trading_section(image) + ratings_html + footer)
    out = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#E9EEEC" '
           f'style="background:#E9EEEC;"><tr><td align="center" style="padding:24px 10px;">'
           f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#FFFFFF" '
           f'style="max-width:600px;background:#FFFFFF;border-radius:14px;">'
           + body + '</table></td></tr></table>')
    assert "—" not in out and "–" not in out, "dash found"
    checker = Nest()
    checker.feed(out)
    assert not checker.stack, f"unclosed {checker.stack}"
    return out


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


if os.path.exists(CHART_PNG):
    os.remove(CHART_PNG)
if IMAGE_MODE == "collector":
    have_image = bool(trade) and not WEEKEND and len(trade["month"]) >= 5
elif IMAGE_MODE == "none":
    have_image = False
else:
    have_image = bool(trade) and not WEEKEND and len(trade["month"]) >= 5 and render_chart(trade["month"], CHART_PNG)
open(os.path.join(OUT_DIR, "out_fallback.html"), "w").write(page(False))
open(OUT_HTML, "w").write(page(have_image, logo=LOGO_CID))

bits = [f"{report_date:%a} {report_date.day} {report_date:%b}"]
if flags:
    bits.append(f"{len(flags)} flag{'' if len(flags) == 1 else 's'}")
if trade and WEEKLY and trade.get("week"):
    bits.append(f"IOV {cents(trade['close'])}, {pct(trade['week']['chg_pct'])} on the week")
elif trade and not WEEKEND:
    bits.append(f"IOV {cents(trade['close'])} {pct(trade['chg_pct'])}")
bits.append(f"{m24} mention{'' if m24 == 1 else 's'}" + (" this week" if WEEKLY else ""))
name = {"monday": "ION weekly monitor", "weekly": "ION weekly report",
        "weekend": "ION weekend monitor"}.get(edition, "ION monitor")
subject = f"{name}, " + bits[0] + ": " + ", ".join(bits[1:])
open(OUT_SUBJECT, "w").write(subject)
print(subject)
print(f"edition {edition}; price source {price_source}; sessions {len(rows)}; markets: {mline or 'none'}")
print("chart: image" if have_image else "chart: tables")
