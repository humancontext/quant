"""
量化交易系统配置

⚠️ API 密钥敏感信息，不要提交到 Git
"""
import os

# ============ OKX 交易所配置 ============
# 尝试从 .env 文件加载
_env_file = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(_env_file):
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, val = line.partition('=')
                os.environ.setdefault(key.strip(), val.strip())

OKX_API_KEY = os.environ.get('OKX_API_KEY', '')
OKX_SECRET = os.environ.get('OKX_SECRET', '')
OKX_PASSPHRASE = os.environ.get('OKX_PASSPHRASE', '')

# 模拟盘（先用模拟盘测试）
OKX_DEMO_MODE = True  # True = 模拟盘, False = 实盘

# ============ 交易标的 ============
SYMBOLS = {
    'BTC/USDT': {
        'min_size': 0.00001,      # OKX 最小下单量
        'size_increment': 0.00001,
        'price_increment': 0.1,
        'max_position_pct': 0.5,  # 单标的最大仓位占比
    },
    'ETH/USDT': {
        'min_size': 0.0001,
        'size_increment': 0.0001,
        'price_increment': 0.01,
        'max_position_pct': 0.5,
    },
}

# ============ 风控参数 ============
RISK = {
    'total_capital_usdt': 200,        # 试水资金 200 USDT（从实盘总额中分配）
    'max_single_risk_pct': 0.02,      # 单笔最大亏损 2% (4 USDT)
    'max_daily_loss_pct': 0.05,       # 日最大亏损 5% (10 USDT)
    'max_drawdown_pct': 0.10,         # 最大回撤保护 10% (20 USDT)
    'max_open_positions': 2,          # 同时持仓数上限
    'default_leverage': 2,            # 默认杠杆（合约）
    'min_rr_ratio': 1.5,             # 最低盈亏比
}

# ============ TradingView Webhook ============
WEBHOOK = {
    'secret': os.environ.get('TV_WEBHOOK_SECRET', 'quant_lobster_2026'),
    'port': 8888,
    'host': '0.0.0.0',
}

# ============ 代理配置 ============
PROXY = os.environ.get('HTTPS_PROXY', 'http://127.0.0.1:7890')

# ============ 日志 ============
LOG_FILE = os.path.join(os.path.dirname(__file__), '..', 'logs', 'trading.log')
