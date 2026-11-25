"""A fresh courier shell must not keep asking to reload its own version."""
from pathlib import Path
import re


def test_pwa_page_and_service_worker_advertise_the_same_shell_version():
    assets=Path(__file__).resolve().parents[1]/'server/miniapp'
    page=int(re.search(r'const SHELL_VERSION=(\d+);',(assets/'index.html').read_text(encoding='utf-8'))[1])
    worker=int(re.search(r'const CACHE = "hard-shell-v(\d+)";',(assets/'sw.js').read_text(encoding='utf-8'))[1])
    assert page==worker
