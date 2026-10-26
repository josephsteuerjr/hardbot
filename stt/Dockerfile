# Служба распознавания речи «Хард»: faster-whisper на CPU за Unix-сокетом.
#
# МОДЕЛЬ ЕДЕТ В ОБРАЗЕ, А НЕ КАЧАЕТСЯ ПРИ ПЕРВОМ СТАРТЕ. Раньше было наоборот, и
# это значило три условия сразу: на объекте есть интернет, из него доступен
# именно huggingface.co, и первый запуск длится столько, сколько качается
# полтора гигабайта. Не выполнилось любое — служба не поднялась.
FROM python:3.12-slim

LABEL ru.hard.service="stt"

WORKDIR /app
COPY requirements.txt .
# Bootstrap pip по проверенному hash: у него отдельный встроенный certifi.
RUN python -m pip install --no-cache-dir --no-deps "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"
RUN sed -i -E '/^[^#!].*\/(emSign_ECC_Root_CA_-_(G3|C3)|emSign_Root_CA_-_C1|Comodo_AAA_Services_root)\.crt$/s/^/!/' /etc/ca-certificates.conf \
    && update-ca-certificates
RUN pip install --no-cache-dir -r requirements.txt

# Модель кладёт сюда `python tools/prepare_stt_model.py` (в репозитории каталог
# пустой). Собрать образ без модели можно — служба тогда честно скажет, что
# модели нет, и не полезет ни в какую сеть.
#
# ПУТЬ НАМЕРЕННО НЕ /models. На /models compose вешает том под кэш скачивания,
# а том перекрывает всё, что лежало в образе по этому пути: запечённая модель
# просто исчезла бы, и понять почему было бы нельзя. /opt/hard/stt-model никто
# не монтирует, и перекрыть его случайно невозможно.
COPY model /opt/hard/stt-model

COPY server.py .

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/models \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    STT_BAKED_MODEL_DIR=/opt/hard/stt-model \
    STT_MODELS_DIR=/models \
    STT_SOCKET=/run/stt/stt.sock \
    STT_TOKEN_FILE=/run/stt/stt.token \
    STT_TEMP_DIR=/tmp/stt

VOLUME ["/models"]

# Модель уже в образе, качать нечего: замерено — large-v3-turbo в int8 грузится
# за 8–10 с. Прежние 15 минут ожидания были рассчитаны на скачивание и означали
# ровно одно: сломанная служба пятнадцать минут молчала, прежде чем признаться.
# Три минуты — с большим запасом на медленный диск, но уже похоже на «сразу».
# Кто включил STT_ALLOW_DOWNLOAD=1, тому start-period надо вернуть длинный:
# healthcheck переопределяется в compose.
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=5 \
  CMD ["python", "server.py", "--check"]

CMD ["python", "server.py"]
