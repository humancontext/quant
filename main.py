"""
量化交易系统 - 主入口
一键运行：数据采集 → 策略信号 → 回测分析
"""
import sys
import os
import json
from datetime import datetime

# 添加项目路径
sys.path.insert(0, os.path.dirname(__file__))

from data.collector import fetch_ohlcv, save_data, load_data, get_current_price, fetch_historical
from strategies.mean_reversion import MeanReversionStrategy
from strategies.trend_following import TrendFollowingStrategy
from strategies.llm_advisor import analyze_with_llm

SYMBOLS = ['BTC/USDT', 'SOL/USDT']
TIMEFRAMES = ['1h', '4h', '1d']


def run_analysis(symbol='BTC/USDT', timeframe='4h', days=90, exchange='binance'):
    """运行完整分析流程"""
    
    print("=" * 70)
    print(f"🚀 量化分析 - {symbol} | {timeframe} | 最近 {days} 天")
    print(f"⏰ 运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    # ============ 1. 获取数据 ============
    print("\n📥 [1/4] 获取行情数据...")
    
    # 尝试先获取历史数据
    df = fetch_historical(symbol, timeframe, days=days, exchange_name=exchange)
    
    if df is None or len(df) < 200:
        print("⚠️ 历史数据不足，尝试获取最近 500 条...")
        df = fetch_ohlcv(symbol, timeframe, limit=500, exchange_name=exchange)
    
    if df is None or len(df) < 50:
        print("❌ 数据获取失败，请检查网络连接")
        return None
    
    # 保存数据
    save_data(df, symbol, timeframe, exchange)
    
    # ============ 2. 当前价格 ============
    print("\n💰 [2/4] 当前价格信息...")
    try:
        price = get_current_price(symbol, exchange)
        print(f"   价格: ${price['price']:,.2f}")
        print(f"   24h涨跌: {price['change_24h']:+.2f}%")
        print(f"   24h高: ${price['high_24h']:,.2f} | 低: ${price['low_24h']:,.2f}")
    except Exception as e:
        print(f"   ⚠️ 获取实时价格失败: {e}")
    
    # ============ 3. 策略信号 ============
    print("\n📊 [3/4] 策略信号分析...")
    
    strategies = [
        MeanReversionStrategy(),
        TrendFollowingStrategy(),
    ]
    
    signals = {}
    for strategy in strategies:
        try:
            signal = strategy.get_latest_signal(df)
            signals[strategy.name] = signal
            
            emoji = {'LONG': '🟢', 'SHORT': '🔴', 'HOLD': '⚪'}.get(signal['signal'], '⚪')
            print(f"\n   {emoji} {signal['strategy']}")
            print(f"   信号: {signal['signal']}")
            print(f"   {signal['details']['reason']}")
        except Exception as e:
            print(f"   ❌ {strategy.name} 分析失败: {e}")
    
    # ============ 4. 回测 ============
    print("\n\n📈 [4/4] 回测结果...")
    print("-" * 70)
    
    backtest_results = {}
    for strategy in strategies:
        try:
            results = strategy.backtest(df)
            strategy.print_report(results)
            backtest_results[strategy.name] = {
                'total_return': f"{results['total_return']:+.2%}",
                'annual_return': f"{results['annual_return']:+.2%}",
                'max_drawdown': f"{results['max_drawdown']:.2%}",
                'sharpe_ratio': f"{results['sharpe_ratio']:.2f}",
                'win_rate': f"{results['win_rate']:.1%}",
                'total_trades': results['total_trades'],
            }
        except Exception as e:
            print(f"   ❌ {strategy.name} 回测失败: {e}")
    
    # ============ 总结 ============
    print("\n" + "=" * 70)
    print("📋 综合分析总结")
    print("=" * 70)
    print(f"标的: {symbol} | 周期: {timeframe} | 数据: {len(df)} 条")
    
    for name, signal in signals.items():
        emoji = {'LONG': '🟢做多', 'SHORT': '🔴做空', 'HOLD': '⚪观望'}.get(signal['signal'], '⚪')
        print(f"  {emoji} {name}")
    
    # 综合建议
    long_count = sum(1 for s in signals.values() if s['signal'] == 'LONG')
    short_count = sum(1 for s in signals.values() if s['signal'] == 'SHORT')
    
    if long_count >= 2:
        recommendation = "🟢 多数策略看多，可考虑开多"
    elif short_count >= 2:
        recommendation = "🔴 多数策略看空，可考虑开空"
    else:
        recommendation = "⚪ 信号分歧，建议观望"
    
    print(f"\n💡 综合建议: {recommendation}")
    print("=" * 70)
    
    return {
        'symbol': symbol,
        'timeframe': timeframe,
        'data_points': len(df),
        'signals': signals,
        'backtest': backtest_results,
        'recommendation': recommendation,
    }


def run_all():
    """对所有标的运行分析"""
    results = {}
    
    for symbol in SYMBOLS:
        for tf in ['4h', '1d']:
            key = f"{symbol}_{tf}"
            print(f"\n\n{'#' * 70}")
            print(f"### {symbol} - {tf}")
            print(f"{'#' * 70}")
            
            result = run_analysis(symbol, tf, days=90, exchange='binance')
            if result:
                results[key] = result
    
    return results


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='量化交易分析系统')
    parser.add_argument('--symbol', default='BTC/USDT', help='交易对 (默认: BTC/USDT)')
    parser.add_argument('--timeframe', default='4h', help='K线周期 (默认: 4h)')
    parser.add_argument('--days', type=int, default=90, help='回测天数 (默认: 90)')
    parser.add_argument('--exchange', default='binance', help='交易所 (默认: binance)')
    parser.add_argument('--all', action='store_true', help='分析所有标的')
    parser.add_argument('--llm', action='store_true', help='启用 LLM 增强分析')
    
    args = parser.parse_args()
    
    if args.all:
        run_all()
    elif args.llm:
        analyze_with_llm(args.symbol, args.exchange)
    else:
        run_analysis(args.symbol, args.timeframe, args.days, args.exchange)
