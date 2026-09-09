# safety.py
import os

from notifications import send_telegram

# We're still in the paper-trading validation phase. This must be flipped to
# "true" (env var, not this file) only once we've decided to trade real money —
# it's a second, explicit gate independent of whatever ALPACA_BASE_URL points to.
# Shared by every script that can submit orders (bot.py, position_manager.py).
LIVE_TRADING_APPROVED = os.getenv("LIVE_TRADING_APPROVED", "false").lower() == "true"


def enforce_paper_mode():
    base_url = os.getenv("ALPACA_BASE_URL", "")
    is_live_endpoint = "paper-api" not in base_url
    if is_live_endpoint and not LIVE_TRADING_APPROVED:
        send_telegram(
            "🛑 *Blocked: live endpoint without go-live approval*\n"
            f"`ALPACA_BASE_URL={base_url}`\n"
            "Set `LIVE_TRADING_APPROVED=true` only when ready to trade real money."
        )
        raise SystemExit("Refusing to trade: live endpoint but LIVE_TRADING_APPROVED is not set.")
