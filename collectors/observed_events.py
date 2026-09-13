import argparse
from backend.database import connect, init_db


def add_event(args):
    init_db()

    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO events (
                event_date,
                event_time,
                location,
                province,
                region,
                event_type,
                source_name,
                source_url,
                evidence,
                verification_status,
                time_verified,
                verification_notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                args.date,
                args.time,
                args.location,
                args.province,
                args.region,
                args.type,
                args.source,
                args.url,
                args.evidence,
                args.status,
                1 if args.time_verified else 0,
                args.notes,
            ),
        )

        event_id = cursor.lastrowid

    print(f"Evento registrato con ID {event_id}")


def list_events():
    init_db()

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                event_date,
                event_time,
                location,
                province,
                region,
                event_type,
                source_name,
                verification_status,
                time_verified
            FROM events
            ORDER BY event_date DESC, event_time DESC
            """
        ).fetchall()

    if not rows:
        print("Nessun evento registrato.")
        return

    for row in rows:
        time_value = row["event_time"] or "--:--"

        print(
            f"{row['id']:>4} | "
            f"{row['event_date']} {time_value} | "
            f"{row['event_type']:<10} | "
            f"{row['location']} ({row['province'] or '-'}) | "
            f"{row['source_name']} | "
            f"{row['verification_status']} | "
            f"ora={'SI' if row['time_verified'] else 'NO'}"
        )


def main():
    parser = argparse.ArgumentParser(
        description="NOWCAST Verify - gestione eventi osservati"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "list",
        help="Mostra gli eventi osservati registrati"
    )

    add_parser = subparsers.add_parser(
        "add",
        help="Registra un evento osservato"
    )

    add_parser.add_argument("--date", required=True)
    add_parser.add_argument("--time")
    add_parser.add_argument("--location", required=True)
    add_parser.add_argument("--province")
    add_parser.add_argument("--region")
    add_parser.add_argument(
        "--type",
        required=True,
        choices=["hail", "downburst", "wind", "tornado", "other"],
    )
    add_parser.add_argument("--source", required=True)
    add_parser.add_argument("--url")
    add_parser.add_argument("--evidence")
    add_parser.add_argument(
        "--status",
        choices=["reported", "verified", "rejected"],
        default="reported",
    )
    add_parser.add_argument(
        "--time-verified",
        action="store_true",
        help="L'orario è documentato dalla fonte",
    )
    add_parser.add_argument("--notes")

    args = parser.parse_args()

    if args.command == "list":
        list_events()
    elif args.command == "add":
        add_event(args)


if __name__ == "__main__":
    main()
