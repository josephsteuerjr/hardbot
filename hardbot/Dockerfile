# Python 3.12 — стабильные колёса для aiogram/pydantic (3.14 пока сыроват для wheels)
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# БД и выгрузки складываем в /data (монтируется томом — переживает пересборку)
ENV HARDBOT_DATA=/data
# Логи сразу в stdout, без буферизации — видно в `docker compose logs`
ENV PYTHONUNBUFFERED=1
CMD ["python", "app.py"]
