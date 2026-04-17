name: Telegram Listener

on:
  workflow_dispatch:
  schedule:
    - cron: "0 */6 * * *"   # Restart every 6 hours

jobs:
  run-listener:
    runs-on: ubuntu-latest
    timeout-minutes: 360   # 6 hours max

    steps:
      - uses: actions/checkout@v3

      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install alpaca-trade-api pandas numpy ta python-dotenv pytz matplotlib requests

      - name: Run Telegram listener
        env:
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          ALPACA_API_KEY: ${{ secrets.ALPACA_API_KEY }}
          ALPACA_SECRET_KEY: ${{ secrets.ALPACA_SECRET_KEY }}
          ALPACA_BASE_URL: ${{ secrets.ALPACA_BASE_URL }}
        run: python listener.py
