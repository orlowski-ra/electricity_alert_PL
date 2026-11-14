"""Command line interface: ``electricity-alert --help``."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from . import __version__, quiet_hours
from .checker import OutageChecker
from .http import HttpClient
from .models import WARSAW, Address, ApiResult, FormatError, HttpError, NetworkError, NotSupported, Outage, ProviderId, Success, TimeWindow
from .providers import default_registry
from .providers.pge import PgeProvider
from .providers.tauron import TauronProvider
from .storage import Storage
from .text import outage_body

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def default_home() -> Path:
    return Path(os.environ.get("ELECTRICITY_ALERT_HOME") or Path.home() / ".electricity_alert")


def describe_error(result: ApiResult[object]) -> str:
    if isinstance(result, NetworkError):
        return "network error (no connection or timeout)"
    if isinstance(result, HttpError):
        return f"server error (HTTP {result.code})"
    if isinstance(result, FormatError):
        return "unexpected response format (the service may have changed) or incomplete address"
    if isinstance(result, NotSupported):
        return "not supported"
    return "unknown error"


class ConsoleNotifier:
    def __init__(self, out: TextIO) -> None:
        self._out = out

    def show_new(self, address: Address, outage: Outage, silent: bool) -> bool:
        tag = " (silent)" if silent else ""
        self._out.write(f"[NEW]{tag} {address.label}\n{_indent(outage_body(outage))}\n")
        return True

    def show_revoked(self, address: Address, outage: Outage, silent: bool) -> bool:
        tag = " (silent)" if silent else ""
        self._out.write(f"[REVOKED]{tag} {address.label}\n{_indent(outage_body(outage))}\n")
        return True


def _indent(text: str) -> str:
    return "\n".join("    " + line for line in text.splitlines())


def _parse_params(items: Sequence[str]) -> dict[str, str]:
    params = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"expected key=value, got {item!r}")
        params[key.strip()] = value.strip()
    return params


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="electricity-alert", description="Planned power outage alerts for addresses in Poland.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--home", type=Path, help="data folder (default: ~/.electricity_alert or $ELECTRICITY_ALERT_HOME)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("providers", help="list supported operators")

    search = sub.add_parser("search", help="find a town (PGE dictionary or Tauron)")
    search.add_argument("provider", choices=["pge", "tauron"])
    search.add_argument("query")
    search.add_argument("--nationwide", action="store_true", help="PGE: all of Poland (for Enea/Energa addresses)")

    streets = sub.add_parser("streets", help="list streets of a town")
    streets.add_argument("provider", choices=["pge", "tauron"])
    streets.add_argument("city", help="PGE: citySym; Tauron: city GAID")
    streets.add_argument("query", nargs="?", default="", help="PGE: town name (for cities with districts); Tauron: street name part")

    add = sub.add_parser("add", help="save an address")
    add.add_argument("provider", choices=[p.value for p in ProviderId])
    add.add_argument("--label", required=True)
    add.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")

    sub.add_parser("list", help="list saved addresses")

    remove = sub.add_parser("remove", help="remove a saved address")
    remove.add_argument("id", type=int)

    check = sub.add_parser("check", help="check all saved addresses and print notifications")
    check.add_argument("--days", type=int, default=1, help="days ahead, 1-4 (default 1 = today and tomorrow)")
    check.add_argument("--no-revoked", action="store_true", help="do not notify about revoked outages")

    outages = sub.add_parser("outages", help="show outages for an address without saving it")
    outages.add_argument("provider", choices=[p.value for p in ProviderId])
    outages.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    outages.add_argument("--days", type=int, default=1)
    return parser


def main(
    argv: Sequence[str] | None = None,
    out: TextIO | None = None,
    http: HttpClient | None = None,
    now: datetime | None = None,
) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    home: Path = args.home or default_home()
    own_http = http is None
    http = http or HttpClient()
    try:
        return _run(args, home, http, out, now)
    except ValueError as e:
        out.write(f"error: {e}\n")
        return EXIT_USAGE
    finally:
        if own_http:
            http.close()


def _run(args: argparse.Namespace, home: Path, http: HttpClient, out: TextIO, now: datetime | None) -> int:
    registry = default_registry(http, cache_dir=home / "cache")
    clock = (lambda: now) if now else (lambda: datetime.now(UTC))

    if args.command == "providers":
        for provider in registry.all:
            out.write(f"{provider.id.value:8} {provider.display_name}\n")
        return EXIT_OK

    if args.command == "search":
        provider = registry.get(ProviderId(args.provider))
        if isinstance(provider, PgeProvider):
            result = provider.search_cities(args.query, nationwide=args.nationwide)
            if not isinstance(result, Success):
                out.write(f"error: {describe_error(result)}\n")
                return EXIT_ERROR
            for c in result.data:
                out.write(
                    f"{c.city_sym}\t{c.name}\tgm. {c.commune or '-'}\tpow. {c.county or '-'}\t{c.voivodeship or '-'}"
                    f"\tcountyCode={c.county_code or ''} communeType={c.commune_type_code or ''}\n"
                )
            return EXIT_OK
        if isinstance(provider, TauronProvider):
            tauron = provider.search_cities(args.query)
            if not isinstance(tauron, Success):
                out.write(f"error: {describe_error(tauron)}\n")
                return EXIT_ERROR
            for t in tauron.data:
                out.write(f"{t.gaid}\t{t.name}\tgm. {t.commune or '-'}\tpow. {t.county or '-'}\t{t.voivodeship or '-'}\n")
            return EXIT_OK

    if args.command == "streets":
        provider = registry.get(ProviderId(args.provider))
        if isinstance(provider, PgeProvider):
            streets = provider.streets_with_districts(args.city, args.query)
            if not isinstance(streets, Success):
                out.write(f"error: {describe_error(streets)}\n")
                return EXIT_ERROR
            for s in streets.data:
                district = f"\t({s.district}, citySym={s.city_sym})" if s.district else ""
                out.write(f"{s.street_sym}\t{s.name}{district}\n")
            return EXIT_OK
        if isinstance(provider, TauronProvider):
            try:
                gaid = int(args.city)
            except ValueError:
                raise ValueError("Tauron city must be a number (GAID)") from None
            found = provider.search_streets(args.query, gaid) if args.query else provider.only_one_street(gaid)
            if not isinstance(found, Success):
                out.write(f"error: {describe_error(found)}\n")
                return EXIT_ERROR
            items = found.data if isinstance(found.data, list) else ([found.data] if found.data else [])
            for street in items:
                out.write(f"{street.gaid}\t{street.name}\n")
            return EXIT_OK

    storage = Storage(home / "data.db")
    try:
        if args.command == "add":
            address = storage.add_address(ProviderId(args.provider), args.label, _parse_params(args.param))
            out.write(f"saved address {address.id}: {address.label}\n")
            return EXIT_OK
        if args.command == "list":
            for a in storage.addresses():
                params = " ".join(f"{k}={v}" for k, v in a.params.items())
                out.write(f"{a.id}\t{a.provider.value}\t{a.label}\t{params}\n")
            return EXIT_OK
        if args.command == "remove":
            if storage.remove_address(args.id):
                out.write(f"removed address {args.id}\n")
                return EXIT_OK
            out.write(f"error: no address {args.id}\n")
            return EXIT_ERROR
        if args.command == "check":
            checker = OutageChecker(
                storage,
                registry,
                ConsoleNotifier(out),
                clock=clock,
                is_quiet=lambda moment: quiet_hours.is_quiet(moment, WARSAW),
            )
            report = checker.run(days=args.days, notify_revoked=not args.no_revoked)
            labels = {a.id: a.label for a in storage.addresses()}
            for address_id, result in report.results.items():
                if not isinstance(result, Success):
                    out.write(f"[ERROR] {labels.get(address_id, address_id)}: {describe_error(result)}\n")
            out.write(f"checked {len(report.results)} address(es), {len(report.notified)} notification(s)\n")
            return EXIT_ERROR if report.connection_problem else EXIT_OK
        if args.command == "outages":
            provider_id = ProviderId(args.provider)
            provider = registry.get(provider_id)
            assert provider is not None
            address = Address(id=0, provider=provider_id, label="(ad hoc)", params=_parse_params(args.param))
            result = provider.planned_outages(address, TimeWindow.upcoming(clock(), args.days))
            if not isinstance(result, Success):
                out.write(f"error: {describe_error(result)}\n")
                return EXIT_ERROR
            if not result.data:
                out.write("no planned outages\n")
            for outage in result.data:
                revoked = " [REVOKED]" if outage.revoked else ""
                out.write(f"{outage.uuid}{revoked}\n{_indent(outage_body(outage))}\n")
            return EXIT_OK
    finally:
        storage.close()
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
