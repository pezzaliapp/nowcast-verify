"""
NOWCAST Verify - Ground Truth Collector
=======================================

Raccoglie segnalazioni di eventi meteorologici osservati da fonti
indipendenti, senza considerarli automaticamente "verificati".

Principi:
- nessun social network come prova primaria;
- nessuna equivalenza automatica wind = downburst;
- nessun falso positivo dedotto dall'assenza di notizie;
- conservazione della fonte originale;
- data, localita' e fenomeno sono obbligatori;
- l'orario puo' rimanere sconosciuto.

Autore: Alessandro Pezzali
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "ground_truth_candidates.jsonl"

USER_AGENT = (
    "NOWCAST-Verify/1.0 "
    "(independent meteorological verification research)"
)

TIMEOUT = 20


# ----------------------------------------------------------------------
# Modello dati
# ----------------------------------------------------------------------

@dataclass
class CandidateEvent:
    candidate_id: str
    event_date: str
    event_time: Optional[str]
    location: str
    province: Optional[str]
    region: Optional[str]
    event_type: str

    source_name: str
    source_type: str
    source_url: str

    title: Optional[str]
    published_at: Optional[str]
    evidence: str

    time_verified: bool
    verification_status: str
    collected_at: str


# ----------------------------------------------------------------------
# Normalizzazione
# ----------------------------------------------------------------------

def clean_text(value: Optional[str]) -> str:
    if not value:
        return ""

    return re.sub(r"\s+", " ", value).strip()


def clean_url(url: str) -> str:
    """
    Rimuove frammenti e normalizza l'URL senza alterarne il contenuto
    significativo.
    """

    url = clean_text(url)

    parts = urlsplit(url)

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            parts.query,
            "",
        )
    )


def normalize_event_type(value: str) -> str:
    """
    Mantiene distinte categorie che non sono scientificamente
    equivalenti.
    """

    value = clean_text(value).casefold()

    mapping = {
        "grandine": "hail",
        "hail": "hail",

        "vento": "wind",
        "wind": "wind",
        "forte vento": "wind",
        "vento forte": "wind",
        "raffiche": "wind",
        "raffiche violente": "wind",

        "downburst": "downburst",

        "tornado": "tornado",
        "tromba d'aria": "tornado",
    }

    return mapping.get(value, value)


def validate_date(value: str) -> str:
    datetime.strptime(value, "%Y-%m-%d")
    return value


def validate_time(value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return None

    datetime.strptime(value, "%H:%M")
    return value


def make_candidate_id(
    event_date: str,
    location: str,
    event_type: str,
    source_url: str,
) -> str:

    raw = "|".join(
        [
            event_date,
            clean_text(location).casefold(),
            normalize_event_type(event_type),
            clean_url(source_url).casefold(),
        ]
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------------
# Download pagina
# ----------------------------------------------------------------------

def download_page(url: str) -> str:
    """
    Scarica una pagina pubblica.

    Non tenta di aggirare autenticazioni, paywall, CAPTCHA,
    protezioni anti-bot o restrizioni del sito.
    """

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    return response.text


# ----------------------------------------------------------------------
# Estrazione metadati
# ----------------------------------------------------------------------

def extract_metadata(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    title = None
    published_at = None

    og_title = soup.find(
        "meta",
        attrs={"property": "og:title"},
    )

    if og_title and og_title.get("content"):
        title = clean_text(og_title["content"])

    if not title and soup.title:
        title = clean_text(soup.title.get_text(" ", strip=True))

    possible_date_fields = [
        ("property", "article:published_time"),
        ("name", "article:published_time"),
        ("name", "date"),
        ("name", "pubdate"),
        ("itemprop", "datePublished"),
    ]

    for attribute, value in possible_date_fields:

        node = soup.find(
            "meta",
            attrs={attribute: value},
        )

        if node and node.get("content"):
            published_at = clean_text(node["content"])
            break

    if not published_at:

        time_node = soup.find("time")

        if time_node:

            published_at = clean_text(
                time_node.get("datetime")
                or time_node.get_text(" ", strip=True)
            )

    return {
        "title": title,
        "published_at": published_at,
    }


# ----------------------------------------------------------------------
# JSONL
# ----------------------------------------------------------------------

def load_existing_ids(path: Path) -> set[str]:

    ids: set[str] = set()

    if not path.exists():
        return ids

    with path.open(
        "r",
        encoding="utf-8",
    ) as handle:

        for line in handle:

            line = line.strip()

            if not line:
                continue

            try:
                record = json.loads(line)

            except json.JSONDecodeError:
                continue

            candidate_id = record.get("candidate_id")

            if candidate_id:
                ids.add(str(candidate_id))

    return ids


def append_candidate(
    path: Path,
    candidate: CandidateEvent,
) -> bool:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    existing = load_existing_ids(path)

    if candidate.candidate_id in existing:
        return False

    with path.open(
        "a",
        encoding="utf-8",
    ) as handle:

        json.dump(
            asdict(candidate),
            handle,
            ensure_ascii=False,
            sort_keys=True,
        )

        handle.write("\n")

    return True


# ----------------------------------------------------------------------
# Inserimento candidato
# ----------------------------------------------------------------------

def add_candidate(args: argparse.Namespace) -> int:

    event_date = validate_date(args.date)

    event_time = validate_time(args.time)

    location = clean_text(args.location)

    event_type = normalize_event_type(args.type)

    source_url = clean_url(args.url)

    if not location:
        raise ValueError("Localita' mancante.")

    if not event_type:
        raise ValueError("Tipo di evento mancante.")

    if not source_url.startswith(
        ("http://", "https://")
    ):
        raise ValueError("URL sorgente non valido.")

    title = None
    published_at = None

    if not args.no_fetch:

        try:

            html = download_page(source_url)

            metadata = extract_metadata(html)

            title = metadata["title"]

            published_at = metadata["published_at"]

        except requests.RequestException as exc:

            print(
                "ATTENZIONE: pagina non acquisita:",
                exc,
            )

            print(
                "Il candidato viene comunque registrato "
                "con la fonte indicata."
            )

    candidate_id = make_candidate_id(
        event_date=event_date,
        location=location,
        event_type=event_type,
        source_url=source_url,
    )

    candidate = CandidateEvent(
        candidate_id=candidate_id,

        event_date=event_date,
        event_time=event_time,

        location=location,
        province=clean_text(args.province) or None,
        region=clean_text(args.region) or None,

        event_type=event_type,

        source_name=clean_text(args.source_name),
        source_type=clean_text(args.source_type),

        source_url=source_url,

        title=title,
        published_at=published_at,

        evidence=clean_text(args.evidence),

        time_verified=bool(args.time_verified),

        # Un candidato NON diventa automaticamente
        # ground truth verificato.
        verification_status="reported",

        collected_at=datetime.now().astimezone().isoformat(),
    )

    output = Path(args.output)

    inserted = append_candidate(
        output,
        candidate,
    )

    print()
    print("NOWCAST VERIFY - GROUND TRUTH")
    print("=============================")
    print()

    if inserted:
        print("Candidato registrato.")
    else:
        print("Candidato gia' presente.")

    print()
    print("ID        :", candidate.candidate_id)
    print("Data      :", candidate.event_date)
    print("Ora       :", candidate.event_time or "--:--")
    print("Localita' :", candidate.location)
    print("Fenomeno  :", candidate.event_type)
    print("Fonte     :", candidate.source_name)
    print("Tipo fonte:", candidate.source_type)
    print("Stato     :", candidate.verification_status)

    if candidate.title:
        print("Titolo    :", candidate.title)

    if candidate.published_at:
        print("Pubblicato:", candidate.published_at)

    print()
    print("File      :", output)
    print()

    return 0


# ----------------------------------------------------------------------
# Lista candidati
# ----------------------------------------------------------------------

def list_candidates(args: argparse.Namespace) -> int:

    path = Path(args.output)

    if not path.exists():

        print("Nessun candidato raccolto.")

        return 0

    count = 0

    with path.open(
        "r",
        encoding="utf-8",
    ) as handle:

        for line in handle:

            line = line.strip()

            if not line:
                continue

            try:
                record = json.loads(line)

            except json.JSONDecodeError:
                continue

            count += 1

            print(
                f"{record.get('event_date', '?')} "
                f"{record.get('event_time') or '--:--'} | "
                f"{record.get('event_type', '?')} | "
                f"{record.get('location', '?')} | "
                f"{record.get('source_name', '?')} | "
                f"{record.get('verification_status', '?')}"
            )

    print()
    print("Totale:", count)

    return 0


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(
        description=(
            "Raccolta conservativa di candidati ground truth "
            "per NOWCAST Verify."
        )
    )

    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="File JSONL di destinazione.",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    add_parser = subparsers.add_parser(
        "add",
        help="Registra un evento candidato.",
    )

    add_parser.add_argument(
        "--date",
        required=True,
        help="Data evento YYYY-MM-DD.",
    )

    add_parser.add_argument(
        "--time",
        help="Ora evento HH:MM.",
    )

    add_parser.add_argument(
        "--location",
        required=True,
        help="Localita' dell'evento.",
    )

    add_parser.add_argument(
        "--province",
        help="Provincia.",
    )

    add_parser.add_argument(
        "--region",
        help="Regione.",
    )

    add_parser.add_argument(
        "--type",
        required=True,
        help="hail, wind, downburst, tornado...",
    )

    add_parser.add_argument(
        "--source-name",
        required=True,
        help="Nome della fonte.",
    )

    add_parser.add_argument(
        "--source-type",
        required=True,
        choices=[
            "primary",
            "instrumental",
            "documented_media",
            "lead",
        ],
        help="Classe della fonte.",
    )

    add_parser.add_argument(
        "--url",
        required=True,
        help="URL originale della fonte.",
    )

    add_parser.add_argument(
        "--evidence",
        required=True,
        help="Descrizione sintetica dell'evidenza.",
    )

    add_parser.add_argument(
        "--time-verified",
        action="store_true",
        help=(
            "Usare solo quando la fonte documenta "
            "sufficientemente l'orario."
        ),
    )

    add_parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Non scaricare la pagina sorgente.",
    )

    add_parser.set_defaults(
        func=add_candidate,
    )

    list_parser = subparsers.add_parser(
        "list",
        help="Mostra i candidati raccolti.",
    )

    list_parser.set_defaults(
        func=list_candidates,
    )

    return parser


def main() -> int:

    parser = build_parser()

    args = parser.parse_args()

    try:
        return args.func(args)

    except (ValueError, OSError) as exc:

        print("ERRORE:", exc)

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
