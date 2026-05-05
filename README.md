# 🦞 Lobster Quant - 量化交易决策系统

TradingView PineScript 策略 + OKX 执行 + 风控闭环

## 项目结构

```
quant/
├── run.py                     # 🚀 主入口（一键分析/执行/状态）
├── config/
│   └── settings.py            # ⚙️ 配置（OKX密钥、风控参数）
├── data/
│   └── collector.py           # 📥 数据采集（ccxt + OKX/Binance）
├── strategies/
│   ├── mean_reversion.py      # 均值回归策略（BB+RSI）
│   ├── trend_following.py     # 趋势跟踪策略（EMA+ADX）
│   └── llm_advisor.py         # 🧠 LLM 增强分析
├── execution/
│   ├── okx_trader.py          # 💱 OKX 交易执行器
│   ├── risk_manager.py        # 🛡️ 风控管理器
│   └── webhook_receiver.py    # 📡 TradingView Webhook 接收器
├── pinescript/
│   ├── ema_cross_strategy.pine   # 📊 EMA交叉策略（TV用）
│   └── bb_rsi_strategy.pine      # 📊 BB+RSI策略（TV用）
├── logs/                      # 📝 交易日志
└── reports/                   # 📋 分析报告
```

## 完整流程

```
TradingView PineScript
    │ (alert webhook)
    ▼
Webhook 接收器 ←── 也可以手动触发
    │
    ▼
风控检查（仓位/回撤/盈亏比/日亏损）
    │ 通过
    ▼
OKX 交易所下单（市价/限价 + 止损止盈）
    │
    ▼
持仓跟踪 & 日志记录
```

## 快速开始

### 1. 配置 API 密钥

```bash
# OKX（去 OKX → 设置 → API 创建）
export OKX_API_KEY='你的key'
export OKX_SECRET='你的secret'
export OKX_PASSPHRASE='你的passphrase'
```

### 2. 查看状态

```bash
python3 run.py --status
```

### 3. 分析行情

```bash
# BTC 4h 分析
python3 run.py --symbol BTC/USDT --analyze-only

# ETH 分析
python3 run.py --symbol ETH/USDT --analyze-only
```

### 4. 手动下单（模拟盘）

```bash
# 自动分析+执行
python3 run.py --symbol BTC/USDT --execute

# 指定方向
python3 run.py --symbol BTC/USDT --direction long

# 带止损止盈
python3 run.py --symbol BTC/USDT --direction long --stop-loss 76000 --take-profit 82000
```

### 5. TradingView Webhook 模式

```bash
# 启动 Webhook 服务器
python3 run.py --webhook
```

在 TradingView 中：
1. 打开 PineScript 编辑器
2. 复制 `pinescript/ema_cross_strategy.pine` 或 `bb_rsi_strategy.pine`
3. 添加到图表
4. 设置 Alert → Webhook URL → `http://你的服务器IP:8888/webhook`
5. Alert message 会自动生成 JSON

### 6. PineScript 策略使用

把 `pinescript/` 下的 `.pine` 文件复制到 TradingView 的 Pine Editor 中：
- `ema_cross_strategy.pine` — 趋势跟踪，适合 4h/1d
- `bb_rsi_strategy.pine` — 均值回归，适合 1h/4h

## 风控参数（200 USDT 试水配置）

| 参数 | 值 | 说明 |
|------|-----|------|
| 总资金 | 200 USDT | 起步资金 |
| 单笔风险 | 2% (4 USDT) | 每笔最多亏 |
| 日亏损上限 | 5% (10 USDT) | 当天停手 |
| 最大回撤 | 10% (20 USDT) | 触发暂停 |
| 杠杆 | 2x | 保守起步 |
| 最低盈亏比 | 1.5:1 | 不做亏本买卖 |
| 最大持仓数 | 2 | 同时最多2个 |

## 切换实盘

修改 `config/settings.py`：

```python
OKX_DEMO_MODE = False  # True→模拟盘, False→实盘
```

⚠️ 切换前确保你了解风险，建议先用模拟盘跑通全流程。
