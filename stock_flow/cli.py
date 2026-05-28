"""命令行接口 - 查询资金流和行情数据"""

import argparse
import csv
import sys

from stock_flow.eastmoney import fetch_fund_flow, fetch_fund_flow_batch
from stock_flow.models import FundFlow, StockQuote
from stock_flow.yahoo import fetch_quote, fetch_quote_batch


def _format_fund_flow_table(flows: list[FundFlow]) -> str:
    """格式化资金流数据为表格字符串"""
    if not flows:
        return "无数据"

    lines = [
        f"股票: {flows[0].stock_name} ({flows[0].stock_code})",
        "",
        f"{'日期':<12} {'主力净流入':>16} {'超大单':>14} {'大单':>14} {'中单':>14} {'小单':>14}",
        "-" * 88,
    ]
    for f in flows:
        lines.append(
            f"{f.trade_date} {f.main_net:>16,.0f} {f.super_large_net:>14,.0f} "
            f"{f.large_net:>14,.0f} {f.medium_net:>14,.0f} {f.small_net:>14,.0f}"
        )
    lines.append("")
    lines.append("单位: 元")
    return "\n".join(lines)


def _format_quote_table(quotes: list[StockQuote]) -> str:
    """格式化行情数据为表格字符串"""
    if not quotes:
        return "无数据"

    lines = [
        f"股票: {quotes[0].name} ({quotes[0].ticker})",
        "",
        f"{'日期':<12} {'开盘':>10} {'最高':>10} {'最低':>10} {'收盘':>10} {'成交量':>14}",
        "-" * 72,
    ]
    for q in quotes:
        lines.append(
            f"{q.trade_date} {q.open:>10.2f} {q.high:>10.2f} "
            f"{q.low:>10.2f} {q.close:>10.2f} {q.volume:>14,d}"
        )
    return "\n".join(lines)


def _export_csv_fund_flow(flows: list[FundFlow], path: str) -> None:
    """导出资金流数据为 CSV"""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "股票代码", "股票名称", "日期",
            "主力净流入", "超大单净流入", "大单净流入", "中单净流入", "小单净流入",
            "主力占比%", "超大单占比%", "大单占比%", "中单占比%", "小单占比%",
        ])
        for fl in flows:
            writer.writerow([
                fl.stock_code, fl.stock_name, fl.trade_date,
                fl.main_net, fl.super_large_net, fl.large_net, fl.medium_net, fl.small_net,
                fl.main_pct, fl.super_large_pct, fl.large_pct, fl.medium_pct, fl.small_pct,
            ])
    print(f"已导出: {path} ({len(flows)} 条)")


def _export_csv_quote(quotes: list[StockQuote], path: str) -> None:
    """导出行情数据为 CSV"""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["股票代码", "名称", "日期", "开盘", "最高", "最低", "收盘", "成交量"])
        for q in quotes:
            writer.writerow([q.ticker, q.name, q.trade_date, q.open, q.high, q.low, q.close, q.volume])
    print(f"已导出: {path} ({len(quotes)} 条)")


def cmd_fund_flow(args: argparse.Namespace) -> None:
    """执行资金流查询"""
    codes = [c.strip() for c in args.stocks.split(",")]

    if len(codes) == 1:
        flows = fetch_fund_flow(codes[0], days=args.days)
        print(_format_fund_flow_table(flows))
        if args.output:
            _export_csv_fund_flow(flows, args.output)
    else:
        batch = fetch_fund_flow_batch(codes, days=args.days)
        all_flows = []
        for code in codes:
            flows = batch.get(code, [])
            if flows:
                print(_format_fund_flow_table(flows))
                print()
                all_flows.extend(flows)
        if args.output and all_flows:
            _export_csv_fund_flow(all_flows, args.output)


def cmd_quote(args: argparse.Namespace) -> None:
    """执行行情查询"""
    tickers = [t.strip() for t in args.stocks.split(",")]

    if len(tickers) == 1:
        quotes = fetch_quote(tickers[0], period=args.period)
        print(_format_quote_table(quotes))
        if args.output:
            _export_csv_quote(quotes, args.output)
    else:
        batch = fetch_quote_batch(tickers, period=args.period)
        all_quotes = []
        for ticker in tickers:
            quotes = batch.get(ticker, [])
            if quotes:
                print(_format_quote_table(quotes))
                print()
                all_quotes.extend(quotes)
        if args.output and all_quotes:
            _export_csv_quote(all_quotes, args.output)


def cmd_all(args: argparse.Namespace) -> None:
    """同时查询行情和资金流"""
    cmd_quote(args)
    print()
    cmd_fund_flow(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="A股/美股数据查询工具 - 行情 + 资金流",
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # 行情命令
    p_quote = sub.add_parser("quote", help="查询行情 (yfinance)")
    p_quote.add_argument("stocks", help="股票代码，多个用逗号分隔 (如 600519,AAPL)")
    p_quote.add_argument("-p", "--period", default="1mo", help="时间范围: 1d/5d/1mo/3mo/1y (默认 1mo)")
    p_quote.add_argument("-o", "--output", help="导出 CSV 文件路径")
    p_quote.set_defaults(func=cmd_quote)

    # 资金流命令
    p_flow = sub.add_parser("flow", help="查询资金流 (东方财富)")
    p_flow.add_argument("stocks", help="A股代码，多个用逗号分隔 (如 600519,000858)")
    p_flow.add_argument("-d", "--days", type=int, default=10, help="获取最近 N 天 (默认 10)")
    p_flow.add_argument("-o", "--output", help="导出 CSV 文件路径")
    p_flow.set_defaults(func=cmd_fund_flow)

    # 同时查询
    p_all = sub.add_parser("all", help="同时查询行情和资金流")
    p_all.add_argument("stocks", help="A股代码，多个用逗号分隔")
    p_all.add_argument("-p", "--period", default="1mo", help="行情时间范围 (默认 1mo)")
    p_all.add_argument("-d", "--days", type=int, default=10, help="资金流天数 (默认 10)")
    p_all.add_argument("-o", "--output", help="导出 CSV 文件路径")
    p_all.set_defaults(func=cmd_all)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)
