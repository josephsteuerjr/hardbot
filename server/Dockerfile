# Python 3.12 — стабильные колёса для aiogram/pydantic (3.14 пока сыроват для wheels)
FROM python:3.12-slim

# Метка — по ней update.sh чистит только НАШИ устаревшие слои (docker image prune).
LABEL ru.hard.service="app"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# БД и выгрузки складываем в /data (монтируется томом — переживает пересборку)
ENV APP_DATA=/data
# Логи сразу в stdout, без буферизации — видно в `docker compose logs`
ENV PYTHONUNBUFFERED=1
HEALTHCHECK --interval=10s --timeout=4s --start-period=30s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/', timeout=3).read(1)"
CMD ["python", "app.py"]
