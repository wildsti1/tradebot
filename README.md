# tradebot

## Risk management (position sizing)

Risk settings live in `config.json` under `risk`:

- `risk_per_trade_pct`: percent of balance to risk per trade (e.g. `30` means 30% — very risky)
- `min_size`: minimum order size
- `max_size`: maximum order size

When the strategy returns `entry_price` and `stop_loss`, the bot computes:

`size = (balance * risk_per_trade_pct/100) / abs(entry_price - stop_loss)`
