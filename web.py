"""Web 前端 — 个股实时资金流向可视化（SQLite + 后台刷新）"""

import logging
import os
from datetime import datetime
from flask import Flask, jsonify, render_template, request, session, redirect, url_for

from stock_flow import db
from stock_flow.eastmoney import fetch_intraday_flow
from stock_flow.scheduler import start_scheduler
from stock_flow.yahoo import fetch_market_cap

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
_ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "123456")

if _ACCESS_TOKEN == "123456" or app.secret_key == "dev-secret-key-change-in-production":
    logging.warning("使用默认 ACCESS_TOKEN 或 SECRET_KEY，请通过环境变量设置")


@app.before_request
def check_auth():
    if request.endpoint in ("login", "static") or request.path.startswith("/login"):
        return None
    if not session.get("authed"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "未授权"}), 401
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        token = (request.form.get("token") or "").strip()
        if token == _ACCESS_TOKEN:
            session["authed"] = True
            return redirect(url_for("index"))
        return render_template("login.html", error="Token 错误")
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.pop("authed", None)
    return redirect(url_for("login"))


@app.route("/")
def index():
    return render_template("index.html")


# ── 股票列表管理 ────────────────────────────────────


@app.route("/api/stocks")
def api_stocks():
    return jsonify(db.get_stocks(source="user"))


@app.route("/api/stocks", methods=["POST"])
def api_add_stock():
    code = request.json.get("code", "").strip() if request.is_json else ""
    if not code:
        code = request.form.get("code", "").strip()
    if not code:
        return jsonify({"error": "请输入股票代码"}), 400

    code = code.zfill(6)
    # 拉取一次数据
    try:
        data = fetch_intraday_flow(code)
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    db.add_stock(code, data.get("name", code))
    db.save_flow(code, data)

    # 异步拉流通市值（yfinance 较慢，不阻塞返回）
    import threading
    def _fetch_mc():
        mc = fetch_market_cap(code)
        if mc:
            db.update_market_cap(code, mc)
    threading.Thread(target=_fetch_mc, daemon=True).start()

    return jsonify({"code": code, "name": data.get("name", code)})


@app.route("/api/stocks/<code>", methods=["DELETE"])
def api_delete_stock(code: str):
    db.remove_stock(code.zfill(6))
    return jsonify({"ok": True})


@app.route("/api/stocks/<code>/name", methods=["PUT"])
def api_rename_stock(code: str):
    name = request.json.get("name", "").strip() if request.is_json else ""
    if not name:
        return jsonify({"error": "名称不能为空"}), 400
    db.rename_stock(code.zfill(6), name)
    return jsonify({"ok": True})


# ── 资金流数据 ──────────────────────────────────────


@app.route("/api/intraday")
def api_intraday():
    code = request.args.get("code", "").strip()
    date = request.args.get("date", "").strip() or None  # "2026-05-25" or None=all
    if not code:
        return jsonify({"error": "请输入股票代码"}), 400

    code = code.zfill(6)

    # 优先从 DB 读
    data = db.get_flow(code, date=date)
    if data and len(data.get("times", [])) > 0:
        return jsonify(data)

    # DB 无数据，实时拉取
    try:
        data = fetch_intraday_flow(code)
        db.add_stock(code, data.get("name", code))
        db.save_flow(code, data)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/dates")
def api_dates():
    code = request.args.get("code", "").strip()
    if not code:
        return jsonify({"error": "请输入股票代码"}), 400
    dates = db.get_available_dates(code.zfill(6))
    return jsonify(dates)


@app.route("/api/status")
def api_status():
    stocks = db.get_stocks()
    result = {}
    for s in stocks:
        last = db.get_last_update(s["code"])
        result[s["code"]] = last
    return jsonify({"last_update": result, "now": datetime.now().isoformat()})


# ── 自定义指数 ──────────────────────────────────────


@app.route("/api/groups")
def api_groups():
    return jsonify(db.get_groups())


@app.route("/api/groups", methods=["POST"])
def api_create_group():
    body = request.json if request.is_json else {}
    name = (body.get("name") or "").strip()
    codes = body.get("codes", [])
    if not name:
        return jsonify({"error": "名称不能为空"}), 400
    if not codes:
        return jsonify({"error": "至少选择一只股票"}), 400
    codes = [c.zfill(6) for c in codes]
    gid = db.create_group(name, codes)
    # 确保成分股在 stocks 表中，以便后台调度器刷新
    for code in codes:
        if not db.get_stock_name(code):
            try:
                d = fetch_intraday_flow(code)
                db.add_stock(code, d.get("name", code), source="group")
                db.save_flow(code, d)
            except Exception:
                db.add_stock(code, code, source="group")
    # 异步拉流通市值
    import threading
    def _fetch_mc():
        for c in codes:
            mc = fetch_market_cap(c)
            if mc:
                db.update_market_cap(c, mc)
    threading.Thread(target=_fetch_mc, daemon=True).start()
    return jsonify({"id": gid, "name": name, "codes": codes})


@app.route("/api/groups/<int:gid>", methods=["DELETE"])
def api_delete_group(gid: int):
    db.delete_group(gid)
    return jsonify({"ok": True})


@app.route("/api/groups/<int:gid>", methods=["PUT"])
def api_update_group(gid: int):
    body = request.json if request.is_json else {}
    if "name" in body:
        db.update_group_name(gid, body["name"])
    if "codes" in body:
        new_codes = [c.zfill(6) for c in body["codes"]]
        db.update_group_members(gid, new_codes)
        for code in new_codes:
            if not db.get_stock_name(code):
                try:
                    d = fetch_intraday_flow(code)
                    db.add_stock(code, d.get("name", code), source="group")
                    db.save_flow(code, d)
                except Exception:
                    db.add_stock(code, code, source="group")
    return jsonify({"ok": True})


@app.route("/api/groups/<int:gid>/flow")
def api_group_flow(gid: int):
    date = request.args.get("date", "").strip() or None
    data = db.get_group_flow(gid, date=date)
    if not data:
        # 指数成分股无数据，尝试实时拉取
        groups = db.get_groups()
        group = next((g for g in groups if g["id"] == gid), None)
        if not group:
            return jsonify({"error": "指数不存在"}), 404
        for code in group["codes"]:
            if not db.get_flow(code):
                try:
                    d = fetch_intraday_flow(code)
                    db.add_stock(code, d.get("name", code), source="group")
                    db.save_flow(code, d)
                except Exception:
                    pass
        data = db.get_group_flow(gid, date=date)
    if not data:
        return jsonify({"error": "指数无数据"}), 404
    return jsonify(data)


# ── 全市场资金流排名 ─────────────────────────────────


@app.route("/api/market_flow")
def api_market_flow():
    """GET /api/market_flow?sort=main_net&order=desc&limit=100&offset=0"""
    sort = request.args.get("sort", "main_net")
    order = request.args.get("order", "desc")
    limit = min(int(request.args.get("limit", 100)), 5500)
    offset = max(int(request.args.get("offset", 0)), 0)
    return jsonify(db.get_market_flow_ranking(sort=sort, order=order, limit=limit, offset=offset))


# ── 启动 ────────────────────────────────────────────


if __name__ == "__main__":
    db.init_db()
    start_scheduler()
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
