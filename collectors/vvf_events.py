"""
NOWCAST Verify - VVF Event Extractor
====================================

Trasforma i documenti candidati dei Vigili del Fuoco in proposte
di eventi strutturati.

Il parser e' deliberatamente conservativo:
- non trasforma wind in downburst;
- non interpreta "tromba d'aria" come prova automatica di tornado;
- non inventa comuni da riferimenti provinciali o regionali;
- non considera automaticamente verificato un evento estratto;
- conserva sempre il testo sorgente come evidenza.

Autore: Alessandro Pezzali
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from collectors.vvf import extract_article


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_INPUT = PROJECT_ROOT / "data" / "vvf_candidates.jsonl"

DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "vvf_event_candidates.jsonl"


MONTHS = {
    "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
    "settembre": 9,
    "ottobre": 10,
    "novembre": 11,
    "dicembre": 12,
}


# ----------------------------------------------------------------------
# Utility
# ----------------------------------------------------------------------

def clean_text(value: Optional[str]) -> str:
    if not value:
        return ""

    return re.sub(r"\s+", " ", value).strip()


def make_event_candidate_id(
    source_url: str,
    event_date: Optional[str],
    event_time: Optional[str],
    location: Optional[str],
    event_type: Optional[str],
) -> str:

    raw = "|".join(
        [
            source_url or "",
            event_date or "",
            event_time or "",
            (location or "").casefold(),
            event_type or "",
        ]
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:16]


# ----------------------------------------------------------------------
# Data di pubblicazione
# ----------------------------------------------------------------------

def publication_year(
    published_at: Optional[str],
) -> Optional[int]:

    if not published_at:
        return None

    try:
        dt = datetime.fromisoformat(
            published_at.replace(
                "Z",
                "+00:00",
            )
        )

        return dt.year

    except ValueError:
        return None


def publication_date(
    published_at: Optional[str],
) -> Optional[str]:

    if not published_at:
        return None

    try:
        dt = datetime.fromisoformat(
            published_at.replace(
                "Z",
                "+00:00",
            )
        )

        return dt.date().isoformat()

    except ValueError:
        return None


# ----------------------------------------------------------------------
# Estrazione data evento
# ----------------------------------------------------------------------

def extract_event_date(
    text: str,
    published_at: Optional[str],
) -> tuple[Optional[str], str]:

    text_clean = clean_text(text)

    # Le pagine VVF includono nel corpo anche l'intestazione
    # "Data pubblicazione ...". Non e' la data dell'evento e
    # deve essere esclusa dalla ricerca.
    text_clean = re.sub(
        r"\bData\s+(?:di\s+)?pubblicazione\s+"
        r"[0-3]?\d\s+"
        r"(?:gennaio|febbraio|marzo|aprile|maggio|giugno|"
        r"luglio|agosto|settembre|ottobre|novembre|dicembre)"
        r"(?:\s+\d{4})?",
        " ",
        text_clean,
        count=1,
        flags=re.IGNORECASE,
    )

    text_clean = clean_text(text_clean)

    year = publication_year(
        published_at
    )

    # Esempio:
    # "Il 9 settembre, intorno alle ore 14:00..."
    pattern = re.compile(
        r"\b(?:il\s+)?"
        r"([0-3]?\d)\s+"
        r"(gennaio|febbraio|marzo|aprile|maggio|giugno|"
        r"luglio|agosto|settembre|ottobre|novembre|dicembre)"
        r"(?:\s+(\d{4}))?\b",
        flags=re.IGNORECASE,
    )

    match = pattern.search(text_clean)

    if match:

        day = int(match.group(1))

        month_name = (
            match.group(2).casefold()
        )

        month = MONTHS[
            month_name
        ]

        explicit_year = (
            int(match.group(3))
            if match.group(3)
            else year
        )

        if explicit_year:

            try:
                result = datetime(
                    explicit_year,
                    month,
                    day,
                ).date().isoformat()

                return (
                    result,
                    match.group(0),
                )

            except ValueError:
                pass

    # Espressioni relative molto semplici e
    # interpretabili rispetto alla pubblicazione.
    pub_date = publication_date(
        published_at
    )

    if pub_date:

        pub = datetime.strptime(
            pub_date,
            "%Y-%m-%d",
        ).date()

        lower = text_clean.casefold()

        if re.search(
            r"\bieri\b",
            lower,
        ):

            from datetime import timedelta

            event_date = (
                pub
                - timedelta(days=1)
            ).isoformat()

            return (
                event_date,
                "ieri",
            )

    return (
        None,
        "",
    )


# ----------------------------------------------------------------------
# Estrazione orario
# ----------------------------------------------------------------------

def extract_event_time(
    text: str,
) -> tuple[Optional[str], bool, str]:

    text_clean = clean_text(text)

    # Forme:
    # "intorno alle ore 14:00"
    # "poco prima delle ore 18:00"
    # "dalle ore 17:00"
    #
    # Sono orari documentati dalla fonte, ma possono
    # essere approssimativi.

    patterns = [
        (
            re.compile(
                r"\bintorno\s+alle\s+ore\s+"
                r"([01]?\d|2[0-3])[:.]([0-5]\d)\b",
                flags=re.IGNORECASE,
            ),
            True,
        ),
        (
            re.compile(
                r"\bpoco\s+prima\s+delle\s+ore\s+"
                r"([01]?\d|2[0-3])[:.]([0-5]\d)\b",
                flags=re.IGNORECASE,
            ),
            True,
        ),
        (
            re.compile(
                r"\bdalle\s+ore\s+"
                r"([01]?\d|2[0-3])[:.]([0-5]\d)\b",
                flags=re.IGNORECASE,
            ),
            True,
        ),
        (
            re.compile(
                r"\b(?:alle|ore)\s+"
                r"([01]?\d|2[0-3])[:.]([0-5]\d)\b",
                flags=re.IGNORECASE,
            ),
            True,
        ),
    ]

    for pattern, documented in patterns:

        match = pattern.search(
            text_clean
        )

        if not match:
            continue

        hour = int(
            match.group(1)
        )

        minute = int(
            match.group(2)
        )

        return (
            f"{hour:02d}:{minute:02d}",
            documented,
            match.group(0),
        )

    return (
        None,
        False,
        "",
    )


# ----------------------------------------------------------------------
# Fenomeno
# ----------------------------------------------------------------------

def extract_event_type(
    text: str,
) -> tuple[Optional[str], str]:

    lower = clean_text(
        text
    ).casefold()

    # Ordine importante:
    # fenomeni specifici prima di quelli generici.

    if re.search(
        r"\bgrandine\b|\bgrandinata\b",
        lower,
    ):
        return (
            "hail",
            "grandine",
        )

    if re.search(
        r"\bdownburst\b",
        lower,
    ):
        return (
            "downburst",
            "downburst",
        )

    # "tromba d'aria" viene conservata come fenomeno
    # ambiguo, NON promossa automaticamente a tornado.
    if re.search(
        r"\btromb[ae]\s+d['’]aria\b",
        lower,
    ):
        return (
            "wind",
            "tromba d'aria",
        )

    if re.search(
        r"\bforti?\s+raffiche\s+di\s+vento\b",
        lower,
    ):
        return (
            "wind",
            "forti raffiche di vento",
        )

    if re.search(
        r"\braffiche\s+di\s+vento\b",
        lower,
    ):
        return (
            "wind",
            "raffiche di vento",
        )

    if re.search(
        r"\bvento\b",
        lower,
    ):
        return (
            "wind",
            "vento",
        )

    # Pioggia/allagamento vengono riconosciuti,
    # ma non sono attualmente categorie NOWCAST.
    if re.search(
        r"\bpiogge?\s+intens[ae]\b|"
        r"\ballagament[oi]\b|"
        r"\bpioggia\b",
        lower,
    ):
        return (
            "rain",
            "pioggia/allagamenti",
        )

    return (
        None,
        "",
    )


# ----------------------------------------------------------------------
# Localita'
# ----------------------------------------------------------------------

def extract_location(
    text: str,
) -> tuple[Optional[str], str]:

    text_clean = clean_text(
        text
    )

    # Primo caso volutamente specifico e affidabile:
    # "territorio di Viareggio"
    match = re.search(
        r"\bterritorio\s+di\s+"
        r"([A-ZÀ-Ý][A-Za-zÀ-ÿ'’\- ]{1,60}?)"
        r"(?=,|\.|\s+determinando\b|\s+con\b)",
        text_clean,
    )

    if match:

        location = clean_text(
            match.group(1)
        )

        if location:
            return (
                location,
                match.group(0),
            )

    # "nel comune di X"
    match = re.search(
        r"\b(?:nel|nel\s+territorio\s+del)\s+"
        r"comune\s+di\s+"
        r"([A-ZÀ-Ý][A-Za-zÀ-ÿ'’\- ]{1,60}?)"
        r"(?=,|\.|\s+le\b|\s+la\b|\s+il\b|\s+sono\b)",
        text_clean,
    )

    if match:

        location = clean_text(
            match.group(1)
        )

        if location:
            return (
                location,
                match.group(0),
            )

    # Non estraiamo automaticamente:
    # - province
    # - regioni
    # - liste di comuni
    # - sedi delle squadre VVF
    #
    # Sono troppo facili da confondere con il luogo
    # effettivo del fenomeno.

    return (
        None,
        "",
    )


# ----------------------------------------------------------------------
# Evidenza
# ----------------------------------------------------------------------

def evidence_excerpt(
    text: str,
    max_length: int = 700,
) -> str:

    value = clean_text(
        text
    )

    if len(value) <= max_length:
        return value

    shortened = value[
        :max_length
    ]

    last_period = shortened.rfind(
        "."
    )

    if last_period > 200:
        shortened = shortened[
            :last_period + 1
        ]

    return shortened


# ----------------------------------------------------------------------
# JSONL
# ----------------------------------------------------------------------

def load_jsonl(
    path: Path,
) -> list[dict]:

    records: list[dict] = []

    if not path.exists():
        return records

    with path.open(
        "r",
        encoding="utf-8",
    ) as handle:

        for line in handle:

            line = line.strip()

            if not line:
                continue

            try:
                records.append(
                    json.loads(line)
                )

            except json.JSONDecodeError:
                continue

    return records


def load_existing_ids(
    path: Path,
) -> set[str]:

    result: set[str] = set()

    for record in load_jsonl(
        path
    ):

        value = record.get(
            "candidate_id"
        )

        if value:
            result.add(
                str(value)
            )

    return result


def append_record(
    path: Path,
    record: dict,
    existing_ids: set[str],
) -> bool:

    cid = record[
        "candidate_id"
    ]

    if cid in existing_ids:
        return False

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "a",
        encoding="utf-8",
    ) as handle:

        json.dump(
            record,
            handle,
            ensure_ascii=False,
            sort_keys=True,
        )

        handle.write(
            "\n"
        )

    existing_ids.add(
        cid
    )

    return True


# ----------------------------------------------------------------------
# Estrazione
# ----------------------------------------------------------------------

def process_document(
    document: dict,
) -> dict:

    source_url = clean_text(
        document.get(
            "source_url"
        )
    )

    article = extract_article(
        source_url
    )

    body = article[
        "body"
    ]

    published_at = (
        article.get(
            "published_at"
        )
        or document.get(
            "published_at"
        )
    )

    event_date, date_evidence = (
        extract_event_date(
            body,
            published_at,
        )
    )

    event_time, time_documented, time_evidence = (
        extract_event_time(
            body
        )
    )

    event_type, type_evidence = (
        extract_event_type(
            body
        )
    )

    location, location_evidence = (
        extract_location(
            body
        )
    )

    cid = make_event_candidate_id(
        source_url=source_url,
        event_date=event_date,
        event_time=event_time,
        location=location,
        event_type=event_type,
    )

    complete_for_review = all(
        (
            event_date,
            location,
            event_type,
        )
    )

    return {
        "candidate_id": cid,

        "source_document_id": (
            document.get(
                "candidate_id"
            )
        ),

        "source_name": (
            document.get(
                "source_name"
            )
            or "Corpo Nazionale dei Vigili del Fuoco"
        ),

        "source_type": "primary",

        "source_url": source_url,

        "title": (
            article.get(
                "title"
            )
            or document.get(
                "title"
            )
        ),

        "published_at": published_at,

        "event_date": event_date,
        "event_time": event_time,

        "location": location,

        "province": None,
        "region": None,

        "event_type": event_type,

        # True significa soltanto che la fonte
        # documenta esplicitamente un orario.
        # Non significa precisione al minuto.
        "time_verified": (
            bool(
                event_time
                and time_documented
            )
        ),

        "date_evidence": (
            date_evidence
            or None
        ),

        "time_evidence": (
            time_evidence
            or None
        ),

        "location_evidence": (
            location_evidence
            or None
        ),

        "type_evidence": (
            type_evidence
            or None
        ),

        "evidence": evidence_excerpt(
            body
        ),

        "complete_for_review": (
            complete_for_review
        ),

        # Mai "verified" automaticamente.
        "verification_status": (
            "reported"
            if complete_for_review
            else "incomplete"
        ),

        "verification_notes": (
            "Estratto automaticamente da fonte VVF. "
            "Richiede revisione prima dell'uso come ground truth."
        ),

        "collected_at": (
            datetime.now()
            .astimezone()
            .isoformat()
        ),
    }


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def run(
    args: argparse.Namespace,
) -> int:

    input_path = Path(
        args.input
    )

    output_path = Path(
        args.output
    )

    documents = load_jsonl(
        input_path
    )

    if not documents:

        print(
            "Nessun documento VVF "
            "da elaborare."
        )

        return 0

    existing_ids = (
        load_existing_ids(
            output_path
        )
    )

    print()
    print(
        "NOWCAST VERIFY - VVF EVENTS"
    )
    print(
        "==========================="
    )
    print()

    processed = 0
    complete = 0
    incomplete = 0
    inserted = 0
    duplicates = 0
    errors = 0

    for document in documents:

        try:

            record = (
                process_document(
                    document
                )
            )

        except Exception as exc:

            errors += 1

            print(
                "ERRORE:",
                document.get(
                    "source_url"
                ),
                "-",
                exc,
            )

            continue

        processed += 1

        if record[
            "complete_for_review"
        ]:
            complete += 1
        else:
            incomplete += 1

        if append_record(
            output_path,
            record,
            existing_ids,
        ):
            inserted += 1
        else:
            duplicates += 1

        print(
            f"{record['event_date'] or '????-??-??'} "
            f"{record['event_time'] or '--:--'} | "
            f"{record['event_type'] or '?'} | "
            f"{record['location'] or '[localita non determinata]'}"
        )

        print(
            "  stato:",
            record[
                "verification_status"
            ],
        )

        if record[
            "date_evidence"
        ]:
            print(
                "  data:",
                record[
                    "date_evidence"
                ],
            )

        if record[
            "time_evidence"
        ]:
            print(
                "  ora :",
                record[
                    "time_evidence"
                ],
            )

        if record[
            "location_evidence"
        ]:
            print(
                "  luogo:",
                record[
                    "location_evidence"
                ],
            )

        if record[
            "type_evidence"
        ]:
            print(
                "  tipo:",
                record[
                    "type_evidence"
                ],
            )

        print()

    print(
        "RISULTATO"
    )
    print(
        "---------"
    )
    print(
        "Documenti elaborati :",
        processed,
    )
    print(
        "Completi per revisione:",
        complete,
    )
    print(
        "Incompleti           :",
        incomplete,
    )
    print(
        "Nuovi                :",
        inserted,
    )
    print(
        "Gia' presenti        :",
        duplicates,
    )
    print(
        "Errori               :",
        errors,
    )
    print()
    print(
        "File:",
        output_path,
    )
    print()

    return 0


def build_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(
        description=(
            "Estrazione conservativa di eventi "
            "dai documenti VVF raccolti."
        )
    )

    parser.add_argument(
        "--input",
        default=str(
            DEFAULT_INPUT
        ),
        help=(
            "JSONL dei documenti "
            "VVF candidati."
        ),
    )

    parser.add_argument(
        "--output",
        default=str(
            DEFAULT_OUTPUT
        ),
        help=(
            "JSONL degli eventi "
            "VVF candidati."
        ),
    )

    return parser


def main() -> int:

    parser = build_parser()

    args = parser.parse_args()

    try:
        return run(
            args
        )

    except (
        ValueError,
        OSError,
    ) as exc:

        print(
            "ERRORE:",
            exc,
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
