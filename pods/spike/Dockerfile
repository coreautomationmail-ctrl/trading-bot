FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir requests==2.32.3 tzdata
COPY spike_pod.py spike_scorecard.py missed_report.py missed_universe.json world_recorder.py intraday_snapshot.py ./
ENV STATE_DIR=/data PYTHONUNBUFFERED=1
CMD ["python", "spike_pod.py"]
