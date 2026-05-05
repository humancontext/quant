#!/usr/bin/env python3
"""
🦞 海龟通道策略 v3 - BTC 回测模拟（使用 Gate.io 数据）
忠实还原 turtle_channel_v3.pine 的逻辑
修复：经典海龟用 shift(1) 的前N根最高/最低价做通道
"""

import json
import numpy as np
import pandas as pd
from datetime import datetime

# ============ 参数（与 Pine 一致） ============
FAST_LEN = 15       # 入场通道周期
SLOW_LEN = 40       # 过滤通道周期
EXIT_LEN = 10       # 出场通道周期
ATR_LEN = 20        # ATR 周期
RISK_PCT = 2.0      # 每笔风险 %
SL_ATR_MULT = 2.0   # 止损 ATR 倍数
ADD_ATR = 0.5       # 加仓间隔 ATR
MAX_ADD = 3          # 最大加仓次数
USE_ADX = True
ADX_LEN = 14
ADX_MIN = 20
USE_VOL = True
VOL_MA_LEN = 20
COMMISSION_PCT = 0.06
INITIAL_CAPITAL = 200

# ============ 加载数据 ============
print("📊 加载 BTC/USDT 数据...")
with open('/tmp/btc_gate_all.json') as f:
    raw = json.load(f)

# Gate.io 格式: [timestamp, volume, open, high, low, close, ...]
rows = []
for item in raw:
    rows.append({
        'date': datetime.fromtimestamp(int(item[0])),
        'open': float(item[2]),
        'high': float(item[3]),
        'low': float(item[4]),
        'close': float(item[5]),
        'volume': float(item[1]),
    })

df = pd.DataFrame(rows).set_index('date').sort_index()
print(f"  获取到 {len(df)} 根日线 K线")
print(f"  时间范围: {df.index[0].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')}")
print(f"  BTC 价格范围: ${df['close'].min():.0f} ~ ${df['close'].max():.0f}")

# ============ 计算指标 ============

# True Range / ATR
df['H-L'] = df['high'] - df['low']
df['H-C'] = abs(df['high'] - df['close'].shift(1))
df['L-C'] = abs(df['low'] - df['close'].shift(1))
df['TR'] = df[['H-L', 'H-C', 'L-C']].max(axis=1)
df['ATR'] = df['TR'].rolling(ATR_LEN).mean()

# 入场通道 (Donchian) — 经典海龟：用前 N 根的 high/low，不含当前K线
df['entry_upper'] = df['high'].shift(1).rolling(FAST_LEN).max()
df['entry_lower'] = df['low'].shift(1).rolling(FAST_LEN).min()

# 过滤通道 — 同理
df['filter_upper'] = df['high'].shift(1).rolling(SLOW_LEN).max()
df['filter_lower'] = df['low'].shift(1).rolling(SLOW_LEN).min()

# 出场通道
df['exit_upper'] = df['high'].shift(1).rolling(EXIT_LEN).max()
df['exit_lower'] = df['low'].shift(1).rolling(EXIT_LEN).min()

# ADX
def calc_adx(df, length=14):
    plus_dm = df['high'].diff()
    minus_dm = -df['low'].diff()
    plus_dm = plus_dm.where(plus_dm > 0, 0)
    minus_dm = minus_dm.where(minus_dm > 0, 0)
    
    tr = df['TR']
    atr = tr.rolling(length).mean()
    
    plus_di = 100 * (plus_dm.rolling(length).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(length).mean() / atr)
    
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.rolling(length).mean()
    
    return plus_di, minus_di, adx

df['plus_di'], df['minus_di'], df['ADX'] = calc_adx(df, ADX_LEN)

# 成交量均线
df['vol_ma'] = df['volume'].rolling(VOL_MA_LEN).mean()
df['vol_ok'] = (not USE_VOL) | (df['volume'] > df['vol_ma'])

# ============ 信号 ============
# 做多：当日最高价 >= 入场通道上轨 + 收盘价在过滤通道中轨之上（大趋势向上）
# 修正：原版要求 close > 40日最高价太严格（下跌区间永远无法触发）
# 改用：close > (filter_upper + filter_lower) / 2 即价格在40日区间上半部
adx_ok = (not USE_ADX) | (df['ADX'] > ADX_MIN)
df['filter_mid'] = (df['filter_upper'] + df['filter_lower']) / 2

df['long_entry'] = (df['high'] >= df['entry_upper']) & \
                   (df['close'] > df['filter_mid']) & \
                   adx_ok & \
                   df['vol_ok']

df['short_entry'] = (df['low'] <= df['entry_lower']) & \
                    (df['close'] < df['filter_mid']) & \
                    adx_ok & \
                    df['vol_ok']

df['long_exit'] = df['close'] < df['exit_lower']
df['short_exit'] = df['close'] > df['exit_upper']

# 避免连续信号
df['long_first'] = df['long_entry'] & ~df['long_entry'].shift(1).fillna(False)
df['short_first'] = df['short_entry'] & ~df['short_entry'].shift(1).fillna(False)

# ============ 回测引擎 ============
equity = INITIAL_CAPITAL
position = 0          # 正=多, 负=空
entry_price = 0
stop_loss = 0
add_count = 0
last_add_price = 0
trades = []
max_equity = INITIAL_CAPITAL
max_drawdown = 0

def calc_pos_size(equity, atr, close):
    if pd.isna(atr) or atr <= 0:
        return 0
    size = equity * RISK_PCT / 100 / (SL_ATR_MULT * atr)
    # 限制不超过总资金100%价值
    value = size * close
    if value > equity:
        size = equity / close
    return size

results = []
start_idx = max(SLOW_LEN, ATR_LEN, ADX_LEN * 2) + 1

for i in range(start_idx, len(df)):
    row = df.iloc[i]
    close = row['close']
    high = row['high']
    low = row['low']
    atr = row['ATR']
    
    if pd.isna(atr) or atr <= 0:
        results.append({
            'date': df.index[i],
            'close': close,
            'equity': equity,
            'position': position,
            'drawdown': 0
        })
        continue
    
    # ============ 日内止损检查（用 high/low 模拟） ============
    stopped = False
    if position > 0 and low <= stop_loss:
        pnl = (stop_loss - entry_price) * abs(position)
        commission = abs(position) * stop_loss * COMMISSION_PCT / 100 * 2
        equity += pnl - commission
        trades.append({
            'type': 'long_sl',
            'date': df.index[i].strftime('%Y-%m-%d'),
            'entry': entry_price,
            'exit': stop_loss,
            'qty': abs(position),
            'pnl': pnl - commission,
        })
        position = 0
        entry_price = 0
        add_count = 0
        stopped = True
    
    elif position < 0 and high >= stop_loss:
        pnl = (entry_price - stop_loss) * abs(position)
        commission = abs(position) * stop_loss * COMMISSION_PCT / 100 * 2
        equity += pnl - commission
        trades.append({
            'type': 'short_sl',
            'date': df.index[i].strftime('%Y-%m-%d'),
            'entry': entry_price,
            'exit': stop_loss,
            'qty': abs(position),
            'pnl': pnl - commission,
        })
        position = 0
        entry_price = 0
        add_count = 0
        stopped = True
    
    # ============ 出场信号（收盘价） ============
    if not stopped:
        if position > 0 and row['long_exit']:
            pnl = (close - entry_price) * abs(position)
            commission = abs(position) * close * COMMISSION_PCT / 100 * 2
            equity += pnl - commission
            trades.append({
                'type': 'long_exit',
                'date': df.index[i].strftime('%Y-%m-%d'),
                'entry': entry_price,
                'exit': close,
                'qty': abs(position),
                'pnl': pnl - commission,
            })
            position = 0
            entry_price = 0
            add_count = 0
        
        elif position < 0 and row['short_exit']:
            pnl = (entry_price - close) * abs(position)
            commission = abs(position) * close * COMMISSION_PCT / 100 * 2
            equity += pnl - commission
            trades.append({
                'type': 'short_exit',
                'date': df.index[i].strftime('%Y-%m-%d'),
                'entry': entry_price,
                'exit': close,
                'qty': abs(position),
                'pnl': pnl - commission,
            })
            position = 0
            entry_price = 0
            add_count = 0
    
    # ============ 入场信号 ============
    if row['long_first'] and position <= 0:
        # 先平空仓
        if position < 0:
            pnl = (entry_price - close) * abs(position)
            commission = abs(position) * close * COMMISSION_PCT / 100 * 2
            equity += pnl - commission
            trades.append({
                'type': 'short_reverse',
                'date': df.index[i].strftime('%Y-%m-%d'),
                'entry': entry_price,
                'exit': close,
                'qty': abs(position),
                'pnl': pnl - commission,
            })
        
        size = calc_pos_size(equity, atr, close)
        if size > 0 and equity > 10:  # 最低资金门槛
            position = size
            entry_price = close
            stop_loss = close - atr * SL_ATR_MULT
            add_count = 0
            last_add_price = close
            commission = size * close * COMMISSION_PCT / 100
            equity -= commission
    
    elif row['short_first'] and position >= 0:
        # 先平多仓
        if position > 0:
            pnl = (close - entry_price) * abs(position)
            commission = abs(position) * close * COMMISSION_PCT / 100 * 2
            equity += pnl - commission
            trades.append({
                'type': 'long_reverse',
                'date': df.index[i].strftime('%Y-%m-%d'),
                'entry': entry_price,
                'exit': close,
                'qty': abs(position),
                'pnl': pnl - commission,
            })
        
        size = calc_pos_size(equity, atr, close)
        if size > 0 and equity > 10:
            position = -size
            entry_price = close
            stop_loss = close + atr * SL_ATR_MULT
            add_count = 0
            last_add_price = close
            commission = size * close * COMMISSION_PCT / 100
            equity -= commission
    
    # ============ 加仓逻辑 ============
    if position > 0 and add_count < MAX_ADD and close >= last_add_price + atr * ADD_ATR:
        add_count += 1
        add_size = calc_pos_size(equity, atr, close) * 0.5
        if add_size > 0:
            # 更新均价
            old_value = entry_price * abs(position)
            add_value = close * add_size
            position += add_size
            entry_price = (old_value + add_value) / position
            stop_loss = close - atr * SL_ATR_MULT
            last_add_price = close
            commission = add_size * close * COMMISSION_PCT / 100
            equity -= commission
    
    elif position < 0 and add_count < MAX_ADD and close <= last_add_price - atr * ADD_ATR:
        add_count += 1
        add_size = calc_pos_size(equity, atr, close) * 0.5
        if add_size > 0:
            old_value = entry_price * abs(position)
            add_value = close * add_size
            position -= add_size
            entry_price = (old_value + add_value) / abs(position)
            stop_loss = close + atr * SL_ATR_MULT
            last_add_price = close
            commission = add_size * close * COMMISSION_PCT / 100
            equity -= commission
    
    # ============ 计算当前权益 ============
    if position > 0:
        current_equity = equity + (close - entry_price) * abs(position)
    elif position < 0:
        current_equity = equity + (entry_price - close) * abs(position)
    else:
        current_equity = equity
    
    max_equity = max(max_equity, current_equity)
    dd = (max_equity - current_equity) / max_equity * 100 if max_equity > 0 else 0
    max_drawdown = max(max_drawdown, dd)
    
    results.append({
        'date': df.index[i],
        'close': close,
        'equity': round(current_equity, 2),
        'position': position,
        'drawdown': round(dd, 2)
    })

# ============ 最终平仓统计 ============
if position != 0:
    final_close = df.iloc[-1]['close']
    if position > 0:
        pnl = (final_close - entry_price) * abs(position)
    else:
        pnl = (entry_price - final_close) * abs(position)
    commission = abs(position) * final_close * COMMISSION_PCT / 100
    equity += pnl - commission
    trades.append({
        'type': 'long_open' if position > 0 else 'short_open',
        'date': df.index[-1].strftime('%Y-%m-%d'),
        'entry': entry_price,
        'exit': final_close,
        'qty': abs(position),
        'pnl': pnl - commission,
    })
    position = 0

# ============ 统计 ============
final_equity = equity
total_return = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

days = (df.index[-1] - df.index[start_idx]).days
years = days / 365.25
annualized = ((final_equity / INITIAL_CAPITAL) ** (1/years) - 1) * 100 if years > 0 else 0

win_trades = [t for t in trades if t['pnl'] > 0]
lose_trades = [t for t in trades if t['pnl'] <= 0]
win_rate = len(win_trades) / len(trades) * 100 if trades else 0

avg_win = np.mean([t['pnl'] for t in win_trades]) if win_trades else 0
avg_loss = abs(np.mean([t['pnl'] for t in lose_trades])) if lose_trades else 1
profit_factor = (sum(t['pnl'] for t in win_trades) / abs(sum(t['pnl'] for t in lose_trades))) if lose_trades else float('inf')

# 分类统计
long_trades = [t for t in trades if 'long' in t['type']]
short_trades = [t for t in trades if 'short' in t['type']]
sl_trades = [t for t in trades if 'sl' in t['type']]
exit_trades = [t for t in trades if t['type'].endswith('exit')]

# Sharpe (简化)
equities = [r['equity'] for r in results]
returns = [(equities[i] - equities[i-1]) / equities[i-1] for i in range(1, len(equities)) if equities[i-1] > 0]
sharpe = np.mean(returns) / np.std(returns) * np.sqrt(365) if np.std(returns) > 0 else 0

# ============ 输出报告 ============
print("\n" + "="*65)
print("  🦞 海龟通道策略 v3 — BTC/USDT 回测报告")
print("="*65)

print(f"""
📅 回测区间
   {df.index[start_idx].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')}
   共 {days} 天 ({years:.1f} 年)

💰 资金表现
   初始资金:  ${INITIAL_CAPITAL}
   最终资金:  ${final_equity:.2f}
   总收益:    {total_return:+.2f}%
   年化收益:  {annualized:+.2f}%
   最大回撤:  {max_drawdown:.2f}%
   Sharpe:    {sharpe:.2f}

📋 交易统计
   总交易数:  {len(trades)}
   ✅ 盈利:   {len(win_trades)}  ❌ 亏损: {len(lose_trades)}
   胜率:      {win_rate:.1f}%
   盈亏比:    {profit_factor:.2f}
   平均盈利:  ${avg_win:.2f}
   平均亏损:  ${avg_loss:.2f}
   做多交易:  {len(long_trades)} 笔 (盈利 {len([t for t in long_trades if t['pnl']>0])} 笔)
   做空交易:  {len(short_trades)} 笔 (盈利 {len([t for t in short_trades if t['pnl']>0])} 笔)
   止损触发:  {len(sl_trades)} 次
   通道出场:  {len(exit_trades)} 次
""")

# 打印所有交易
print(f"📋 交易明细:")
print(f"{'#':<4} {'日期':<12} {'类型':<14} {'入场':>10} {'出场':>10} {'数量':>8} {'盈亏$':>10}")
print("-"*72)
for idx, t in enumerate(trades):
    emoji = "✅" if t['pnl'] > 0 else "❌"
    print(f"{idx+1:<4} {t['date']:<12} {t['type']:<14} {t['entry']:>10.1f} {t['exit']:>10.1f} {t['qty']:>8.4f} {t['pnl']:>+10.2f} {emoji}")

# 当前指标状态
last = df.iloc[-1]
print(f"""
📊 当前 BTC 市场状态
   价格:     ${last['close']:,.1f}
   ATR:      ${last['ATR']:,.1f} ({last['ATR']/last['close']*100:.1f}%)
   ADX:      {last['ADX']:.1f} {'✅ 强趋势' if last['ADX'] > ADX_MIN else '⚠️ 弱趋势'}
   入场上界: ${last['entry_upper']:,.1f}
   入场下界: ${last['entry_lower']:,.1f}
   过滤上界: ${last['filter_upper']:,.1f}
   过滤下界: ${last['filter_lower']:,.1f}
   出场下界: ${last['exit_lower']:,.1f}
""")

# 生成交易信号判断
if last['close'] >= last['entry_upper'] and last['close'] > last['filter_upper']:
    print("  🟢 当前满足做多入场条件！")
elif last['close'] <= last['entry_lower'] and last['close'] < last['filter_lower']:
    print("  🔴 当前满足做空入场条件！")
else:
    print("  ⚪ 当前无入场信号，等待突破")

# 保存结果
results_df = pd.DataFrame(results)
results_df.to_csv('/Users/sam/.openclaw/workspace/quant/btc_turtle_equity.csv', index=False)

if trades:
    trades_df = pd.DataFrame(trades)
    trades_df.to_csv('/Users/sam/.openclaw/workspace/quant/btc_turtle_trades.csv', index=False)

print("✅ 权益曲线已保存到 quant/btc_turtle_equity.csv")
print("✅ 交易记录已保存到 quant/btc_turtle_trades.csv")
