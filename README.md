# ion-media-monitor

Collects public market and media data on ION Video Limited (ASX: IOV) each morning for an internal daily summary.

- `collect.py` runs on GitHub Actions at 18:07 and 19:07 UTC and writes `data/latest.json` plus a dated copy in `data/archive/`.
- `build.py` turns that data and the day's review into the summary email.

Sources: Yahoo Finance and Stooq (price and volume), ASX (releases), Google News and Bing News (headlines), Reddit, HotCopper, and public third-party rating pages. Standard library only.
