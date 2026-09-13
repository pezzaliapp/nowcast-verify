"""
NOWCAST Verify - Vigili del Fuoco Collector
===========================================

Scorre l'archivio pubblico delle notizie del Corpo Nazionale
dei Vigili del Fuoco e individua articoli potenzialmente
relativi a fenomeni meteorologici severi.

Gli articoli trovati sono soltanto CANDIDATI.
Non diventano automaticamente ground truth verificato.

Autore: Alessandro Pezzali
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "vvf_candidates.jsonl"

BASE_URL = "https://www.vigilfuoco.it"
NEWS_URL = f"{BASE_URL}/media/notizie"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 "
    "Chrome/140.0 Safari/537.36 "
    "NOWCAST-Verify/1.0"
)

TIMEOUT = 25

WEATHER_TERMS = (
    "grandine",
    "grandinata",
    "maltempo",
    "temporale",
    "temporali",
    "nubifragio",
    "nubifragi",
    "vento",
    "raffica",
    "raffiche",
    "tromba d'aria",
    "trombe d'aria",
    "tornado",
    "downburst",
    "allagamento",
    "allagamenti",
    "bomba d'acqua",
)


# ----------------------------------------------------------------------
# Utility
# ----------------------------------------------------------------------

def clean_text(value: Optional[str]) -> str:
    if not value:
        return ""

    return re.sub(r"\s+", " ", value).strip()


def canonical_url(url: str) -> str:
    parts = urlsplit(url)

    # Il portale VVF espone nell'archivio anche link http,
    # mentre le pagine sono disponibili correttamente in HTTPS.
    scheme = "https" if parts.netloc in (
        "www.vigilfuoco.it",
        "vigilfuoco.it",
    ) else parts.scheme

    return (
        f"{scheme}://"
        f"{parts.netloc}"
        f"{parts.path}"
    )


def make_candidate_id(url: str) -> str:
    return hashlib.sha256(
        canonical_url(url).encode("utf-8")
    ).hexdigest()[:16]


def contains_weather_term(text: str) -> bool:
    value = clean_text(text).casefold()

    return any(
        term.casefold() in value
        for term in WEATHER_TERMS
    )


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------

def get_page(url: str) -> str:
    response = requests.get(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.5",
        },
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    return response.text


# ----------------------------------------------------------------------
# Date
# ----------------------------------------------------------------------

def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    value = clean_text(value)

    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

    except ValueError:
        pass

    formats = (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%d %B %Y",
    )

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    return None


def article_in_period(
    published_at: Optional[str],
    start_date: datetime,
    end_date: datetime,
) -> bool:

    parsed = parse_datetime(published_at)

    if parsed is None:
        # Se manca il metadato non scartiamo automaticamente
        # la fonte: la verifica avverra' successivamente.
        return True

    return (
        start_date.date()
        <= parsed.date()
        <= end_date.date()
    )


# ----------------------------------------------------------------------
# Archivio notizie VVF
# ----------------------------------------------------------------------

def extract_news_links(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")

    links: set[str] = set()

    for node in soup.find_all("a", href=True):

        href = clean_text(node.get("href"))

        if not href:
            continue

        absolute = urljoin(BASE_URL, href)

        parts = urlsplit(absolute)

        if parts.netloc not in (
            "www.vigilfuoco.it",
            "vigilfuoco.it",
        ):
            continue

        path = parts.path.rstrip("/")

        # Le notizie dei comandi territoriali hanno normalmente
        # URL che contengono /notizie/.
        if "/notizie/" not in path:
            continue

        links.add(canonical_url(absolute))

    return sorted(links)


def archive_page_url(page: int) -> str:
    """
    Drupal usa normalmente ?page=N nelle viste paginate.

    Pagina 0 = prima pagina.
    """

    if page == 0:
        return NEWS_URL

    return f"{NEWS_URL}?page={page}"


# ----------------------------------------------------------------------
# Estrazione articolo
# ----------------------------------------------------------------------

def extract_article(url: str) -> dict:
    html = get_page(url)

    soup = BeautifulSoup(html, "html.parser")

    title = None
    published_at = None

    node = soup.find(
        "meta",
        attrs={"property": "og:title"},
    )

    if node and node.get("content"):
        title = clean_text(node["content"])

    if not title and soup.title:
        title = clean_text(
            soup.title.get_text(" ", strip=True)
        )

    date_candidates = (
        ("property", "article:published_time"),
        ("name", "article:published_time"),
        ("itemprop", "datePublished"),
        ("name", "date"),
        ("name", "pubdate"),
    )

    for attribute, value in date_candidates:

        node = soup.find(
            "meta",
            attrs={attribute: value},
        )

        if node and node.get("content"):
            published_at = clean_text(
                node["content"]
            )
            break

    if not published_at:

        node = soup.find("time")

        if node:
            published_at = clean_text(
                node.get("datetime")
                or node.get_text(
                    " ",
                    strip=True,
                )
            )

    article = soup.find("article")

    if article:
        body = clean_text(
            article.get_text(
                " ",
                strip=True,
            )
        )

    else:
        main = soup.find("main")

        if main:
            body = clean_text(
                main.get_text(
                    " ",
                    strip=True,
                )
            )

        else:
            body = clean_text(
                soup.get_text(
                    " ",
                    strip=True,
                )
            )

    return {
        "title": title,
        "published_at": published_at,
        "body": body,
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

            cid = record.get("candidate_id")

            if cid:
                ids.add(str(cid))

    return ids


def append_record(
    path: Path,
    record: dict,
    existing_ids: set[str],
) -> bool:

    cid = record["candidate_id"]

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

        handle.write("\n")

    existing_ids.add(cid)

    return True


# ----------------------------------------------------------------------
# Collector
# ----------------------------------------------------------------------

def collect(args: argparse.Namespace) -> int:

    start_date = datetime.strptime(
        args.from_date,
        "%Y-%m-%d",
    )

    end_date = datetime.strptime(
        args.to_date,
        "%Y-%m-%d",
    )

    if start_date > end_date:
        raise ValueError(
            "La data iniziale e' successiva "
            "alla data finale."
        )

    output = Path(args.output)

    existing_ids = load_existing_ids(output)

    all_urls: set[str] = set()

    print()
    print("NOWCAST VERIFY - VVF")
    print("====================")
    print()
    print(
        "Periodo:",
        start_date.date(),
        "->",
        end_date.date(),
    )
    print()

    # --------------------------------------------------------------
    # 1. Scansione archivio ufficiale
    # --------------------------------------------------------------

    for page in range(args.max_pages):

        url = archive_page_url(page)

        print(
            f"Archivio pagina {page + 1}:",
            url,
        )

        try:
            html = get_page(url)

        except requests.RequestException as exc:

            print(
                "  ERRORE:",
                exc,
            )

            break

        links = extract_news_links(html)

        print(
            "  articoli trovati:",
            len(links),
        )

        if not links:
            # Evitiamo di continuare inutilmente
            # se la paginazione e' terminata.
            if page > 0:
                break

        before = len(all_urls)

        all_urls.update(links)

        if page > 0 and len(all_urls) == before:
            print(
                "  nessun nuovo articolo: "
                "fine scansione."
            )
            break

    print()
    print(
        "Articoli unici raccolti dall'archivio:",
        len(all_urls),
    )
    print()

    # --------------------------------------------------------------
    # 2. Analisi articoli
    # --------------------------------------------------------------

    examined = 0
    weather_related = 0
    in_period = 0
    inserted = 0
    duplicates = 0
    errors = 0

    for url in sorted(all_urls):

        try:
            article = extract_article(url)

        except requests.RequestException as exc:

            errors += 1

            print(
                "ERRORE articolo:",
                url,
                "-",
                exc,
            )

            continue

        examined += 1

        title_text = article["title"] or ""
        body_text = article["body"]

        # Un articolo diventa candidato solo se il titolo e'
        # esplicitamente meteorologico oppure se la pagina VVF
        # lo classifica nella categoria "Maltempo".
        title_is_weather = contains_weather_term(title_text)

        category_is_maltempo = bool(
            re.search(
                r"\\bCategoria\\s+Maltempo\\b",
                body_text,
                flags=re.IGNORECASE,
            )
        )

        if not (title_is_weather or category_is_maltempo):
            continue

        weather_related += 1

        if not article_in_period(
            article["published_at"],
            start_date,
            end_date,
        ):
            continue

        in_period += 1

        record = {
            "candidate_id": make_candidate_id(url),

            "source_name": (
                "Corpo Nazionale dei Vigili del Fuoco"
            ),

            "source_type": "primary",

            "source_url": url,

            "title": article["title"],

            "published_at": article["published_at"],

            # Non vengono inventati automaticamente.
            "event_date": None,
            "event_time": None,
            "location": None,
            "province": None,
            "region": None,
            "event_type": None,

            "evidence": None,

            "verification_status": "candidate",

            "collected_at": (
                datetime.now()
                .astimezone()
                .isoformat()
            ),
        }

        if append_record(
            output,
            record,
            existing_ids,
        ):

            inserted += 1

            print(
                "CANDIDATO:",
                article["title"]
                or url,
            )

        else:

            duplicates += 1

    print()
    print("RISULTATO")
    print("---------")
    print(
        "Articoli esaminati     :",
        examined,
    )
    print(
        "Con termini meteo      :",
        weather_related,
    )
    print(
        "Nel periodo richiesto  :",
        in_period,
    )
    print(
        "Nuovi candidati        :",
        inserted,
    )
    print(
        "Gia' presenti          :",
        duplicates,
    )
    print(
        "Errori                 :",
        errors,
    )
    print()
    print(
        "File:",
        output,
    )
    print()

    return 0


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def default_dates() -> tuple[str, str]:

    today = datetime.now().date()

    start = today - timedelta(days=2)

    return (
        start.isoformat(),
        today.isoformat(),
    )


def build_parser() -> argparse.ArgumentParser:

    default_from, default_to = default_dates()

    parser = argparse.ArgumentParser(
        description=(
            "Scansione conservativa dell'archivio "
            "pubblico delle notizie dei Vigili del Fuoco."
        )
    )

    parser.add_argument(
        "--from-date",
        default=default_from,
        help="Data iniziale YYYY-MM-DD.",
    )

    parser.add_argument(
        "--to-date",
        default=default_to,
        help="Data finale YYYY-MM-DD.",
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help=(
            "Numero massimo di pagine dell'archivio "
            "da esaminare. Default: 10."
        ),
    )

    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="File JSONL di destinazione.",
    )

    return parser


def main() -> int:

    parser = build_parser()

    args = parser.parse_args()

    if args.max_pages < 1:
        parser.error(
            "--max-pages deve essere almeno 1"
        )

    try:
        return collect(args)

    except (
        ValueError,
        OSError,
        requests.RequestException,
    ) as exc:

        print(
            "ERRORE:",
            exc,
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
