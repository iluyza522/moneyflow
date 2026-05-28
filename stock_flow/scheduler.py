"""后台定时刷新 — 交易时间内每分钟拉取所有股票的资金流数据"""

import logging
import threading
import time
from datetime import datetime

from stock_flow import db
from stock_flow.eastmoney import fetch_intraday_flow, fetch_market_flow_ranking
from stock_flow.yahoo import fetch_market_cap

logger = logging.getLogger(__name__)

_INTERVAL = 60  # 秒
_TRADE_START = (9, 15)
_TRADE_END = (15, 15)
_MCAP_REFRESH_HOUR = 9  # 每天几点刷新一次流通市值

_stop_event = threading.Event()
_started = False  # 防止重复启动
_mcap_date: str | None = None  # 上次刷新市值的日期
_mflow_last_ts: float = 0.0  # 上次全市场快照的时间戳
_MFLOW_INTERVAL: int = 300  # 全市场快照间隔（5 分钟）
_mflow_running: bool = False  # 全市场快照是否正在拉取中


def _is_trade_time() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:  # 周末
        return False
    t = now.hour * 60 + now.minute
    return _TRADE_START[0] * 60 + _TRADE_START[1] <= t <= _TRADE_END[0] * 60 + _TRADE_END[1]


def _refresh_market_caps() -> None:
    """每天刷新一次流通市值（yfinance 较慢，放在单独函数里）"""
    global _mcap_date
    today = datetime.now().strftime("%Y-%m-%d")
    if _mcap_date == today:
        return
    stocks = db.get_stocks()
    if not stocks:
        return
    logger.info("刷新流通市值（%d 只）", len(stocks))
    for s in stocks:
        if _stop_event.is_set():
            break
        code = s["code"]
        mc = fetch_market_cap(code)
        if mc:
            db.update_market_cap(code, mc)
            logger.info("  %s 流通市值: %.2f亿", code, mc / 1e8)
        time.sleep(0.3)
    _mcap_date = today


def _refresh_all() -> None:
    stocks = db.get_stocks()
    if not stocks:
        return
    logger.info("开始刷新 %d 只股票", len(stocks))
    for s in stocks:
        if _stop_event.is_set():
            break
        code = s["code"]
        try:
            data = fetch_intraday_flow(code)
            db.save_flow(code, data)
            # 更新 API 返回的名称（首次可能没有）
            if not db.get_stock_name(code):
                db.add_stock(code, data.get("name", code))
            logger.info("  %s %s — %d 条", code, data.get("name", ""), len(data.get("times", [])))
        except Exception as e:
            logger.warning("  %s 刷新失败: %s", code, e)
        time.sleep(0.5)  # rate limit


def _refresh_market_flow() -> None:
    """每 5 分钟拉取全市场资金流排名并存入 DB（在后台线程中运行，不阻塞个股刷新）。"""
    global _mflow_last_ts, _mflow_running
    now = time.time()
    if now - _mflow_last_ts < _MFLOW_INTERVAL:
        return
    if _mflow_running:
        return
    _mflow_running = True
    try:
        rows = fetch_market_flow_ranking()
        if rows:
            dt = rows[0].get("snapshot_dt", datetime.now().strftime("%Y-%m-%d %H:%M"))
            db.save_market_flow_snapshot(dt, rows)
            _mflow_last_ts = now
            logger.info("全市场快照已保存: %d 只, dt=%s", len(rows), dt)
    except Exception:
        logger.exception("全市场资金流刷新失败")
    finally:
        _mflow_running = False


def _refresh_market_flow_async() -> None:
    """在后台线程中启动全市场快照拉取，不阻塞主循环。"""
    t = threading.Thread(target=_refresh_market_flow, daemon=True, name="mflow-refresh")
    t.start()


def _loop() -> None:
    while not _stop_event.is_set():
        if _is_trade_time():
            _refresh_market_caps()  # 每天一次
            _refresh_market_flow_async()  # 每 5 分钟，后台线程不阻塞
            _refresh_all()
        # 交易时间内等 60 秒，非交易时间等 5 分钟
        wait = _INTERVAL if _is_trade_time() else 300
        _stop_event.wait(wait)


def start_scheduler() -> None:
    global _started
    if _started:
        return
    _started = True
    t = threading.Thread(target=_loop, daemon=True, name="flow-scheduler")
    t.start()
    logger.info("后台调度器已启动（每 %ds 刷新）", _INTERVAL)


def stop_scheduler() -> None:
    _stop_event.set()
