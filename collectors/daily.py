#!/usr/bin/env python3
"""
NOWCAST Verify - conservative daily collection runner.

Runs collectors in sequence but never approves an event automatically.

Pipeline:
1. VVF archive collection
2. Conservative VVF event extraction
3. ARPAE post-event candidate refresh
4. Pending review queue

Each collector keeps its own default paths and rules.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date, timedelta


def run_module(module: str, args: list[str] | None = None) -> None:
    cmd = [sys.executable, "-m", module]
    if args:
        cmd.extend(args)

    print()
    print("=" * 68)
    print("ESEGUO:", " ".join(cmd))
    print("=" * 68)

    completed = subprocess.run(cmd, check=False)

    if completed.returncode != 0:
        raise SystemExit(
            f"\nERRORE: {module} terminato con codice "
            f"{completed.returncode}. Pipeline interrotta."
        )


def valid_iso_date(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "La data deve essere nel formato YYYY-MM-DD."
        ) from exc
    return value


def main() -> int:
    today = date.today()
    default_from = today - timedelta(days=2)

    parser = argparse.ArgumentParser(
        description=(
            "Raccoglie e prepara ground truth candidata senza "
            "approvare automaticamente alcun evento."
        )
    )
    parser.add_argument(
        "--from-date",
        type=valid_iso_date,
        default=default_from.isoformat(),
        help=(
            "Data iniziale VVF YYYY-MM-DD. "
            "Default: due giorni fa."
        ),
    )
    parser.add_argument(
        "--to-date",
        type=valid_iso_date,
        default=today.isoformat(),
        help="Data finale VVF YYYY-MM-DD. Default: oggi.",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=today.year,
        help="Anno dei rapporti ARPAE. Default: anno corrente.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help="Massimo pagine archivio VVF. Default: 10.",
    )
    parser.add_argument(
        "--skip-arpae",
        action="store_true",
        help="Non aggiorna i candidati ARPAE.",
    )

    args = parser.parse_args()

    if date.fromisoformat(args.from_date) > date.fromisoformat(args.to_date):
        parser.error("--from-date non può essere successiva a --to-date")

    if args.max_pages < 1:
        parser.error("--max-pages deve essere almeno 1")

    print("NOWCAST VERIFY - RACCOLTA GIORNALIERA")
    print("------------------------------------")
    print(f"VVF   : {args.from_date} -> {args.to_date}")
    print(f"ARPAE : {args.year}" if not args.skip_arpae else "ARPAE : saltata")
    print()
    print(
        "Modalità conservativa: nessun candidato viene approvato "
        "automaticamente."
    )

    run_module(
        "collectors.vvf",
        [
            "--from-date", args.from_date,
            "--to-date", args.to_date,
            "--max-pages", str(args.max_pages),
        ],
    )

    run_module("collectors.vvf_events")

    if not args.skip_arpae:
        run_module(
            "collectors.arpae",
            ["--year", str(args.year)],
        )

    run_module(
        "collectors.review_queue",
        ["--only-pending"],
    )

    print()
    print("=" * 68)
    print("RACCOLTA COMPLETATA")
    print("=" * 68)
    print(
        "I candidati sono stati raccolti e preparati per la revisione. "
        "Nessun evento è stato approvato automaticamente."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
