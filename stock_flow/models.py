from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class FundFlow:
    """个股单日资金流数据（单位：元）"""
    stock_code: str
    stock_name: str
    trade_date: date
    main_net: float        # 主力净流入
    super_large_net: float # 超大单净流入
    large_net: float       # 大单净流入
    medium_net: float      # 中单净流入
    small_net: float       # 小单净流入
    main_pct: float        # 主力净流入占比(%)
    super_large_pct: float
    large_pct: float
    medium_pct: float
    small_pct: float


@dataclass(frozen=True)
class StockQuote:
    """股票行情数据"""
    ticker: str
    name: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
