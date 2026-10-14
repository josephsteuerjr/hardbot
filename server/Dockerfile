# Python 3.12 — стабильные колёса для aiogram/pydantic (3.14 пока сыроват для wheels).
#
# Версия базового образа указана до патча НАМЕРЕННО. Тег «3.12-slim» переезжает:
# сегодня это 3.12.13, через месяц — другое, и один и тот же архив исходников
# соберётся в разные образы. Чужой ИТ-службе такую разницу отлаживать нечем.
# Обновление базы — осознанная правка этой строки с последующим прогоном тестов.
FROM python:3.12.13-slim-bookworm

# Метка — по ней update.sh чистит только НАШИ устаревшие слои (docker image prune).
LABEL ru.hard.service="app"

# Тише в журнале сборки и без обращения к сети за «а нет ли нового pip».
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

# ЗАПАСНОЙ ПУТЬ «сборка без интернета»: если рядом с этим файлом положить
# каталог wheelhouse/ с колёсами (его готовит deploy/offline/bundle.sh), сборку
# можно провести с --build-arg PIP_FLAGS="--no-index --find-links=/build".
# Запись «wheelhous[e]» — это шаблон имени, а не опечатка: шаблон, который ни с
# чем не совпал, COPY просто пропускает, тогда как обычный COPY несуществующего
# каталога обрывает сборку. Так одна и та же строка работает в обоих случаях.
COPY requirements.txt wheelhous[e]/ /build/
ARG PIP_FLAGS=""
RUN pip install --no-cache-dir ${PIP_FLAGS} -r /build/requirements.txt && rm -rf /build

COPY . .

# БД и выгрузки складываем в /data (монтируется томом — переживает пересборку)
ENV APP_DATA=/data
# Логи сразу в stdout, без буферизации — видно в `docker compose logs`
ENV PYTHONUNBUFFERED=1
HEALTHCHECK --interval=10s --timeout=4s --start-period=30s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ready.json', timeout=3).read(1)"
CMD ["python", "app.py"]
