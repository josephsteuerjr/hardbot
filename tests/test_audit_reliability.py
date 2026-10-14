"""Lost responses, failed local storage and environment changes, all synthetic."""
import io
import urllib.request
import os

import pytest

from pult.core import api
from pult.core.settings import Settings
from shell_node import run_shell


SHELL = r'''
const token="TOK-A-aaaaaaaaaaaaaaaaaaaa";
const srv=fakeServer({tokens:{[token]:{employee:"Synthetic Soft",persons:[]}}});
const w=newWorld({ls:{app_token:token},idb:{queue:[]}});
const p=await openPage(w,{url:"https://hard.test/",server:srv});
await p.booted();await settle(w);
'''


def test_offline_call_replays_append_semantics_and_preserves_rejected_fields():
    result = run_shell(SHELL + r'''
    srv.cfg.routes={"/api/save":r=>({status:200,body:{ok:true,rejected:{promise_date:"too_far:3"}}})};
    await p.run(`enqueue({kind:"save",save_kind:"call",op_id:"call-one",rep:"R1",
      values:{soft_call_status:"Нет контакта",soft_comment:"New note"},
      input_keys:["soft_call_status","soft_comment"],form_version:3,at:1})`);
    await p.run("flushQueue()");await settle(w);
    out.body=JSON.parse(p.fetchLog.find(r=>r.url==="/api/save").body);
    out.queue=await p.run('IDB.all("queue")');
    ''')
    assert result["body"]["append_comment"] is True
    assert result["queue"][0]["failed"] == "server-fields"
    assert result["queue"][0]["rejected"] == {"promise_date": "too_far:3"}


def test_operation_is_durable_before_http_and_retains_id_after_lost_response():
    result = run_shell(SHELL + r'''
    let durable=false;
    srv.cfg.routes={"/api/save":r=>{
      durable=w.dump().queue.includes("payment-one"); return "neterr";
    }};
    out.result=await p.run(`submitSavedOperation({kind:"payment",op_id:"payment-one",rep:"R1",
      paid_at:"2026-10-01",amount:"20.50",occurred_at:123},
      {kind:"payment",finish:true,op_id:"payment-one",rep:"R1",paid_at:"2026-10-01",amount:"20.50",occurred_at:123})`);
    out.durable=durable;out.queue=await p.run('IDB.all("queue")');
    srv.cfg.routes={"/api/save":()=>({status:200,body:{ok:true}})};
    await p.run("flushQueue()");out.after=w.dump().queue;
    out.ids=p.fetchLog.filter(r=>r.url==="/api/save").map(r=>JSON.parse(r.body).op_id);
    ''')
    assert result["durable"] and result["result"]["queued"]
    assert result["queue"][0]["occurred_at"] == 123
    assert result["after"] == [] and result["ids"] == ["payment-one", "payment-one"]


def test_quota_failure_cannot_claim_success_or_send_volatile_operation():
    result = run_shell(SHELL + r'''
    p.run('IDB.put=async()=>{throw new Error("quota")};');
    out.result=await p.run('submitSavedOperation({op_id:"lost",kind:"payment"},{kind:"payment",finish:true})');
    out.sent=p.fetchLog.filter(r=>r.url==="/api/save").length;
    out.toast=p.run('document.getElementById("toast").textContent');
    ''')
    assert result["result"] is None and result["sent"] == 0
    assert "Не удалось сохранить" in result["toast"]


@pytest.mark.parametrize("status", [408, 429])
def test_transient_rejection_keeps_queue_retryable(status):
    result = run_shell(SHELL + f'''
    srv.cfg.routes={{"/api/save":()=>({{status:{status},body:{{}}}})}};
    await p.run('enqueue({{op_id:"retry",kind:"payment",rep:"R1",amount:"1",paid_at:"2026-10-01"}})');
    await p.run('flushQueue()');out.queue=await p.run('IDB.all("queue")');
    ''')
    assert len(result["queue"]) == 1 and not result["queue"][0].get("failed")


def test_https_redirect_cannot_forward_an_analyst_token_to_another_origin():
    req = urllib.request.Request("https://one.synthetic.invalid/admin/stats", headers={"X-Device-Token": "synthetic"})
    for target in ["https://two.synthetic.invalid/admin/stats", "https://one.synthetic.invalid:444/admin/stats"]:
        body = io.BytesIO(b"")
        with pytest.raises(RuntimeError, match="другой сервер"):
            api._NoDowngradeRedirect().redirect_request(req, body, 302, "Found", {}, target)
        assert body.closed


def test_analyst_cannot_switch_environment_while_a_job_is_running(tmp_path, monkeypatch):
    from pult import desk_backend as backend
    monkeypatch.setattr(backend, "_settings", Settings({"profiles": {
        "One": {"url": "https://one.synthetic.invalid", "device_token": "one"},
        "Two": {"url": "https://two.synthetic.invalid", "device_token": "two"}},
        "active": "One"}, tmp_path / "settings.json"))
    monkeypatch.setattr(backend, "_jobs", {"job": {"state": "running"}})
    with pytest.raises(RuntimeError, match="завершения"):
        backend.profile_action({"action": "switch", "name": "Two"})
    assert backend._settings["active"] == "One"
    backend._jobs.clear()
    with pytest.raises(RuntimeError, match="Адрес привязанного"):
        backend.profile_action({"action": "save", "url": "https://two.synthetic.invalid"})


def test_environment_switch_invalidates_registry_preview(tmp_path, monkeypatch):
    from pult import desk_backend as backend, desk_registry as registry
    monkeypatch.setattr(backend, "_settings", Settings({"profiles": {
        "One": {"url": "https://one.synthetic.invalid"}, "Two": {"url": "https://two.synthetic.invalid"}},
        "active": "One"}, tmp_path / "settings.json"))
    monkeypatch.setattr(backend, "_jobs", {})
    monkeypatch.setattr(registry, "S", {"book": {"sensitive": "old"}, "preview": {"ready": True}})
    backend.profile_action({"action": "switch", "name": "Two"})
    assert registry.S["book"] is None and registry.S["preview"] is None


def test_retained_phone_queue_does_not_follow_a_new_employee_login():
    result = run_shell(SHELL + r'''
    await p.run('enqueue({kind:"note",op_id:"old-note",text:"Original employee note",at:1})');
    await p.run('wipeDevice({keepQueue:true})');
    srv.cfg.tokens[token]={uid:2,employee:"Other employee",persons:[]};
    w.ls.setItem("app_token",token);await p.run("boot()");await settle(w);
    out.before=p.fetchLog.filter(r=>r.url==="/api/note").length;
    out.queue=await p.run('IDB.all("queue")');
    srv.cfg.tokens[token]={uid:1,employee:"Synthetic Soft",persons:[]};
    await p.run("boot()");await settle(w);
    out.after=p.fetchLog.filter(r=>r.url==="/api/note").length;out.left=w.dump().queue;
    ''')
    assert result["before"] == 0 and result["queue"][0]["failed"] == "server-owner"
    assert result["after"] == 1 and result["left"] == []


def test_offline_soft_cache_keeps_mode_and_tracking_principal():
    result = run_shell(SHELL + r'''
    await p.run(`cacheBootstrap({uid:1,employee:"Synthetic Soft",persons:[],roles:{soft:true,field:false},_mode:"soft"})`);
    srv.cfg.tokens[token]="neterr";await p.run("boot()");await settle(w);
    out.mode=p.run("STATE.mode");out.uid=p.run("STATE.uid");out.employee=p.run("STATE.employee");
    ''')
    assert result == {"mode": "soft", "uid": 1, "employee": "Synthetic Soft"}


def test_readiness_refuses_missing_corrupt_or_locked_database(tmp_path, monkeypatch):
    import db, web, sqlite3
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "readiness.db")
    with pytest.raises(sqlite3.Error):
        web._database_ready()
    assert not db.DB_PATH.exists()
    db.init_db()
    assert web._database_ready()
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError):
            web._database_ready()
    finally:
        conn.rollback(); conn.close()
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"SQLite format 3\x00" + b"corrupt")
    monkeypatch.setattr(db, "DB_PATH", broken)
    with pytest.raises(sqlite3.Error):
        web._database_ready()


def test_visit_transaction_rolls_back_journal_when_snapshot_fails(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "visit-atomic.db")
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT INTO clients(contract_id,client,responsible) VALUES ('V','Synthetic','H1')")
        conn.commit()
    def crash(*a, **kw):
        raise RuntimeError("Injected failure before snapshot")
    with monkeypatch.context() as m:
        m.setattr(db, "record_bot_visit", crash)
        with pytest.raises(RuntimeError, match="Injected"):
            db.save_visit_result("V", 1, "H1", {"call_status": "Нет контакта"}, "Synthetic", op_id="visit-one")
    assert not db.visit_history("V") and db.get_client("V")["call_status"] is None
    assert db.save_visit_result("V", 1, "H1", {"call_status": "Нет контакта"}, "Synthetic", op_id="visit-one")
    assert len(db.visit_history("V")) == 1 and db.has_bot_visit("V")
    assert not db.save_visit_result("V", 1, "H1", {"call_status": "Оплачен"}, "Changed retry", op_id="visit-one")
    assert db.get_client("V")["call_status"] == "Нет контакта"


@pytest.mark.skipif(os.name != "nt", reason="Windows user DPAPI")
def test_installed_analyst_tokens_are_encrypted_and_migrate_without_enrollment(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("HARD_PULT_PROTECT_TOKENS", "1")
    path = tmp_path / "settings.json"
    original = {"profiles": {"One": {"url": "https://one.synthetic.invalid", "device_token": "synthetic-secret-one"},
                             "Two": {"url": "https://two.synthetic.invalid", "device_token": "synthetic-secret-two"}}, "active": "Two"}
    settings = Settings(original, path)
    assert settings.save()
    raw = path.read_text(encoding="utf-8")
    assert "synthetic-secret-one" not in raw and "synthetic-secret-two" not in raw
    assert json.loads(raw)["device_token"].startswith("dpapi-v1:")
    monkeypatch.delenv("HARD_PULT_PROTECT_TOKENS")
    loaded = Settings.load(path)
    assert loaded["profiles"]["One"]["device_token"] == "synthetic-secret-one"
    assert loaded["device_token"] == "synthetic-secret-two"
    assert loaded.save() and "synthetic-secret-two" not in path.read_text(encoding="utf-8")


def test_failed_token_protection_never_falls_back_to_plaintext(tmp_path, monkeypatch):
    from pult.core import settings as module
    monkeypatch.setenv("HARD_PULT_PROTECT_TOKENS", "1")
    def unavailable(*a, **kw):
        raise OSError("DPAPI unavailable")
    monkeypatch.setattr(module, "_dpapi", unavailable)
    path = tmp_path / "settings.json"
    assert not Settings({"device_token": "synthetic-secret"}, path).save()
    assert not path.exists() and not path.with_suffix(".json.tmp").exists()


def test_restore_rollback_snapshot_includes_uncheckpointed_wal(tmp_path, monkeypatch):
    import sqlite3, shutil, sys
    from pathlib import Path
    original = tmp_path / "original.db"
    writer = sqlite3.connect(original)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE events(id INTEGER)")
        writer.execute("INSERT INTO events VALUES(7)")
        writer.commit()
        main_only = tmp_path / "main-only.db"
        shutil.copyfile(original, main_only)
        with sqlite3.connect(main_only) as main:
            with pytest.raises(sqlite3.Error):
                main.execute("SELECT id FROM events").fetchall()
        script = (Path(__file__).resolve().parents[1] / "deploy/units/core/restore.sh").read_text(encoding="utf-8")
        snippet = script.split("SNAPSHOT <<'PY'")[1].split("\nPY")[0].split("\n", 1)[1]
        snippet = snippet.replace('"file:/data/hardbot.db?mode=ro"', repr(original.as_uri() + "?mode=ro"))
        snippet = snippet.replace('"/data/hardbot.db.before-restore-"', repr(str(tmp_path / "rollback-")))
        monkeypatch.setattr(sys, "argv", ["snapshot", "synthetic"])
        exec(compile(snippet, "restore.sh snapshot", "exec"), {})
        with sqlite3.connect(tmp_path / "rollback-synthetic") as snapshot:
            assert snapshot.execute("SELECT id FROM events").fetchall() == [(7,)]
            assert snapshot.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert Path(str(original) + "-wal").exists()
    finally:
        writer.close()


def test_waiting_for_database_writer_does_not_block_other_http_requests(tmp_path, monkeypatch):
    import asyncio, json, threading, time
    import db, web
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "responsive.db")
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT INTO clients(contract_id,client,responsible_soft) VALUES ('S','Synthetic','S1')")
        conn.commit()
    monkeypatch.setattr(web, "_auth", lambda req: (101, "S1"))
    ready, release = threading.Event(), threading.Event()
    def hold_writer():
        conn = db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE"); ready.set(); release.wait(5); conn.rollback()
        finally:
            conn.close()
    holder = threading.Thread(target=hold_writer)
    holder.start(); assert ready.wait(2)
    timer = threading.Timer(1, release.set); timer.start()
    async def scenario():
        async with TestClient(TestServer(web.build_app(None))) as client:
            saving = asyncio.create_task(client.post("/api/save", json={"kind": "call", "rep": "S", "finish": True,
                "op_id": "responsive-call", "form_version": web.FORM_PROTOCOL_VERSION,
                "values": {"soft_call_status": "Нет контакта"}, "input_keys": ["soft_call_status"]}))
            await asyncio.sleep(.05)
            started = time.monotonic()
            version = await client.get("/version.json")
            assert version.status == 200 and time.monotonic() - started < .5
            assert not saving.done(), "Save must still be waiting for the writer"
            release.set()
            response = await saving
            assert response.status == 200 and (await response.json())["ok"]
    try:
        asyncio.run(scenario())
    finally:
        release.set(); timer.cancel(); holder.join(5)


def test_settings_write_failure_cannot_switch_the_active_contour(tmp_path, monkeypatch):
    from pult import desk_backend as backend
    monkeypatch.setattr(backend, "_settings", Settings({"profiles": {"One": {}, "Two": {}}, "active": "One"}, tmp_path / "s.json"))
    monkeypatch.setattr(backend, "_jobs", {})
    monkeypatch.setattr(Settings, "save", lambda self: False)
    with pytest.raises(RuntimeError, match="не сохранены"):
        backend.profile_action({"action": "switch", "name": "Two"})
    assert backend._settings["active"] == "One"


def test_imported_date_text_cannot_become_pwa_markup(tmp_path, monkeypatch):
    import json
    import db, web
    from test_review_soft_integrity import grid
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "xss-source.db")
    db.init_db()
    text = '<img id="audit-xss" src="invalid://audit" onerror="window.auditXssExecuted=true">'
    result = db.import_grid(grid([{"contract_id": "SYN-XSS", "client": "Synthetic client",
        "birth_date": text, "assign_date": text, "responsible": "H1"}]), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert not result.get("blocked")
    card = web._person_full("SYN-XSS", "ru")
    assert card["birth_date"] == text  # registry strings really reach the card
    rendered = run_shell(r'''
    const CARD=__CARD__,token="TOK-X-aaaaaaaaaaaaaaaaaaaa";
    const srv=fakeServer({tokens:{[token]:{employee:"H1",persons:[]}},routes:{
      "/api/person":()=>({status:200,body:CARD})}});
    const w=newWorld({ls:{app_token:token}}),p=await openPage(w,{server:srv});
    await p.booted();await p.run('openP("SYN-XSS")');await settle(w);
    out.header=p.html("app");out.panel=p.html("panel");
    '''.replace("__CARD__", json.dumps(card)))
    assert '<img id="audit-xss"' not in rendered["header"] + rendered["panel"]
    assert '&lt;img id=&quot;audit-xss&quot;' in rendered["header"]
