#!/usr/bin/env python3
"""
NOWCAST Verify — ponte verso l'area tecnica di NOWCAST.

QUESTO FILE NON FA PARTE DELLA CATENA SCIENTIFICA.

Non decide niente. Non confronta niente. Non approva niente. Prende quello
che Verify ha gia' concluso e lo scrive in due file — uno Markdown e uno
JSON — dentro una cartella che NOWCAST puo' leggere. Tutte le regole di
verifica restano dove stanno: `backend/compare.py` e `backend/report.py`
non vengono ne' modificati ne' reinterpretati qui.

Perche' una cartella di file e non un accesso al database: NOWCAST gira
come utente `nowcast` da /opt/nowcast, questo progetto vive nella home di
alessandro. Farli parlare attraverso SQLite significherebbe permessi
incrociati, due processi sullo stesso file e — soprattutto — NOWCAST che
importa codice di Verify, cioe' i due progetti che smettono di essere
separati. Due file in una cartella non hanno nessuno di questi problemi:
se Verify e' fermo, NOWCAST mostra «nessun report» e basta.

L'UNICA SCRITTURA VERSO IL DATABASE e' l'import del registro NOWCAST, ed
e' il collector di questo progetto che fa il lavoro suo. Serve perche'
`collectors/daily.py` NON chiama `collectors.nowcast_alerts`: senza questo
passo un report puo' dire «mancato» soltanto perche' l'avviso non era mai
stato importato. Tutto il resto qui dentro e' lettura.

Uso:
    python -m export_bridge --date 2026-09-14
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

from backend.compare import local_time_text
from backend.database import connect, init_db
from backend.report import comparison_data, render_markdown
from collectors.nowcast_alerts import import_jsonl
from collectors.review_queue import classify, load_candidates

ROOT = Path(__file__).resolve().parent

REGISTRO_NOWCAST = Path("/opt/nowcast/dati/registro.jsonl")
CARTELLA_PONTE = Path("/opt/nowcast/dati/verify")
CANDIDATI = ROOT / "data" / "vvf_event_candidates.jsonl"
REPORTS = ROOT / "reports"


# ---------------------------------------------------------------------
# Lettura di contorno: quello che `evaluate_event` non restituisce
# ---------------------------------------------------------------------
#
# Il dizionario prodotto da `evaluate_event` non porta con se' la fonte:
# `source_name` e `source_url` stanno sulla riga di `events`. Per mostrarli
# nell'area tecnica li si rilegge da li', in sola lettura, invece di
# cambiare la forma di cio' che `compare.py` restituisce. Un modulo che
# serve a decidere non va toccato per aggiungere una colonna a una pagina.

def dettagli_evento(conn, event_id):
    row = conn.execute(
        """
        SELECT location, province, region, event_type, severity,
               source_name, source_url, evidence,
               verification_status, time_verified, verification_notes
        FROM events
        WHERE id = ?
        """,
        (event_id,),
    ).fetchone()

    if row is None:
        return {}

    prove = conn.execute(
        """
        SELECT source_name, source_type, source_url, published_at,
               observed_time_text, reliability
        FROM event_evidence
        WHERE event_id = ?
        ORDER BY id
        """,
        (event_id,),
    ).fetchall()

    return {
        "provincia": row["province"],
        "regione": row["region"],
        "gravita": row["severity"],
        "fonte": row["source_name"],
        "fonte_url": row["source_url"],
        "evidenza": row["evidence"],
        "stato_verifica": row["verification_status"],
        "note_verifica": row["verification_notes"],
        "prove": [dict(p) for p in prove],
    }


# ---------------------------------------------------------------------
# I candidati: si mostrano, non si contano
# ---------------------------------------------------------------------
#
# `collectors/review_queue.py` li classifica gia'. Qui li si legge con la
# SUA funzione `classify()`, senza reimplementarla, e li si tiene in un
# ramo separato del JSON. Non entrano in nessun totale: un candidato non
# revisionato non e' una verifica, e sommarlo alle verifiche vere e'
# esattamente il modo in cui una statistica comincia a mentire.

def coda_revisione(conn, giorno):
    if not CANDIDATI.exists():
        return {"file": str(CANDIDATI), "presente": False,
                "da_revisionare": [], "incompleti": [], "gia_approvati": 0}

    try:
        righe = load_candidates(CANDIDATI)
    except ValueError as exc:
        return {"file": str(CANDIDATI), "presente": True,
                "errore": str(exc),
                "da_revisionare": [], "incompleti": [], "gia_approvati": 0}

    da_revisionare, incompleti, approvati = [], [], 0

    for riga in righe:
        if giorno and riga.get("event_date") != giorno:
            continue

        stato, event_id = classify(conn, riga)

        voce = {
            "candidato": riga.get("candidate_id"),
            "data": riga.get("event_date"),
            "ora": riga.get("event_time"),
            "localita": riga.get("location"),
            "tipo": riga.get("event_type"),
            "fonte": riga.get("source_name"),
            "fonte_url": riga.get("source_url"),
            "event_id": event_id,
        }

        if stato == "GIA' APPROVATO":
            approvati += 1
        elif stato == "INCOMPLETO":
            voce["manca"] = [
                nome for nome, chiave in (
                    ("data", "event_date"), ("ora", "event_time"),
                    ("località", "location"), ("tipo", "event_type"))
                if not riga.get(chiave)
            ]
            incompleti.append(voce)
        else:
            da_revisionare.append(voce)

    return {
        "file": str(CANDIDATI),
        "presente": True,
        "da_revisionare": da_revisionare,
        "incompleti": incompleti,
        "gia_approvati": approvati,
    }


def versione_verify():
    """Quale commit di Verify ha prodotto questo report.

    Un report senza la versione di chi l'ha generato e' difficile da
    rileggere fra un mese, quando il codice sara' cambiato.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


def scrivi_atomico(percorso: Path, testo: str):
    """Prima un file temporaneo, poi una rinomina.

    NOWCAST legge questa cartella mentre noi ci scriviamo. Scrivere in
    chiaro significa che una lettura capitata a meta' vede un JSON
    troncato; la rinomina, sullo stesso filesystem, e' atomica.
    """
    tmp = percorso.with_suffix(percorso.suffix + ".tmp")
    tmp.write_text(testo, encoding="utf-8")
    os.replace(tmp, percorso)


def costruisci(giorno, registro, salta_import):
    passi = []

    # 1. Gli avvisi NOWCAST aggiornati, prima di qualsiasi confronto.
    if salta_import:
        passi.append("import del registro saltato su richiesta")
    elif not registro.is_file():
        passi.append(f"registro non trovato: {registro} — confronto sugli "
                     f"avvisi gia' presenti")
    else:
        print(f"Importo il registro NOWCAST da {registro}")
        import_jsonl(registro)
        passi.append(f"registro importato da {registro}")

    # 2. Verify decide. Noi no.
    dati = comparison_data(giorno)
    markdown = render_markdown(dati, giorno)

    # 3. Il contorno che la pagina tecnica mostra.
    init_db()
    conn = connect()
    try:
        eventi = []
        for r in dati["results"]:
            voce = dict(r)
            voce["emissione_locale"] = local_time_text(r["latest_issued_at"])
            voce["eta_locale"] = local_time_text(r["latest_predicted_time"])
            voce.update(dettagli_evento(conn, r["event_id"]))
            eventi.append(voce)

        candidati = coda_revisione(conn, giorno)
    finally:
        conn.close()

    conteggi = {"confirmed": 0, "compatible": 0,
                "missed": 0, "unverifiable": 0}
    for e in eventi:
        if e["status"] in conteggi:
            conteggi[e["status"]] += 1

    istantanea = {
        "generato_il": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": giorno,
        "verify_commit": versione_verify(),
        "passi": passi,
        "categorie_nowcast": sorted(dati["nowcast_types"]),
        # I candidati NON sono qui dentro, e non e' una dimenticanza.
        "riepilogo": {"verificati": len(eventi), **conteggi},
        "copertura": dati["coverage"],
        "eventi": eventi,
        "candidati": candidati,
        "nota": (
            "L'assenza di un riscontro indipendente non dimostra che un "
            "avviso fosse falso. Le forti raffiche non sono di per se' "
            "prova di un downburst. Senza orario sufficientemente "
            "verificato non viene dichiarata una conferma temporale. I "
            "candidati in coda di revisione non entrano in nessun "
            "conteggio."
        ),
    }

    return markdown, istantanea


def main():
    oggi = date.today().isoformat()

    p = argparse.ArgumentParser(
        description=("Esporta un report di NOWCAST Verify nella cartella "
                     "letta dall'area tecnica di NOWCAST."))
    p.add_argument("--date", dest="giorno", default=oggi,
                   help="Data YYYY-MM-DD. Default: oggi.")
    p.add_argument("--registro", default=str(REGISTRO_NOWCAST),
                   help=f"registro.jsonl di NOWCAST. Default: {REGISTRO_NOWCAST}")
    p.add_argument("--out", default=str(CARTELLA_PONTE),
                   help=f"Cartella ponte. Default: {CARTELLA_PONTE}")
    p.add_argument("--skip-import", action="store_true",
                   help="Non importa il registro NOWCAST.")
    args = p.parse_args()

    try:
        date.fromisoformat(args.giorno)
    except ValueError:
        p.error("--date deve essere nel formato YYYY-MM-DD")

    fuori = Path(args.out)
    try:
        fuori.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        raise SystemExit(
            f"\nNon posso scrivere in {fuori}.\n"
            f"Va preparata una volta sola, come alessandro:\n\n"
            f"  sudo mkdir -p {fuori}\n"
            f"  sudo chown $USER:nowcast {fuori}\n"
            f"  sudo chmod 750 {fuori}\n")

    markdown, istantanea = costruisci(
        args.giorno, Path(args.registro), args.skip_import)

    REPORTS.mkdir(parents=True, exist_ok=True)
    scrivi_atomico(REPORTS / f"{args.giorno}.md", markdown)

    scrivi_atomico(fuori / f"{args.giorno}.md", markdown)
    scrivi_atomico(fuori / f"{args.giorno}.json",
                   json.dumps(istantanea, ensure_ascii=False, indent=2))

    r = istantanea["riepilogo"]
    c = istantanea["candidati"]

    print()
    print("PONTE VERIFY -> NOWCAST")
    print("-----------------------")
    print(f"Data              : {args.giorno}")
    print(f"Eventi verificati : {r['verificati']}")
    print(f"  confermati      : {r['confirmed']}")
    print(f"  compatibili     : {r['compatible']}")
    print(f"  mancati         : {r['missed']}")
    print(f"  non verificabili: {r['unverifiable']}")
    print(f"Da revisionare    : {len(c['da_revisionare'])}  "
          f"(fuori da ogni conteggio)")
    print(f"Scritto in        : {fuori}")
    print()


if __name__ == "__main__":
    main()
