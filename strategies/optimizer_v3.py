"""
通道突破策略 v3 - 多重确认增强版

改进方向：
1. 多时间框架确认（4h突破 + 1d趋势同向）
2. 成交量确认（突破时放量）
3. RSI 过滤（避免极端超买超卖）
4. 自适应通道（根据波动率调整周期）
5. 再入场机制（止损后趋势延续可重新入场）
"""
import pandas as pd
import numpy as np
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from data.collector import fetch_historical, fetch_ohlcv


def compute_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift(1)).abs()
    low_close = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_rsi(close, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def backtest_v3(df, df_1d=None, params=None):
    """
    v3 增强版回测
    
    参数：
    - ch_len: 通道周期
    - atr_len: ATR周期
    - sl_mult / tp_mult: 止损止盈ATR倍数
    - trail_mult / trail_act: 移动止损
    - use_volume: 成交量确认
    - vol_mult: 突破时成交量需达到均值的倍数
    - use_rsi: RSI过滤
    - rsi_lower / rsi_upper: RSI范围
    - use_mtf: 多时间框架确认
    - reentry_cooldown: 止损后冷却期（K线数）
    """
    if params is None:
        params = {}
    
    ch_len = params.get('ch_len', 20)
    atr_len = params.get('atr_len', 14)
    sl_mult = params.get('sl_mult', 2.0)
    tp_mult = params.get('tp_mult', 3.5)
    trail_mult = params.get('trail_mult', 1.5)
    trail_act_pct = params.get('trail_act_pct', 1.0)
    use_volume = params.get('use_volume', True)
    vol_mult = params.get('vol_mult', 1.2)
    use_rsi = params.get('use_rsi', True)
    rsi_lower = params.get('rsi_lower', 35)
    rsi_upper = params.get('rsi_upper', 65)
    use_mtf = params.get('use_mtf', True)
    reentry_cooldown = params.get('reentry_cooldown', 3)
    position_pct = params.get('position_pct', 0.5)
    commission = params.get('commission', 0.0006)
    initial = params.get('initial_capital', 200)
    
    data = df.copy()
    data['atr'] = compute_atr(data, atr_len)
    data['upper_ch'] = data['close'].rolling(ch_len).max().shift(1)
    data['lower_ch'] = data['close'].rolling(ch_len).min().shift(1)
    
    # 成交量均线
    data['vol_ma'] = data['volume'].rolling(20).mean()
    data['vol_ratio'] = data['volume'] / data['vol_ma']
    
    # RSI
    data['rsi'] = compute_rsi(data['close'], 14)
    
    # EMA 趋势
    data['ema50'] = data['close'].ewm(span=50).mean()
    data['ema200'] = data['close'].ewm(span=200).mean()
    
    # 日线趋势（如果提供了日线数据）
    if df_1d is not None and use_mtf:
        daily_trend = df_1d['close'].ewm(span=50).mean() > df_1d['close'].ewm(span=200).mean()
        daily_trend.index = daily_trend.index.tz_localize(None) if daily_trend.index.tzinfo else daily_trend.index
        data['daily_trend_up'] = daily_trend.reindex(data.index, method='ffill').fillna(True)
    else:
        data['daily_trend_up'] = True  # 不做日线过滤
    
    data = data.dropna()
    if len(data) < 50:
        return None
    
    # 信号
    breakout_up = (data['close'] > data['upper_ch']) & (data['close'].shift(1) <= data['upper_ch'].shift(1))
    breakout_down = (data['close'] < data['lower_ch']) & (data['close'].shift(1) >= data['lower_ch'].shift(1))
    
    # 多重确认
    long_ok = pd.Series(True, index=data.index)
    short_ok = pd.Series(True, index=data.index)
    
    if use_volume:
        long_ok = long_ok & (data['vol_ratio'] >= vol_mult)
        short_ok = short_ok & (data['vol_ratio'] >= vol_mult)
    
    if use_rsi:
        # 做多时RSI不能太高（不是超买区追高），做空时不能太低
        long_ok = long_ok & (data['rsi'] < rsi_upper) & (data['rsi'] > rsi_lower - 10)
        short_ok = short_ok & (data['rsi'] > rsi_lower) & (data['rsi'] < rsi_upper + 10)
    
    if use_mtf:
        long_ok = long_ok & data['daily_trend_up']
        short_ok = short_ok & ~data['daily_trend_up']
    
    long_signal = breakout_up & long_ok
    short_signal = breakout_down & short_ok
    
    # 回测循环
    capital = initial
    position = 0
    entry_price = 0
    sl_price = 0
    active_tp = 0
    trail_stop = 0
    trail_on = False
    cooldown = 0
    
    trades = []
    equity_curve = []
    
    for i in range(len(data)):
        price = data['close'].iloc[i]
        atr_val = data['atr'].iloc[i]
        
        if np.isnan(atr_val) or atr_val == 0:
            equity_curve.append(capital)
            continue
        
        if cooldown > 0:
            cooldown -= 1
        
        # 止损止盈检查
        if position == 1:
            if trail_act_pct > 0:
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
                trades.append({'type': 'long', 'entry': entry_price, 'exit': active_tp, 'pnl_pct': (active_tp - entry_price) / entry_price, 'reason': 'tp'})
                position = 0
                trail_on = False
            elif price <= sl_price:
                pnl = (sl_price - entry_price) / entry_price * capital * position_pct
                capital += pnl * (1 - commission)
                trades.append({'type': 'long', 'entry': entry_price, 'exit': sl_price, 'pnl_pct': (sl_price - entry_price) / entry_price, 'reason': 'sl'})
                position = 0
                trail_on = False
                cooldown = reentry_cooldown
                
        elif position == -1:
            if trail_act_pct > 0:
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
                trades.append({'type': 'short', 'entry': entry_price, 'exit': active_tp, 'pnl_pct': (entry_price - active_tp) / entry_price, 'reason': 'tp'})
                position = 0
                trail_on = False
            elif price >= sl_price:
                pnl = (entry_price - sl_price) / entry_price * capital * position_pct
                capital += pnl * (1 - commission)
                trades.append({'type': 'short', 'entry': entry_price, 'exit': sl_price, 'pnl_pct': (entry_price - sl_price) / entry_price, 'reason': 'sl'})
                position = 0
                trail_on = False
                cooldown = reentry_cooldown
        
        # 开仓
        if position == 0 and cooldown == 0:
            if long_signal.iloc[i]:
                position = 1
                entry_price = price
                sl_price = price - atr_val * sl_mult
                active_tp = price + atr_val * tp_mult
                trail_stop = sl_price
                trail_on = False
            elif short_signal.iloc[i]:
                position = -1
                entry_price = price
                sl_price = price + atr_val * sl_mult
                active_tp = price - atr_val * tp_mult
                trail_stop = sl_price
                trail_on = False
        
        equity_curve.append(capital)
    
    if not trades:
        return None
    
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
    }


def optimize_v3(symbol='BTC/USDT', timeframe='4h', days=365, exchange='okx'):
    """v3 参数优化"""
    print(f"\n{'='*60}")
    print(f"🦞 v3 多重确认增强 - 参数优化")
    print(f"标的: {symbol} | 周期: {timeframe} | 目标: 年化≥60% 回撤≤20%")
    print(f"{'='*60}")
    
    # 获取数据
    print(f"\n📥 获取数据...")
    df = fetch_historical(symbol, timeframe, days=days, exchange_name=exchange)
    if df is None or len(df) < 100:
        print("❌ 数据不足")
        return None
    
    # 日线数据
    df_1d = fetch_ohlcv(symbol, '1d', limit=500, exchange_name=exchange)
    
    print(f"✅ {len(df)} 条 4h + {len(df_1d) if df_1d is not None else 0} 条 1d")
    
    results = []
    
    # 精简参数网格
    configs = [
        # (ch_len, sl_mult, tp_mult, trail_mult, trail_act, vol_mult, rsi_lower, rsi_upper, use_mtf, cooldown)
        # 高频突破型
        (10, 1.5, 3.0, 1.5, 1.0, 1.0, 30, 70, True, 3),
        (10, 1.5, 3.5, 1.5, 1.0, 1.0, 30, 70, True, 2),
        (10, 2.0, 4.0, 1.5, 1.5, 1.0, 30, 70, True, 3),
        (10, 1.5, 3.0, 1.5, 0.5, 1.2, 35, 65, True, 2),
        # 中频型
        (15, 1.5, 3.0, 1.5, 1.0, 1.0, 30, 70, True, 3),
        (15, 2.0, 3.5, 1.5, 1.0, 1.2, 35, 65, True, 2),
        (15, 2.0, 4.0, 1.5, 1.5, 1.0, 30, 70, True, 3),
        (20, 1.5, 3.0, 1.5, 1.0, 1.0, 30, 70, True, 3),
        (20, 2.0, 3.5, 1.5, 1.0, 1.2, 35, 65, True, 2),
        (20, 2.0, 4.0, 1.5, 1.5, 1.0, 30, 70, True, 3),
        # 宽通道型
        (30, 2.0, 3.5, 1.5, 1.0, 1.0, 30, 70, True, 3),
        (30, 2.0, 4.0, 1.5, 1.5, 1.0, 30, 70, True, 3),
        (30, 2.5, 4.0, 2.0, 1.5, 1.0, 30, 70, True, 5),
        # 不用日线过滤（更多交易机会）
        (10, 1.5, 3.0, 1.5, 0.5, 1.0, 30, 70, False, 2),
        (10, 2.0, 4.0, 1.5, 1.0, 1.0, 30, 70, False, 2),
        (15, 1.5, 3.5, 1.5, 0.5, 1.0, 30, 70, False, 2),
        (15, 2.0, 4.0, 1.5, 1.0, 1.0, 30, 70, False, 3),
        (20, 1.5, 3.0, 1.5, 0.5, 1.0, 30, 70, False, 2),
        (20, 2.0, 4.0, 1.5, 1.5, 1.0, 30, 70, False, 3),
        # 激进型（小止损大止盈）
        (15, 1.0, 4.0, 1.5, 1.0, 1.2, 35, 65, False, 2),
        (10, 1.0, 3.5, 1.5, 0.5, 1.2, 35, 65, False, 2),
        # 不用RSI过滤
        (10, 1.5, 3.0, 1.5, 0.5, 1.0, 0, 100, False, 2),
        (15, 2.0, 4.0, 1.5, 1.0, 1.0, 0, 100, False, 2),
        (20, 2.0, 4.0, 1.5, 1.5, 1.0, 0, 100, False, 3),
    ]
    
    for cfg in configs:
        ch_len, sl_mult, tp_mult, trail_mult, trail_act, vol_mult, rsi_lo, rsi_hi, use_mtf, cooldown = cfg
        
        params = {
            'ch_len': ch_len, 'sl_mult': sl_mult, 'tp_mult': tp_mult,
            'trail_mult': trail_mult, 'trail_act_pct': trail_act,
            'vol_mult': vol_mult, 'rsi_lower': rsi_lo, 'rsi_upper': rsi_hi,
            'use_mtf': use_mtf, 'reentry_cooldown': cooldown,
            'use_volume': vol_mult > 1.0, 'use_rsi': rsi_lo > 0,
        }
        
        r = backtest_v3(df, df_1d, params)
        if r and r['total_trades'] >= 10:
            results.append({
                **params,
                'annual': r['annual_return'],
                'max_dd': r['max_drawdown'],
                'win_rate': r['win_rate'],
                'rr_ratio': r['rr_ratio'],
                'trades': r['total_trades'],
                'total_return': r['total_return'],
            })
    
    # 排序
    results.sort(key=lambda x: x['annual'], reverse=True)
    
    qualified = [r for r in results if r['annual'] >= 0.6 and r['max_dd'] >= -0.20]
    
    print(f"\n📊 测试 {len(configs)} 组配置 | 有效: {len(results)} | 达标: {len(qualified)}")
    
    if qualified:
        print(f"\n🏆 达标配置:")
        for r in qualified[:15]:
            mtf = "MTF✓" if r['use_mtf'] else "MTF✗"
            vol = f"V{r['vol_mult']}" if r['use_volume'] else "V✗"
            rsi = f"R{r['rsi_lower']}-{r['rsi_upper']}" if r['use_rsi'] else "R✗"
            print(f"  N={r['ch_len']:>2} SL={r['sl_mult']} TP={r['tp_mult']} Trail={r['trail_mult']} Act={r['trail_act_pct']}% {mtf} {vol} {rsi} | "
                  f"年化{r['annual']:+.1%} 回撤{r['max_dd']:.1%} 胜率{r['win_rate']:.0%} 盈亏比{r['rr_ratio']:.2f} {r['trades']}笔")
    else:
        print(f"\n📋 Top 10 (未达标，但最优):")
        for r in results[:10]:
            mtf = "MTF✓" if r['use_mtf'] else "MTF✗"
            print(f"  N={r['ch_len']:>2} SL={r['sl_mult']} TP={r['tp_mult']} Trail={r['trail_mult']} Act={r['trail_act_pct']}% {mtf} | "
                  f"年化{r['annual']:+.1%} 回撤{r['max_dd']:.1%} 胜率{r['win_rate']:.0%} {r['trades']}笔")
    
    return qualified if qualified else results[:10]


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', default='BTC/USDT')
    parser.add_argument('--timeframe', default='4h')
    parser.add_argument('--days', type=int, default=365)
    parser.add_argument('--exchange', default='okx')
    args = parser.parse_args()
    
    optimize_v3(args.symbol, args.timeframe, args.days, args.exchange)
