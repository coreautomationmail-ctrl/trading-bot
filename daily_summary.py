name: Daily Summary

on:
  schedule:
    - cron: "5 20 * * 1-5"   # 4:05 PM ET, after market close
  workflow_dispatch:

jobs:
  run-summary:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v3

      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install alpaca-trade-api pandas numpy ta python-dotenv pytz matplotlib requests

      - name: Run daily summary
        env:
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python daily_summary.py
