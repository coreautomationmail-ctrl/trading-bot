FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir requests==2.32.3 tzdata
COPY spike_pod.py spike_scorecard.py bench.py golive.py ladder.py strategies.py thesis.py intraday_book.py registry.py forward_registry.json missed_universe.json ./
ENV STATE_DIR=/data PYTHONUNBUFFERED=1
CMD ["python", "bench.py"]
