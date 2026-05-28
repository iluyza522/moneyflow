"""Yahoo Finance 客户端 - 获取美股/A股行情"""

import logging
from datetime import date

import yfinance as yf

from stock_flow.models import StockQuote

logger = logging.getLogger(__name__)


def _to_yahoo_ticker(ticker: str) -> str:
    """转换股票代码为 Yahoo Finance 格式

    支持输入:
      - 美股: "AAPL", "MSFT" (直接使用)
      - A股沪市: "600519", "600519.SS" (转为 600519.SS)
      - A股深市: "000858", "000858.SZ" (转为 000858.SZ)
    """
    if ticker.endswith((".SS", ".SZ")):
        return ticker
    if ticker.startswith(("6", "9")):
        return f"{ticker}.SS"
    if ticker.startswith(("0", "3")):
        return f"{ticker}.SZ"
    return ticker


def fetch_quote(
    ticker: str,
    period: str = "5d",
) -> list[StockQuote]:
    """获取股票行情数据

    Args:
        ticker: 股票代码
        period: 时间范围 (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, max)

    Returns:
        按日期升序排列的行情列表
    """
    yahoo_ticker = _to_yahoo_ticker(ticker)
    t = yf.Ticker(yahoo_ticker)
    df = t.history(period=period)

    if df.empty:
        raise ValueError(f"未获取到 {ticker} 的行情数据")

    try:
        info = t.info
        name = info.get("shortName") or info.get("longName") or ticker
    except Exception:
        name = ticker

    return [
        StockQuote(
            ticker=ticker,
            name=name,
            trade_date=date(idx.year, idx.month, idx.day),
            open=row["Open"],
            high=row["High"],
            low=row["Low"],
            close=row["Close"],
            volume=int(row["Volume"]),
        )
        for idx, row in df.iterrows()
    ]


def fetch_quote_batch(
    tickers: list[str],
    period: str = "5d",
) -> dict[str, list[StockQuote]]:
    """批量获取多只股票行情

    Args:
        tickers: 股票代码列表
        period: 时间范围

    Returns:
        {股票代码: [StockQuote, ...]} 字典
    """
    result = {}
    for ticker in tickers:
        try:
            result[ticker] = fetch_quote(ticker, period)
        except Exception as e:
            print(f"[WARN] {ticker} 获取失败: {e}")
            result[ticker] = []
    return result


def fetch_market_cap(ticker: str) -> float | None:
    """获取流通市值（单位：元）

    优先 yfinance，失败则从东方财富 API 兜底。
    Returns:
        流通市值（元），获取失败返回 None
    """
    # 优先 yfinance
    yahoo_ticker = _to_yahoo_ticker(ticker)
    try:
        t = yf.Ticker(yahoo_ticker)
        info = t.info
        mc = info.get("marketCap")
        if mc and mc > 0:
            return float(mc)
    except Exception as e:
        logger.warning("yfinance 获取 %s 市值失败: %s", ticker, e)

    # 兜底：东方财富
    return _fetch_market_cap_eastmoney(ticker)


def _fetch_market_cap_eastmoney(ticker: str) -> float | None:
    """从东方财富 API 获取流通市值（单位：元）"""
    import json
    import time

    import requests

    code = ticker.zfill(6)
    secid = f"1.{code}" if code.startswith(("6", "9")) else f"0.{code}"
    url = (
        f"https://push2delay.eastmoney.com/api/qt/stock/get"
        f"?secid={secid}&fields=f117"
    )
    try:
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com/",
        })
        data = resp.json()
        mc = data.get("data", {}).get("f117")
        if mc and mc > 0:
            return float(mc)
    except Exception as e:
        logger.warning("东方财富获取 %s 市值失败: %s", ticker, e)
    return None
