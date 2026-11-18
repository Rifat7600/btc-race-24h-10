#!/usr/bin/env python3
"""Multi-timeframe momentum bot for the make.fun BTC race.

Reads agent-fun/bars-replay/v1 newline JSON on stdin, answers every
batch with one reply on stdout. Long only, no leverage, no shorts.

Idea: two momentum clocks. A slow window (an hour of bars) says which
way the bigger move is running; a fast window (half an hour) says
whether the price is pushing that way right now. Enter only when both
agree the price is rising. Scale in with a second tranche when the
fast push strengthens. Get out when the slow trend turns down, when a
profit target is hit, or when the price trails off its peak.

Everything is deterministic: all decisions come from the bars seen so
far and the hello message. No clock, no randomness, no files, no
network. Standard library only.
"""

import json
import sys

# ---- tunable strategy parameters (fractions, not percent) ----
FAST_WIN = 30        # fast momentum window, bars
SLOW_WIN = 60        # slow momentum window, bars
ENTER_FAST = 0.004   # fast return must beat +40 bps to enter
ENTER_SLOW = 0.002   # slow return must beat +20 bps to enter ...
SLOW_SLOPE_WIN = 30  # ... or the slow return must be improving over
SLOPE_MIN = None     # this many bars by at least this much (a turn)
CROSS_UP = None      # ... or the fast return crossed up through this
CROSS_LAG = 5        # after being below zero this many bars ago
SLOW_MODE = "ret"    # slow agreement: "ret" (return positive/turning),
                     # "above" (close above slow EMA), "rising" (slow
                     # EMA rising), "above_rising" (both)
ADD_FAST = 0.0050    # fast return that triggers the second tranche
EXIT_FAST = None     # fast return below this: momentum turned, exit
EXIT_SLOW = -0.002   # slow return below -20 bps: trend turned, exit
TARGET = 0.02        # take profit: +2% above average entry cost
TRAIL = 0.008        # trailing stop: 0.8% below the peak since entry
PARTIAL_TAKE = None  # bank half the position at this gain above cost
PARTIAL_FRAC = 0.5   # share of the position the partial sale takes
FIRST_SHARE = 0.5    # first tranche as a share of equity
MAX_SLIP_BPS = 150   # max slippage allowed on any order
MIN_NOTIONAL = 1000000  # 1 USDC in micro-USDC


def main():
    f_in = sys.stdin
    f_out = sys.stdout
    closes = []
    slow_hist = []  # slow-window return seen on each past batch
    ema_hist = []   # slow EMA value seen on each past batch
    ema_slow = None
    symbol = None
    warmup = 0
    min_notional = MIN_NOTIONAL
    counter = 0
    peak = 0  # highest close seen while holding, raw price units
    took_partial = False  # half banked on this leg already

    def reply(seq, orders):
        f_out.write(json.dumps({"type": "reply", "seq": seq,
                                "orders": orders}) + "\n")
        f_out.flush()

    def buy_order(seq, notional):
        nonlocal counter
        counter += 1
        return {"side": "buy",
                "client_id": "m10-%d" % counter,
                "symbol": symbol,
                "notional_quote_units": str(int(notional)),
                "max_slippage_bps": MAX_SLIP_BPS}

    def sell_order(seq, qty):
        nonlocal counter
        counter += 1
        return {"side": "sell",
                "client_id": "m10-%d" % counter,
                "symbol": symbol,
                "qty": str(int(qty)),
                "max_slippage_bps": MAX_SLIP_BPS}

    for line in f_in:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mtype = msg.get("type")
        if mtype == "hello":
            syms = msg.get("symbols") or []
            if syms:
                symbol = syms[0].get("symbol")
            warmup = int(msg.get("warmup_bars") or 0)
            fees = msg.get("fees") or {}
            try:
                min_notional = int(fees.get("min_notional_quote_units")
                                   or MIN_NOTIONAL)
            except ValueError:
                min_notional = MIN_NOTIONAL
            continue
        if mtype == "done":
            break
        if mtype != "batch":
            continue
        seq = msg.get("seq", 0)
        try:
            bars = msg.get("bars") or []
            close_raw = None
            for bar in bars:
                if bar.get("symbol") == symbol:
                    close_raw = int(bar["c"])
                    break
            if close_raw is None and bars:
                close_raw = int(bars[0]["c"])
            if close_raw is None:
                reply(seq, [])
                continue
            closes.append(close_raw)
            # slow exponential average of closes (trend level)
            if ema_slow is None:
                ema_slow = float(close_raw)
            else:
                alpha = 2.0 / (SLOW_WIN + 1.0)
                ema_slow = alpha * close_raw + (1.0 - alpha) * ema_slow
            ema_hist.append(ema_slow)

            account = msg.get("account") or {}
            cash = int(account.get("cash_quote_units") or 0)
            equity = int(account.get("equity_quote_units") or cash)
            positions = account.get("positions") or []
            qty = 0
            cost = 0
            for p in positions:
                if p.get("symbol") == symbol:
                    qty = int(p.get("qty") or 0)
                    cost = int(p.get("cost_quote_units") or 0)
            orders = []
            n_bars = len(closes)
            if symbol is None or n_bars <= warmup or equity <= 0:
                reply(seq, orders)
                continue

            # position size as a share of equity, from the account
            # (qty_raw/1e8 BTC * close_raw/1e5 USDC * 1e6 micro)
            pos_value = qty * close_raw // 10 ** 7 if qty else 0
            share = pos_value / equity if equity else 0.0

            if qty:
                if close_raw > peak:
                    peak = close_raw
            else:
                peak = close_raw

            # momentum on both clocks; need enough history first
            fast_ret = None
            fast_ret_lag = None
            slow_ret = None
            if n_bars > FAST_WIN:
                fast_ret = closes[-1] / closes[-1 - FAST_WIN] - 1.0
            if n_bars > FAST_WIN + CROSS_LAG:
                fast_ret_lag = (closes[-1 - CROSS_LAG]
                                / closes[-1 - CROSS_LAG - FAST_WIN] - 1.0)
            if n_bars > SLOW_WIN:
                slow_ret = closes[-1] / closes[-1 - SLOW_WIN] - 1.0
            slow_hist.append(slow_ret)
            slow_turn = False
            if (SLOPE_MIN is not None and slow_ret is not None
                    and len(slow_hist) > SLOW_SLOPE_WIN
                    and slow_hist[-1 - SLOW_SLOPE_WIN] is not None):
                slow_turn = (slow_ret
                             - slow_hist[-1 - SLOW_SLOPE_WIN] > SLOPE_MIN)
            slow_ok = False
            if SLOW_MODE == "ret":
                slow_ok = (slow_ret is not None
                           and (slow_ret > ENTER_SLOW or slow_turn))
            elif SLOW_MODE == "above":
                slow_ok = close_raw > ema_slow
            elif SLOW_MODE == "rising":
                slow_ok = (len(ema_hist) > 10
                           and ema_slow > ema_hist[-11])
            elif SLOW_MODE == "above_rising":
                slow_ok = (close_raw > ema_slow and len(ema_hist) > 10
                           and ema_slow > ema_hist[-11])
            # level entry: fast push already strong; cross entry: the
            # fast clock just turned up from below zero, near a trough
            fast_level = fast_ret is not None and fast_ret > ENTER_FAST
            fast_cross = (CROSS_UP is not None and fast_ret is not None
                          and fast_ret_lag is not None
                          and fast_ret > CROSS_UP and fast_ret_lag < 0.0)

            if qty == 0:
                took_partial = False
                # flat: enter only when the fast clock pushes up and
                # the slow clock agrees - either already positive or
                # turning up over its own window
                if (fast_ret is not None and slow_ok
                        and (fast_level or fast_cross)):
                    notional = int(equity * FIRST_SHARE)
                    if notional > cash:
                        notional = cash
                    if notional >= min_notional:
                        orders.append(buy_order(seq, notional))
                        peak = close_raw
            else:
                # holding: average entry cost in raw price units
                avg_cost = cost * 10 ** 7 // qty if qty else 0
                want_out = False
                if (EXIT_FAST is not None and fast_ret is not None
                        and fast_ret < EXIT_FAST):
                    want_out = True
                if (EXIT_SLOW is not None and slow_ret is not None
                        and slow_ret < EXIT_SLOW):
                    want_out = True
                if avg_cost and close_raw >= avg_cost * (1.0 + TARGET):
                    want_out = True
                if peak and close_raw <= peak * (1.0 - TRAIL):
                    want_out = True
                if want_out:
                    orders.append(sell_order(seq, qty))
                elif (PARTIAL_TAKE is not None and not took_partial
                        and avg_cost
                        and close_raw >= avg_cost * (1.0 + PARTIAL_TAKE)):
                    # bank half the leg, let the rest ride
                    half = int(qty * PARTIAL_FRAC)
                    if half > 0 and half < qty:
                        orders.append(sell_order(seq, half))
                        took_partial = True
                elif (share < 0.75 and not took_partial
                        and fast_ret is not None
                        and fast_ret > ADD_FAST):
                    # scale in: spend the remaining cash
                    if cash >= min_notional:
                        orders.append(buy_order(seq, cash))
            reply(seq, orders)
        except Exception:
            # never break the protocol: answer with an empty reply
            try:
                reply(seq, [])
            except Exception:
                pass


if __name__ == "__main__":
    main()
