"""
🦞 量化交易闭环 - 一键执行入口

完整流程：数据采集 → 策略信号 → 风控审核 → OKX下单 → 结果报告

用法：
  # 完整流程（分析 + 执行）
  python3 run.py --symbol BTC/USDT

  # 只分析不下单
  python3 run.py --symbol BTC/USDT --analyze-only

  # 强制执行（跳过风控）
  python3 run.py --symbol BTC/USDT --force

  # 查看状态
  python3 run.py --status

  # 启动 Webhook 模式
  python3 run.py --webhook
"""
import sys
import os
import json
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from data.collector import fetch_ohlcv, get_current_price, fetch_historical
from strategies.mean_reversion import MeanReversionStrategy
from strategies.trend_following import TrendFollowingStrategy
from execution.okx_trader import OKXTrader
from execution.risk_manager import RiskManager
from config.settings import RISK, SYMBOLS, OKX_DEMO_MODE


def print_banner():
    print("""
╔══════════════════════════════════════════════════════╗
║  🦞 Lobster Quant - 量化交易决策系统               ║
║  TradingView PineScript + OKX 执行                  ║
╚══════════════════════════════════════════════════════╝
    """)


def show_status(trader: OKXTrader, risk_mgr: RiskManager):
    """显示系统状态"""
    print_banner()
    mode = "🧪 模拟盘" if OKX_DEMO_MODE else "🔴 实盘"
    print(f"模式: {mode}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    # 资金
    balance = trader.get_balance()
    risk_status = risk_mgr.get_status()

    print(f"\n💰 账户资金")
    print(f"   可用: {balance['free']:.2f} USDT | 占用: {balance['used']:.2f} USDT")
    print(f"   总盈亏: {risk_status['total_pnl']:+.2f} USDT | 今日: {risk_status['daily_pnl']:+.2f}")
    print(f"   回撤: {risk_status['drawdown']} | 交易次数: {risk_status['total_trades']}")

    # 持仓
    print(f"\n📊 持仓状态")
    positions = trader.get_positions()
    if not positions:
        print("   无持仓")

    # 价格
    print(f"\n💱 实时价格")
    for sym in SYMBOLS:
        price = trader.get_market_price(sym)
        if price:
            print(f"   {sym:12s} ${price:>12,.2f}")

    # 风控参数
    print(f"\n🛡️ 风控参数")
    print(f"   总资金: ${RISK['total_capital_usdt']} | 单笔风险: {RISK['max_single_risk_pct']:.0%} | 日亏损上限: {RISK['max_daily_loss_pct']:.0%}")
    print(f"   回撤保护: {RISK['max_drawdown_pct']:.0%} | 杠杆: {RISK['default_leverage']}x | 最大持仓: {RISK['max_open_positions']}")
    print(f"   最低盈亏比: {RISK['min_rr_ratio']}")

    return {'balance': balance, 'positions': positions, 'risk': risk_status}


def run_analysis(symbol='BTC/USDT', timeframe='4h', days=90, exchange='okx'):
    """运行策略分析（不下单）"""
    print(f"\n{'='*55}")
    print(f"📊 策略分析: {symbol} | {timeframe}")
    print(f"{'='*55}")

    # 1. 获取数据
    print(f"\n📥 获取行情数据...")
    df = fetch_historical(symbol, timeframe, days=days, exchange_name=exchange)
    if df is None or len(df) < 50:
        df = fetch_ohlcv(symbol, timeframe, limit=500, exchange_name=exchange)
    if df is None or len(df) < 50:
        print("❌ 数据获取失败")
        return None

    # 2. 当前价格
    try:
        price = get_current_price(symbol, exchange)
        print(f"💰 {symbol}: ${price['price']:,.2f} ({price['change_24h']:+.2f}%)")
    except:
        price = {'price': df['close'].iloc[-1], 'change_24h': 0}

    # 3. 策略信号
    print(f"\n📊 策略信号:")
    strategies = [MeanReversionStrategy(), TrendFollowingStrategy()]
    signals = {}
    for s in strategies:
        try:
            sig = s.get_latest_signal(df)
            signals[s.name] = sig
            emoji = {'LONG': '🟢', 'SHORT': '🔴', 'HOLD': '⚪'}.get(sig['signal'], '⚪')
            print(f"   {emoji} {sig['signal']:5s} | {s.name}")
            print(f"      {sig['details']['reason']}")
        except Exception as e:
            print(f"   ❌ {s.name} 失败: {e}")

    # 4. 回测
    print(f"\n📈 近期回测:")
    for s in strategies:
        try:
            bt = s.backtest(df)
            print(f"   {s.name}: 收益 {bt['total_return']:+.2%} | 回撤 {bt['max_drawdown']:.2%} | 胜率 {bt['win_rate']:.0%} | 夏普 {bt['sharpe_ratio']:.2f}")
        except Exception as e:
            print(f"   ❌ {s.name} 回测失败: {e}")

    return {'price': price, 'signals': signals, 'data_points': len(df)}


def execute_trade(symbol, direction, trader, risk_mgr, stop_loss=None, take_profit=None, confidence=0.7):
    """执行交易（带完整风控）"""
    print(f"\n{'='*55}")
    print(f"🚀 交易执行: {symbol} {direction.upper()}")
    print(f"{'='*55}")

    # 1. 获取价格
    price = trader.get_market_price(symbol)
    if not price:
        print("❌ 无法获取价格")
        return None
    print(f"💰 当前价格: ${price:,.2f}")

    # 2. 计算止损止盈（如果未提供）
    if not stop_loss:
        if direction == 'long':
            stop_loss = price * 0.97  # 3%
            take_profit = price * 1.06  # 6%
        else:
            stop_loss = price * 1.03
            take_profit = price * 0.94
        print(f"📐 自动止损止盈: SL=${stop_loss:,.2f} TP=${take_profit:,.2f}")
    elif not take_profit:
        if direction == 'long':
            take_profit = price + (price - stop_loss) * 2
        else:
            take_profit = price - (stop_loss - price) * 2
        print(f"📐 自动止盈: TP=${take_profit:,.2f}")

    # 3. 风控检查
    can, reason = risk_mgr.check_can_trade(symbol, direction, price, stop_loss, take_profit)
    print(f"\n🛡️ 风控: {reason}")
    if not can:
        return None

    # 4. 计算仓位
    position = risk_mgr.calculate_position(symbol, price, stop_loss, confidence)
    print(f"📐 仓位: {position['coin_amount']} {symbol.split('/')[0]} ≈ ${position['position_value_usdt']:.2f}")
    print(f"   风险: ${position['risk_amount_usdt']:.2f} | 杠杆: {position['leverage_needed']}x")

    # 5. 下单
    result = trader.open_position(
        symbol=symbol,
        direction=direction,
        amount=position['position_value_usdt'],
        leverage=RISK['default_leverage'],
        stop_loss=stop_loss,
        take_profit=take_profit,
    )

    if result:
        risk_mgr.record_trade(symbol, direction, f'open_{direction}', position['position_value_usdt'], price)
        print(f"\n✅ 下单成功!")
        print(f"   订单ID: {result.get('id')}")
        print(f"   成交价: {result.get('average', 'N/A')}")
    else:
        print(f"\n❌ 下单失败")

    return result


def main():
    parser = argparse.ArgumentParser(description='🦞 Lobster Quant 量化交易系统')
    parser.add_argument('--symbol', default='BTC/USDT', help='交易对')
    parser.add_argument('--timeframe', default='4h', help='K线周期')
    parser.add_argument('--days', type=int, default=90, help='回测天数')
    parser.add_argument('--status', action='store_true', help='查看系统状态')
    parser.add_argument('--analyze-only', action='store_true', help='只分析不下单')
    parser.add_argument('--execute', action='store_true', help='执行交易')
    parser.add_argument('--direction', choices=['long', 'short'], help='交易方向')
    parser.add_argument('--stop-loss', type=float, help='止损价')
    parser.add_argument('--take-profit', type=float, help='止盈价')
    parser.add_argument('--force', action='store_true', help='跳过风控')
    parser.add_argument('--webhook', action='store_true', help='启动 Webhook 模式')
    parser.add_argument('--exchange', default='okx', help='交易所')

    args = parser.parse_args()

    # 初始化
    trader = OKXTrader()
    risk_mgr = RiskManager()

    # Webhook 模式
    if args.webhook:
        from execution.webhook_receiver import run_server
        run_server()
        return

    # 状态查询
    if args.status:
        show_status(trader, risk_mgr)
        return

    # 分析模式
    if args.analyze_only or (not args.execute and not args.direction):
        analysis = run_analysis(args.symbol, args.timeframe, args.days, args.exchange)

        # 综合建议
        if analysis and analysis.get('signals'):
            print(f"\n{'='*55}")
            print("📋 综合建议")
            long_count = sum(1 for s in analysis['signals'].values() if s['signal'] == 'LONG')
            short_count = sum(1 for s in analysis['signals'].values() if s['signal'] == 'SHORT')

            if long_count >= 2:
                print("🟢 多数策略看多 → 建议: python3 run.py --execute --direction long")
            elif short_count >= 2:
                print("🔴 多数策略看空 → 建议: python3 run.py --execute --direction short")
            else:
                print("⚪ 信号分歧，建议观望")
        return

    # 执行交易
    if args.direction:
        execute_trade(
            symbol=args.symbol,
            direction=args.direction,
            trader=trader,
            risk_mgr=risk_mgr,
            stop_loss=args.stop_loss,
            take_profit=args.take_profit,
        )
    elif args.execute:
        # 自动执行：先分析，再根据信号下单
        analysis = run_analysis(args.symbol, args.timeframe, args.days, args.exchange)
        if not analysis:
            print("❌ 分析失败，无法执行")
            return

        signals = analysis.get('signals', {})
        long_count = sum(1 for s in signals.values() if s['signal'] == 'LONG')
        short_count = sum(1 for s in signals.values() if s['signal'] == 'SHORT')

        if long_count >= 2:
            print(f"\n💡 综合信号: 做多 (多{long_count} / 空{short_count})")
            execute_trade(args.symbol, 'long', trader, risk_mgr)
        elif short_count >= 2:
            print(f"\n💡 综合信号: 做空 (多{long_count} / 空{short_count})")
            execute_trade(args.symbol, 'short', trader, risk_mgr)
        else:
            print(f"\n⚪ 信号不足，不开仓 (多{long_count} / 空{short_count})")


if __name__ == '__main__':
    main()
