# Служба распознавания речи «Хард»: faster-whisper на CPU за Unix-сокетом.
# Модель в образ НЕ входит — скачивается при первом старте в том /models,
# поэтому образ остаётся маленьким, а смена STT_MODEL не требует пересборки.
FROM python:3.12-slim

LABEL ru.hard.service="stt"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/models \
    HF_HUB_DISABLE_TELEMETRY=1 \
    STT_MODELS_DIR=/models \
    STT_SOCKET=/run/stt/stt.sock \
    STT_TOKEN_FILE=/run/secrets/stt.token \
    STT_TEMP_DIR=/tmp/stt

VOLUME ["/models"]

# Первый старт качает модель (small ≈ 460 МБ), поэтому start-period щедрый:
# пока он идёт, неудачные проверки не считаются.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15m --retries=5 \
  CMD ["python", "server.py", "--check"]

CMD ["python", "server.py"]
