"""NOWCAST Verify - coda di revisione dei candidati osservati."""

import argparse
import json
from pathlib import Path

from backend.database import connect, init_db

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "vvf_event_candidates.jsonl"


def load_candidates(path):
    path = Path(path)
    if not path.exists():
        raise ValueError(f"File non trovato: {path}")

    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON non valido alla riga {n}: {exc}") from exc
    return rows


def find_approved_event(conn, row):
    if not all(row.get(k) for k in ("event_date", "location", "event_type")):
        return None

    return conn.execute(
        """
        SELECT id, verification_status
        FROM events
        WHERE event_date = ?
          AND COALESCE(event_time, '') = COALESCE(?, '')
          AND lower(trim(location)) = lower(trim(?))
          AND lower(trim(event_type)) = lower(trim(?))
          AND verification_status = 'verified'
        ORDER BY id
        LIMIT 1
        """,
        (
            row["event_date"],
            row.get("event_time"),
            row["location"],
            row["event_type"],
        ),
    ).fetchone()


def has_source_evidence(conn, event_id, source_url):
    if not event_id or not source_url:
        return False

    row = conn.execute(
        """
        SELECT 1
        FROM event_evidence
        WHERE event_id = ?
          AND source_url = ?
        LIMIT 1
        """,
        (event_id, source_url),
    ).fetchone()
    return row is not None


def classify(conn, row):
    if not row.get("complete_for_review"):
        return "INCOMPLETO", None

    event = find_approved_event(conn, row)

    if event and has_source_evidence(conn, event["id"], row.get("source_url")):
        return "GIA' APPROVATO", event["id"]

    return "DA REVISIONARE", event["id"] if event else None


def show(row, status, event_id):
    date = row.get("event_date") or "????-??-??"
    time = row.get("event_time") or "--:--"
    location = row.get("location") or "[localita non determinata]"
    event_type = row.get("event_type") or "?"
    candidate_id = row.get("candidate_id") or "?"
    source = row.get("source_name") or "?"

    print(f"[{status}] {date} {time} | {event_type} | {location}")
    print(f"  candidato: {candidate_id}")
    print(f"  fonte    : {source}")

    if event_id:
        print(f"  event ID : {event_id}")

    if status == "INCOMPLETO":
        missing = []
        if not row.get("event_date"):
            missing.append("data")
        if not row.get("event_time"):
            missing.append("ora")
        if not row.get("location"):
            missing.append("localita")
        if not row.get("event_type"):
            missing.append("tipo")
        if missing:
            print("  manca    : " + ", ".join(missing))

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Mostra la coda di revisione degli eventi candidati."
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument(
        "--only-pending",
        action="store_true",
        help="Mostra solo i candidati che richiedono revisione.",
    )
    args = parser.parse_args()

    try:
        candidates = load_candidates(args.input)
        init_db()

        counts = {
            "DA REVISIONARE": 0,
            "INCOMPLETO": 0,
            "GIA' APPROVATO": 0,
        }

        print()
        print("NOWCAST VERIFY - REVIEW QUEUE")
        print("=============================")
        print()

        with connect() as conn:
            classified = []
            for row in candidates:
                status, event_id = classify(conn, row)
                counts[status] += 1
                classified.append((row, status, event_id))

            for row, status, event_id in classified:
                if args.only_pending and status != "DA REVISIONARE":
                    continue
                show(row, status, event_id)

        print("RIEPILOGO")
        print("---------")
        print("Candidati totali :", len(candidates))
        print("Da revisionare   :", counts["DA REVISIONARE"])
        print("Incompleti       :", counts["INCOMPLETO"])
        print("Gia' approvati   :", counts["GIA' APPROVATO"])
        print()

        return 0

    except (ValueError, OSError) as exc:
        print("ERRORE:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
