"""NOWCAST Verify - approvazione controllata degli eventi."""
import argparse, json
from pathlib import Path
from backend.database import connect, init_db

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "vvf_event_candidates.jsonl"

def load_candidate(path, candidate_id):
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("candidate_id") == candidate_id:
                return row
    raise ValueError(f"Candidato non trovato: {candidate_id}")

def validate(row):
    if row.get("verification_status") != "reported":
        raise ValueError("Il candidato non e' in stato reported.")
    if not row.get("complete_for_review"):
        raise ValueError("Il candidato e' incompleto.")
    required = ("event_date","location","event_type","source_name","source_url","evidence")
    missing = [x for x in required if not row.get(x)]
    if missing:
        raise ValueError("Campi mancanti: " + ", ".join(missing))

def find_event(conn, row):
    return conn.execute("""
        SELECT * FROM events
        WHERE event_date=?
          AND COALESCE(event_time,'')=COALESCE(?,'')
          AND lower(trim(location))=lower(trim(?))
          AND lower(trim(event_type))=lower(trim(?))
        ORDER BY id LIMIT 1
    """, (row["event_date"], row.get("event_time"), row["location"], row["event_type"])).fetchone()

def approve(row, province, region):
    validate(row)
    province = province or row.get("province")
    region = region or row.get("region")
    if not province or not region:
        raise ValueError("Provincia e regione sono obbligatorie.")
    init_db()
    with connect() as conn:
        event = find_event(conn, row)
        created = False
        if event:
            event_id = event["id"]
            if event["verification_status"] != "verified":
                conn.execute("""UPDATE events SET verification_status='verified',
                    province=COALESCE(province,?), region=COALESCE(region,?) WHERE id=?""",
                    (province, region, event_id))
        else:
            cur = conn.execute("""INSERT INTO events (
                event_date,event_time,location,province,region,event_type,
                source_name,source_url,evidence,verification_status,
                time_verified,verification_notes
            ) VALUES (?,?,?,?,?,?,?,?,?,'verified',?,?)""", (
                row["event_date"], row.get("event_time"), row["location"],
                province, region, row["event_type"], row["source_name"],
                row["source_url"], row["evidence"],
                1 if row.get("time_verified") else 0,
                "Approvato manualmente dal candidato " + row["candidate_id"]
            ))
            event_id = cur.lastrowid
            created = True

        before = conn.total_changes
        conn.execute("""INSERT OR IGNORE INTO event_evidence (
            event_id,source_name,source_type,source_url,published_at,
            observed_time_text,evidence,reliability
        ) VALUES (?,?,?,?,?,?,?,?)""", (
            event_id, row["source_name"], row.get("source_type") or "primary",
            row["source_url"], row.get("published_at"), row.get("time_evidence"),
            row["evidence"], "primary"
        ))
        evidence_added = conn.total_changes > before
        conn.commit()
    return event_id, created, evidence_added

def main():
    p = argparse.ArgumentParser(description="Approva manualmente un candidato VVF.")
    p.add_argument("candidate_id")
    p.add_argument("--input", default=str(DEFAULT_INPUT))
    p.add_argument("--province")
    p.add_argument("--region")
    a = p.parse_args()
    try:
        row = load_candidate(a.input, a.candidate_id)
        event_id, created, evidence_added = approve(row, a.province, a.region)
        print("APPROVATO")
        print("Event ID :", event_id)
        print("Evento   :", "creato" if created else "gia' esistente - riutilizzato")
        print("Evidenza :", "aggiunta" if evidence_added else "gia' presente")
        print("Stato    : verified")
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print("ERRORE:", exc)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
