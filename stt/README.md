# Служба распознавания речи

Свой контейнер для голосовых комментариев выездника: сотрудник надиктовывает
текст в приложении, сервер отдаёт запись сюда, назад приходит текст. Внутри —
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) на CPU, модель по
выбору (см. таблицу памяти ниже).

Служба **не имеет TCP-порта**. Она слушает только Unix-сокет в общем томе,
требует Bearer-токен из файла и обрабатывает один запрос за раз. В журнал не
попадают ни аудио, ни имена файлов, ни распознанный текст — только код ответа,
язык и длительность.

Запускается вместе с приложением через `deploy/docker-compose.yml`; отдельно
ставить ничего не нужно. Этот файл — справка для тех, кто хочет понять, что
внутри, или поднять службу вручную.

## Контракт

Все ответы — JSON. Схемы `praxis.stt.*` — это версия проводного формата, под
которую написан клиент `hardbot/stt_client.py`; менять их нельзя без смены
клиента.

| Запрос | Ответ |
|---|---|
| `GET /healthz` (Bearer) | `200 {"schema":"praxis.stt.health.v1","status":"ready","ready":true,"busy":false}`; пока модель грузится — `503` с `"status":"warming"` |
| `POST /v1/transcriptions` (Bearer, multipart: `audio` — байты, `language` — `ru`/`uz`/`auto`) | `200 {"schema":"praxis.stt.v1","request_id":"…","text":"…","language":"ru"}` |

Ошибки: `{"schema":"praxis.stt.error.v1","request_id":"…","error":{"code":"…","message":"…"}}`

| Статус | `code` | Когда |
|---|---|---|
| 401 | `unauthorized` | нет или неверный токен (заголовок `WWW-Authenticate: Bearer`) |
| 400 | `invalid_multipart`, `missing_audio`, `empty_audio`, `missing_language`, `invalid_language`, `unexpected_field`, `duplicate_*`, `field_too_large` | форма собрана неправильно |
| 408 | `upload_timeout` | тело не дошло за `STT_UPLOAD_TIMEOUT` секунд |
| 413 | `audio_too_large` | больше `STT_MAX_BYTES` (потолок 15 MiB) |
| 415 | `unsupported_media_type` | не `multipart/form-data` |
| 422 | `transcription_failed` | файл не декодируется или модель не справилась |
| 429 | `busy` (`Retry-After: 2`) | модель занята другим запросом |
| 429 | `rate_limited` (`Retry-After: N`) | превышен лимит `STT_RATE_PER_MINUTE`/`STT_BURST` |
| 503 | `warming` (`Retry-After: 2`) | модель ещё грузится |
| 500 | `internal_error` | неожиданный сбой; подробности только в журнале, без данных |

## Настройки (env)

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `STT_MODEL` | `small` | модель faster-whisper: `tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo` или путь/repo id |
| `STT_COMPUTE_TYPE` | `int8` | точность вычислений; `int8` — самый экономный на CPU |
| `STT_DEVICE` | `cpu` | `cpu`; `cuda` только при наличии GPU и соответствующего образа |
| `STT_THREADS` | `0` | потоков CPU на распознавание; `0` = по числу ядер, но не больше 4 |
| `STT_BEAM_SIZE` | `5` | ширина поиска; меньше — быстрее и чуть хуже |
| `STT_MODELS_DIR` | `/models` | кэш моделей (том); первый старт скачивает модель сюда |
| `STT_SOCKET` | `/run/stt/stt.sock` | путь сокета в общем томе (права 0660) |
| `STT_TOKEN_FILE` | `/run/secrets/stt.token` | файл с токеном: 32+ печатных ASCII-символов, права не шире 0640 |
| `STT_MAX_BYTES` | `15728640` | лимит аудио (не больше 15 MiB — столько же режет клиент) |
| `STT_RATE_PER_MINUTE` | `6` | запросов в минуту после исчерпания запаса |
| `STT_BURST` | `2` | запас запросов «подряд» |
| `STT_UPLOAD_TIMEOUT` | `30` | секунд на приём тела |
| `STT_TEMP_DIR` | `/tmp/stt` | куда класть аудио на время распознавания; в compose это tmpfs |

Загрузка модели при старте идёт в фоне: сокет отвечает сразу, но `/healthz`
отдаёт `503 warming`, а распознавание — `503` с `Retry-After`, пока модель не
готова. Приложение показывает выезднику «голос временно недоступен» и оставляет
ручной ввод.

## Память и модели

Замеры на CPU, `int8`, один запрос в работе (RSS контейнера по `docker stats`):

| Модель | Память | Скачивание | Русский/узбекский |
|---|---|---|---|
| `small` | ≈ 0,7 ГБ | ≈ 460 МБ | достаточно для коротких надиктовок; по умолчанию |
| `medium` | ≈ 1,8 ГБ | ≈ 1,5 ГБ | заметно лучше на шумной записи |
| `large-v3` | ≈ 3,5 ГБ | ≈ 3 ГБ | лучшее качество, в 3–4 раза медленнее `small` |

Смена модели — правка `STT_MODEL` в `deploy/.env` и `docker compose up -d stt`:
образ пересобирать не нужно, новая модель скачается в тот же том.

## Проверка вручную

Изнутри контейнера (так работает healthcheck):

```bash
docker compose exec stt python server.py --check      # «готова» / «не готова: warming»
```

Через сокет из контейнера приложения:

```bash
docker compose exec app python -c "
import asyncio, stt_client
c = stt_client.STTClient(token_file='/run/secrets/stt.token', unix_socket='/run/stt/stt.sock')
print(asyncio.run(c.health()))
"
```

Или `curl` с хоста, если сокет виден (`docker volume inspect`):

```bash
curl --unix-socket /путь/к/stt.sock -H "Authorization: Bearer $(cat secrets/stt.token)" http://stt/healthz
```

## Тесты

`tests/test_stt_service.py` гоняет всю HTTP-часть на подменном распознавателе
без модели (секунды, без сети). Тест с настоящей моделью запускается только
при наличии `faster-whisper`, каталога моделей в `STT_MODELS_DIR` и
синтетической записи `_local/praxis-stt-canary-ru.mp3`; иначе пропускается.
