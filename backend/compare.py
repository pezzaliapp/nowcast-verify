"""NOWCAST Verify - motore di confronto prudente.

Confronta gli alert NOWCAST con eventi osservati e verificati
da fonti indipendenti.

Regole:
- confirmed:
  fenomeno, localita' e tempo sono confrontabili.
- compatible:
  fenomeno e localita' coincidono, ma manca un tempo osservato
  sufficientemente verificato per misurare la prestazione.
- missed:
  evento verificato appartenente a una categoria realmente
  prevista da NOWCAST, ma nessun alert compatibile e' presente.
- unverifiable:
  le prove disponibili non consentono un confronto corretto.
- false_positive:
  NON viene assegnato automaticamente. Richiede evidenza
  indipendente sufficiente dell'assenza del fenomeno.

Uso:
    python -m backend.compare
"""

import re
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from backend.database import connect, init_db


ITALY_TZ = ZoneInfo("Europe/Rome")

PLACEHOLDER_LOCATION = "[località non disponibile]"


# ---------------------------------------------------------------------
# Normalizzazione
# ---------------------------------------------------------------------

def normalize_text(value):
    if not value:
        return ""

    value = str(value).casefold().strip()

    # Forte dei Marmi (LU) -> forte dei marmi
    value = re.sub(
        r"\s*\([a-z]{2}\)\s*$",
        "",
        value,
    )

    value = re.sub(r"\s+", " ", value)

    return value.strip()


def normalize_event_type(value):
    """Normalizza solo equivalenze linguistiche sicure.

    IMPORTANTE:
    wind NON viene trasformato in downburst.
    """
    value = normalize_text(value)

    mapping = {
        "grandine": "hail",
        "hail": "hail",

        "vento": "wind",
        "wind": "wind",
        "forte vento": "wind",
        "vento forte": "wind",
        "raffiche": "wind",
        "forti raffiche": "wind",

        "downburst": "downburst",
    }

    return mapping.get(value, value)


# ---------------------------------------------------------------------
# Tempo
# ---------------------------------------------------------------------

def parse_iso_datetime(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt

    except ValueError:
        return None


def observed_datetime(event):
    """Restituisce il datetime osservato solo se il tempo e' verificato."""

    if not event["time_verified"]:
        return None

    if not event["event_date"] or not event["event_time"]:
        return None

    try:
        dt = datetime.strptime(
            f"{event['event_date']} {event['event_time']}",
            "%Y-%m-%d %H:%M",
        )

        return dt.replace(tzinfo=ITALY_TZ)

    except ValueError:
        return None


# ---------------------------------------------------------------------
# Localita'
# ---------------------------------------------------------------------

def location_matches(alert_location, event_location):
    a = normalize_text(alert_location)
    e = normalize_text(event_location)

    if not a or not e:
        return False

    if a == normalize_text(PLACEHOLDER_LOCATION):
        return False

    return a == e


# ---------------------------------------------------------------------
# Dati
# ---------------------------------------------------------------------

def get_verified_events(conn):
    return conn.execute(
        """
        SELECT *
        FROM events
        WHERE verification_status = 'verified'
        ORDER BY event_date, event_time, id
        """
    ).fetchall()


def get_nowcast_event_types(conn):
    rows = conn.execute(
        """
        SELECT DISTINCT event_type
        FROM nowcast_alerts
        """
    ).fetchall()

    return {
        normalize_event_type(row["event_type"])
        for row in rows
        if row["event_type"]
    }


def get_same_day_alerts(conn, event):
    """Alert della stessa categoria nella stessa data locale."""

    wanted_type = normalize_event_type(
        event["event_type"]
    )

    rows = conn.execute(
        """
        SELECT *
        FROM nowcast_alerts
        ORDER BY issued_at, id
        """
    ).fetchall()

    result = []

    for alert in rows:
        alert_type = normalize_event_type(
            alert["event_type"]
        )

        if alert_type != wanted_type:
            continue

        issued = parse_iso_datetime(
            alert["issued_at"]
        )

        if issued is None:
            continue

        local_date = (
            issued
            .astimezone(ITALY_TZ)
            .date()
            .isoformat()
        )

        if local_date != event["event_date"]:
            continue

        result.append(alert)

    return result


def unique_logical_alerts(rows):
    """Una sola voce per alert_id."""

    unique = {}

    for row in rows:
        unique.setdefault(
            row["alert_id"],
            row,
        )

    return list(unique.values())


# ---------------------------------------------------------------------
# Valutazione evento
# ---------------------------------------------------------------------

def evaluate_event(conn, event, nowcast_types):
    event_type = normalize_event_type(
        event["event_type"]
    )

    result = {
        "event_id": event["id"],
        "event_date": event["event_date"],
        "event_time": event["event_time"],
        "event_type": event["event_type"],
        "normalized_type": event_type,
        "location": event["location"],
        "time_verified": bool(
            event["time_verified"]
        ),
        "status": None,
        "matching_alert_ids": [],
        "matching_alert_count": 0,
        "latest_alert_id": None,
        "latest_issued_at": None,
        "latest_predicted_time": None,
        "time_difference_minutes": None,
        "reason": None,
    }

    # -------------------------------------------------------------
    # Il fenomeno osservato non e' una categoria direttamente
    # confrontabile con quelle prodotte da NOWCAST.
    #
    # Esempio:
    # fonte = "forti raffiche"
    # NOWCAST = "downburst"
    #
    # Le raffiche non dimostrano da sole che fosse un downburst.
    # -------------------------------------------------------------

    if event_type not in nowcast_types:
        result["status"] = "unverifiable"

        result["reason"] = (
            "Il fenomeno osservato e' verificato, ma non "
            "corrisponde direttamente a una categoria NOWCAST. "
            "Non viene applicata un'equivalenza non dimostrata."
        )

        return result

    same_day = get_same_day_alerts(
        conn,
        event,
    )

    same_location = [
        alert
        for alert in same_day
        if location_matches(
            alert["location"],
            event["location"],
        )
    ]

    logical_alerts = unique_logical_alerts(
        same_location
    )

    result["matching_alert_ids"] = [
        alert["alert_id"]
        for alert in logical_alerts
    ]

    result["matching_alert_count"] = len(
        logical_alerts
    )

    # -------------------------------------------------------------
    # Nessun alert dello stesso fenomeno e localita'.
    #
    # Qui "missed" e' ammesso perche' abbiamo gia' verificato
    # che il fenomeno appartenga realmente alle categorie NOWCAST.
    #
    # Questa rimane una valutazione NOMINALE della localita':
    # non stiamo ancora applicando un raggio geografico.
    # -------------------------------------------------------------

    if not logical_alerts:
        result["status"] = "missed"

        result["reason"] = (
            "Evento indipendente verificato appartenente a una "
            "categoria NOWCAST, ma nessun alert dello stesso "
            "fenomeno e sulla stessa localita' e' presente nella "
            "stessa data locale. Valutazione geografica per ora "
            "limitata alla corrispondenza nominale."
        )

        return result

    # -------------------------------------------------------------
    # Abbiamo fenomeno + luogo, ma NON un orario osservato
    # sufficientemente verificato.
    #
    # Non scegliamo arbitrariamente uno degli alert come
    # 'quello giusto' e non calcoliamo alcun lead time.
    # -------------------------------------------------------------

    event_dt = observed_datetime(event)

    if event_dt is None:
        latest = max(
            logical_alerts,
            key=lambda row: parse_iso_datetime(
                row["issued_at"]
            ),
        )

        result["status"] = "compatible"

        result["latest_alert_id"] = (
            latest["alert_id"]
        )

        result["latest_issued_at"] = (
            latest["issued_at"]
        )

        result["latest_predicted_time"] = (
            latest["predicted_time"]
        )

        result["reason"] = (
            f"Evento verificato e "
            f"{len(logical_alerts)} alert NOWCAST compatibili "
            f"per fenomeno, localita' e giornata. "
            f"L'orario osservato non e' sufficientemente "
            f"verificato: nessun alert viene dichiarato "
            f"temporalmente confermato e nessun lead time "
            f"viene calcolato."
        )

        return result

    # -------------------------------------------------------------
    # Evento con orario osservato verificato.
    # Consideriamo solo alert emessi prima dell'evento.
    # -------------------------------------------------------------

    event_utc = event_dt.astimezone(
        timezone.utc
    )

    previous = []

    for alert in logical_alerts:
        issued = parse_iso_datetime(
            alert["issued_at"]
        )

        if issued is None:
            continue

        if issued <= event_utc:
            previous.append(alert)

    if not previous:
        result["status"] = "missed"

        result["reason"] = (
            "Sono presenti alert dello stesso fenomeno e "
            "localita' nella giornata, ma nessuno risulta "
            "emesso prima dell'evento osservato."
        )

        return result

    # -------------------------------------------------------------
    # Alert piu' recente emesso prima dell'evento.
    #
    # NON imponiamo ancora una finestra temporale arbitraria.
    # La differenza viene MISURATA, non giudicata.
    # -------------------------------------------------------------

    latest = max(
        previous,
        key=lambda row: parse_iso_datetime(
            row["issued_at"]
        ),
    )

    predicted = parse_iso_datetime(
        latest["predicted_time"]
    )

    result["latest_alert_id"] = latest["alert_id"]
    result["latest_issued_at"] = latest["issued_at"]
    result["latest_predicted_time"] = latest["predicted_time"]

    # Senza una finestra temporale predefinita non dichiariamo
    # l'alert temporalmente confermato. Misuriamo soltanto lo
    # scarto fra ETA prevista e ora osservata.
    result["status"] = "compatible"

    if predicted is not None:
        delta = event_utc - predicted
        result["time_difference_minutes"] = round(
            delta.total_seconds() / 60, 1
        )
        result["reason"] = (
            "Evento verificato con orario confrontabile e alert "
            "precedente dello stesso fenomeno e localita'. "
            "Lo scarto temporale e' calcolato rispetto all'ETA "
            "prevista dall'alert (predicted_time), non rispetto "
            "all'ora di emissione. Non essendo ancora definita "
            "una finestra temporale di validita' a priori, "
            "l'evento resta compatibile e non viene dichiarato "
            "temporalmente confermato."
        )
    else:
        result["reason"] = (
            "Evento verificato con orario confrontabile e alert "
            "precedente dello stesso fenomeno e localita', ma "
            "l'ETA prevista non e' disponibile. Nessuna conferma "
            "temporale viene assegnata."
        )

    return result


# ---------------------------------------------------------------------
# Alert non associati
# ---------------------------------------------------------------------

def get_unique_alert_ids(conn):
    rows = conn.execute(
        """
        SELECT DISTINCT alert_id
        FROM nowcast_alerts
        WHERE alert_id IS NOT NULL
        """
    ).fetchall()

    return {
        row["alert_id"]
        for row in rows
    }


def classify_alert_coverage(
    conn,
    event_results,
):
    """Conta gli alert per cui non abbiamo ground truth sufficiente.

    IMPORTANTE:
    non sono falsi positivi.
    """

    all_alert_ids = get_unique_alert_ids(
        conn
    )

    associated = set()

    for result in event_results:
        associated.update(
            result["matching_alert_ids"]
        )

    unverifiable_alerts = (
        all_alert_ids - associated
    )

    return {
        "total_alerts": len(all_alert_ids),
        "associated_alerts": len(
            associated
        ),
        "unverifiable_alerts": len(
            unverifiable_alerts
        ),
        "false_positives": 0,
    }


# ---------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------

def local_time_text(value):
    dt = parse_iso_datetime(value)

    if dt is None:
        return None

    return dt.astimezone(
        ITALY_TZ
    ).strftime("%Y-%m-%d %H:%M")


def run_comparison():
    init_db()
    conn = connect()

    try:
        events = get_verified_events(conn)

        nowcast_types = get_nowcast_event_types(
            conn
        )

        results = [
            evaluate_event(
                conn,
                event,
                nowcast_types,
            )
            for event in events
        ]

        coverage = classify_alert_coverage(
            conn,
            results,
        )

    finally:
        conn.close()

    print()
    print("NOWCAST VERIFY")
    print("==============" )
    print()

    print(
        "Categorie NOWCAST:",
        ", ".join(sorted(nowcast_types)),
    )
    print()

    print("EVENTI OSSERVATI")
    print("----------------")

    if not results:
        print(
            "Nessun evento indipendente "
            "verificato disponibile."
        )

    for result in results:
        time_text = (
            result["event_time"]
            if result["event_time"]
            else "--:--"
        )

        print(
            f"{result['event_date']} "
            f"{time_text} | "
            f"{result['event_type']} | "
            f"{result['location']}"
        )

        print(
            f"  risultato : "
            f"{result['status']}"
        )

        if result["matching_alert_count"]:
            print(
                f"  alert compatibili: "
                f"{result['matching_alert_count']}"
            )

        if result["matching_alert_ids"]:
            print(
                "  ID alert  : "
                + ", ".join(
                    result["matching_alert_ids"]
                )
            )

        if result["latest_alert_id"]:
            print(
                f"  ultimo ID : "
                f"{result['latest_alert_id']}"
            )

        if result["latest_issued_at"]:
            local = local_time_text(
                result["latest_issued_at"]
            )

            print(
                f"  emissione : "
                f"{local} ora locale"
            )

        if result["latest_predicted_time"]:
            local = local_time_text(
                result[
                    "latest_predicted_time"
                ]
            )

            print(
                f"  previsione: "
                f"{local} ora locale"
            )

        if (
            result[
                "time_difference_minutes"
            ]
            is not None
        ):
            print(
                f"  differenza: "
                f"{result['time_difference_minutes']} "
                f"minuti"
            )

        print(
            f"  motivo    : "
            f"{result['reason']}"
        )

        print()

    counts = Counter(
        result["status"]
        for result in results
    )

    print("RIEPILOGO EVENTI")
    print("----------------")
    print(
        "Eventi verificati      :",
        len(results),
    )
    print(
        "Confermati temporalmente:",
        counts.get("confirmed", 0),
    )
    print(
        "Compatibili             :",
        counts.get("compatible", 0),
    )
    print(
        "Mancati                 :",
        counts.get("missed", 0),
    )
    print(
        "Non verificabili        :",
        counts.get("unverifiable", 0),
    )

    print()
    print("COPERTURA ALERT")
    print("--------------")
    print(
        "Alert NOWCAST unici     :",
        coverage["total_alerts"],
    )
    print(
        "Alert associati a eventi:",
        coverage["associated_alerts"],
    )
    print(
        "Senza ground truth      :",
        coverage[
            "unverifiable_alerts"
        ],
    )
    print(
        "Falsi positivi dimostrati:",
        coverage[
            "false_positives"
        ],
    )

    print()
    print(
        "NOTA METODOLOGICA: l'assenza di un riscontro "
        "indipendente non dimostra che un alert fosse falso. "
        "Analogamente, forti raffiche non vengono considerate "
        "automaticamente prova di un downburst."
    )


def main():
    run_comparison()


if __name__ == "__main__":
    main()
