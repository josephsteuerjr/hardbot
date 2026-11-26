"""Build a hash-bound primary patch offline, preserving unrelated live code/routes."""
import argparse,ast,hashlib,json
from pathlib import Path

SOURCE=Path(__file__).resolve().parents[1]/'server'
RELEASE_GATE=SOURCE.parent/'deploy/crm/release_gate.py'


def functions(text):
    return {n.name:n for n in ast.parse(text).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}


def replace_functions(before,after,names,anchor=None):
    original=functions(before);incoming=functions(after);lines=before.splitlines(keepends=True);edits=[]
    for name in names:
        value=ast.get_source_segment(after,incoming[name])+'\n'
        if name in original:
            n=original[name];edits.append((n.lineno-1,n.end_lineno,value))
        else:
            offset=original[anchor].lineno-1 if anchor else len(lines)
            edits.append((offset,offset,'\n\n'+value+'\n'))
    for start,end,value in sorted(edits,reverse=True):lines[start:end]=[value]
    result=''.join(lines);compile(result,'prepared-primary','exec');return result


def build(baseline,output):
    baseline=Path(baseline).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('Use a new output directory; no existing package is overwritten')
    selected={'db.py':['save_call_result','save_visit_result','import_grid','init_db'],
              'accounts.py':['login','entered_list','manager_team','manager_revoke_device'],
              'web.py':['_manager_team','_call_script','_manager_call_script']}
    built={};manifest={}
    for name,names in selected.items():
        path=baseline/name;before=path.read_text(encoding='utf-8-sig');after=(SOURCE/name).read_text(encoding='utf-8-sig')
        result=replace_functions(before,after,names,'build_app' if name=='web.py' else None)
        if name=='accounts.py' and not any(isinstance(n,ast.Import) and any(a.name=='os' for a in n.names) for n in ast.parse(result).body):
            lines=result.splitlines(keepends=True);offset=min(n.lineno for n in functions(result).values())-1
            lines.insert(offset,'import os\n\n');result=''.join(lines)
        if name=='web.py':
            marker='    app.router.add_get("/api/manager/entered", _manager_entered)\n'
            if result.count(marker)!=1:raise ValueError('Primary route anchor changed; review the new baseline')
            routes=[("get",'/api/manager/team','_manager_team'),('post','/api/manager/team/device-revoke','_manager_team'),
                    ('get','/api/manager/call-script','_manager_call_script'),('post','/api/manager/call-script','_manager_call_script'),('get','/api/call-script','_call_script')]
            for method,route,handler in routes:
                if route in before:raise ValueError('Primary already has a target route; reconcile instead of adding a duplicate')
            result=result.replace(marker,marker+''.join(f'    app.router.add_{method}({route!r},{handler})\n' for method,route,handler in routes))
            app_marker='    app.on_response_prepare.append(_security_headers)\n'
            if result.count(app_marker)!=1:raise ValueError('Primary middleware anchor changed')
            result=result.replace(app_marker,app_marker+'    from crm.release_gate import install as install_release_gate\n    install_release_gate(app)\n')
        # Every other top-level function remains structurally identical.
        old=functions(before);new=functions(result)
        for key in old.keys()-set(names)-({'build_app'} if name=='web.py' else set()):
            assert ast.dump(old[key],include_attributes=False)==ast.dump(new[key],include_attributes=False),key
        compile(result,name,'exec');built[name]=result.encode()
        manifest[name]={'before_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'functions':names}
    for name in ['call_scripts.py','crm_registration.py','crm/__init__.py','crm/primary_fence.py','crm/storage.py','crm/contract.py']:
        previous=baseline/name
        built[name]=(SOURCE/name).read_bytes();manifest[name]={'before_sha256':hashlib.sha256(previous.read_bytes()).hexdigest() if previous.exists() else None,'module':True}
    gate='crm/release_gate.py'
    previous=baseline/gate
    built[gate]=RELEASE_GATE.read_bytes()
    manifest[gate]={'before_sha256':hashlib.sha256(previous.read_bytes()).hexdigest() if previous.exists() else None,'module':True}
    return write_package(output,built,manifest)


def write_package(output,built,manifest):
    output.mkdir(parents=True)
    for name,value in built.items():
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(value)
        manifest[name]['after_sha256']=hashlib.sha256(value).hexdigest()
    (output/'manifest.json').write_text(json.dumps(dict(schema=1,activation=False,files=manifest),indent=2),encoding='utf-8')
    return manifest


def build_registration(baseline,output):
    """Patch only the registration changes on a primary with the CRM identity bridge."""
    baseline=Path(baseline).resolve()
    output=Path(output).resolve()
    if output.exists():
        raise ValueError('Use a new output directory; no existing package is overwritten')
    selected={'accounts.py':['create_account','login','admin_accounts'],
              'staff_roster.py':['candidates'], 'crm_identity.py':['identity'],
              'account_access.py':['cmd_approve','cmd_registration_code','main'],
              'web.py':['_account_login','_admin_account_approve','_admin','_crm_account_registration','_manager_device_me'],
              'db.py':[]}
    built={}
    manifest={}
    for name,names in selected.items():
        path=baseline/name
        before=path.read_text(encoding='utf-8-sig')
        after=(SOURCE/name).read_text(encoding='utf-8-sig')
        original=functions(before)
        if any(key not in original for key in names if key not in {'_crm_account_registration','cmd_registration_code'}):
            raise ValueError(f'Primary CRM baseline lacks a required function in {name}')
        result=replace_functions(before,after,names,'build_app' if name=='web.py' else 'main' if name=='account_access.py' else None)
        if name=='web.py':
            marker='    app.router.add_post("/api/account/register", _account_register)\n'
            route='/api/account/crm-registration'
            if result.count(marker)!=1 or route in before:
                raise ValueError('Primary registration routes changed; reconcile the new baseline')
            result=result.replace(marker,marker+''.join(
                f'    app.router.add_{method}({route!r},_crm_account_registration)\n' for method in ('get','post')))
        if name=='db.py':
            marker='        accounts.init_schema(conn)\n'
            if result.count(marker)!=1 or 'crm_registration.init_schema' in result:
                raise ValueError('Primary account schema anchor changed; reconcile the new baseline')
            result=result.replace(marker,marker+'        import crm_registration\n        crm_registration.init_schema(conn)\n')
        compile(result,name,'exec')
        built[name]=result.encode()
        manifest[name]={'before_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'functions':names}
    name='crm_registration.py'
    if (baseline/name).exists():
        raise ValueError('Primary registration module already exists; reconcile the new baseline')
    built[name]=(SOURCE/name).read_bytes()
    manifest[name]={'before_sha256':None,'module':True}
    return write_package(output,built,manifest)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--baseline',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--registration',action='store_true')
    args=parser.parse_args();result=(build_registration if args.registration else build)(args.baseline,args.output)
    print(json.dumps(dict(files=len(result),working_server_changed=False)))
