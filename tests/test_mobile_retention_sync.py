"""Execute the shipped shell: preserved offline sessions and durable acknowledgements."""
import pytest
from shell_node import run_shell

PRELUDE = r'''
const A="synthetic-test-token-aaaaaaaa", base=1900000000000;
let clock=base;
const PERSONS=[{rep:"S1",client:"Synthetic client",visited:false,n:1,totals:[],ids:["S1"],tels:[]}];
const ME={uid:1,employee:"Synthetic employee",persons:PERSONS,cards:{S1:{rep:"S1",client:"Synthetic client"}}};
const Q=(id,kind="note")=>({op_id:id,kind,rep:"S1",text:"Synthetic note",at:base-1000,_owner:{uid:1,employee:ME.employee}});
const world=(age,queue=[Q("one")])=>newWorld({ls:{app_token:A,"draft:1:S1":"private draft",hb_device_uuid:"synthetic-device"},
  ss:{pending_token:"unrelated"},idb:{kv:{bootstrap:{...ME,lang:"ru",meta:{},at:base-age,_credential:"token:"+A}},cards:ME.cards,queue},
  caches:{"hard-shell-v100":[],"private-map":[]}});
const mobile={url:"https://hard.test/",userAgent:"Mozilla/5.0 (iPhone)",now:()=>clock};
'''


@pytest.mark.parametrize("agent", ["iPhone", "Android"])
@pytest.mark.parametrize("age", [14*3600000, 14*3600000+1, 20*3600000, 30*24*3600000, -3600000])
def test_mobile_offline_sessions_survive_old_deadline_and_clock_changes(agent, age):
    out = run_shell(PRELUDE + f'''
const w=world({age});
const p=await openPage(w,{{...mobile,userAgent:{agent!r},onLine:false,server:fakeServer({{tokens:{{[A]:"neterr"}}}})}});
await p.booted();out.dump=w.dump();out.app=p.html("app");out.portfolio=p.html("plist");out.bar=p.html("offbar");out.requests=p.fetchLog;
''')
    assert out["dump"]["queue"] == ["one"]
    assert out["dump"]["cards"] == ["S1"]
    assert "bootstrap" in out["dump"]["kv"]
    assert out["dump"]["ls"]["app_token"] == "synthetic-test-token-aaaaaaaa"
    assert out["dump"]["ls"]["hb_device_uuid"] == "synthetic-device"
    assert out["dump"]["ls"]["draft:1:S1"] == "private draft"
    assert "Synthetic client" in out["portfolio"]
    assert 'id="loginuser"' not in out["app"]
    assert "14 часов" not in out["bar"]
    assert "Всё синхронизировано" not in out["bar"]


def test_mobile_crosses_old_deadline_then_reconnects_and_sends_after_access_check():
    out = run_shell(PRELUDE + r'''
const w=world(14*3600000-1),cfg={tokens:{[A]:"neterr"}};
const p=await openPage(w,{...mobile,onLine:false,server:fakeServer(cfg)});await p.booted();
out.before={dump:w.dump(),app:p.html("plist")};
clock+=7*24*3600000;await p.run("enforceRetention()");out.offline=w.dump();
cfg.tokens[A]=ME;await p.run("resumeProfile()");await settle(w);
out.after=w.dump();out.requests=p.fetchLog;out.bar=p.html("offbar");
const again=await openPage(w,{...mobile,url:mobile.url+"#t="+A,server:fakeServer(cfg)});await again.booted();
out.again=w.dump();out.againApp=again.html("plist");
''')
    assert "Synthetic client" in out["before"]["app"]
    assert out["before"]["dump"]["queue"] == ["one"]
    assert out["offline"]["queue"] == ["one"]
    assert out["after"]["queue"] == out["again"]["queue"] == []
    assert out["again"]["ls"]["app_token"] == "synthetic-test-token-aaaaaaaa"
    assert out["again"]["ls"]["hb_device_uuid"] == "synthetic-device"
    assert "Synthetic client" in out["againApp"]
    identity = next(i for i, r in enumerate(out["requests"]) if r["url"].startswith("/api/identity"))
    note = next(i for i, r in enumerate(out["requests"]) if r["url"].startswith("/api/note"))
    assert identity < note
    assert "Всё синхронизировано" in out["bar"]


def test_desktop_web_does_not_apply_mobile_deadline():
    out = run_shell(PRELUDE + r'''
const w=world(30*3600000),p=await openPage(w,{url:mobile.url,now:()=>clock,onLine:false,server:fakeServer({tokens:{[A]:"neterr"}})});
await p.booted();out.dump=w.dump();out.app=p.html("plist");
''')
    assert out["dump"]["queue"] == ["one"]
    assert out["dump"]["ls"]["app_token"]
    assert "Synthetic client" in out["app"]


def test_mobile_cold_reopens_keep_account_session_and_device_identity():
    out = run_shell(PRELUDE + r'''
const w=world(1000,[]);w.ls.setItem("account_role","agent");
let p=await openPage(w,{...mobile,server:fakeServer({tokens:{[A]:ME}})});await p.booted();
const original=await p.run("deviceProfile().then(d=>d.fp)");out.reopens=[];
for(const hours of [1,5,13]){
  clock+=hours*3600000;
  p=await openPage(w,{...mobile,server:fakeServer({tokens:{[A]:ME}})});await p.booted();
  out.reopens.push({sameDevice:(await p.run("deviceProfile().then(d=>d.fp)"))===original,
    token:w.ls.getItem("app_token"),login:p.html("app").includes('id="loginuser"')});
}
''')
    assert out["reopens"] == [
        {"sameDevice": True, "token": "synthetic-test-token-aaaaaaaa", "login": False}
    ] * 3


def test_mobile_short_network_outage_keeps_account_session_and_device_identity():
    out = run_shell(PRELUDE + r'''
const w=world(1000,[]);w.ls.setItem("account_role","agent");
let p=await openPage(w,{...mobile,server:fakeServer({tokens:{[A]:ME}})});await p.booted();
const original=await p.run("deviceProfile().then(d=>d.fp)");clock+=3600000;
p=await openPage(w,{...mobile,onLine:false,server:fakeServer({tokens:{[A]:"neterr"}})});await p.booted();
out.sameDevice=(await p.run("deviceProfile().then(d=>d.fp)"))===original;
out.token=w.ls.getItem("app_token");out.login=p.html("app").includes('id="loginuser"');
out.portfolio=p.html("plist");
''')
    assert out["sameDevice"] is True
    assert out["token"] == "synthetic-test-token-aaaaaaaa"
    assert out["login"] is False
    assert "Synthetic client" in out["portfolio"]


@pytest.mark.parametrize("age", [3600000, 30*24*3600000])
@pytest.mark.parametrize("status", [401, 403])
def test_reconnect_checks_revocation_before_sending_any_queued_operation(age, status):
    out = run_shell(PRELUDE + f'const age={age},status={status};' + r'''
const cfg={tokens:{[A]:"neterr"}},w=world(age);
w.ss.removeItem("pending_token");
const p=await openPage(w,{...mobile,server:fakeServer(cfg)});await p.booted();
cfg.tokens[A]=status;await p.run("resumeProfile()");await settle(w);
out.dump=w.dump();out.requests=p.fetchLog;out.bar=p.html("offbar");
''')
    assert out["dump"]["queue"] == out["dump"]["cards"] == out["dump"]["kv"] == []
    assert "app_token" not in out["dump"]["ls"]
    assert not any(r["url"].startswith("/api/note") for r in out["requests"])
    assert "Доступ отозван" in out["bar"]


def test_partial_acknowledgements_survive_restart_and_only_finish_after_last_ack():
    out = run_shell(PRELUDE + r'''
let calls=0,failed=true;
const cfg={tokens:{[A]:ME},routes:{"/api/note":()=>({status:++calls===1||!failed?200:503,body:{ok:true}})}};
const w=world(1000,[Q("one"),Q("two"),Q("three")]);
let p=await openPage(w,{...mobile,server:fakeServer(cfg)});await p.booted();await settle(w);await p.run("updateOffBar()");
out.partial={dump:w.dump(),bar:p.html("offbar")};
cfg.tokens[A]="neterr";p=await openPage(w,{...mobile,server:fakeServer(cfg)});await p.booted();await p.run("updateOffBar()");
out.restart={dump:w.dump(),bar:p.html("offbar")};
cfg.tokens[A]=ME;failed=false;await p.run("resumeProfile()");await settle(w);await p.run("updateOffBar()");
out.done={dump:w.dump(),bar:p.html("offbar")};
''')
    assert out["partial"]["dump"]["queue"] == ["two", "three"]
    assert "Отправлено 1 из 3, осталось 2" in out["partial"]["bar"]
    assert "Отправлено 1 из 3, осталось 2" in out["restart"]["bar"]
    assert "Всё синхронизировано" not in out["restart"]["bar"]
    assert out["done"]["dump"]["queue"] == []
    assert "Всё синхронизировано" in out["done"]["bar"]
    assert "Отправлено 3 из 3, осталось 0" in out["done"]["bar"]


def test_new_shell_is_offered_even_on_idle_page_without_reloading():
    out = run_shell(PRELUDE + r'''
const p=await openPage(newWorld(),{url:mobile.url,server:fakeServer({shellVersion:999})});await p.booted();
await p.run("updateOffBar()");out.reloads=p.reloads;out.bar=p.html("offbar");
''')
    assert out["reloads"] == []
    assert "reloadForUpdate()" in out["bar"]


@pytest.mark.parametrize("response", [{"ok": False}, {}, {"error": "synthetic"}])
def test_http_200_without_application_acknowledgement_keeps_queue(response):
    import json
    out = run_shell(PRELUDE + f'''
const w=world(1000),p=await openPage(w,{{...mobile,server:fakeServer({{tokens:{{[A]:ME}},routes:{{"/api/note":()=>({{status:200,body:{json.dumps(response)}}})}}}})}});
await p.booted();await settle(w);await p.run("updateOffBar()");out.dump=w.dump();out.bar=p.html("offbar");
''')
    assert out["dump"]["queue"] == ["one"]
    assert "Всё синхронизировано" not in out["bar"]


def test_failed_erasure_blocks_new_work_and_retries_before_next_login():
    out = run_shell(PRELUDE + r'''
const w=world(1000),p=await openPage(w,{...mobile,server:fakeServer({tokens:{[A]:"neterr"}})});await p.booted();
await p.run("globalThis.originalClear=IDB.clear;IDB.clear=async()=>{throw Error('synthetic unavailable storage')};purgeProfile('revoked')");
out.pending=w.dump();out.first=await p.run("enqueue({op_id:'must-not-save',kind:'note'})");
await p.run("IDB.clear=originalClear;enforceRetention()");await settle(w);out.done=w.dump();
''')
    assert out["pending"]["ls"]["hb_erasure_pending"] == "1"
    assert out["first"] is False
    assert out["done"]["queue"] == out["done"]["kv"] == out["done"]["cards"] == []
    assert "hb_erasure_pending" not in out["done"]["ls"]
