"""Operator CLI for the revision-2 market breadth shadow rebuild."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from datetime import date

from app.database import SessionLocal
from app.domain.markets.catalog import get_market_catalog
from app.services.breadth.rebuild import BreadthRebuildService

EXIT_OK = 0
EXIT_CONFIRMATION_REQUIRED = 2
EXIT_VALIDATION_REQUIRED = 3


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="phase", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--market", action="append")
    build.add_argument("--start-date", type=_date, required=True)
    build.add_argument("--end-date", type=_date)
    subparsers.add_parser("validate")
    activate = subparsers.add_parser("activate")
    activate.add_argument("--confirm-replace", action="store_true")
    subparsers.add_parser("cleanup")
    return parser


def _default_service_factory() -> BreadthRebuildService:
    return BreadthRebuildService(SessionLocal())


def main(
    argv: Sequence[str] | None = None,
    *,
    service_factory: Callable[[], BreadthRebuildService] = _default_service_factory,
) -> int:
    args = _parser().parse_args(argv)
    if args.phase == "activate" and not args.confirm_replace:
        return EXIT_CONFIRMATION_REQUIRED

    service = service_factory()
    if args.phase == "build":
        markets = tuple(
            value.upper()
            for value in (
                args.market
                or get_market_catalog().market_codes_with_capability("breadth")
            )
        )
        report = service.build(
            markets=markets,
            start_date=args.start_date,
            end_date=args.end_date,
        )
    elif args.phase == "validate":
        report = service.validate()
        if not report["valid"]:
            print(json.dumps(report, indent=2, sort_keys=True))
            return EXIT_VALIDATION_REQUIRED
    elif args.phase == "activate":
        report = service.validate()
        if not report["valid"]:
            print(json.dumps(report, indent=2, sort_keys=True))
            return EXIT_VALIDATION_REQUIRED
        report = service.activate()
    else:
        service.cleanup()
        report = {"cleaned": True}
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
