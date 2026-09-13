"""Importa gli eventi osservati verificati nel database NOWCAST Verify.

Uso:
    python -m collectors.import_observed_events
    python -m collectors.import_observed_events data/observed_events.jsonl
"""

import argparse
import json
from pathlib import Path

from backend.database import connect, init_db


DEFAULT_FILE = Path("data/observed_events.jsonl")


def already_exists(conn, event):
    row = conn.execute(
        """
        SELECT id
        FROM events
        WHERE event_date = ?
          AND COALESCE(event_time, '') = COALESCE(?, '')
          AND location = ?
          AND event_type = ?
          AND source_name = ?
        LIMIT 1
        """,
        (
            event.get("event_date"),
            event.get("event_time"),
            event.get("location"),
            event.get("event_type"),
            event.get("source_name"),
        ),
    ).fetchone()

    return row["id"] if row else None


def validate(event):
    required = (
        "event_date",
        "location",
        "event_type",
        "source_name",
        "verification_status",
    )

    missing = [
        field
        for field in required
        if not event.get(field)
    ]

    if missing:
        raise ValueError(
            "campi mancanti: " + ", ".join(missing)
        )


def import_event(conn, event):
    validate(event)

    existing_id = already_exists(conn, event)

    if existing_id is not None:
        return "skipped", existing_id

    cursor = conn.execute(
        """
        INSERT INTO events (
            event_date,
            event_time,
            location,
            province,
            region,
            latitude,
            longitude,
            event_type,
            severity,
            source_name,
            source_url,
            evidence,
            verification_status,
            time_verified,
            verification_notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.get("event_date"),
            event.get("event_time"),
            event.get("location"),
            event.get("province"),
            event.get("region"),
            event.get("latitude"),
            event.get("longitude"),
            event.get("event_type"),
            event.get("severity"),
            event.get("source_name"),
            event.get("source_url"),
            event.get("evidence"),
            event.get("verification_status", "reported"),
            1 if event.get("time_verified") else 0,
            event.get("verification_notes"),
        ),
    )

    return "inserted", cursor.lastrowid


def import_file(path):
    path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(
            f"File non trovato: {path}"
        )

    init_db()
    conn = connect()

    read_count = 0
    inserted = 0
    skipped = 0
    invalid = 0

    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(
                handle,
                start=1,
            ):
                line = line.strip()

                if not line:
                    continue

                read_count += 1

                try:
                    event = json.loads(line)

                    if not isinstance(event, dict):
                        raise ValueError(
                            "il record non e' un oggetto JSON"
                        )

                    result, event_id = import_event(
                        conn,
                        event,
                    )

                    if result == "inserted":
                        inserted += 1
                        print(
                            f"+ ID {event_id}: "
                            f"{event['event_date']} | "
                            f"{event['event_type']} | "
                            f"{event['location']}"
                        )
                    else:
                        skipped += 1

                except (
                    json.JSONDecodeError,
                    ValueError,
                ) as exc:
                    invalid += 1
                    print(
                        f"! Riga {line_number} ignorata: "
                        f"{exc}"
                    )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    print()
    print("Importazione eventi osservati completata")
    print("---------------------------------------")
    print(f"Record letti:       {read_count}")
    print(f"Eventi nuovi:       {inserted}")
    print(f"Gia' presenti:      {skipped}")
    print(f"Record problematici:{invalid}")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Importa gli eventi osservati "
            "nel database NOWCAST Verify"
        )
    )

    parser.add_argument(
        "file",
        nargs="?",
        default=str(DEFAULT_FILE),
        help=(
            "File JSONL degli eventi "
            "(default: data/observed_events.jsonl)"
        ),
    )

    args = parser.parse_args()
    import_file(args.file)


if __name__ == "__main__":
    main()
