"""Check a cold mobile reopen after PWA upgrade while requests stop answering."""
import argparse
import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as BrowserTimeout, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--previous-assets", required=True, type=Path)
parser.add_argument("--assets", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("--engine", choices=["chromium", "webkit"], default="webkit")
args = parser.parse_args()
source = Path(__file__).resolve().parents[2]
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=False)
legacy, candidate = args.previous_assets.resolve(), args.assets.resolve()
version = json.loads((candidate / "BUILD.json").read_text("utf8"))["version"]
sys.path[:0] = [str(path) for path in (source, source / "server", source / "engine")]
os.environ["PYTHONPATH"] = os.pathsep.join(str(path) for path in (source, source / "server", source / "engine"))

from aiohttp import web
from crm import mobile_server
from crm.mobile_backend import SyntheticBackend

prefix = "/iphone-crm-qa/"
state = {"mode": "online", "stalled": []}
ready, stop = threading.Event(), threading.Event()

@web.middleware
async def network(request, handler):
    path = request.path.removeprefix(prefix)
    if state["mode"] == "stall":
        action = None
        if path == "api":
            document = await request.json()
            if document.get("method") == "GET":
                action = document.get("action")
        if path == "boot" or action in {"work-portfolio", "portfolio", "card"}:
            state["stalled"].append(action or path)
            await asyncio.sleep(90)
            return web.json_response({"error": "timeout"}, status=503)
    return await handler(request)

async def serve():
    mobile_server.ASSETS = legacy
    home = output / "data"
    app = mobile_server.create_app(SyntheticBackend(home / "synthetic"), home, prefix=prefix)
    app.middlewares.insert(0, network)
    runner = web.AppRunner(app, shutdown_timeout=0.5, access_log=None)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        state["url"] = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}" + prefix
        ready.set()
        await asyncio.to_thread(stop.wait)
    finally:
        await runner.cleanup()

def wait_for_worker(page):
    page.wait_for_function(
        """async expected => {
          const registration = await navigator.serviceWorker.getRegistration();
          return !!registration?.active?.scriptURL.endsWith('/sw-'+expected+'.js') ||
            await new Promise(resolve => {
              const active = registration?.active;
              if (!active) { resolve(false); return; }
              const channel = new MessageChannel();
              const timeout = setTimeout(() => { channel.port1.close(); resolve(false); }, 700);
              channel.port1.onmessage = event => {
                clearTimeout(timeout);
                channel.port1.close();
                resolve(event.data.version === expected);
              };
              active.postMessage({type: 'GET_VERSION'}, [channel.port2]);
            });
        }""", arg=version, timeout=20000,
    )

def cold_portfolio(context, timeout=12000):
    page = context.new_page()
    page.set_default_timeout(timeout)
    page.goto(state["url"], wait_until="domcontentloaded")
    try:
        page.locator("[data-person]").first.wait_for()
    except BrowserTimeout:
        result = page.evaluate("""() => ({heading: document.querySelector('#title')?.textContent,
          has_bridge:!!window.crm, empty:!document.querySelector('#view')?.childElementCount,
          nominally_online:navigator.onLine})""")
        result.update(mode=state["mode"], stalled=state["stalled"])
        (output / "BLOCKED.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
        print(json.dumps(result, ensure_ascii=False), flush=True)
        raise
    assert page.locator("meta[name=crm-build]").get_attribute("content") == version
    return page

def shell_cache(page):
    return page.evaluate("""async () => {
      const registration = await navigator.serviceWorker.getRegistration();
      const script = document.querySelector('script[src]')?.src;
      const index = new URL('./index.html', location.href).href;
      return {active:registration?.active?.scriptURL, controller:navigator.serviceWorker.controller?.scriptURL,
        saved_index:!!await caches.match(index), saved_script:!!script && !!await caches.match(script)};
    }""")

worker = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
worker.start()
try:
    assert ready.wait(20), "Isolated mobile BFF did not start"
    with sync_playwright() as playwright:
        browser = getattr(playwright, args.engine).launch()
        context = browser.new_context(viewport={"width": 393, "height": 852}, is_mobile=True, has_touch=True)
        page = context.new_page()
        page.goto(state["url"], wait_until="domcontentloaded")
        page.locator("[data-mobile-role=employee]").click()
        page.locator("[data-person]").first.wait_for()
        page.wait_for_function("() => !!navigator.serviceWorker.controller")
        owner = page.evaluate("() => crm.state().then(s => s.owner)")
        page.evaluate("() => crm.draft('qa-cold-reopen', {text:'Retained draft after upgrade'})")

        mobile_server.ASSETS = candidate
        page.evaluate("() => navigator.serviceWorker.getRegistration().then(r => r.update())")
        wait_for_worker(page)
        page.close()
        page = cold_portfolio(context)
        assert page.evaluate("() => crm.state().then(s => s.owner)") == owner
        page.close()

        state["mode"] = "stall"
        started = time.monotonic()
        page = cold_portfolio(context)
        stalled_seconds = round(time.monotonic() - started, 3)
        assert page.evaluate("() => navigator.onLine"), "Stall test needs a browser with a nominal connection"
        assert page.evaluate("() => crm.state().then(s => s.owner)") == owner
        assert page.evaluate("() => crm.draft('qa-cold-reopen').then(r => r.data.text)") == "Retained draft after upgrade"
        assert "boot" in state["stalled"] and any(action in state["stalled"] for action in ("portfolio", "work-portfolio"))
        cache = shell_cache(page)
        (output / "SHELL-BEFORE-DISCONNECT.json").write_text(json.dumps(cache, indent=2), encoding="utf8")
        assert cache["saved_index"] and cache["saved_script"], "Updated shell is not cached"
        browser.close()

    result = {"engine": args.engine, "passed": True, "bundle": version,
              "cold_reopen_after_upgrade": True, "stalled_network_cached_portfolio": True,
              "stalled_start_seconds": stalled_seconds, "same_owner_and_draft": True}
    (output / "RESULT.json").write_text(json.dumps(result, indent=2), encoding="utf8")
    print(json.dumps(result), flush=True)
finally:
    stop.set()
    worker.join(20)
