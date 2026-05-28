"""SQLite 数据层 — 股票列表 + 分钟级资金流 + 自定义指数"""

import sqlite3
from datetime import datetime
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent / "data.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """建表（幂等）"""
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stocks (
                code TEXT PRIMARY KEY,
                custom_name TEXT,
                api_name TEXT,
                added_at TEXT,
                source TEXT NOT NULL DEFAULT 'user'
            );
            CREATE TABLE IF NOT EXISTS intraday_flow (
                code TEXT NOT NULL,
                dt TEXT NOT NULL,
                main_net REAL,
                super_large_net REAL,
                large_net REAL,
                medium_net REAL,
                small_net REAL,
                price REAL,
                PRIMARY KEY (code, dt)
            );
            CREATE INDEX IF NOT EXISTS idx_flow_code ON intraday_flow(code);
            CREATE TABLE IF NOT EXISTS index_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS index_members (
                group_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                PRIMARY KEY (group_id, code),
                FOREIGN KEY (group_id) REFERENCES index_groups(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS market_flow_snapshot (
                dt TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT,
                price REAL,
                change_pct REAL,
                main_net REAL,
                super_large_net REAL,
                large_net REAL,
                medium_net REAL,
                small_net REAL,
                main_ratio REAL,
                PRIMARY KEY (dt, code)
            );
            CREATE INDEX IF NOT EXISTS idx_mflow_code ON market_flow_snapshot(code);
        """)
        # 旧库迁移：给 stocks 表加 source 列
        try:
            conn.execute("ALTER TABLE stocks ADD COLUMN source TEXT NOT NULL DEFAULT 'user'")
        except sqlite3.OperationalError:
            pass  # 列已存在
        # 旧库迁移：给 stocks 表加 market_cap 列（流通市值，单位：元）
        try:
            conn.execute("ALTER TABLE stocks ADD COLUMN market_cap REAL")
        except sqlite3.OperationalError:
            pass  # 列已存在


# ── 股票列表 ──────────────────────────────────────────


def add_stock(code: str, api_name: str, source: str = "user") -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO stocks (code, api_name, added_at, source) VALUES (?, ?, ?, ?)
               ON CONFLICT(code) DO UPDATE SET source = excluded.source
               WHERE excluded.source = 'user'""",
            (code, api_name, datetime.now().isoformat(), source),
        )


def remove_stock(code: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM stocks WHERE code = ?", (code,))
        conn.execute("DELETE FROM intraday_flow WHERE code = ?", (code,))
        conn.execute("DELETE FROM index_members WHERE code = ?", (code,))


def rename_stock(code: str, custom_name: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE stocks SET custom_name = ? WHERE code = ?",
            (custom_name, code),
        )


def update_market_cap(code: str, market_cap: float) -> None:
    """更新流通市值（单位：元）"""
    with _conn() as conn:
        conn.execute(
            "UPDATE stocks SET market_cap = ? WHERE code = ?",
            (market_cap, code),
        )


def get_market_cap(code: str) -> float | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT market_cap FROM stocks WHERE code = ?", (code,)
        ).fetchone()
    return row["market_cap"] if row else None


def get_stocks(source: str | None = None) -> list[dict]:
    with _conn() as conn:
        if source:
            rows = conn.execute(
                "SELECT code, custom_name, api_name FROM stocks WHERE source = ?", (source,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT code, custom_name, api_name FROM stocks").fetchall()
    return [
        {"code": r["code"], "name": r["custom_name"] or r["api_name"] or r["code"]}
        for r in rows
    ]


def get_stock_name(code: str) -> str | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT custom_name, api_name FROM stocks WHERE code = ?", (code,)
        ).fetchone()
    if row is None:
        return None
    return row["custom_name"] or row["api_name"] or code


# ── 资金流数据 ────────────────────────────────────────


def save_flow(code: str, data: dict) -> None:
    """将 fetch_intraday_flow 返回的 dict 存入 DB（upsert）"""
    times: list[str] = data.get("times", [])
    if not times:
        return

    today = datetime.now().strftime("%Y-%m-%d")
    rows = []
    prices = data.get("prices", [None] * len(times))
    for i, t in enumerate(times):
        dt = f"{today} {t}"
        rows.append((
            code, dt,
            data["main_net"][i],
            data["super_large_net"][i],
            data["large_net"][i],
            data["medium_net"][i],
            data["small_net"][i],
            prices[i] if prices[i] is not None else None,
        ))

    with _conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO intraday_flow
               (code, dt, main_net, super_large_net, large_net, medium_net, small_net, price)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )


def get_flow(code: str, date: str | None = None) -> dict | None:
    """从 DB 读取资金流数据，重组为前端格式。

    Args:
        code: 股票代码
        date: 指定日期（"2026-05-25"），None 则返回全部数据
    """
    with _conn() as conn:
        if date:
            rows = conn.execute(
                """SELECT dt, main_net, super_large_net, large_net, medium_net, small_net, price
                   FROM intraday_flow
                   WHERE code = ? AND dt LIKE ?
                   ORDER BY dt""",
                (code, f"{date}%"),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT dt, main_net, super_large_net, large_net, medium_net, small_net, price
                   FROM intraday_flow
                   WHERE code = ?
                   ORDER BY dt""",
                (code,),
            ).fetchall()

    if not rows:
        return None

    name = get_stock_name(code) or code
    times = []
    main_net = []
    super_large_net = []
    large_net = []
    medium_net = []
    small_net = []
    prices = []

    # 跨天累加：每天的资金流从0开始，需要加上前一天的末值才能连续
    _FLOW_KEYS = ("main_net", "super_large_net", "large_net", "medium_net", "small_net")
    offsets = {k: 0.0 for k in _FLOW_KEYS}
    prev_date = ""
    prev_last = {k: 0.0 for k in _FLOW_KEYS}

    for r in rows:
        dt_full = r["dt"]
        cur_date = dt_full[:10]
        if prev_date and cur_date != prev_date:
            offsets = {k: offsets[k] + prev_last[k] for k in _FLOW_KEYS}
        prev_last = {k: r[k] for k in _FLOW_KEYS}
        prev_date = cur_date

        times.append(dt_full[5:])
        main_net.append(r["main_net"] + offsets["main_net"])
        super_large_net.append(r["super_large_net"] + offsets["super_large_net"])
        large_net.append(r["large_net"] + offsets["large_net"])
        medium_net.append(r["medium_net"] + offsets["medium_net"])
        small_net.append(r["small_net"] + offsets["small_net"])
        prices.append(r["price"] if r["price"] is not None else None)

    return {
        "code": code,
        "name": name,
        "times": times,
        "main_net": main_net,
        "super_large_net": super_large_net,
        "large_net": large_net,
        "medium_net": medium_net,
        "small_net": small_net,
        "prices": prices,
    }


def get_available_dates(code: str) -> list[str]:
    """返回该股票有数据的日期列表"""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT DISTINCT SUBSTR(dt, 1, 10) as d FROM intraday_flow
               WHERE code = ? ORDER BY d""",
            (code,),
        ).fetchall()
    return [r["d"] for r in rows]


def get_last_update(code: str) -> str | None:
    """返回该股票最新数据的时间点"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT MAX(dt) as last_dt FROM intraday_flow WHERE code = ?",
            (code,),
        ).fetchone()
    return row["last_dt"] if row and row["last_dt"] else None


# ── 自定义指数 ────────────────────────────────────────


def create_group(name: str, codes: list[str]) -> int:
    """创建指数分组，返回 group_id"""
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO index_groups (name) VALUES (?)", (name,)
        )
        gid = cur.lastrowid
        conn.executemany(
            "INSERT OR IGNORE INTO index_members (group_id, code) VALUES (?, ?)",
            [(gid, c) for c in codes],
        )
    return gid


def delete_group(group_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM index_groups WHERE id = ?", (group_id,))
        conn.execute("DELETE FROM index_members WHERE group_id = ?", (group_id,))


def update_group_name(group_id: int, name: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE index_groups SET name = ? WHERE id = ?", (name, group_id))


def update_group_members(group_id: int, codes: list[str]) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM index_members WHERE group_id = ?", (group_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO index_members (group_id, code) VALUES (?, ?)",
            [(group_id, c) for c in codes],
        )


def get_groups() -> list[dict]:
    """返回所有指数分组 [{id, name, codes, code_names}]"""
    with _conn() as conn:
        groups = conn.execute("SELECT id, name FROM index_groups ORDER BY id").fetchall()
        result = []
        for g in groups:
            members = conn.execute(
                "SELECT m.code, COALESCE(s.custom_name, s.api_name, m.code) as stock_name "
                "FROM index_members m LEFT JOIN stocks s ON m.code = s.code "
                "WHERE m.group_id = ?",
                (g["id"],),
            ).fetchall()
            result.append({
                "id": g["id"],
                "name": g["name"],
                "codes": [m["code"] for m in members],
                "code_names": {m["code"]: m["stock_name"] for m in members},
            })
    return result


def get_group_flow(group_id: int, date: str | None = None) -> dict | None:
    """汇总指数分组内所有股票的资金流数据（按流通市值标准化 + 等权涨跌幅均值）

    资金流字段先除以各股票流通市值，转为占市值百分比后再求和。
    """
    with _conn() as conn:
        members = conn.execute(
            "SELECT code FROM index_members WHERE group_id = ?", (group_id,)
        ).fetchall()

    if not members:
        return None

    codes = [m["code"] for m in members]

    # 拉取每只股票的数据 + 流通市值
    all_data: list[dict] = []
    market_caps: dict[str, float] = {}
    with _conn() as conn:
        for code in codes:
            d = get_flow(code, date=date)
            if d:
                all_data.append(d)
            row = conn.execute(
                "SELECT market_cap FROM stocks WHERE code = ?", (code,)
            ).fetchone()
            if row and row["market_cap"]:
                market_caps[code] = row["market_cap"]

    if not all_data:
        return None

    # 用时间点并集
    all_times: set[str] = set()
    for d in all_data:
        all_times.update(d["times"])
    times = sorted(all_times)

    # 基准市值：用数据第一天的收盘价 × 流通股本，保证跨天连续
    # 流通股本 = 当前市值 / 当前价格（近似，股本短期不变）
    baseline_mc: dict[str, float] = {}
    for d in all_data:
        code = d["code"]
        cur_mc = market_caps.get(code)
        if not cur_mc or cur_mc <= 0:
            continue
        prices = d["prices"]
        # 当前价格：取最后一天的最后一个有效价格
        cur_price = 0
        for p in reversed(prices):
            if p is not None and p > 0:
                cur_price = p
                break
        if cur_price <= 0:
            baseline_mc[code] = cur_mc
            continue
        shares = cur_mc / cur_price
        # 第一天收盘价：取第一个有效价格
        first_price = 0
        for p in prices:
            if p is not None and p > 0:
                first_price = p
                break
        if first_price <= 0:
            baseline_mc[code] = cur_mc
        else:
            baseline_mc[code] = shares * first_price

    # 资金流字段：除以基准市值（转为占比），再求和
    flow_data = [d for d in all_data if baseline_mc.get(d["code"])]

    def _sum_field(field: str) -> list[float]:
        result = []
        for t in times:
            total = 0.0
            for d in flow_data:
                try:
                    idx = d["times"].index(t)
                    val = d[field][idx]
                    mc = baseline_mc[d["code"]]
                    total += val / mc * 100
                except (ValueError, IndexError):
                    pass
            result.append(total)
        return result

    # 涨跌幅：等权均值（跳过 null 价格）
    # 用最新一天的开盘价作为基准，避免跨天数据导致基准错误
    def _avg_pct() -> list[float]:
        pct_series: list[tuple[list[str], list[float | None]]] = []
        for d in all_data:
            prices = d["prices"]
            if not prices:
                continue
            # 找最新一天的日期
            latest_date = ""
            for t in d["times"]:
                cur_date = t[:5]  # "MM-DD"
                if cur_date > latest_date:
                    latest_date = cur_date
            # 用最新一天的第一个有效价格作为开盘基准
            open_p = None
            for t, p in zip(d["times"], prices):
                if t[:5] == latest_date and p is not None and p > 0:
                    open_p = p
                    break
            if not open_p:
                continue
            # 对所有时间点计算涨跌幅：最新一天用当天开盘基准，更早的天用 0
            pct = []
            for t, p in zip(d["times"], prices):
                if t[:5] == latest_date and p is not None and p > 0:
                    pct.append((p - open_p) / open_p * 100)
                else:
                    pct.append(None)
            pct_series.append((d["times"], pct))

        if not pct_series:
            return [0.0] * len(times)

        result = []
        for t in times:
            vals = []
            for t_list, p_list in pct_series:
                try:
                    idx = t_list.index(t)
                    if p_list[idx] is not None:
                        vals.append(p_list[idx])
                except ValueError:
                    pass
            result.append(sum(vals) / len(vals) if vals else 0.0)
        return result

    with _conn() as conn:
        group = conn.execute("SELECT name FROM index_groups WHERE id = ?", (group_id,)).fetchone()

    return {
        "code": f"group_{group_id}",
        "name": group["name"] if group else f"指数{group_id}",
        "times": times,
        "main_net": _sum_field("main_net"),
        "super_large_net": _sum_field("super_large_net"),
        "large_net": _sum_field("large_net"),
        "medium_net": _sum_field("medium_net"),
        "small_net": _sum_field("small_net"),
        "prices": _avg_pct(),
        "is_group": True,
    }


# ── 全市场资金流快照 ────────────────────────────────────

_MARKET_FLOW_COLUMNS = (
    "code", "name", "price", "change_pct",
    "main_net", "super_large_net", "large_net", "medium_net", "small_net", "main_ratio",
)


def save_market_flow_snapshot(dt: str, rows: list[dict]) -> None:
    """批量写入全市场资金流快照（upsert）"""
    if not rows:
        return
    tuples = [
        (dt, r["code"], r.get("name"), r.get("price"), r.get("change_pct"),
         r.get("main_net"), r.get("super_large_net"), r.get("large_net"),
         r.get("medium_net"), r.get("small_net"), r.get("main_ratio"))
        for r in rows
    ]
    with _conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO market_flow_snapshot
               (dt, code, name, price, change_pct,
                main_net, super_large_net, large_net, medium_net, small_net, main_ratio)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            tuples,
        )


def get_latest_snapshot_dt() -> str | None:
    """返回最新一次快照的时间"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT MAX(dt) as max_dt FROM market_flow_snapshot"
        ).fetchone()
    return row["max_dt"] if row and row["max_dt"] else None


def get_market_flow_ranking(
    dt: str | None = None,
    limit: int = 100,
    offset: int = 0,
    sort: str = "main_net",
    order: str = "desc",
) -> dict:
    """获取全市场资金流排名。

    Returns:
        {dt, rows: [...], total}
    """
    if dt is None:
        dt = get_latest_snapshot_dt()
    if dt is None:
        return {"dt": None, "rows": [], "total": 0}

    # 安全的排序字段白名单
    _SORTABLE = {"main_net", "super_large_net", "large_net", "medium_net", "small_net",
                 "change_pct", "main_ratio", "code", "name"}
    sort_col = sort if sort in _SORTABLE else "main_net"
    order_dir = "DESC" if order.lower() == "desc" else "ASC"

    with _conn() as conn:
        total_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM market_flow_snapshot WHERE dt = ?", (dt,)
        ).fetchone()
        total = total_row["cnt"] if total_row else 0

        rows = conn.execute(
            f"""SELECT {', '.join(_MARKET_FLOW_COLUMNS)}
                FROM market_flow_snapshot
                WHERE dt = ?
                ORDER BY {sort_col} {order_dir} NULLS LAST
                LIMIT ? OFFSET ?""",
            (dt, limit, offset),
        ).fetchall()

    return {
        "dt": dt,
        "rows": [dict(r) for r in rows],
        "total": total,
    }
