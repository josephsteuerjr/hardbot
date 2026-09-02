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
from .publishing import (
    publication_cycle_lock,
    publish_validated,
    reconcile_incomplete_publication,
)
from .rehearsal import (
    execute_first_baseline_rehearsal,
    promote_first_baseline_rehearsal,
)
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
        description="Выезды · Реестры — безопасная сборка и разборка СНГ",
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
    plan.add_argument(
        "--rollover-month",
        help="явно закрыть месяц YYYY-MM (для ручного/аварийного запуска)",
    )
    stage = commands.add_parser("stage", help="собрать локальный staging")
    stage.add_argument("--snapshot", type=Path)
    stage.add_argument("--run-id")
    stage.add_argument("--rollover-month")
    validate = commands.add_parser("validate", help="повторно проверить staging")
    validate.add_argument("--snapshot", type=Path, required=True)
    validate.add_argument("--stage", type=Path, required=True)
    validate.add_argument("--rollover-month")
    publish = commands.add_parser("publish", help="опубликовать зелёный staging")
    publish.add_argument("--snapshot", type=Path, required=True)
    publish.add_argument("--stage", type=Path, required=True)
    publish.add_argument("--confirm-digest", required=True)
    publish.add_argument("--rollover-month")
    publish.add_argument(
        "--busy-mode",
        choices=("block", "sidecar", "force_close"),
        help="переопределить config только для этой публикации",
    )
    reconcile = commands.add_parser(
        "reconcile-publish",
        help="свести аварийную публикацию к проверенному all-old/all-new",
    )
    reconcile.add_argument("--run-dir", type=Path, required=True)
    reconcile.add_argument(
        "--mode",
        choices=("auto", "all-old", "all-new"),
        default="auto",
    )
    execute = commands.add_parser("execute", help="полный свежий цикл")
    execute.add_argument("--publish", action="store_true")
    execute.add_argument("--scheduled", action="store_true")
    execute.add_argument("--run-id")
    execute.add_argument("--rollover-month")
    execute.add_argument(
        "--busy-mode",
        choices=("block", "sidecar", "force_close"),
        help="переопределить config только для этого цикла",
    )
    rehearse = commands.add_parser(
        "rehearse-first-baseline",
        help="непубликуемая staging-репетиция первого baseline",
    )
    rehearse.add_argument("--snapshot", type=Path, required=True)
    rehearse.add_argument("--rollover-month")
    rehearse.add_argument(
        "--summary",
        action="store_true",
        help="вывести только компактный итог; полный отчёт останется в run_dir",
    )
    promote = commands.add_parser(
        "promote-first-baseline",
        help="явно опубликовать зелёную одноразовую first-baseline репетицию",
    )
    promote.add_argument("--snapshot", type=Path, required=True)
    promote.add_argument("--stage", type=Path, required=True)
    promote.add_argument(
        "--candidate-baseline",
        type=Path,
        required=True,
    )
    promote.add_argument("--confirm-digest", required=True)
    promote.add_argument("--rollover-month")
    promote.add_argument(
        "--busy-mode",
        choices=("block", "sidecar", "force_close"),
    )
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
                service.prepare_existing(
                    args.snapshot,
                    rollover_month=args.rollover_month,
                )
                if args.snapshot
                else service.prepare(
                    run_id=args.run_id,
                    rollover_month=args.rollover_month,
                )
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
                service.prepare_existing(
                    args.snapshot,
                    rollover_month=args.rollover_month,
                )
                if args.snapshot
                else service.prepare(
                    run_id=args.run_id,
                    rollover_month=args.rollover_month,
                )
            )
            staged = build_staging(
                bundle,
                prepared.snapshot,
                prepared.plan,
            )
            _print_json(staged.as_dict())
            return 0
        if command == "validate":
            prepared = service.prepare_existing(
                args.snapshot,
                rollover_month=args.rollover_month,
            )
            staged = load_stage(args.stage)
            report = validate_staging(
                bundle,
                staged,
                prepared.plan,
            )
            _print_json(report.as_dict())
            return 2 if report.blocked else 0
        if command == "publish":
            with publication_cycle_lock(bundle) as cycle_lock:
                prepared = service.prepare_existing(
                    args.snapshot,
                    rollover_month=args.rollover_month,
                )
                staged = load_stage(args.stage)
                report = validate_staging(bundle, staged, prepared.plan)
                result = publish_validated(
                    bundle,
                    staged,
                    prepared.plan,
                    report,
                    confirmation_digest=args.confirm_digest,
                    busy_mode=args.busy_mode,
                    cycle_lock=cycle_lock,
                )
            _print_json(result.as_dict())
            return 0 if result.complete else 2
        if command == "reconcile-publish":
            # Только explicit reconcile получает allow_incomplete lock внутри
            # recovery-функции и вправе atomically claim stale owner.
            result = reconcile_incomplete_publication(
                bundle,
                args.run_dir,
                mode=args.mode,
            )
            _print_json(result)
            return 0
        if command == "execute":
            outcome = execute_cycle(
                bundle,
                publish=args.publish,
                scheduled=args.scheduled,
                run_id=args.run_id,
                busy_mode=args.busy_mode,
                rollover_month=args.rollover_month,
            )
            _print_json(outcome.as_dict())
            return 0 if outcome.status in {"ОПУБЛИКОВАНО", "DRY-RUN ПРОЙДЕН"} else 2
        if command == "rehearse-first-baseline":
            outcome = execute_first_baseline_rehearsal(
                bundle,
                args.snapshot,
                rollover_month=args.rollover_month,
            )
            if args.summary:
                _print_json(
                    {
                        "kind": "candidate_first_baseline_rehearsal",
                        "publishable": False,
                        "run_dir": str(outcome.run_dir),
                        "snapshot": str(args.snapshot),
                        "stage": (
                            str(outcome.stage.staging_dir / "stage.json")
                            if outcome.stage
                            else None
                        ),
                        "candidate_baseline": str(
                            outcome.run_dir
                            / "candidate-baseline.json.gz"
                        ),
                        "blocked": outcome.blocked,
                        "plan_digest": outcome.prepared.plan.digest,
                        "changes": len(outcome.prepared.plan.changes),
                        "projections": len(
                            outcome.prepared.plan.projections
                        ),
                        "plan_issues": [
                            issue.as_dict()
                            for issue in outcome.prepared.plan.issues
                        ],
                        "validation": (
                            outcome.validation.as_dict()
                            if outcome.validation
                            else None
                        ),
                    }
                )
            else:
                _print_json(outcome.as_dict())
            return 2 if outcome.blocked else 0
        if command == "promote-first-baseline":
            result = promote_first_baseline_rehearsal(
                bundle,
                args.snapshot,
                args.stage,
                args.candidate_baseline,
                confirmation_digest=args.confirm_digest,
                rollover_month=args.rollover_month,
                busy_mode=args.busy_mode,
            )
            _print_json(result.as_dict())
            return 0 if result.complete else 2
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
