"""Importa il registro storico di NOWCAST nel database NOWCAST Verify.

Il file sorgente e' il registro JSONL prodotto dal motore NOWCAST.
L'importazione e' in sola lettura e non modifica mai il registro originale.

Uso:
    python -m collectors.nowcast_alerts /percorso/registro.jsonl
"""

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.database import connect, init_db


def iso_from_timestamp(value):
    """Converte un Unix timestamp in ISO 8601 UTC."""
    if value is None:
        return None

    try:
        return datetime.fromtimestamp(
            float(value),
            tz=timezone.utc
        ).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def clean_location(location):
    """Normalizza solo gli spazi, senza modificare il dato originale."""
    if not isinstance(location, str):
        return None

    location = location.strip()
    return location or None


def already_imported(conn, alert_id, location):
    """Evita di importare due volte lo stesso alert/localita'."""
    row = conn.execute(
        """
        SELECT 1
        FROM nowcast_alerts
        WHERE alert_id = ? AND location = ?
        LIMIT 1
        """,
        (alert_id, location),
    ).fetchone()

    return row is not None


def import_record(conn, record):
    """Importa un singolo record del registro NOWCAST."""

    alert_id = str(record.get("id", "")).strip()
    if not alert_id:
        return 0, 0

    issued_at = iso_from_timestamp(record.get("quando"))
    if not issued_at:
        return 0, 0

    event_type = str(record.get("pericolo", "")).strip().lower()
    if not event_type:
        return 0, 0

    # anticipo_min e' l'orizzonte indicato dal motore.
    lead_minutes = record.get("anticipo_min")

    try:
        lead_minutes = (
            int(lead_minutes)
            if lead_minutes is not None
            else None
        )
    except (TypeError, ValueError):
        lead_minutes = None

    predicted_time = None

    if lead_minutes is not None:
        try:
            issued_datetime = datetime.fromtimestamp(
                float(record["quando"]),
                tz=timezone.utc,
            )

            predicted_time = (
                issued_datetime
                + timedelta(minutes=lead_minutes)
            ).isoformat()

        except (TypeError, ValueError, KeyError, OSError):
            predicted_time = None

    message = record.get("titolo")

    places = record.get("luoghi") or []

    if not isinstance(places, list):
        places = []

    places = [clean_location(place) for place in places]
    places = [place for place in places if place]

    # Alcuni alert non hanno una localita' nominata.
    # Non ne inventiamo una.
    if not places:
        places = ["[località non disponibile]"]

    # Conserviamo l'intero record originale.
    # Qui rimangono anche:
    # - punteggio
    # - livello
    # - paese
    # - push
    # - esito
    # - note
    # - traccia
    # - doppione_di
    #
    # Non interpretiamo questi campi finche' il loro significato
    # non e' stato verificato nel codice NOWCAST.
    raw_data = json.dumps(
        record,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    inserted = 0
    skipped = 0

    for location in places:

        if already_imported(conn, alert_id, location):
            skipped += 1
            continue

        conn.execute(
            """
            INSERT INTO nowcast_alerts (
                alert_id,
                issued_at,
                predicted_time,
                location,
                province,
                region,
                latitude,
                longitude,
                event_type,
                probability,
                lead_minutes,
                message,
                raw_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alert_id,
                issued_at,
                predicted_time,
                location,
                None,
                None,
                None,
                None,
                event_type,

                # IMPORTANTE:
                # "punteggio" NON viene trattato come probabilita'.
                # Rimane conservato integralmente in raw_data.
                None,

                lead_minutes,
                message,
                raw_data,
            ),
        )

        inserted += 1

    return inserted, skipped


def import_jsonl(path):
    """Importa un file registro.jsonl."""

    path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(
            f"File non trovato: {path}"
        )

    init_db()
    conn = connect()

    records = 0
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

                try:
                    record = json.loads(line)

                except json.JSONDecodeError as exc:
                    print(
                        f"Riga {line_number} ignorata: "
                        f"JSON non valido ({exc})"
                    )
                    invalid += 1
                    continue

                if not isinstance(record, dict):
                    invalid += 1
                    continue

                records += 1

                try:
                    new_rows, old_rows = import_record(
                        conn,
                        record,
                    )

                    inserted += new_rows
                    skipped += old_rows

                except sqlite3.Error as exc:
                    print(
                        f"Riga {line_number} non importata: "
                        f"errore database ({exc})"
                    )
                    invalid += 1

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    print()
    print("Importazione NOWCAST completata")
    print("-------------------------------")
    print(f"Record letti:         {records}")
    print(f"Alert/localita' nuovi:{inserted:>6}")
    print(f"Gia' presenti:        {skipped:>6}")
    print(f"Record problematici:  {invalid:>6}")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Importa registro.jsonl di NOWCAST "
            "in NOWCAST Verify"
        )
    )

    parser.add_argument(
        "file",
        help="Percorso del registro.jsonl di NOWCAST",
    )

    args = parser.parse_args()

    import_jsonl(args.file)


if __name__ == "__main__":
    main()
