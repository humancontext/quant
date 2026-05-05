"""
震荡通道突破策略 - Python 回测

和 PineScript 策略逻辑一致，用于本地验证效果
"""
import pandas as pd
import numpy as np
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from data.collector import fetch_historical, get_current_price


class ChannelBreakoutStrategy:
    """震荡通道突破策略"""

    def __init__(self, channel_len=20, channel_type='Donchian',
                 use_trend_filter=True, ema_fast=50, ema_slow=200,
                 min_channel_width_pct=0.5,
                 atr_len=14, sl_atr_mult=1.5, tp_atr_mult=3.0,
                 use_trailing=True, trail_atr_mult=2.0, trail_activate_pct=1.0,
                 commission=0.0006):
        self.channel_len = channel_len
        self.channel_type = channel_type
        self.use_trend_filter = use_trend_filter
        self.ema_fast_len = ema_fast
        self.ema_slow_len = ema_slow
        self.min_channel_width_pct = min_channel_width_pct
        self.atr_len = atr_len
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.use_trailing = use_trailing
        self.trail_atr_mult = trail_atr_mult
        self.trail_activate_pct = trail_activate_pct / 100
        self.commission = commission
        self.name = "震荡通道突破"

    def generate_signals(self, df):
        """生成交易信号"""
        data = df.copy()

        # === ATR ===
        high_low = data['high'] - data['low']
        high_close = (data['high'] - data['close'].shift(1)).abs()
        low_close = (data['low'] - data['close'].shift(1)).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        data['atr'] = tr.rolling(self.atr_len).mean()

        # === 通道 ===
        if self.channel_type == 'Donchian':
            # 核心：用 close 的历史极值作为通道边界
            # close > 过去N根K线close最高值 = 突破上界（创新高）
            # close < 过去N根K线close最低值 = 跌破下界（创新低）
            data['upper_band'] = data['close'].rolling(self.channel_len).max().shift(1)
            data['lower_band'] = data['close'].rolling(self.channel_len).min().shift(1)
            data['middle_band'] = data['close'].rolling(self.channel_len).mean()
        elif self.channel_type == 'Keltner':
            ema = data['close'].ewm(span=self.channel_len).mean()
            data['upper_band'] = ema + data['atr'] * 1.5
            data['lower_band'] = ema - data['atr'] * 1.5
            data['middle_band'] = ema

        # 通道宽度
        data['channel_width'] = (data['upper_band'] - data['lower_band']) / data['middle_band'] * 100

        # 通道斜率
        data['channel_slope'] = (data['middle_band'] - data['middle_band'].shift(5)) / data['middle_band'].shift(5) * 100

        # 趋势过滤
        data['ema_fast'] = data['close'].ewm(span=self.ema_fast_len).mean()
        data['ema_slow'] = data['close'].ewm(span=self.ema_slow_len).mean()
        data['is_uptrend'] = data['ema_fast'] > data['ema_slow']
        data['is_downtrend'] = data['ema_fast'] < data['ema_slow']

        # 突破信号
        data['prev_close'] = data['close'].shift(1)
        data['prev_upper'] = data['upper_band'].shift(1)
        data['prev_lower'] = data['lower_band'].shift(1)

        # 突破判定：close > 通道上界（上界是前N根的最高close）
        data['breakout_up'] = data['close'] > data['upper_band']
        data['breakout_down'] = data['close'] < data['lower_band']

        data['channel_wide'] = data['channel_width'] >= self.min_channel_width_pct

        # 最终信号
        data['signal'] = 0
        long_ok = data['channel_wide'] & (data['is_uptrend'] if self.use_trend_filter else True)
        short_ok = data['channel_wide'] & (data['is_downtrend'] if self.use_trend_filter else True)
        data.loc[data['breakout_up'] & long_ok, 'signal'] = 1
        data.loc[data['breakout_down'] & short_ok, 'signal'] = -1

        return data

    def backtest(self, df, initial_capital=200):
        """回测（模拟完整交易过程）"""
        data = self.generate_signals(df)
        data = data.dropna()

        capital = initial_capital
        position = 0  # 0=空仓, 1=多, -1=空
        entry_price = 0
        sl_price = 0
        tp_price = 0
        trail_stop = 0
        trail_active = False

        trades = []
        equity_curve = []

        for i in range(len(data)):
            row = data.iloc[i]
            price = row['close']
            atr = row['atr']

            if pd.isna(atr) or atr == 0:
                equity_curve.append(capital)
                continue

            # === 检查止损止盈 ===
            if position == 1:  # 多头
                # 盈利检查移动止损
                if self.use_trailing:
                    profit_pct = (price - entry_price) / entry_price
                    if profit_pct >= self.trail_activate_pct:
                        trail_active = True
                    if trail_active:
                        new_trail = price - atr * self.trail_atr_mult
                        trail_stop = max(trail_stop, new_trail)
                        sl_price = trail_stop

                # 止盈
                if price >= tp_price:
                    pnl = (tp_price - entry_price) / entry_price * capital * 0.5  # 50%仓位
                    capital += pnl * (1 - self.commission)
                    trades.append({
                        'type': 'long', 'entry': entry_price, 'exit': tp_price,
                        'pnl_pct': (tp_price - entry_price) / entry_price,
                        'exit_reason': 'take_profit', 'time': data.index[i]
                    })
                    position = 0
                    trail_active = False
                    trail_stop = 0
                # 止损
                elif price <= sl_price:
                    pnl = (sl_price - entry_price) / entry_price * capital * 0.5
                    capital += pnl * (1 - self.commission)
                    trades.append({
                        'type': 'long', 'entry': entry_price, 'exit': sl_price,
                        'pnl_pct': (sl_price - entry_price) / entry_price,
                        'exit_reason': 'stop_loss', 'time': data.index[i]
                    })
                    position = 0
                    trail_active = False
                    trail_stop = 0

            elif position == -1:  # 空头
                if self.use_trailing:
                    profit_pct = (entry_price - price) / entry_price
                    if profit_pct >= self.trail_activate_pct:
                        trail_active = True
                    if trail_active:
                        new_trail = price + atr * self.trail_atr_mult
                        trail_stop = min(trail_stop, new_trail)
                        sl_price = trail_stop

                # 止盈
                if price <= tp_price:
                    pnl = (entry_price - tp_price) / entry_price * capital * 0.5
                    capital += pnl * (1 - self.commission)
                    trades.append({
                        'type': 'short', 'entry': entry_price, 'exit': tp_price,
                        'pnl_pct': (entry_price - tp_price) / entry_price,
                        'exit_reason': 'take_profit', 'time': data.index[i]
                    })
                    position = 0
                    trail_active = False
                    trail_stop = 0
                # 止损
                elif price >= sl_price:
                    pnl = (entry_price - sl_price) / entry_price * capital * 0.5
                    capital += pnl * (1 - self.commission)
                    trades.append({
                        'type': 'short', 'entry': entry_price, 'exit': sl_price,
                        'pnl_pct': (entry_price - sl_price) / entry_price,
                        'exit_reason': 'stop_loss', 'time': data.index[i]
                    })
                    position = 0
                    trail_active = False
                    trail_stop = 0

            # === 开仓信号 ===
            if position == 0:
                if row['signal'] == 1:  # 做多
                    position = 1
                    entry_price = price
                    sl_price = price - atr * self.sl_atr_mult
                    tp_price = price + atr * self.tp_atr_mult
                    trail_stop = sl_price
                    trail_active = False
                elif row['signal'] == -1:  # 做空
                    position = -1
                    entry_price = price
                    sl_price = price + atr * self.sl_atr_mult
                    tp_price = price - atr * self.tp_atr_mult
                    trail_stop = tp_price
                    trail_active = False

            equity_curve.append(capital)

        data = data.copy()
        data['equity'] = equity_curve

        # === 计算指标 ===
        total_trades = len(trades)
        winning = [t for t in trades if t['pnl_pct'] > 0]
        losing = [t for t in trades if t['pnl_pct'] <= 0]

        final_capital = capital
        total_return = (final_capital - initial_capital) / initial_capital
        win_rate = len(winning) / total_trades if total_trades > 0 else 0

        # 最大回撤
        equity_series = pd.Series(equity_curve)
        peak = equity_series.cummax()
        drawdown = (equity_series - peak) / peak
        max_drawdown = drawdown.min()

        # 年化收益
        days = (data.index[-1] - data.index[0]).days
        annual_return = ((final_capital / initial_capital) ** (365 / max(days, 1))) - 1 if days > 0 else 0

        # 盈亏比
        avg_win = np.mean([t['pnl_pct'] for t in winning]) if winning else 0
        avg_loss = abs(np.mean([t['pnl_pct'] for t in losing])) if losing else 0.001
        rr_ratio = avg_win / avg_loss

        return {
            'strategy': self.name,
            'initial_capital': initial_capital,
            'final_capital': round(final_capital, 2),
            'total_return': total_return,
            'annual_return': annual_return,
            'max_drawdown': max_drawdown,
            'total_trades': total_trades,
            'winning_trades': len(winning),
            'losing_trades': len(losing),
            'win_rate': win_rate,
            'avg_win_pct': avg_win,
            'avg_loss_pct': avg_loss,
            'rr_ratio': rr_ratio,
            'trades': trades,
            'data': data,
        }

    def print_report(self, results):
        """打印回测报告"""
        print("\n" + "=" * 60)
        print(f"📊 回测报告 - {results['strategy']}")
        print("=" * 60)
        print(f"💰 初始资金: ${results['initial_capital']:.2f}")
        print(f"💰 最终资金: ${results['final_capital']:.2f}")
        print(f"📈 总收益: {results['total_return']:+.2%}")
        print(f"📈 年化收益: {results['annual_return']:+.2%}")
        print(f"📊 最大回撤: {results['max_drawdown']:.2%}")
        print(f"🔄 总交易: {results['total_trades']} | 胜: {results['winning_trades']} | 负: {results['losing_trades']}")
        print(f"✅ 胜率: {results['win_rate']:.1%}")
        print(f"📐 平均盈利: {results['avg_win_pct']:+.2%} | 平均亏损: {results['avg_loss_pct']:+.2%}")
        print(f"⚖️ 盈亏比: {results['rr_ratio']:.2f}")

        # 最近交易明细
        if results['trades']:
            print(f"\n📋 最近 10 笔交易:")
            recent = results['trades'][-10:]
            for t in recent:
                emoji = '✅' if t['pnl_pct'] > 0 else '❌'
                direction = '🟢多' if t['type'] == 'long' else '🔴空'
                exit_r = {'take_profit': '🎯止盈', 'stop_loss': '🛑止损'}.get(t['exit_reason'], t['exit_reason'])
                time_str = t['time'].strftime('%m/%d %H:%M') if hasattr(t['time'], 'strftime') else str(t['time'])
                print(f"   {emoji} {direction} {time_str} | 入${t['entry']:,.0f} → 出${t['exit']:,.0f} | {t['pnl_pct']:+.2%} | {exit_r}")

        print("=" * 60)


def run_backtest(symbol='BTC/USDT', timeframe='4h', days=180, exchange='okx'):
    """运行多参数回测"""
    print(f"\n{'='*60}")
    print(f"🦞 震荡通道突破策略 - 回测")
    print(f"标的: {symbol} | 周期: {timeframe} | 天数: {days}")
    print(f"{'='*60}")

    # 获取数据
    print(f"\n📥 获取数据...")
    df = fetch_historical(symbol, timeframe, days=days, exchange_name=exchange)
    if df is None or len(df) < 100:
        print("❌ 数据不足")
        return

    print(f"✅ {len(df)} 条数据 ({df.index[0].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')})")

    # === 参数组合测试 ===
    configs = [
        {'name': '默认 (Donchian 20)', 'channel_len': 20, 'channel_type': 'Donchian',
         'sl_atr_mult': 1.5, 'tp_atr_mult': 3.0, 'use_trend_filter': True},
        {'name': '宽通道 (Donchian 40)', 'channel_len': 40, 'channel_type': 'Donchian',
         'sl_atr_mult': 1.5, 'tp_atr_mult': 3.0, 'use_trend_filter': True},
        {'name': '激进 (Donchian 20 宽止损)', 'channel_len': 20, 'channel_type': 'Donchian',
         'sl_atr_mult': 2.0, 'tp_atr_mult': 4.0, 'use_trend_filter': True},
        {'name': '无趋势过滤', 'channel_len': 20, 'channel_type': 'Donchian',
         'sl_atr_mult': 1.5, 'tp_atr_mult': 3.0, 'use_trend_filter': False},
        {'name': '保守 (Donchian 30 窄止损)', 'channel_len': 30, 'channel_type': 'Donchian',
         'sl_atr_mult': 1.0, 'tp_atr_mult': 2.5, 'use_trend_filter': True},
    ]

    best_result = None
    best_config = None

    for cfg in configs:
        name = cfg.pop('name')
        strategy = ChannelBreakoutStrategy(**cfg)
        results = strategy.backtest(df)
        results['config_name'] = name

        print(f"\n--- {name} ---")
        print(f"   收益: {results['total_return']:+.2%} | 回撤: {results['max_drawdown']:.2%} | "
              f"胜率: {results['win_rate']:.0%} | 盈亏比: {results['rr_ratio']:.2f} | "
              f"交易: {results['total_trades']}")

        if best_result is None or results['total_return'] > best_result['total_return']:
            best_result = results
            best_config = name

    # 最佳策略详细报告
    print(f"\n\n🏆 最佳配置: {best_config}")
    best_strategy = ChannelBreakoutStrategy()
    best_result = best_strategy.backtest(df)
    best_strategy.print_report(best_result)

    return best_result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', default='BTC/USDT')
    parser.add_argument('--timeframe', default='4h')
    parser.add_argument('--days', type=int, default=180)
    parser.add_argument('--exchange', default='okx')
    args = parser.parse_args()

    run_backtest(args.symbol, args.timeframe, args.days, args.exchange)
