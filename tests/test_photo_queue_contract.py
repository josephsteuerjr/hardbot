"""Статический контракт iPhone photo queue без браузерных зависимостей.

Поведенческий серверный путь проверяет test_photo_upload.py; здесь фиксируем
критические свойства единственного vanilla-JS файла, чтобы будущий рефакторинг
не вернул File→IndexedDB→FormData и не заблокировал очередь одним 4xx.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "server" / "miniapp" / "index.html").read_text("utf-8")


def test_selected_file_is_materialized_before_geolocation_wait():
    """Порядок важнее конкретной строки: iOS выгружает PWA на любом await.

    Раньше тест был прибит к точному литералу и падал от любой правки рядом.
    Проверяем сам инвариант: байты получены → очередь committed → только потом
    ждём геолокацию.
    """
    materialize = INDEX.index("prepared.push({bytes:await photoBytes(")
    durable_queue = INDEX.index("if(!(await enqueue(entry)))", materialize)
    geolocation = INDEX.index("geo=await grabGeo(4000,5000);", materialize)
    assert materialize < durable_queue < geolocation
    assert 'fd.append("photo"' not in INDEX


def test_photo_is_shrunk_before_it_is_materialized():
    """Жать надо ДО чтения в ArrayBuffer.

    04.08.2026: снимок с телефона 3-8 МБ уходил по мобильной сети 1-2 минуты, и
    сеть рвала соединение — «POST /api/photo 200 120.209» рядом с
    ConnectionResetError и 500. Если сжать ПОСЛЕ материализации, восьмимегабайтный
    оригинал всё равно ляжет в память и в IndexedDB.
    """
    shrink = INDEX.index("const small=await shrinkPhoto(f);")
    materialize = INDEX.index("photoBytes(small||f)", shrink)
    assert shrink < materialize
    # Провал сжатия не должен терять фото — уходит оригинал
    assert "photoBytes(small||f)" in INDEX


def test_photo_geo_does_not_depend_on_exif():
    """Пережатие стирает EXIF. Координата обязана ехать заголовком."""
    assert 'h["X-Photo-Geo"]' in INDEX or "X-Photo-Geo" in INDEX


def test_indexeddb_writes_wait_for_transaction_commit():
    assert "tx.oncomplete=()=>res();" in INDEX
    assert 'tx.onabort=()=>rej(tx.error||new Error("idb abort"));' in INDEX
    assert 'put:(s,v,k,valid)=>write(s,o=>' in INDEX
    assert 'if(generation!==WIPE_GEN||(valid&&!valid())) return false;' in INDEX
    assert 'del:(s,k)=>write(s,o=>o.delete(k))' in INDEX


def test_raw_protocol_and_legacy_blob_migration_are_present():
    assert 'h["Content-Type"]="application/octet-stream";' in INDEX
    assert 'h["X-Photo-Raw"]="1";' in INDEX
    assert 'h["X-Photo-Ext"]=ext||".jpg";' in INDEX
    assert "async function queuedPhotoBytes(it)" in INDEX
    assert "const bytes=await photoBytes(it.blob);" in INDEX
    assert "delete migrated.blob;" in INDEX
    assert "ext:it.ext||photoExt(" in INDEX       # тот же ext/op переживает миграцию


def test_permanent_photo_error_stays_visible_but_does_not_block_queue():
    assert 'if(it.kind==="photo"&&it.failed) continue;' in INDEX
    assert 'if(it.kind==="photo"&&PHOTO_PREPARING.has(it.op_id)) continue;' in INDEX
    assert "await markPhotoFailed(it,String(r.status));" in INDEX
    assert 'const failedItems=items.filter(it=>it&&it.kind==="photo"&&it.failed);' in INDEX
    assert "failed+\" \"+t.photoretake" in INDEX
    assert "failedReps.slice(0,3).join" in INDEX
    assert 'onclick="retakeFailedPhoto()"' in INDEX
    assert "await retireReplacedPhoto(it);" in INDEX
    assert "const old=it.replaces_op_id;" in INDEX
    assert 'await IDB.ack(op_id)' in INDEX
    failure = INDEX[INDEX.index('else if(it.kind==="photo"||it.kind==="receipt")'):]
    failure = failure[:failure.index('else if(it.kind===')]
    assert 'retireReplacedPhoto' not in failure, 'A rejected replacement must preserve the original'


def test_retake_keeps_ios_user_activation_and_links_exact_failed_op():
    start = INDEX.index("function retakeFailedPhoto(){")
    end = INDEX.index("\n}", start) + 2
    body = INDEX[start:end]
    assert "const it=_retakeTarget;" in body
    assert "input.click()" in body
    assert "async function retakeFailedPhoto" not in body
    # Связь ставится по явному нажатию, а не по «самому старому failed» этого КД:
    # иначе обычное добавление снимка молча убирало чужую запись из очереди.
    assert "RETAKE_FOR={op_id:it.op_id, rep:it.rep};" in body
    assert "entry.replaces_op_id=retakeOf;" in INDEX


def test_indexeddb_write_failure_is_not_reported_as_queued():
    body = INDEX[INDEX.index("async function enqueue("):INDEX.index("async function stampQueueOwners(")]
    assert 'await IDB.put("queue", entry)' in body
    assert 'catch(e){ return false; }' in body
    assert 'if(!(await enqueue(entry))){ toast(L().photolost); return; }' in INDEX


def test_exif_is_read_before_the_photo_is_shrunk():
    """shrinkPhoto стирает EXIF безвозвратно и молча — читать строго до него."""
    up = INDEX.index("async function upPhoto(")
    body = INDEX[up:INDEX.index("async function sendPhoto(")]
    exif = body.index("readExifMeta(f)")
    shrink = body.index("Promise.race([shrinkPhoto(f)")
    materialize = body.index("photoBytes(small||f)")
    clear = body.index('el.value=""')
    assert exif < shrink < materialize < clear


def test_photo_metadata_headers_stay_optional():
    """Обязательный новый заголовок положил бы v12/v13 в вечную очередь (503)."""
    i = INDEX.index("function photoHeaders(")
    body = INDEX[i:INDEX.index("\n}", i)]
    for h in ("X-Photo-Shot", "X-Photo-Source", "X-Photo-Exif", "X-Photo-Shrunk"):
        assert h in body
    assert "if(meta){" in body
    assert 'h["X-Photo-Raw"]="1";' in body     # обязательные — прежние
