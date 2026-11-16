"""Build a hash-bound primary patch offline, preserving unrelated live code/routes."""
import argparse,ast,hashlib,json
from pathlib import Path

SOURCE=Path(__file__).resolve().parents[1]/'server'


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
        # Every other top-level function remains structurally identical.
        old=functions(before);new=functions(result)
        for key in old.keys()-set(names)-({'build_app'} if name=='web.py' else set()):
            assert ast.dump(old[key],include_attributes=False)==ast.dump(new[key],include_attributes=False),key
        compile(result,name,'exec');built[name]=result.encode()
        manifest[name]={'before_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'functions':names}
    for name in ['call_scripts.py','crm/__init__.py','crm/primary_fence.py','crm/storage.py','crm/contract.py']:
        previous=baseline/name
        built[name]=(SOURCE/name).read_bytes();manifest[name]={'before_sha256':hashlib.sha256(previous.read_bytes()).hexdigest() if previous.exists() else None,'module':True}
    output.mkdir(parents=True)
    for name,value in built.items():
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(value)
        manifest[name]['after_sha256']=hashlib.sha256(value).hexdigest()
    (output/'manifest.json').write_text(json.dumps(dict(schema=1,activation=False,files=manifest),indent=2),encoding='utf-8')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--baseline',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();result=build(args.baseline,args.output)
    print(json.dumps(dict(files=len(result),working_server_changed=False)))
