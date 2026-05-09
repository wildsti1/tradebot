# tradebot

## Risk management (position sizing)

Risk settings live in `config.json` under `risk`:

- `risk_per_trade_pct`: percent of balance to risk per trade (e.g. `30` means 30% — very risky)
- `min_size`: minimum order size
- `max_size`: maximum order size

Legacy config: you can also set a top-level `risk_pct`. If `risk_pct` is `0 < value <= 1` it is treated as a fraction of balance (e.g. `0.15` => 15%). If `risk_pct > 1` it is treated as a percent (e.g. `15` => 15%).

## Per-pair strategy params

Some strategies keep state (e.g. whether a position is open) and have tunable parameters (like `tp_sl_pct`).
You can set different params per pair in `config.json` using `strategy_params`.

Example (different `tp_sl_pct` for `BTCUSD`):

```json
{
  "selected_strategy": "ConsolidationBreakoutStrategy",
  "watch_pairs": ["EURUSD", "BTCUSD"],
  "risk_pct": 0.15,
  "strategy_params": {
    "ConsolidationBreakoutStrategy": {
      "default": { "lookback": 10, "tp_sl_pct": 0.5, "start_hour": 0, "end_hour": 23 },
      "pairs": {
        "BTCUSD": { "tp_sl_pct": 1.0, "lookback": 20 }
      }
    }
  }
}
```

When the strategy returns `entry_price` and `stop_loss`, the bot computes:

`size = (balance * risk_per_trade_pct/100) / abs(entry_price - stop_loss)`
