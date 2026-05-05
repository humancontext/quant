"""
数据采集模块 - 从交易所获取 BTC/SOL 的 OHLCV 数据
使用 ccxt 统一接口，支持多个交易所
"""
import ccxt
import pandas as pd
import time
import os
import json
from datetime import datetime, timedelta

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')

# 支持的交易所和标的
EXCHANGES = {
    'binance': ccxt.binance,
    'okx': ccxt.okx,
}

SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

# 时间框架映射
TIMEFRAMES = {
    '1m': '1m',
    '5m': '5m',
    '15m': '15m',
    '1h': '1h',
    '4h': '4h',
    '1d': '1d',
}


# 本地代理配置（Clash/V2Ray）
PROXY = os.environ.get('HTTPS_PROXY', os.environ.get('HTTP_PROXY', ''))
LOCAL_PROXY = 'http://127.0.0.1:7890'


def get_exchange(name='binance'):
    """获取交易所实例（公开数据，无需 API key）"""
    exchange_class = EXCHANGES.get(name)
    if not exchange_class:
        raise ValueError(f"不支持的交易所: {name}，可选: {list(EXCHANGES.keys())}")
    
    config = {
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'},
    }
    
    # 配置代理（国内访问交易所需要代理）
    proxy = PROXY or LOCAL_PROXY
    if proxy:
        config['proxies'] = {
            'http': proxy,
            'https': proxy,
        }
        config['aiohttp_proxy'] = proxy
        config['aiohttp_trust_env'] = True
    
    exchange = exchange_class(config)
    return exchange


def fetch_ohlcv(symbol='BTC/USDT', timeframe='1h', limit=500, exchange_name='binance'):
    """
    获取 OHLCV 数据（开高低收成交量）
    
    Args:
        symbol: 交易对，如 'BTC/USDT'
        timeframe: K线周期，如 '1h', '4h', '1d'
        limit: 获取数量（最多 1000）
        exchange_name: 交易所名称
    
    Returns:
        DataFrame with columns: timestamp, open, high, low, close, volume
    """
    exchange = get_exchange(exchange_name)
    
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        
        # 添加基本衍生指标
        df['returns'] = df['close'].pct_change()
        df['volatility'] = df['returns'].rolling(20).std() * (252 ** 0.5)  # 年化波动率
        
        print(f"✅ 获取 {symbol} {timeframe} 数据: {len(df)} 条 ({df.index[0]} ~ {df.index[-1]})")
        return df
        
    except Exception as e:
        print(f"❌ 获取数据失败: {e}")
        return None


def fetch_multi_timeframe(symbol='BTC/USDT', timeframes=None, exchange_name='binance'):
    """获取多时间框架数据"""
    if timeframes is None:
        timeframes = ['1h', '4h', '1d']
    
    results = {}
    for tf in timeframes:
        df = fetch_ohlcv(symbol, tf, limit=500, exchange_name=exchange_name)
        if df is not None:
            results[tf] = df
        time.sleep(1)  # 避免限流
    
    return results


def fetch_historical(symbol='BTC/USDT', timeframe='1h', days=30, exchange_name='binance'):
    """
    获取历史数据（通过分批请求获取更多数据）
    交易所单次最多返回 1000 条，此函数通过多次请求获取指定天数的数据
    """
    exchange = get_exchange(exchange_name)
    
    # 计算需要多少条数据
    tf_to_minutes = {
        '1m': 1, '5m': 5, '15m': 15, '1h': 60, '4h': 240, '1d': 1440
    }
    minutes_per_bar = tf_to_minutes.get(timeframe, 60)
    bars_per_day = 1440 / minutes_per_bar
    total_bars = int(days * bars_per_day)
    
    all_data = []
    remaining = total_bars
    since = None  # 从最新数据开始往前取
    
    print(f"📊 获取 {symbol} {timeframe} 最近 {days} 天数据（约 {total_bars} 条）...")
    
    while remaining > 0:
        batch_size = min(remaining, 1000)
        
        try:
            if since is None:
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=batch_size)
            else:
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=batch_size)
            
            if not ohlcv:
                break
            
            all_data.extend(ohlcv)
            remaining -= len(ohlcv)
            
            # 下一批从这条数据之前开始
            since = ohlcv[0][0] - (batch_size * minutes_per_bar * 60 * 1000)
            
            time.sleep(0.5)
            
        except Exception as e:
            print(f"⚠️ 批次请求失败: {e}")
            break
    
    if not all_data:
        return None
    
    # 去重并排序
    seen = set()
    unique_data = []
    for row in all_data:
        if row[0] not in seen:
            seen.add(row[0])
            unique_data.append(row)
    
    unique_data.sort(key=lambda x: x[0])
    
    df = pd.DataFrame(unique_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    df['returns'] = df['close'].pct_change()
    df['volatility'] = df['returns'].rolling(20).std() * (252 ** 0.5)
    
    print(f"✅ 共获取 {len(df)} 条数据 ({df.index[0]} ~ {df.index[-1]})")
    return df


def save_data(df, symbol, timeframe, exchange='binance'):
    """保存数据到本地 CSV"""
    os.makedirs(DATA_DIR, exist_ok=True)
    safe_symbol = symbol.replace('/', '_')
    filename = f"{exchange}_{safe_symbol}_{timeframe}.csv"
    filepath = os.path.join(DATA_DIR, filename)
    df.to_csv(filepath)
    print(f"💾 数据已保存: {filepath}")
    return filepath


def load_data(symbol, timeframe, exchange='binance'):
    """从本地 CSV 加载数据"""
    safe_symbol = symbol.replace('/', '_')
    filename = f"{exchange}_{safe_symbol}_{timeframe}.csv"
    filepath = os.path.join(DATA_DIR, filename)
    
    if not os.path.exists(filepath):
        print(f"⚠️ 本地数据不存在: {filepath}")
        return None
    
    df = pd.read_csv(filepath, index_col='timestamp', parse_dates=True)
    print(f"📂 加载本地数据: {len(df)} 条 ({filepath})")
    return df


def get_current_price(symbol='BTC/USDT', exchange_name='binance'):
    """获取当前价格"""
    exchange = get_exchange(exchange_name)
    ticker = exchange.fetch_ticker(symbol)
    return {
        'symbol': symbol,
        'price': ticker['last'],
        'change_24h': ticker.get('percentage', 0),
        'volume_24h': ticker.get('quoteVolume', 0),
        'high_24h': ticker.get('high', 0),
        'low_24h': ticker.get('low', 0),
        'timestamp': datetime.now().isoformat(),
    }


if __name__ == '__main__':
    # 测试：获取 BTC 和 SOL 的当前价格和 1h K线
    print("=" * 60)
    print("🚀 量化交易数据采集 - 快速测试")
    print("=" * 60)
    
    for sym in ['BTC/USDT', 'SOL/USDT']:
        print(f"\n--- {sym} ---")
        
        # 当前价格
        price = get_current_price(sym)
        print(f"💰 当前价格: ${price['price']:,.2f}")
        print(f"📈 24h涨跌: {price['change_24h']:+.2f}%")
        print(f"📊 24h成交量: ${price['volume_24h']:,.0f}")
        
        # 获取最近 1h K线数据
        df = fetch_ohlcv(sym, '1h', limit=100)
        if df is not None:
            print(f"\n最近 5 条 K线:")
            print(df[['open', 'high', 'low', 'close', 'volume']].tail(5).to_string())
            
            # 保存
            save_data(df, sym, '1h')
    
    print("\n" + "=" * 60)
    print("✅ 数据采集测试完成！")
