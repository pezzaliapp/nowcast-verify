"""NOWCAST Verify - collector conservativo rapporti post-evento ARPAE.

Versione 2:
- un candidato per rapporto + tipo di fenomeno;
- aggrega gli estratti invece di creare un record per ogni parola trovata;
- non inferisce data/ora/localita;
- nessun candidato viene verificato automaticamente.
"""

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = "https://www.arpae.it/it/temi-ambientali/meteo/report-meteo/rapporti-post-evento"
DEFAULT_OUTPUT = ROOT / "data" / "arpae_candidates.jsonl"
UA = "NOWCAST-Verify/1.0"

TERMS = {
    "hail": ("grandine", "grandinate", "grandinata", "chicchi"),
    "downburst": ("downburst",),
    "wind": (
        "forti raffiche",
        "raffiche di vento",
        "raffiche registrate",
        "vento forte",
        "forte vento",
        "forte ventilazione",
        "ventilazione molto forte",
    ),
}


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def fetch(url):
    response = requests.get(url, timeout=25, headers={"User-Agent": UA})
    response.raise_for_status()
    return response


def is_arpae(url):
    host = (urlparse(url).hostname or "").lower()
    return host == "arpae.it" or host.endswith(".arpae.it")


def discover(year):
    soup = BeautifulSoup(fetch(ARCHIVE).text, "html.parser")
    found = {}

    for a in soup.find_all("a", href=True):
        label = clean(a.get_text(" ", strip=True))
        href = urljoin(ARCHIVE, a["href"]).split("#")[0]
        haystack = f"{label} {href}".lower()

        if str(year) not in haystack:
            continue
        if not is_arpae(href):
            continue
        if "rapport" not in haystack and "meteo" not in haystack:
            continue

        found[href] = label

    return sorted(found.items())


def extract_html(url):
    response = fetch(url)
    content_type = response.headers.get("content-type", "").lower()

    if "pdf" in content_type or url.lower().endswith(".pdf"):
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    for tag in soup(["script", "style", "noscript", "nav", "footer"]):
        tag.decompose()

    title = clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    text = clean(soup.get_text(" ", strip=True))
    return title, text


def sentence_chunks(text):
    # Mantiene abbastanza contesto per le frasi meteorologiche senza
    # trasformare l'intera pagina in una singola evidenza.
    chunks = re.split(r"(?<=[.!?])\s+|(?<=;)\s+", text)
    return [clean(c) for c in chunks if clean(c)]


def matching_evidence(text, terms):
    hits = []
    seen = set()

    for chunk in sentence_chunks(text):
        low = chunk.casefold()
        matched = [term for term in terms if term in low]
        if not matched:
            continue

        # Esclude evidenti elementi di navigazione/URL se privi di testo utile.
        if len(chunk) < 20:
            continue

        key = chunk.casefold()
        if key in seen:
            continue
        seen.add(key)
        hits.append((chunk, matched))

    return hits


def candidate_id(url, event_type):
    raw = f"{url}|{event_type}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def build_candidate(url, title, event_type, matches):
    evidence_parts = [item[0] for item in matches]
    matched_terms = sorted({term for _, terms in matches for term in terms})

    # Limite prudenziale: conserviamo le prime evidenze utili, senza
    # duplicare interi rapporti nel JSONL.
    evidence = " | ".join(evidence_parts[:6])

    return {
        "candidate_id": candidate_id(url, event_type),
        "source_name": "ARPAE Emilia-Romagna",
        "source_type": "primary",
        "source_url": url,
        "title": title,
        "published_at": None,
        "event_date": None,
        "event_time": None,
        "location": None,
        "province": None,
        "region": "Emilia-Romagna",
        "event_type": event_type,
        "time_verified": False,
        "date_evidence": None,
        "time_evidence": None,
        "location_evidence": None,
        "type_evidence": ", ".join(matched_terms),
        "evidence": evidence,
        "complete_for_review": False,
        "verification_status": "incomplete",
        "verification_notes": (
            "Candidato aggregato automaticamente da un rapporto post-evento "
            "ARPAE. Conferma documentale del fenomeno nel rapporto, ma data, "
            "ora e localita puntuali non vengono inferite automaticamente. "
            "Richiede revisione prima dell'uso come ground truth."
        ),
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


def collect(year):
    reports = discover(year)
    candidates = []
    errors = 0

    for url, archive_label in reports:
        try:
            result = extract_html(url)
            if result is None:
                continue

            title, text = result
            title = title or archive_label

            for event_type, terms in TERMS.items():
                matches = matching_evidence(text, terms)
                if matches:
                    candidates.append(
                        build_candidate(url, title, event_type, matches)
                    )

        except requests.RequestException as exc:
            errors += 1
            print(f"ERRORE {url}: {exc}")

    return reports, candidates, errors


def write_fresh(path, candidates):
    """Rigenera il dataset ARPAE.

    Questo file e' un prodotto del collector, non un registro di approvazioni:
    la rigenerazione evita di conservare i duplicati prodotti dalla v1.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in candidates:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Raccoglie candidati dai rapporti post-evento ARPAE."
    )
    parser.add_argument("--year", type=int, default=datetime.now().year)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    output = Path(args.output)

    try:
        reports, candidates, errors = collect(args.year)
    except requests.RequestException as exc:
        print("ERRORE archivio ARPAE:", exc)
        return 1

    write_fresh(output, candidates)

    counts = {}
    for row in candidates:
        counts[row["event_type"]] = counts.get(row["event_type"], 0) + 1

    print()
    print("NOWCAST VERIFY - ARPAE v2")
    print("=========================")
    print("Anno                 :", args.year)
    print("Rapporti individuati :", len(reports))
    print("Candidati aggregati  :", len(candidates))
    for event_type in ("hail", "downburst", "wind"):
        print(f"  {event_type:<19}:", counts.get(event_type, 0))
    print("Errori               :", errors)
    print("File rigenerato      :", output)
    print()
    print("REGOLA: 1 candidato massimo per rapporto + tipo di fenomeno.")
    print("Nessun candidato ARPAE viene verificato automaticamente.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
