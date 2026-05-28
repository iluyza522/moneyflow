"""东方财富 API 客户端 - 获取个股资金流数据

直接调用 push2.eastmoney.com API，参考反爬策略：
- 浏览器 UA + Referer 头
- JSONP 回调参数模拟
- 请求间隔控制 + 重试机制
"""

import json
import random
import subprocess
import time
from datetime import date, datetime
from urllib.parse import urlencode

import requests

from stock_flow.models import FundFlow

# 主接口 push2，备用接口 push2delay（参考 Java 项目降级策略）
_KLINE_URL_PRIMARY = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
_KLINE_URL_BACKUP = "https://push2delay.eastmoney.com/api/qt/stock/fflow/kline/get"

# 股价K线接口
_PRICE_KLINE_URL = "https://push2delay.eastmoney.com/api/qt/stock/kline/get"

# 全市场资金流排名接口
_MARKET_FLOW_URL_PRIMARY = "https://push2.eastmoney.com/api/qt/clist/get"
_MARKET_FLOW_URL_BACKUP = "https://push2delay.eastmoney.com/api/qt/clist/get"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/zjlx/detail.html",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 请求间隔控制（上次请求完成时间戳）
_last_call_ts: float = 0.0
_MIN_INTERVAL: float = 0.5  # 最小间隔秒数


def _to_secid(stock_code: str) -> str:
    """转换股票代码为东方财富 secid 格式
    沪市(6/9开头) -> 1.xxxx, 深市(0/3开头) -> 0.xxxx
    """
    code = stock_code.zfill(6)
    if code.startswith(("6", "9")):
        return f"1.{code}"
    return f"0.{code}"


def _generate_jsonp_callback() -> str:
    """生成 JSONP 回调函数名，模拟 jQuery 请求"""
    ts = int(time.time() * 1000)
    rand = random.randint(100000, 999999)
    return f"jQuery{rand}_{ts}"


def _rate_limit_wait() -> None:
    """请求间隔控制，确保两次请求之间有最小间隔"""
    global _last_call_ts
    now = time.time()
    elapsed = now - _last_call_ts
    if elapsed < _MIN_INTERVAL:
        jitter = random.uniform(0.1, 0.5)
        time.sleep(_MIN_INTERVAL - elapsed + jitter)
    _last_call_ts = time.time()


def _strip_jsonp(text: str) -> dict:
    """剥离 JSONP 包装: jQuery123_456({"rc":0,...}) -> {"rc":0,...}"""
    text = text.strip()
    if text.startswith("jQuery") or text.startswith("callback"):
        json_str = text[text.index("(") + 1 : text.rindex(")")]
        return json.loads(json_str)
    return json.loads(text)


def _do_request_url(url: str, params: dict) -> dict:
    """对单个 URL 发起请求"""
    session = requests.Session()
    session.headers.update(_HEADERS)
    resp = session.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return _strip_jsonp(resp.text)


def _do_request_curl(url: str, params: dict) -> dict:
    """使用 curl 发送请求（降级方案）"""
    full_url = url + "?" + urlencode(params)
    cmd = [
        "curl", "-s", "--max-time", "20",
        "-H", f"User-Agent: {_HEADERS['User-Agent']}",
        "-H", f"Referer: {_HEADERS['Referer']}",
        full_url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    if result.returncode != 0:
        raise RuntimeError(f"curl 失败: exit={result.returncode}")
    text = result.stdout.strip()
    if "502 Bad Gateway" in text or not text:
        raise RuntimeError("curl 返回 502 或空响应")
    return _strip_jsonp(text)


def _do_request(params: dict, max_retries: int = 3) -> dict:
    """发送请求，带重试和降级策略。
    降级顺序: requests+push2 → requests+push2delay → curl+push2delay
    """
    params["cb"] = _generate_jsonp_callback()
    params["_"] = str(int(time.time() * 1000))

    urls = [_KLINE_URL_PRIMARY, _KLINE_URL_BACKUP]
    last_exc: Exception | None = None

    for attempt in range(max_retries):
        if attempt > 0:
            backoff = (2 ** attempt) + random.uniform(0.5, 2.0)
            time.sleep(backoff)

        _rate_limit_wait()

        # 依次尝试主接口和备用接口
        for url in urls:
            try:
                return _do_request_url(url, params)
            except Exception as e:
                last_exc = e
                continue

        # requests 都失败时，用 curl 试备用接口
        try:
            return _do_request_curl(_KLINE_URL_BACKUP, params)
        except Exception:
            pass

    raise RuntimeError(f"请求失败（重试{max_retries}次）: {last_exc}")


def fetch_fund_flow(
    stock_code: str,
    days: int = 10,
) -> list[FundFlow]:
    """获取个股资金流数据

    Args:
        stock_code: 股票代码，如 "600519"、"000858"
        days: 获取最近 N 天的数据

    Returns:
        按日期升序排列的资金流数据列表
    """
    params = {
        "secid": _to_secid(stock_code),
        "klt": "101",       # 日K
        "lmt": str(days),   # 最近 N 条
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
    }

    data = _do_request(params)
    if data.get("rc") != 0 or not data.get("data"):
        raise ValueError(f"未找到股票 {stock_code} 的资金流数据")

    klines = data["data"].get("klines", [])
    name = data["data"].get("name", stock_code)
    code = stock_code.zfill(6)

    result = []
    for kline in klines:
        # kline 格式: "日期,主力净流入,超大单净流入,大单净流入,中单净流入,小单净流入"
        parts = kline.split(",")
        if len(parts) < 6:
            continue

        trade_date = datetime.strptime(parts[0], "%Y-%m-%d").date()

        # API 返回值符号与官网相反，需要取反
        sl = -float(parts[2])  # 超大单净流入
        lg = -float(parts[3])  # 大单净流入
        main_net = sl + lg  # 主力 = 超大单 + 大单（API f52 是散户资金，不是主力）
        super_large_net = sl
        large_net = lg
        medium_net = -float(parts[4])
        small_net = -float(parts[5])

        result.append(
            FundFlow(
                stock_code=code,
                stock_name=name,
                trade_date=trade_date,
                main_net=main_net,
                super_large_net=super_large_net,
                large_net=large_net,
                medium_net=medium_net,
                small_net=small_net,
                main_pct=0.0,
                super_large_pct=0.0,
                large_pct=0.0,
                medium_pct=0.0,
                small_pct=0.0,
            )
        )

    return result


def _fetch_intraday_prices(stock_code: str, flow_times: list[str]) -> list[float]:
    """获取分钟级收盘价，与资金流时间点对齐"""
    params = {
        "secid": _to_secid(stock_code),
        "klt": "1",
        "fqt": "1",
        "lmt": "240",
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "cb": _generate_jsonp_callback(),
        "_": str(int(time.time() * 1000)),
    }
    try:
        data = _do_request_url(_PRICE_KLINE_URL, params)
        if data.get("rc") != 0 or not data.get("data"):
            return []
        klines = data["data"].get("klines", [])
        # 建立 time -> close 映射
        price_map: dict[str, float] = {}
        for kline in klines:
            parts = kline.split(",")
            if len(parts) < 3:
                continue
            dt_str = parts[0]
            time_part = dt_str.split(" ")[-1] if " " in dt_str else dt_str
            price_map[time_part[:5]] = float(parts[2])  # close price
        # 按 flow_times 顺序取价格，没匹配到的保留 None
        return [price_map.get(t) for t in flow_times]
    except Exception:
        return []


def fetch_intraday_flow(stock_code: str) -> dict:
    """获取个股分钟级实时资金流数据（当日）+ 股价

    Returns:
        {code, name, times, main_net, super_large_net, large_net, medium_net, small_net, prices}
    """
    params = {
        "secid": _to_secid(stock_code),
        "klt": "1",         # 分钟K
        "lmt": "0",         # 全部分钟数据
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
    }

    data = _do_request(params)
    if data.get("rc") != 0 or not data.get("data"):
        raise ValueError(f"未找到股票 {stock_code} 的实时资金流数据")

    klines = data["data"].get("klines", [])
    name = data["data"].get("name", stock_code)
    code = stock_code.zfill(6)

    times = []
    main_net = []
    super_large_net = []
    large_net = []
    medium_net = []
    small_net = []

    for kline in klines:
        # 格式: "2026-05-25 09:31,主力净流入,..."
        parts = kline.split(",")
        if len(parts) < 6:
            continue
        # 提取 HH:MM
        dt_str = parts[0]
        time_part = dt_str.split(" ")[-1] if " " in dt_str else dt_str
        times.append(time_part[:5])
        # API 返回值符号与官网相反，需要取反
        sl = -float(parts[2])  # 超大单净流入
        lg = -float(parts[3])  # 大单净流入
        super_large_net.append(sl)
        large_net.append(lg)
        main_net.append(sl + lg)  # 主力 = 超大单 + 大单
        medium_net.append(-float(parts[4]))
        small_net.append(-float(parts[5]))

    return {
        "code": code,
        "name": name,
        "times": times,
        "main_net": main_net,
        "super_large_net": super_large_net,
        "large_net": large_net,
        "medium_net": medium_net,
        "small_net": small_net,
        "prices": _fetch_intraday_prices(stock_code, times),
    }


def fetch_fund_flow_batch(
    stock_codes: list[str],
    days: int = 10,
) -> dict[str, list[FundFlow]]:
    """批量获取多只股票的资金流数据"""
    result = {}
    for code in stock_codes:
        try:
            result[code] = fetch_fund_flow(code, days=days)
        except Exception as e:
            print(f"[WARN] {code} 获取失败: {e}")
            result[code] = []
    return result


def _do_market_flow_request(params: dict, max_retries: int = 3) -> dict:
    """对全市场排名接口发起请求，带重试和多端点降级。"""
    params["cb"] = _generate_jsonp_callback()
    params["_"] = str(int(time.time() * 1000))

    urls = [_MARKET_FLOW_URL_PRIMARY, _MARKET_FLOW_URL_BACKUP]
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        if attempt > 0:
            backoff = (2 ** attempt) + random.uniform(0.5, 2.0)
            time.sleep(backoff)

        _rate_limit_wait()

        for url in urls:
            try:
                return _do_request_url(url, params)
            except Exception as e:
                last_exc = e

        # requests 都失败时，用 curl 试备用接口
        try:
            return _do_request_curl(_MARKET_FLOW_URL_BACKUP, params)
        except Exception:
            pass

    raise RuntimeError(f"全市场资金流请求失败（重试{max_retries}次）: {last_exc}")


def _parse_market_flow_item(item: dict, snapshot_dt: str) -> dict | None:
    """解析单条全市场资金流数据。"""
    code = str(item.get("f12", "")).zfill(6)
    if not code or code == "000000":
        return None

    def _val(v):
        if v is None or v == "-" or v == "--":
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    return {
        "code": code,
        "name": item.get("f14", ""),
        "price": _val(item.get("f2")),
        "change_pct": _val(item.get("f3")),
        "main_net": _val(item.get("f62")),
        "main_ratio": _val(item.get("f184")),
        "super_large_net": _val(item.get("f66")),
        "large_net": _val(item.get("f72")),
        "medium_net": _val(item.get("f78")),
        "small_net": _val(item.get("f84")),
        "snapshot_dt": snapshot_dt,
    }


def fetch_market_flow_ranking() -> list[dict]:
    """分页拉取全市场 A 股资金流排名（~5500 只）。

    API 每页硬限制 100 条，需要逐页获取。
    返回列表，每项包含: code, name, price, change_pct, main_net,
    super_large_net, large_net, medium_net, small_net, main_ratio
    """
    _PAGE_SIZE = 100
    base_params = {
        "pz": str(_PAGE_SIZE),
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fid": "f62",
        "fields": "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87",
    }

    # 第一页：获取 total
    params = {**base_params, "pn": "1"}
    data = _do_market_flow_request(params)
    if data.get("rc") != 0 or not data.get("data") or not data["data"].get("diff"):
        raise ValueError("全市场资金流数据为空")

    total = data["data"].get("total", 0)
    snapshot_dt = datetime.now().strftime("%Y-%m-%d %H:%M")
    result = []
    for item in data["data"]["diff"]:
        parsed = _parse_market_flow_item(item, snapshot_dt)
        if parsed:
            result.append(parsed)

    # 剩余页
    pages = (total + _PAGE_SIZE - 1) // _PAGE_SIZE
    for pn in range(2, pages + 1):
        params = {**base_params, "pn": str(pn)}
        try:
            data = _do_market_flow_request(params)
            if data.get("rc") != 0 or not data.get("data") or not data["data"].get("diff"):
                break
            for item in data["data"]["diff"]:
                parsed = _parse_market_flow_item(item, snapshot_dt)
                if parsed:
                    result.append(parsed)
        except Exception:
            break  # 某页失败不中断，已有数据仍可用

    return result
