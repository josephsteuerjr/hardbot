"""CLI для оператора и Планировщика Windows."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .config import load_config_bundle
from .errors import RegistryCycleError
from .publishing import publish_validated
from .runner import execute_cycle
from .scheduler import build_task_xml, install_task, remove_task, task_status
from .service import CycleService
from .staging import build_staging, load_stage
from .validation import validate_staging


def _print_json(payload) -> None:
    raw = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    sys.stdout.buffer.write(raw.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="registry-cycle",
        description="Контур · Реестры — безопасная сборка и разборка СНГ",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        help="папка app.json / fields.json / theme.json",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("discover", help="построить manifest без открытия Excel")
    plan = commands.add_parser("plan", help="снять локальные копии и построить dry-run")
    plan.add_argument("--run-id", help="идентификатор запуска для тестов/аудита")
    plan.add_argument(
        "--snapshot",
        type=Path,
        help="повторно построить план из существующего snapshot.json",
    )
    stage = commands.add_parser("stage", help="собрать локальный staging")
    stage.add_argument("--snapshot", type=Path)
    stage.add_argument("--run-id")
    validate = commands.add_parser("validate", help="повторно проверить staging")
    validate.add_argument("--snapshot", type=Path, required=True)
    validate.add_argument("--stage", type=Path, required=True)
    publish = commands.add_parser("publish", help="опубликовать зелёный staging")
    publish.add_argument("--snapshot", type=Path, required=True)
    publish.add_argument("--stage", type=Path, required=True)
    publish.add_argument("--confirm-digest", required=True)
    execute = commands.add_parser("execute", help="полный свежий цикл")
    execute.add_argument("--publish", action="store_true")
    execute.add_argument("--scheduled", action="store_true")
    execute.add_argument("--run-id")
    scheduler = commands.add_parser("scheduler", help="Планировщик Windows")
    scheduler.add_argument(
        "action",
        choices=("status", "xml", "install", "remove"),
    )
    commands.add_parser("gui", help="открыть интерфейс")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "gui"
    try:
        bundle = load_config_bundle(args.config_dir)
        service = CycleService(bundle)
        if command == "discover":
            report = service.discover()
            _print_json(report.as_dict())
            return 2 if report.blocked else 0
        if command == "plan":
            if args.snapshot and args.run_id:
                parser.error("--snapshot и --run-id взаимоисключающие")
            prepared = (
                service.prepare_existing(args.snapshot)
                if args.snapshot
                else service.prepare(run_id=args.run_id)
            )
            payload = prepared.plan.as_dict()
            payload["run_id"] = prepared.snapshot.run_id
            payload["run_dir"] = str(prepared.snapshot.run_dir)
            _print_json(payload)
            return 2 if prepared.plan.blocked else 0
        if command == "stage":
            if args.snapshot and args.run_id:
                parser.error("--snapshot и --run-id взаимоисключающие")
            prepared = (
                service.prepare_existing(args.snapshot)
                if args.snapshot
                else service.prepare(run_id=args.run_id)
            )
            staged = build_staging(
                bundle,
                prepared.snapshot,
                prepared.plan,
            )
            _print_json(staged.as_dict())
            return 0
        if command == "validate":
            prepared = service.prepare_existing(args.snapshot)
            staged = load_stage(args.stage)
            report = validate_staging(
                bundle,
                staged,
                prepared.plan,
            )
            _print_json(report.as_dict())
            return 2 if report.blocked else 0
        if command == "publish":
            prepared = service.prepare_existing(args.snapshot)
            staged = load_stage(args.stage)
            report = validate_staging(bundle, staged, prepared.plan)
            result = publish_validated(
                bundle,
                staged,
                prepared.plan,
                report,
                confirmation_digest=args.confirm_digest,
            )
            _print_json(result.as_dict())
            return 0
        if command == "execute":
            outcome = execute_cycle(
                bundle,
                publish=args.publish,
                scheduled=args.scheduled,
                run_id=args.run_id,
            )
            _print_json(outcome.as_dict())
            return 0 if outcome.status in {"ОПУБЛИКОВАНО", "DRY-RUN ПРОЙДЕН"} else 2
        if command == "scheduler":
            if args.action == "status":
                status = task_status(bundle)
                _print_json(asdict(status))
                return 0
            if args.action == "xml":
                sys.stdout.write(build_task_xml(bundle) + "\n")
                return 0
            status = (
                install_task(bundle)
                if args.action == "install"
                else remove_task(bundle)
            )
            _print_json(asdict(status))
            return 0
        if command == "gui":
            from .gui import run_gui

            run_gui(bundle)
            return 0
        parser.error(f"неизвестная команда: {command}")
    except RegistryCycleError as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2
    return 2
