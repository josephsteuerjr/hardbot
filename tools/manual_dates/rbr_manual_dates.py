"""Portable Python bootstrap. Keep beside the analyst scripts and package JSON."""
from pathlib import Path,PurePosixPath
import hashlib,json,os,shutil,sys,tempfile
sys.dont_write_bytecode=True
os.environ['PYTHONDONTWRITEBYTECODE']='1'

_directory=Path(__file__).resolve().parent
_package=json.loads((_directory/'rbr-manual-dates-package.json').read_text('utf-8'))
_source=_directory/_package['directory']
def _sha(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):result.update(chunk)
    return result.hexdigest()
if _source.name!=_package['directory'] or hashlib.sha256(json.dumps(_package['files'],sort_keys=True,separators=(',',':')).encode()).hexdigest()!=_package['sha256']:
    raise RuntimeError('Повреждён Python-комплект учёта дат')
_root=Path(os.environ.get('LOCALAPPDATA',Path.home()))/'Hard/manual-edits'
_root.mkdir(parents=True,exist_ok=True)
_code=_root/'packages'/_package['sha256'][:20]
if not _code.is_dir():
    _code.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='package-',dir=_code.parent) as _temp:
        _staging=Path(_temp)/'code';_staging.mkdir()
        for _name,_digest in _package['files'].items():
            _part=PurePosixPath(_name)
            if _part.is_absolute() or '..' in _part.parts or '\\' in _name:
                raise RuntimeError('Недопустимый путь Python-комплекта')
            _target=_staging.joinpath(*_part.parts);_target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(_source.joinpath(*_part.parts),_target)
            if _sha(_target)!=_digest:raise RuntimeError('Повреждён файл Python-комплекта: '+_name)
        try:os.rename(_staging,_code)
        except FileExistsError:pass
for _name,_digest in _package['files'].items():
    if _sha(_code/_name)!=_digest:raise RuntimeError('Изменён файл Python-комплекта: '+_name)
_paths=[str(_code),str(_code/'server'),str(_code/'pult')]
_vendor=_code/'vendor'/sys.implementation.cache_tag
if _vendor.is_dir():_paths.insert(0,str(_vendor))
sys.path[:0]=_paths
os.environ['PYTHONPATH']=os.pathsep.join(_paths+[os.environ.get('PYTHONPATH','')])
_config=Path(os.environ.get('HARD_MANUAL_DATES_CONFIG',_directory/'rbr-manual-dates.json'))
os.environ['HARD_MANUAL_DATES_CONFIG']=str(_config)
# The frozen analyst and the background Python use the same explicit manifest.
_pointer=_root/'config.json';_tmp=_root/('config-'+str(os.getpid())+'.tmp')
_tmp.write_text(json.dumps({'manifest':str(_config)},ensure_ascii=False),encoding='utf-8');os.replace(_tmp,_pointer)
from pult.core.manual_runtime import (read_frame,prepare,personal_paths,preferred_path,
                                     fingerprint,publish,staged_master,start_worker,save_local,case_key)
from pult.core.manual_config import load_config,department_folders

def configured_roots(fallback):
    """Explicit department roots also cover bounded nested portfolio folders."""
    config=load_config()
    roots=config.get('personal_roots') if config.get('delivery_ready',config.get('enabled',False)) else None
    return [Path(p) for p in roots] if roots else list(fallback)

def workbook_kind(path,headers=None):
    """A legacy folder name cannot decide the workbook's Hard/Soft block."""
    import edit_tracking
    path=Path(path)
    if headers is None and path.is_dir():path=preferred_path(path/(path.name+'.xlsb'))
    if headers is None and Path(path).is_file():
        rows=read_frame(path,nrows=3).values.tolist()
        headers=next((row for row in rows if any(edit_tracking.normalized(c)==edit_tracking.normalized('Номер КД / ID') for c in row)),[])
    headers=headers or []
    kinds=set(edit_tracking.columns(headers).values())
    normalized={edit_tracking.normalized(x) for x in headers}
    kinds.update(key for key,title in edit_tracking.LOGICAL_TITLES.items() if edit_tracking.normalized(title) in normalized)
    if kinds=={edit_tracking.EXCEL_KEYS[0]}:return 'hard'
    if kinds=={edit_tracking.EXCEL_KEYS[1]}:return 'soft'
    root=str(Path(path).parent.parent).casefold()
    profile=next((value for name,value in load_config().get('root_profiles',{}).items() if str(Path(name)).casefold()==root),None)
    if profile in ('hard','soft'):return profile
    return 'soft' if any(part.casefold()=='soft' for part in Path(path).parts) else 'hard'

if load_config().get('enabled') and not os.environ.get('HARD_MANUAL_DATES_NO_WORKER'):
    start_worker(_config)
