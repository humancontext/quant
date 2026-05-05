"""
每日市场分析报告

运行方式: python3 daily_report.py
可选: python3 daily_report.py --email（发送邮件报告）
"""
import sys
import os
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from data.collector import fetch_ohlcv, get_current_price, save_data
from strategies.mean_reversion import MeanReversionStrategy
from strategies.trend_following import TrendFollowingStrategy
from strategies.llm_advisor import analyze_with_llm

SYMBOLS = ['BTC/USDT', 'SOL/USDT', 'ETH/USDT']


def generate_report(symbols=None, exchange='binance'):
    """生成完整的市场分析报告"""
    
    if symbols is None:
        symbols = SYMBOLS
    
    report = {
        'generated_at': datetime.now().isoformat(),
        'exchange': exchange,
        'markets': {},
    }
    
    print("=" * 70)
    print(f"📊 每日量化分析报告")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    # ===== 第一部分：快速价格总览 =====
    print("\n💰 价格总览")
    print("-" * 50)
    
    prices = {}
    for symbol in symbols:
        try:
            price = get_current_price(symbol, exchange)
            prices[symbol] = price
            print(f"  {symbol:12s} ${price['price']:>10,.2f}  24h {price['change_24h']:+.2f}%")
        except Exception as e:
            print(f"  {symbol:12s} ⚠️ 获取失败: {e}")
    
    # ===== 第二部分：LLM 深度分析 =====
    print("\n\n🧠 LLM 深度分析")
    print("=" * 70)
    
    for symbol in symbols:
        try:
            analysis = analyze_with_llm(symbol, exchange)
            if analysis:
                report['markets'][symbol] = {
                    'price': prices.get(symbol, {}).get('price', 0),
                    'change_24h': prices.get(symbol, {}).get('change_24h', 0),
                    'llm_analysis': analysis,
                }
        except Exception as e:
            print(f"  ⚠️ {symbol} LLM 分析失败: {e}")
    
    # ===== 第三部分：综合建议 =====
    print("\n\n" + "=" * 70)
    print("📋 综合建议汇总")
    print("=" * 70)
    
    for symbol, data in report['markets'].items():
        llm = data.get('llm_analysis', {})
        rec = llm.get('recommendation', 'hold')
        conf = llm.get('confidence', 0)
        trend = llm.get('trend', '?')
        
        rec_emoji = {'long': '🟢做多', 'short': '🔴做空', 'hold': '⚪观望'}.get(rec, '⚪')
        trend_emoji = {'bullish': '📈', 'bearish': '📉', 'neutral': '➡️'}.get(trend, '➡️')
        
        price = data.get('price', 0)
        change = data.get('change_24h', 0)
        
        print(f"\n  {symbol}: ${price:,.2f} ({change:+.2f}%)")
        print(f"  {trend_emoji} 趋势: {trend} | {rec_emoji} 建议: {rec.upper()} | 置信度: {conf}%")
        
        if rec != 'hold' and llm.get('entry_price'):
            print(f"  📍 入场: ${llm['entry_price']:,.2f} | 止损: ${llm['stop_loss']:,.2f} | 止盈: ${llm['take_profit']:,.2f}")
    
    # 保存报告
    report_dir = os.path.join(os.path.dirname(__file__), 'reports')
    os.makedirs(report_dir, exist_ok=True)
    report_file = os.path.join(report_dir, f"report_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
    
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    
    print(f"\n💾 报告已保存: {report_file}")
    print("=" * 70)
    
    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='+', default=['BTC/USDT', 'SOL/USDT', 'ETH/USDT'])
    parser.add_argument('--exchange', default='binance')
    args = parser.parse_args()
    
    generate_report(args.symbols, args.exchange)
