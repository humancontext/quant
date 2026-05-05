"""
震荡通道突破策略 - 参数优化器

目标：年化 > 60%，最大回撤 < 20%
在 BTC 和 ETH 上多周期验证
"""
import pandas as pd
import numpy as np
from datetime import datetime
import json
import sys
import os
import itertools

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from data.collector import fetch_historical


def compute_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift(1)).abs()
    low_close = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def backtest_channel_breakout(df, ch_len=20, atr_len=14, sl_mult=1.5, tp_mult=3.0,
                               use_trailing=True, trail_mult=2.0, trail_act_pct=1.0,
                               position_pct=0.5, commission=0.0006, initial=200):
    """
    回测通道突破策略
    
    和 PineScript 逻辑对齐：
    - upper_ch = ta.highest(close, ch_len)[1]  (不含当前K线)
    - lower_ch = ta.lowest(close, ch_len)[1]
    - 突破 = close > upper_ch 且 close[1] <= upper_ch[1]
    """
    data = df.copy()
    data['atr'] = compute_atr(data, atr_len)
    
    # 通道（不含当前K线）
    data['upper_ch'] = data['close'].rolling(ch_len).max().shift(1)
    data['lower_ch'] = data['close'].rolling(ch_len).min().shift(1)
    
    data = data.dropna()
    if len(data) < 50:
        return None
    
    # 信号
    breakout_up = (data['close'] > data['upper_ch']) & (data['close'].shift(1) <= data['upper_ch'].shift(1))
    breakout_down = (data['close'] < data['lower_ch']) & (data['close'].shift(1) >= data['lower_ch'].shift(1))
    
    capital = initial
    position = 0  # 0=空仓, 1=多, -1=空
    entry_price = 0
    sl_price = 0
    tp_price = 0
    active_tp = 0
    trail_stop = 0
    trail_on = False
    
    trades = []
    equity_curve = []
    
    atr_series = data['atr'].values
    close_series = data['close'].values
    
    for i in range(len(data)):
        price = close_series[i]
        atr_val = atr_series[i]
        
        if np.isnan(atr_val) or atr_val == 0:
            equity_curve.append(capital)
            continue
        
        # 检查止损止盈
        if position == 1:
            if use_trailing:
                profit_pct = (price - entry_price) / entry_price * 100
                if profit_pct >= trail_act_pct:
                    trail_on = True
                if trail_on:
                    new_trail = price - atr_val * trail_mult
                    trail_stop = max(trail_stop, new_trail)
                    sl_price = trail_stop
            
            if price >= active_tp:
                pnl = (active_tp - entry_price) / entry_price * capital * position_pct
                capital += pnl * (1 - commission)
                trades.append({'type': 'long', 'entry': entry_price, 'exit': active_tp, 'pnl_pct': (active_tp - entry_price) / entry_price, 'exit_reason': 'take_profit'})
                position = 0
                trail_on = False
            elif price <= sl_price:
                pnl = (sl_price - entry_price) / entry_price * capital * position_pct
                capital += pnl * (1 - commission)
                trades.append({'type': 'long', 'entry': entry_price, 'exit': sl_price, 'pnl_pct': (sl_price - entry_price) / entry_price, 'exit_reason': 'stop_loss'})
                position = 0
                trail_on = False
                
        elif position == -1:
            if use_trailing:
                profit_pct = (entry_price - price) / entry_price * 100
                if profit_pct >= trail_act_pct:
                    trail_on = True
                if trail_on:
                    new_trail = price + atr_val * trail_mult
                    trail_stop = min(trail_stop, new_trail)
                    sl_price = trail_stop
            
            if price <= active_tp:
                pnl = (entry_price - active_tp) / entry_price * capital * position_pct
                capital += pnl * (1 - commission)
                trades.append({'type': 'short', 'entry': entry_price, 'exit': active_tp, 'pnl_pct': (entry_price - active_tp) / entry_price, 'exit_reason': 'take_profit'})
                position = 0
                trail_on = False
            elif price >= sl_price:
                pnl = (entry_price - sl_price) / entry_price * capital * position_pct
                capital += pnl * (1 - commission)
                trades.append({'type': 'short', 'entry': entry_price, 'exit': sl_price, 'pnl_pct': (entry_price - sl_price) / entry_price, 'exit_reason': 'stop_loss'})
                position = 0
                trail_on = False
        
        # 开仓
        if position == 0:
            if breakout_up.iloc[i]:
                position = 1
                entry_price = price
                sl_price = price - atr_val * sl_mult
                tp_price = price + atr_val * tp_mult
                active_tp = tp_price
                trail_stop = sl_price
                trail_on = False
            elif breakout_down.iloc[i]:
                position = -1
                entry_price = price
                sl_price = price + atr_val * sl_mult
                tp_price = price - atr_val * tp_mult
                active_tp = tp_price
                trail_stop = sl_price
                trail_on = False
        
        equity_curve.append(capital)
    
    if not trades:
        return None
    
    # 统计
    total_trades = len(trades)
    winning = [t for t in trades if t['pnl_pct'] > 0]
    losing = [t for t in trades if t['pnl_pct'] <= 0]
    final = capital
    total_return = (final - initial) / initial
    
    equity_s = pd.Series(equity_curve)
    peak = equity_s.cummax()
    dd = (equity_s - peak) / peak
    max_dd = dd.min()
    
    days = (data.index[-1] - data.index[0]).days
    annual = ((final / initial) ** (365 / max(days, 1))) - 1 if days > 0 else 0
    
    win_rate = len(winning) / total_trades if total_trades > 0 else 0
    avg_win = np.mean([t['pnl_pct'] for t in winning]) if winning else 0
    avg_loss = abs(np.mean([t['pnl_pct'] for t in losing])) if losing else 0.001
    rr = avg_win / avg_loss
    
    return {
        'total_return': total_return,
        'annual_return': annual,
        'max_drawdown': max_dd,
        'total_trades': total_trades,
        'win_rate': win_rate,
        'rr_ratio': rr,
        'final_capital': round(final, 2),
        'trades': trades,
        'equity_curve': equity_curve,
    }


def optimize(df, symbol, timeframe):
    """网格搜索最优参数"""
    print(f"\n{'='*60}")
    print(f"🔍 参数优化: {symbol} | {timeframe}")
    print(f"{'='*60}")
    
    results = []
    
    # 参数网格
    ch_lens = [10, 15, 20, 25, 30, 40]
    sl_mults = [1.0, 1.5, 2.0, 2.5]
    tp_mults = [2.0, 2.5, 3.0, 3.5, 4.0]
    trail_mults = [1.5, 2.0, 2.5]
    trail_acts = [0.5, 1.0, 1.5, 2.0]
    
    total = len(ch_lens) * len(sl_mults) * len(tp_mults) * len(trail_mults) * len(trail_acts)
    count = 0
    
    for ch_len in ch_lens:
        for sl_mult in sl_mults:
            for tp_mult in tp_mults:
                for trail_mult in trail_mults:
                    for trail_act in trail_acts:
                        count += 1
                        
                        r = backtest_channel_breakout(
                            df, ch_len=ch_len, sl_mult=sl_mult, tp_mult=tp_mult,
                            trail_mult=trail_mult, trail_act_pct=trail_act
                        )
                        
                        if r and r['total_trades'] >= 10:
                            # 综合评分：年化收益 - 回撤惩罚 - 低交易惩罚
                            score = r['annual_return'] - abs(r['max_drawdown']) * 0.5
                            if r['annual_return'] > 0:
                                score += r['win_rate'] * 0.3
                            
                            results.append({
                                'ch_len': ch_len,
                                'sl_mult': sl_mult,
                                'tp_mult': tp_mult,
                                'trail_mult': trail_mult,
                                'trail_act': trail_act,
                                'annual': r['annual_return'],
                                'max_dd': r['max_drawdown'],
                                'win_rate': r['win_rate'],
                                'rr_ratio': r['rr_ratio'],
                                'trades': r['total_trades'],
                                'total_return': r['total_return'],
                                'score': score,
                            })
    
    # 排序
    results.sort(key=lambda x: x['score'], reverse=True)
    
    # 筛选满足条件的
    qualified = [r for r in results if r['annual'] >= 0.6 and r['max_dd'] >= -0.20]
    
    print(f"\n📊 搜索完成: {count} 组合 | 有效结果: {len(results)} | 达标: {len(qualified)}")
    
    if qualified:
        print(f"\n🏆 达标配置 (年化≥60% 回撤≤20%):")
        print(f"{'通道N':>6} {'止损':>5} {'止盈':>5} {'移动止损':>8} {'激活%':>6} | {'年化':>8} {'回撤':>8} {'胜率':>6} {'盈亏比':>6} {'交易':>4}")
        print("-" * 85)
        for r in qualified[:20]:
            print(f"{r['ch_len']:>6} {r['sl_mult']:>5.1f} {r['tp_mult']:>5.1f} {r['trail_mult']:>8.1f} {r['trail_act']:>6.1f} | "
                  f"{r['annual']:>+7.1%} {r['max_dd']:>8.1%} {r['win_rate']:>5.0%} {r['rr_ratio']:>6.2f} {r['trades']:>4}")
    else:
        print("\n⚠️ 没有配置达标，显示 Top 10:")
        for r in results[:10]:
            print(f"  N={r['ch_len']} SL={r['sl_mult']} TP={r['tp_mult']} Trail={r['trail_mult']} Act={r['trail_act']}% | "
                  f"年化{r['annual']:+.1%} 回撤{r['max_dd']:.1%} 胜率{r['win_rate']:.0%}")
    
    return qualified if qualified else results[:10]


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', default='BTC/USDT')
    parser.add_argument('--timeframe', default='4h')
    parser.add_argument('--days', type=int, default=365)
    parser.add_argument('--exchange', default='okx')
    args = parser.parse_args()
    
    print(f"{'='*60}")
    print(f"🦞 通道突破策略 - 参数优化")
    print(f"目标: 年化 ≥ 60% | 回撤 ≤ 20%")
    print(f"{'='*60}")
    
    # 获取数据
    print(f"\n📥 获取 {args.symbol} {args.timeframe} 数据...")
    df = fetch_historical(args.symbol, args.timeframe, days=args.days, exchange_name=args.exchange)
    if df is None or len(df) < 100:
        print("❌ 数据不足")
        return
    
    print(f"✅ {len(df)} 条 ({df.index[0].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')})")
    
    # 优化
    qualified = optimize(df, args.symbol, args.timeframe)
    
    # 最佳配置详细回测
    if qualified:
        best = qualified[0]
        print(f"\n{'='*60}")
        print(f"🏆 最佳配置详情:")
        print(f"   通道周期: {best['ch_len']}")
        print(f"   止损: {best['sl_mult']}x ATR | 止盈: {best['tp_mult']}x ATR")
        print(f"   移动止损: {best['trail_mult']}x ATR | 激活: {best['trail_act']}%")
        print(f"   年化: {best['annual']:+.1%} | 回撤: {best['max_dd']:.1%}")
        print(f"   胜率: {best['win_rate']:.0%} | 盈亏比: {best['rr_ratio']:.2f}")
        print(f"   总收益: {best['total_return']:+.1%} | 交易次数: {best['trades']}")
        
        # 输出 PineScript 参数
        print(f"\n📋 PineScript 参数:")
        print(f"   ch_len = {best['ch_len']}")
        print(f"   sl_mult = {best['sl_mult']}")
        print(f"   tp_mult = {best['tp_mult']}")
        print(f"   trail_mult = {best['trail_mult']}")
        print(f"   trail_act = {best['trail_act']}")
    
    return qualified


if __name__ == '__main__':
    main()
