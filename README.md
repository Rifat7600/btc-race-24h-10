# Multi-Timeframe Momentum — BTC Race Bot

[![make.fun](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/Rifat7600/btc-race-24h-10/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/Rifat7600/btc-race-24h-10/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)

A deterministic trading bot for the make.fun bounty
[Race a BTC trading bot live for 24 hours](https://make.fun/bounty/btc-race-24h-20261118-1444).
It speaks the `agent-fun/bars-replay/v1` protocol (newline-delimited JSON
on stdin/stdout) and trades one symbol (CBBTC) long-only, no leverage.

## Strategy

Two momentum clocks: a fast 30-bar return and a slow 60-bar return.
Enter with half the equity only when both agree the price is rising
(fast return above +40 bps and slow return above +20 bps). Scale in
with the remaining cash when the fast push strengthens past +50 bps.
Exit when the slow trend turns down (60-bar return below −20 bps),
on a +2% profit target, or on a 0.8% trailing stop from the peak
since entry.

Every decision comes from the bars seen so far and the hello message:
no clock, no randomness, no files, no network — two runs on the same
data produce byte-identical action logs.

## Run

```sh
python3 -P -s main.py
```

## Dev-window result (local replay)

- Net PnL: **+7.66 USDC** on 1,000 USDC start
- Trades: 6 (3 round trips)
- Max drawdown: 0.95%
- Deterministic: two runs produce identical action logs
