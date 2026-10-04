from shell_node import run_shell


def test_first_soft_entry_selects_soft_portfolio_and_cache():
    result = run_shell(r'''
    const token="TOK-S-aaaaaaaaaaaaaaaaaaaa";
    const hard={employee:"S1",persons:[],roles:{soft:true,field:false}};
    const soft={employee:"S1",persons:[{rep:"A",client:"Тест",n:1,totals:[]}],roles:{soft:true,field:false}};
    const srv=fakeServer({tokens:{[token]:hard},routes:{
      "/api/bootstrap":r=>({status:200,body:r.url.includes("mode=soft")?soft:hard}),
      "/api/cards":()=>({status:200,body:{cards:{A:{rep:"A",client:"Тест",_profile:"soft"}}}})}});
    const w=newWorld({ls:{app_token:token}});
    const p=await openPage(w,{url:"https://hard.test/",server:srv});
    await p.booted();await settle(w);
    out.mode=p.run("STATE.mode");out.persons=p.run("STATE.persons.length");out.dump=w.dump();
    ''')
    assert result["mode"] == "soft"
    assert result["persons"] == 1


def test_day_tail_is_persisted_before_flush_and_button_stops_recording():
    result=run_shell(r'''
    const token="TOK-H-aaaaaaaaaaaaaaaaaaaa";
    const srv=fakeServer({tokens:{[token]:{employee:"H1",persons:[]}}});
    const w=newWorld({ls:{app_token:token}});
    const p=await openPage(w,{url:"https://hard.test/",server:srv});await p.booted();await settle(w);
    p.run(`DAY.on=true;DAY.session="day:test";DAY.started=Date.now();renderLine();
      dayOnPos({timestamp:Date.now(),coords:{latitude:41,longitude:69,accuracy:15}});`);
    out.before=JSON.parse(w.dump().ls.hb_day_queue||"[]").length;
    out.buf=p.run("DAY.buf.length");
    p.run("dayStop()");out.stopped=p.run("DAY.on===false");
    out.button=p.html("daybtn");
    ''')
    assert result["before"] == 1
    assert result["buf"] == 0
    assert result["stopped"]
    assert "Начать смену" in result["button"]


def test_navigation_points_survive_without_http_and_foreign_day_is_not_sent():
    result=run_shell(r'''
    const token="TOK-H-aaaaaaaaaaaaaaaaaaaa";
    const srv=fakeServer({tokens:{[token]:{employee:"H1",persons:[]}}});
    const w=newWorld({ls:{app_token:token}});
    const p=await openPage(w,{url:"https://hard.test/",server:srv});await p.booted();await settle(w);
    p.run(`NAV.session="nav:test";NAV.owner="H1";
      trackPush({lat:41,lon:69},{timestamp:Date.now(),coords:{accuracy:15}});
      dayQueueSet([{bid:"foreign",employee:"H2",session_id:"other",points:[{lat:42,lon:69,ts:new Date().toISOString()}]}]);`);
    out.nav=JSON.parse(w.dump().ls.hb_track_queue||"[]");
    await p.run("dayDrainQueue()");await settle(w);
    out.day=JSON.parse(w.dump().ls.hb_day_queue||"[]");
    ''')
    assert result["nav"][0]["employee"] == "H1"
    assert len(result["nav"][0]["points"]) == 1
    assert result["day"][0]["bid"] == "foreign"


def test_plain_field_role_still_available_without_mixed_roles_object():
    result=run_shell(r'''
    const token="TOK-H-aaaaaaaaaaaaaaaaaaaa";
    const srv=fakeServer({tokens:{[token]:{employee:"H1",persons:[]}}});
    const w=newWorld({ls:{app_token:token}});
    const p=await openPage(w,{url:"https://hard.test/",server:srv});await p.booted();await settle(w);
    out.field=p.run("canField()");
    ''')
    assert result["field"]
