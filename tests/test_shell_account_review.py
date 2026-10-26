from shell_node import run_shell


def test_password_reset_waits_for_owner_after_reload():
    result=run_shell(r'''
    const token="TOK-H-aaaaaaaaaaaaaaaaaaaa";
    const srv=fakeServer({tokens:{[token]:{employee:"Synthetic",pending:true,pending_kind:"password_review"}}});
    const w=newWorld({ls:{app_token:token}});
    const p=await openPage(w,{url:"https://hard.test/",server:srv});await p.booted();await settle(w);
    out.page=p.html("app");
    ''')
    assert 'ждём владельца' in result['page']
    assert 'Ваш личный пароль сохранён' in result['page']


def test_boot_restores_required_password_change_after_reload():
    result = run_shell(r'''
    const token="TOK-H-aaaaaaaaaaaaaaaaaaaa";
    const srv=fakeServer({tokens:{[token]:{employee:"Synthetic",pending:true,
      must_change_password:true,username:"test-login",pending_kind:"password"}}});
    const w=newWorld({ls:{app_token:token}});
    const p=await openPage(w,{url:"https://hard.test/",server:srv});await p.booted();await settle(w);
    out.username=p.run("AUTH_USER");out.page=p.html("app");
    ''')
    assert result["username"] == "test-login"
    assert "Смените пароль" in result["page"]


def test_phone_install_identity_survives_screen_and_browser_changes():
    result = run_shell(r'''
    const token="TOK-H-aaaaaaaaaaaaaaaaaaaa";
    const srv=fakeServer({tokens:{[token]:{employee:"Synthetic",persons:[]}}});
    const w=newWorld({ls:{app_token:token}});
    const p=await openPage(w,{url:"https://hard.test/",server:srv});await p.booted();await settle(w);
    p.run('deviceUuid=async()=>"install-one"; _DEVICE_PROFILE=null; globalThis.screen={width:390,height:844};');
    out.first=(await p.run('deviceProfile()')).fp;
    p.run('_DEVICE_PROFILE=null; screen.width=844; screen.height=390; navigator.userAgent="Android new-version";');
    out.changed=(await p.run('deviceProfile()')).fp;
    p.run('deviceUuid=async()=>"install-two"; _DEVICE_PROFILE=null;');
    out.reinstalled=(await p.run('deviceProfile()')).fp;
    ''')
    assert result["first"] == result["changed"]
    assert result["first"] != result["reinstalled"]
