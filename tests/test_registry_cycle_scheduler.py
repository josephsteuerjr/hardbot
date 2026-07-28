from datetime import datetime
from dataclasses import replace
from xml.etree import ElementTree as ET

from registry_cycle.config import load_config_bundle
from registry_cycle.scheduler import TASK_NS, build_task_xml


def test_scheduler_runs_only_in_interactive_user_session():
    bundle = load_config_bundle()
    xml = build_task_xml(
        bundle,
        python_executable=r"C:\Python\python.exe",
        user_id=r"DOMAIN\egor",
        now=datetime(2026, 7, 27, 12, 0),
    )
    root = ET.fromstring(xml)
    ns = {"t": TASK_NS}

    assert root.findtext(".//t:LogonType", namespaces=ns) == "InteractiveToken"
    assert root.findtext(".//t:UserId", namespaces=ns) == r"DOMAIN\egor"
    assert root.findtext(".//t:Command", namespaces=ns) == r"C:\Python\python.exe"
    assert (
        root.findtext(".//t:Arguments", namespaces=ns)
        == "-m registry_cycle execute --scheduled"
    )
    assert root.find(".//t:Monday", ns) is not None
    assert root.find(".//t:Friday", ns) is not None
    assert root.find(".//t:Saturday", ns) is not None
    assert root.find(".//t:Sunday", ns) is not None


def test_scheduler_adds_publish_only_when_explicitly_enabled():
    bundle = load_config_bundle()
    scheduler = dict(bundle.app.scheduler)
    scheduler["publish"] = True
    bundle = replace(
        bundle,
        app=replace(bundle.app, scheduler=scheduler),
    )

    xml = build_task_xml(
        bundle,
        python_executable=r"C:\Python\python.exe",
        user_id=r"DOMAIN\egor",
        now=datetime(2026, 7, 27, 12, 0),
    )
    root = ET.fromstring(xml)
    ns = {"t": TASK_NS}

    assert (
        root.findtext(".//t:Arguments", namespaces=ns)
        == "-m registry_cycle execute --scheduled --publish"
    )
