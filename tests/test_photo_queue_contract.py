"""Статический контракт iPhone photo queue без браузерных зависимостей.

Поведенческий серверный путь проверяет test_photo_upload.py; здесь фиксируем
критические свойства единственного vanilla-JS файла, чтобы будущий рефакторинг
не вернул File→IndexedDB→FormData и не заблокировал очередь одним 4xx.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "hardbot" / "miniapp" / "index.html").read_text("utf-8")


def test_selected_file_is_materialized_before_geolocation_wait():
    materialize = INDEX.index("try{ bytes=await photoBytes(f); }")
    durable_queue = INDEX.index("if(!(await enqueue(entry)))", materialize)
    geolocation = INDEX.index("geo=await grabGeo(4000,5000);", materialize)
    assert materialize < durable_queue < geolocation
    assert 'fd.append("photo"' not in INDEX


def test_indexeddb_writes_wait_for_transaction_commit():
    assert "tx.oncomplete=()=>res();" in INDEX
    assert 'tx.onabort=()=>rej(tx.error||new Error("idb abort"));' in INDEX
    assert 'put:(s,v,k)=>write(s,o=>' in INDEX
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
    assert 'await IDB.del("queue",op_id)' in INDEX


def test_retake_keeps_ios_user_activation_and_links_exact_failed_op():
    start = INDEX.index("function retakeFailedPhoto(){")
    end = INDEX.index("\n}", start) + 2
    body = INDEX[start:end]
    assert "const it=_retakeTarget;" in body
    assert "input.click()" in body
    assert "async function retakeFailedPhoto" not in body
    assert "entry.replaces_op_id=prior.op_id;" in INDEX


def test_indexeddb_write_failure_is_not_reported_as_queued():
    assert 'try{ await IDB.put("queue", entry); return true; }catch(e){ return false; }' in INDEX
    assert 'if(!(await enqueue(entry))){ toast(L().photolost); return; }' in INDEX
