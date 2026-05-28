# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Two interfaces for fetching A-share (中国A股) stock data:

1. **CLI** (`main.py`) — stock quotes via yfinance, fund flow via eastmoney API
2. **Web app** (`web.py`) — real-time fund flow visualization with ECharts charts, SQLite storage, background auto-refresh

## Running

```bash
# CLI
python3 main.py quote 600519,AAPL -p 1mo        # Stock quotes (yfinance)
python3 main.py flow 600519,000858 -d 10         # A-share fund flow (eastmoney)
python3 main.py flow 600519 -o output.csv        # Export to CSV

# Web app
python3 web.py                                    # Flask on http://localhost:5000
```

No test suite exists.

## Architecture

```
main.py                     → CLI entry point
web.py                      → Flask web app (API + template rendering)
templates/index.html        → Single-page frontend (ECharts, vanilla JS)
data.db                     → SQLite database (auto-created)
stock_flow/
  models.py                 → Frozen dataclasses: FundFlow, StockQuote
  config.py                 → ProxyConfig + detect_proxy()
  yahoo.py                  → yfinance wrapper: quotes + market cap (流通市值, with eastmoney fallback)
  eastmoney.py              → Eastmoney API: fund flow kline + price kline
  cli.py                    → argparse CLI: quote/flow/all subcommands
  db.py                     → SQLite data layer (stocks, flow, index groups)
  scheduler.py              → Background thread: refreshes fund flow every 60s during trading hours (9:15-15:15)
```

### Data flow (Web)

```
Frontend (index.html)
  ↕ JSON API
Flask (web.py)
  ↕
SQLite (data.db) ← scheduler.py (background, 60s interval) ← eastmoney.py / yahoo.py
```

- **Individual stocks**: fund flow stored per-minute in `intraday_flow` table
- **Custom index groups**: aggregated on-the-fly from member stocks' flow data, normalized by market cap
- **Stock visibility**: `source` column in `stocks` table — `'user'` shown in tabs, `'group'` hidden (only used as index members)

### Key DB tables

- `stocks` — code, name, source (user/group), market_cap (流通市值)
- `intraday_flow` — per-minute data: dt, fund flow fields (main/super_large/large/medium/small net), price
- `index_groups` + `index_members` — custom index definitions

## Key Constraints

- **Fund flow API**: `push2.eastmoney.com` primary, `push2delay.eastmoney.com` fallback, curl as last resort.
- **Anti-crawl**: JSONP callback simulation, browser headers, 0.5s rate limit with jitter, exponential backoff retry. Requests are sequential (not concurrent) to avoid triggering anti-crawling.
- **Fund flow sign**: API `main` field is reversed — code computes `main_net = super_large + large`.
- **Fund flow is cumulative per day**: API returns cumulative flow from market open. `get_flow()` in db.py adds cross-day offsets so multi-day data is continuous (day 2 starts where day 1 ended).
- **Price data may be NULL**: When the price kline API hasn't returned data for a time point yet, price is stored as NULL. Frontend and aggregation code must handle null prices (skip, don't treat as 0).
- **Market cap**: Fetched via yfinance with eastmoney fallback (`_fetch_market_cap_eastmoney` via curl+push2delay), refreshed once daily by scheduler. Used to normalize group fund flow (flow / market_cap * 100 = percentage).
- **Group baseline market cap**: For multi-day consistency, uses `first_day_price × shares` as fixed denominator (shares = current_market_cap / current_price). Ensures cross-day flow percentages are additive.
- **A-share secid**: codes starting with 6/9 → `1.xxxx` (Shanghai), 0/3 → `0.xxxx` (Shenzhen).
- **Group aggregation**: fund flow summed as % of baseline market cap; price change as equal-weight average of member stocks.
- **Database migration**: `init_db()` uses `ALTER TABLE ... ADD COLUMN` with `try/except` for backward-compatible schema evolution.

## Dependencies

- `yfinance` — stock quotes + market cap
- `requests` — HTTP client for eastmoney API
- `flask` — web framework (web app only)

No `requirements.txt` or `pyproject.toml` exists.
