#!/usr/bin/env python3
"""
NOWCAST Verify - Markdown report generator.

Generates a conservative report from the existing comparison engine.
It does not redefine verification rules: event evaluation and alert
coverage remain delegated to backend.compare.
"""

from collections import Counter
from datetime import date
from pathlib import Path
import argparse

from backend.database import connect, init_db
from backend.compare import (
    classify_alert_coverage,
    evaluate_event,
    get_nowcast_event_types,
    get_verified_events,
    local_time_text,
)


STATUS_LABELS = {
    "confirmed": "confermato temporalmente",
    "compatible": "compatibile",
    "missed": "mancato",
    "unverifiable": "non verificabile",
}

EVENT_LABELS = {
    "hail": "Grandine",
    "wind": "Forti raffiche di vento",
    "downburst": "Downburst",
}


def comparison_data(report_date=None):
    init_db()
    conn = connect()

    try:
        events = get_verified_events(conn)

        if report_date:
            events = [
                event for event in events
                if event["event_date"] == report_date
            ]

        nowcast_types = get_nowcast_event_types(conn)

        results = [
            evaluate_event(conn, event, nowcast_types)
            for event in events
        ]

        # Coverage is meaningful over the whole imported alert registry.
        # For a date-scoped report we deliberately do not present the
        # global coverage figures as if they referred only to that day.
        coverage = None
        if report_date is None:
            coverage = classify_alert_coverage(conn, results)

    finally:
        conn.close()

    return {
        "results": results,
        "nowcast_types": nowcast_types,
        "coverage": coverage,
    }


def fmt_local(value):
    if not value:
        return None
    return local_time_text(value)


def render_markdown(data, report_date=None):
    results = data["results"]
    nowcast_types = data["nowcast_types"]
    coverage = data["coverage"]

    title = "# NOWCAST Verify"
    if report_date:
        title += f" — {report_date}"

    lines = [
        title,
        "",
        "> Report di verifica indipendente. Un riscontro assente non viene "
        "considerato un falso positivo e fenomeni diversi non vengono "
        "equiparati senza evidenza.",
        "",
        f"**Categorie NOWCAST confrontabili:** "
        f"{', '.join(EVENT_LABELS.get(t, t) for t in sorted(nowcast_types)) or 'nessuna'}",
        "",
        "## Eventi osservati",
        "",
    ]

    if not results:
        lines += [
            "Nessun evento indipendente verificato disponibile "
            "per il periodo selezionato.",
            "",
        ]

    for result in results:
        event_time = result["event_time"] or "orario non verificato"
        status = STATUS_LABELS.get(result["status"], result["status"])
        event_label = EVENT_LABELS.get(
            result["event_type"],
            result["event_type"],
        )

        lines += [
            f"### {result['location']} — {event_label}",
            "",
            f"- **Evento osservato:** {result['event_date']} — {event_time}",
            f"- **Valutazione:** {status}",
        ]

        if result["matching_alert_count"]:
            lines.append(
                f"- **Alert NOWCAST compatibili:** "
                f"{result['matching_alert_count']}"
            )

        if result["matching_alert_ids"]:
            lines.append(
                f"- **ID alert:** {', '.join(result['matching_alert_ids'])}"
            )

        if result["latest_alert_id"]:
            lines.append(
                f"- **Ultimo alert considerato:** "
                f"{result['latest_alert_id']}"
            )

        issued = fmt_local(result["latest_issued_at"])
        if issued:
            lines.append(f"- **Emissione:** {issued} ora locale")

        predicted = fmt_local(result["latest_predicted_time"])
        if predicted:
            lines.append(f"- **ETA prevista:** {predicted} ora locale")

        if result["time_difference_minutes"] is not None:
            lines.append(
                f"- **Scarto osservato rispetto all'ETA:** "
                f"{result['time_difference_minutes']} minuti"
            )

        reason = (
            result["reason"]
            .replace("localita'", "località")
            .replace("L'orario", "L’orario")
            .replace("non e'", "non è")
            .replace("e' ", "è ")
        )

        lines += [
            f"- **Motivo:** {reason}",
            "",
        ]

    counts = Counter(result["status"] for result in results)

    lines += [
        "## Riepilogo",
        "",
        f"- Eventi verificati: **{len(results)}**",
        f"- Confermati temporalmente: "
        f"**{counts.get('confirmed', 0)}**",
        f"- Compatibili: **{counts.get('compatible', 0)}**",
        f"- Mancati: **{counts.get('missed', 0)}**",
        f"- Non verificabili: **{counts.get('unverifiable', 0)}**",
        "",
    ]

    if coverage is not None:
        lines += [
            "## Copertura alert importati",
            "",
            f"- Alert NOWCAST unici: **{coverage['total_alerts']}**",
            f"- Alert associati a eventi: "
            f"**{coverage['associated_alerts']}**",
            f"- Senza ground truth: "
            f"**{coverage['unverifiable_alerts']}**",
            f"- Falsi positivi dimostrati: "
            f"**{coverage['false_positives']}**",
            "",
        ]

    lines += [
        "## Nota metodologica",
        "",
        "L'assenza di un riscontro indipendente non dimostra che un alert "
        "fosse falso. Le forti raffiche non vengono considerate "
        "automaticamente prova di un downburst. Quando l'orario osservato "
        "non è sufficientemente verificato, non viene calcolata una "
        "conferma temporale. Quando esiste un orario verificato, l'eventuale "
        "scarto viene misurato rispetto all'ETA prevista senza applicare "
        "a posteriori una finestra di tolleranza scelta per adattarsi ai dati.",
        "",
    ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Genera un report Markdown di NOWCAST Verify."
    )
    parser.add_argument(
        "--date",
        dest="report_date",
        help="Data da includere nel formato YYYY-MM-DD.",
    )
    parser.add_argument(
        "--output",
        help="File Markdown di destinazione. Se omesso stampa a video.",
    )
    args = parser.parse_args()

    if args.report_date:
        try:
            date.fromisoformat(args.report_date)
        except ValueError:
            parser.error("--date deve essere nel formato YYYY-MM-DD")

    data = comparison_data(args.report_date)
    markdown = render_markdown(data, args.report_date)

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
        print(f"Report scritto in {output}")
    else:
        print(markdown)


if __name__ == "__main__":
    main()
