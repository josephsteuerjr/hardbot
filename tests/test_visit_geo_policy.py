"""Временный GPS gate: настоящая оболочка сохраняет без точки и не дублирует визит."""
import asyncio
import json

import pytest
from aiohttp.test_utils import make_mocked_request

import config
import db
import web
from shell_node import run_shell


SETUP = r'''
const token="TOK-G-aaaaaaaaaaaaaaaaaaaa";
const person={rep:"G1",client:"Тест",n:1,totals:[],contracts:[]};
const srv=fakeServer({tokens:{[token]:{employee:"H1",persons:[person],cards:{G1:person}}}});
srv.cfg.routes={"/api/save":()=>({status:200,body:{ok:true,person}})};
const w=newWorld({ls:{app_token:token}});
const p=await openPage(w,{url:"https://hard.test/",server:srv});
await p.booted();await settle(w);
p.run(`STATE.cur={rep:"G1",client:"Тест",contracts:[]};STATE.tab="form";
  FORM_TOUCHED.add("call_status");readForm=()=>({call_status:"Нет контакта"});`);
'''


@pytest.mark.parametrize("metadata", ["{}", "{visitGeoRequired:false}"])
def test_optional_or_legacy_metadata_saves_without_gps(metadata):
    result = run_shell(SETUP + f'p.run("STATE.meta={metadata}");' + r'''
    await p.run("saveVisit()");await settle(w);
    out.saves=p.fetchLog.filter(r=>r.url==="/api/save").map(r=>JSON.parse(r.body));
    out.visited=p.run("STATE.persons[0].visited");out.saving=p.run("SAVING");
    out.message=p.html("toast");
    ''')
    assert len(result["saves"]) == 1
    assert result["saves"][0]["finish"] is True
    assert "geo" not in result["saves"][0]
    assert result["visited"] and not result["saving"]
    assert "без точки GPS" in result["message"]


def test_without_network_visit_is_queued_without_gps_and_later_delivered():
    result = run_shell(SETUP + r'''
    srv.cfg.routes={"/api/save":()=>"neterr"};
    await p.run("saveVisit()");await settle(w);
    out.queue=await p.run('IDB.all("queue")');
    out.visited=p.run("STATE.persons[0].visited");out.message=p.html("toast");
    srv.cfg.routes["/api/save"]=()=>({status:200,body:{ok:true,person}});
    await p.run("flushQueue()");await settle(w);
    out.after=await p.run('IDB.all("queue")');
    out.saves=p.fetchLog.filter(r=>r.url==="/api/save").map(r=>JSON.parse(r.body));
    ''')
    assert len(result["queue"]) == 1
    queued = result["queue"][0]
    assert queued["kind"] == "save" and queued["geo"] is None
    assert result["visited"] and "уйдёт при связи" in result["message"]
    assert result["after"] == []
    assert len(result["saves"]) == 2
    assert result["saves"][0]["op_id"] == result["saves"][1]["op_id"] == queued["op_id"]
    assert not result["saves"][1].get("geo")


def test_available_gps_is_kept_and_double_click_sends_once():
    result = run_shell(SETUP + r'''
    p.run(`let releaseGeo;grabGeo=()=>new Promise(r=>{releaseGeo=r});`);
    const first=p.run("saveVisit()");
    out.waiting=p.run("SAVING");
    await p.run("saveVisit()");
    p.run('releaseGeo({lat:41.3,lon:69.2,acc:15,ts:Date.now()})');
    await first;await settle(w);
    out.saves=p.fetchLog.filter(r=>r.url==="/api/save").map(r=>JSON.parse(r.body));
    out.saving=p.run("SAVING");
    ''')
    assert result["waiting"] and not result["saving"]
    assert len(result["saves"]) == 1
    assert result["saves"][0]["geo"]["lat"] == 41.3


def test_silent_location_callback_is_bounded():
    result = run_shell(SETUP + r'''
    p.run("grabGeo=()=>new Promise(()=>{})");
    const started=Date.now();await p.run("saveVisit()");
    out.elapsed=Date.now()-started;out.saves=p.tokensSeen("/api/save").length;
    out.saving=p.run("SAVING");
    ''')
    assert 3500 <= result["elapsed"] < 8000
    assert result["saves"] == 1 and not result["saving"]


def test_required_mode_stays_in_form_on_cancel_and_can_retry():
    result = run_shell(SETUP + r'''
    p.run(`STATE.meta.visitGeoRequired=true;let blocked=0;
      geoBlockDialog=async()=>{blocked++;return false;};`);
    await p.run("saveVisit()");
    out.savesBefore=p.tokensSeen("/api/save").length;out.blocked=p.run("blocked");
    out.saving=p.run("SAVING");out.touched=p.run('FORM_TOUCHED.has("call_status")');
    p.run('geoBlockDialog=async()=>true;askGeo=async()=>({lat:41.3,lon:69.2,acc:15})');
    await p.run("saveVisit()");
    out.savesAfter=p.tokensSeen("/api/save").length;
    ''')
    assert result["savesBefore"] == 0 and result["blocked"] == 1
    assert not result["saving"] and result["touched"]
    assert result["savesAfter"] == 1


@pytest.mark.parametrize("required", [False, True])
def test_server_exposes_policy_in_bootstrap_and_public_version(monkeypatch, required):
    db.init_db()
    monkeypatch.setattr(config, "VISIT_GEO_REQUIRED", required)
    assert web._meta("ru")["visitGeoRequired"] is required
    response = asyncio.run(web._version(make_mocked_request("GET", "/version.json")))
    assert json.loads(response.text)["visitGeoRequired"] is required
    assert json.loads(response.text)["shellVersion"] == 75
