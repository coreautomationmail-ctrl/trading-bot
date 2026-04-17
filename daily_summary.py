# daily_summary.py
from datetime import datetime
import pytz
import os
from bot import daily_summary_for_date, send_telegram

ET = pytz.timezone("America/New_York")

def run_daily_summary():
    today_et = datetime.now(ET)
    summary = daily_summary_for_date(today_et)
    send_telegram(summary)

if __name__ == "__main__":
    run_daily_summary()
