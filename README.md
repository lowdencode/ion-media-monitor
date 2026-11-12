# ion-media-monitor

Collects public market and media data on ION Video Limited (ASX: IOV) each morning for an internal summary.

- `collect.py` runs on GitHub Actions at 18:07 and 19:07 UTC (and whenever the collector changes) and writes `data/latest.json` plus a dated copy in `data/archive/`.
- `build.py` turns that data and the day's review into the email: a weekday edition, a Monday weekly edition and a weekend edition. It draws the one-month price and volume chart as `chart.png` (matplotlib) and also writes a version with table-drawn charts for when an image can't be sent.

Sources: Yahoo Finance and Stooq (IOV price and volume; ASX 200, ASX 200 IT, S&P 500 and Nasdaq), ASX (releases, and the text of substantial holder and director's interest notices), Google News and Bing News (headlines), Reddit, and public third-party rating pages. HotCopper is not fetched.
