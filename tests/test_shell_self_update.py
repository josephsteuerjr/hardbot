"""Приложение должно обновляться само, а не по устной инструкции.

04.08.2026: выкатили сжатие фото и свой список вариантов, но людям в поле надо
было объяснить «закройте приложение из списка задач и откройте заново». До
половины это не дошло — выездник продолжал работать на старой оболочке и снова
упирался в то, что уже починено.

Теперь сервер отдаёт номер своей оболочки, приложение сравнивает со своим и
предлагает обновиться. Версия объявлена в ОДНОМ месте — sw.js.
"""

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "server" / "miniapp" / "index.html").read_text(encoding="utf-8")
SW = (ROOT / "server" / "miniapp" / "sw.js").read_text(encoding="utf-8")


def _sw_version() -> int:
    return int(re.search(r"hard-shell-v(\d+)", SW).group(1))


def _index_version() -> int:
    return int(re.search(r"const SHELL_VERSION=(\d+);", INDEX).group(1))


def test_shell_version_matches_between_index_and_sw():
    """Разъехались — приложение будет вечно предлагать обновление или молчать."""
    assert _index_version() == _sw_version(), (
        f"index.html={_index_version()} sw.js={_sw_version()} — синхронизируйте")


def test_server_reads_the_version_from_sw_and_does_not_duplicate_it():
    web = (ROOT / "server" / "web.py").read_text(encoding="utf-8")
    assert "def _shell_version()" in web
    assert "hard-shell-v" in web, "сервер должен читать версию из sw.js"
    assert '"shellVersion": _shell_version()' in web


def test_update_never_interrupts_work():
    """Перезагрузка посреди заполнения формы стоила бы человеку работы."""
    i = INDEX.index("function noteShellVersion(")
    body = INDEX[i:INDEX.index("function reloadForUpdate(", i)]
    assert "location.reload()" not in body
    reload_body = INDEX[INDEX.index("async function reloadForUpdate("):INDEX.index("// Проверка версии", i)]
    assert "PHOTO_PREPARING.size" in reload_body
    assert "saveDraft()===false" in reload_body


def test_busy_user_gets_a_button_instead_of_a_reload():
    assert "reloadForUpdate" in INDEX
    assert "updready:" in INDEX and "updbtn:" in INDEX


def test_update_message_exists_in_all_three_languages():
    assert INDEX.count("updready:") == 3
    assert INDEX.count("updbtn:") == 3


def test_older_or_equal_server_version_does_nothing():
    """Сервер позади или вровень — обновляться не с чего."""
    i = INDEX.index("function noteShellVersion(")
    body = INDEX[i:INDEX.index("function reloadForUpdate(", i)]
    assert "srv<=SHELL_VERSION" in body


def test_actual_update_handler_does_not_reload_current_login_page():
    """Execute the shipped JS with the server's actual advertised version."""
    start = INDEX.index("function noteShellVersion(")
    handler = INDEX[start:INDEX.index("async function reloadForUpdate(", start)]
    script = f"""
const SHELL_VERSION={_index_version()};
let UPDATE_READY=false, reloads=0, banners=0, busy=false;
const PHOTO_PREPARING=new Set(), PHOTO_BUSY=0, draftDirty=false;
const location={{reload(){{reloads++;}}}};
function clientMetric(){{}}
function setTimeout(fn){{fn();}}
function formVisible(){{return busy;}}
function updateOffBar(){{banners++;}}
{handler}
for(let i=0;i<8;i++) noteShellVersion({_sw_version()},true);
if(reloads!==0 || UPDATE_READY) throw Error('Current login page reloads itself');
noteShellVersion(SHELL_VERSION+1,false);
if(reloads!==0 || UPDATE_READY) throw Error('Offline snapshot triggered update');
busy=true; noteShellVersion(SHELL_VERSION+1,true);
if(reloads!==0 || banners!==1) throw Error('Update interrupted active work');
UPDATE_READY=false; busy=false;
noteShellVersion(SHELL_VERSION+1,true);
noteShellVersion(SHELL_VERSION+1,true);
if(reloads!==0 || banners!==2) throw Error('Idle user must also get an explicit update button');
"""
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
