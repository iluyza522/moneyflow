# 资金流观测台

A股个股资金流数据查询工具 + 实时可视化看板。

- **CLI** — 查询个股资金流、行情报价
- **Web** — 实时资金流 ECharts 图表、全市场排名、自定义指数

数据来源：东方财富 API（push2.eastmoney.com）+ yfinance（美股行情 + A股流通市值）

## 快速开始

### 直接运行

```bash
pip install yfinance requests flask

# CLI
python main.py flow 600519 -d 10        # 贵州茅台近10日资金流
python main.py flow 600519 -o out.csv   # 导出 CSV
python main.py quote 600519,AAPL -p 1mo # 行情报价

# Web 看板
python web.py                           # http://localhost:5000
```

### Docker（推荐）

```bash
docker build -t yfinance .
docker run -d -p 5000:5000 -v $PWD/data.db:/app/data.db yfinance
```

## 功能

### Web 看板

- 添加/删除个股，实时查看分钟级资金流
- 主力净流入、超大单、大单、中单、小单逐笔展示
- 双图联动：价格涨跌幅 + 主力净流入 / 资金流明细
- 全市场 A 股资金流排名（主力、超大单、涨跌幅等维度排序）
- 自定义指数：自由组合多只股票，按流通市值归一化
- 后台自动刷新（交易时段 60s/次）

### CLI

| 命令 | 说明 |
|------|------|
| `python main.py flow <code1,code2,...> -d <天数>` | 查询资金流 |
| `python main.py flow <code> -o <文件.csv>` | 导出 CSV |
| `python main.py quote <代码> -p <周期>` | 行情报价 |
| `python main.py all <代码1,代码2> -d 10` | 资金流 + 报价 |

### 自定义指数

多只股票按流通市值加权合成指数，支持跨日连续观察：

```
python main.py all 600519,000858,002594 -d 10
```

Web 端可直接在界面创建和保存指数组。

## 项目结构

```
main.py              CLI 入口
web.py               Flask Web 应用
templates/index.html 前端页面（ECharts + vanilla JS）
data.db              SQLite 数据库（自动创建）
stock_flow/
  cli.py             argparse 命令行入口
  eastmoney.py       东方财富 API 客户端（含反爬策略）
  yahoo.py           yfinance 封装（行情 + 流通市值）
  db.py              SQLite 数据层
  models.py          数据模型
  scheduler.py       后台定时刷新
  config.py          代理配置
```

## 数据说明

- **资金流**：东方财富 API，分钟级数据，逐日累计
- **价格**：东方财富 K 线接口，分钟级收盘价
- **流通市值**：yfinance 获取，东方财富 API 兜底，每日自动刷新
- **红涨绿跌**（A股惯例）：红色 = 上涨 / 净流入，绿色 = 下跌 / 净流出

## 反爬策略

- 浏览器 UA + Referer 头伪装
- JSONP 回调模拟（jQuery 风格）
- 请求间隔 0.5s + 随机抖动
- 指数退避重试（最多 3 次）
- 多端降级：push2 → push2delay → curl
- 严格串行请求，无并发
