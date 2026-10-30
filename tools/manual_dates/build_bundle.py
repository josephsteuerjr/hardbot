"""Build a small, reviewable Python source package. No private configuration."""
from pathlib import Path
import argparse,hashlib,json

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--calamine-cp314',type=Path)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2]
    files={}
    def add(path,name=None):files[name or path.relative_to(root).as_posix()]=path.read_bytes()
    for name in ('pult/__init__.py','pult/core/__init__.py','pult/core/bootstrap.py','pult/enrich.py',
                 'server/edit_tracking.py','server/exchange_contract.py','server/data_exchange_contract.json','server/fields.py'):
        add(root/name)
    for path in (root/'pult/core').glob('manual_*.py'):add(path)
    for path in (root/'registry_cycle').glob('*.py'):add(path)
    if args.calamine_cp314:
        for path in args.calamine_cp314.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                add(path,'vendor/cpython-314/'+path.relative_to(args.calamine_cp314).as_posix())
    args.output.mkdir(parents=True,exist_ok=True)
    directory=args.output/'rbr-manual-dates-python'
    for name,blob in sorted(files.items()):
        path=directory/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(blob)
    hashes={name:hashlib.sha256(blob).hexdigest() for name,blob in sorted(files.items())}
    manifest=dict(schema=2,directory=directory.name,
                  sha256=hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest(),files=hashes)
    (args.output/'rbr-manual-dates-package.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    for name in ('rbr_manual_dates.py','rbr_edit_dates.py','start_manual_dates.pyw'):
        (args.output/name).write_bytes(Path(__file__).with_name(name).read_bytes())
    print(json.dumps(dict(files=len(files),bytes=sum(map(len,files.values())),sha256=manifest['sha256'])))
if __name__=='__main__':main()
