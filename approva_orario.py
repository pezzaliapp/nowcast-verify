#!/usr/bin/env python3
"""
NOWCAST Verify — approvazione manuale dell'orario osservato.

QUESTO FILE NON CAMBIA NESSUNA REGOLA DI VERIFICA.

Non decide se un avviso era buono, non confronta niente, non tocca
`backend/compare.py` ne' `backend/report.py`. Fa una cosa sola: scrive
l'ora di un evento gia' approvato, e la marca come verificata — ma solo
dietro una fonte citata.

PERCHE' SERVE
-------------
Un evento puo' entrare in Verify con data, luogo e fenomeno accertati e
senza un orario abbastanza solido. In quel caso `time_verified` resta 0,
`compare.py` non calcola nessuno scarto rispetto all'ETA, e il report
scrive «orario non verificato». E' giusto cosi': un orario dedotto non
vale come un orario documentato.

Ma quando l'ora c'e' davvero — una diretta di giornale con l'orario della
voce, un anemometro, una foto con i dati di scatto — serve un modo per
dirlo, e in questo progetto non c'era: `collectors/approve_event.py`
lavora solo sui candidati VVF e non sa mettere un orario su un evento che
esiste gia'.

PERCHE' PRETENDE LA FONTE
-------------------------
Dichiarare verificata un'ora e' l'atto manuale piu' pesante di tutto il
sistema: da li' nascono gli scarti temporali, e domani la finestra di
validita'. Un'ora scritta a mano senza fonte sarebbe indistinguibile da
un'ora misurata, e nel giro di un mese nessuno saprebbe piu' quale sia
quale. Quindi: senza `--fonte`, `--url`, `--prova` e `--affidabilita`
questo comando rifiuta, e la prova finisce in `event_evidence` accanto
all'ora.

E NON SCRIVE NIENTE FINCHE' NON GLIELO DICI
-------------------------------------------
Per difetto mostra solo che cosa cambierebbe. Serve `--conferma` per
toccare il database.

Uso:
    python3 -m approva_orario --elenca
    python3 -m approva_orario --evento 12 --ora 13:30 \\
        --fonte "La Nazione" \\
        --url "https://www.lanazione.it/cronaca/diretta-..." \\
        --prova "13:30 — Grandine come palline da tennis a Forte dei Marmi" \\
        --affidabilita documented_media
    ... e poi lo stesso comando con --conferma
"""

from __future__ import annotations

import argparse
import re
import sys

from backend.database import connect, init_db

AFFIDABILITA = ("primary", "instrumental", "documented_media", "lead")

SPIEGA = {
    "primary": "chi era sul posto, o l'ente che ha gestito l'intervento",
    "instrumental": "uno strumento: anemometro, pluviometro, radar al suolo",
    "documented_media": "cronaca con l'orario, foto o video con i dati di scatto",
    "lead": "traccia da verificare — NON basta per dichiarare un'ora",
}


def elenca(conn):
    righe = conn.execute(
        """
        SELECT id, event_date, event_time, location, province, event_type,
               time_verified, source_name
        FROM events
        WHERE verification_status = 'verified'
        ORDER BY event_date, id
        """
    ).fetchall()

    if not righe:
        print("Nessun evento approvato nel database.")
        return

    print()
    print("EVENTI APPROVATI")
    print("================")
    print()
    for r in righe:
        segno = "ora verificata" if r["time_verified"] else "ORA DA APPROVARE"
        ora = r["event_time"] or "--:--"
        luogo = r["location"] + (f" ({r['province']})" if r["province"] else "")
        print(f"  [{r['id']:>4}] {r['event_date']} {ora}  {r['event_type']:<10}"
              f" {luogo}")
        print(f"         {segno} · fonte: {r['source_name'] or '—'}")
    print()
    print("Per approvare un'ora serve l'identificativo fra parentesi quadre.")
    print()


def trova(conn, id_evento):
    return conn.execute(
        "SELECT * FROM events WHERE id = ?", (id_evento,)
    ).fetchone()


# UN POSTO SI CHIAMA COL SUO NOME.
#
# La prima versione accettava solo `--evento 12`. Quel numero e' una
# chiave di database: non lo sa nessuno a memoria, va cercato ogni volta
# con --elenca e ricopiato a mano, e ricopiare un numero fra due comandi
# e' il modo piu' banale di approvare l'ora sull'evento sbagliato.
#
# Adesso si puo' dire «Forte dei Marmi». Se il nome e' ambiguo il comando
# NON sceglie per conto suo: mostra i candidati e si ferma. Indovinare
# quale intendesse l'utente, qui, vorrebbe dire scrivere un orario
# verificato sulla riga di un altro comune.
def trova_per_nome(conn, pezzo, giorno=None):
    sql = ("SELECT * FROM events WHERE verification_status = 'verified' "
           "AND lower(location) LIKE lower(?)")
    parametri = [f"%{pezzo}%"]
    if giorno:
        sql += " AND event_date = ?"
        parametri.append(giorno)
    return conn.execute(sql + " ORDER BY event_date, id", parametri).fetchall()


def riga_breve(r):
    luogo = r["location"] + (f" ({r['province']})" if r["province"] else "")
    ora = r["event_time"] or "--:--"
    stato = "ora verificata" if r["time_verified"] else "ora da approvare"
    return f"  [{r['id']:>4}] {r['event_date']} {ora}  {r['event_type']:<10} {luogo}  — {stato}"


def main():
    p = argparse.ArgumentParser(
        description=("Approva l'orario osservato di un evento gia' "
                     "verificato, dietro fonte citata."))
    p.add_argument("--elenca", action="store_true",
                   help="Mostra gli eventi approvati e quali hanno l'ora.")
    p.add_argument("--evento", type=int,
                   help="Identificativo dell'evento. In alternativa: --luogo.")
    p.add_argument("--luogo",
                   help="Nome del comune, anche parziale. Se ne trova piu' "
                        "di uno si ferma e te li mostra.")
    p.add_argument("--data", help="Restringe --luogo a una data YYYY-MM-DD.")
    p.add_argument("--ora", help="Orario osservato, HH:MM (ora locale).")
    p.add_argument("--fonte", help="Chi lo dice. Per esteso.")
    p.add_argument("--url", help="Dove lo dice.")
    p.add_argument("--prova", help="La frase esatta che riporta l'ora.")
    p.add_argument("--affidabilita", choices=AFFIDABILITA,
                   help="Che tipo di fonte e': "
                        + "; ".join(f"{k} = {v}" for k, v in SPIEGA.items()))
    p.add_argument("--pubblicato", help="Quando la fonte ha pubblicato (ISO).")
    p.add_argument("--sovrascrivi", action="store_true",
                   help="Consente di cambiare un'ora gia' verificata.")
    p.add_argument("--conferma", action="store_true",
                   help="Scrive davvero. Senza, mostra solo cosa cambierebbe.")
    a = p.parse_args()

    init_db()
    conn = connect()

    try:
        if a.elenca:
            elenca(conn)
            return 0

        # Prima si capisce DI CHE EVENTO si parla, poi si guarda se c'e'
        # tutto il resto: dire «manca --fonte» a chi ha sbagliato il nome
        # del comune manda a cercare la cosa sbagliata.
        if a.luogo and a.evento:
            print("ERRORE: o --evento o --luogo, non tutti e due.")
            return 1

        if a.luogo:
            trovati = trova_per_nome(conn, a.luogo, a.data)
            if not trovati:
                print(f"Nessun evento approvato con «{a.luogo}» nel nome"
                      + (f" il {a.data}" if a.data else "") + ".")
                print("Per vedere quali ci sono: "
                      "python3 -m approva_orario --elenca")
                return 1
            if len(trovati) > 1:
                print(f"«{a.luogo}» corrisponde a {len(trovati)} eventi. "
                      f"Non scelgo io: qui si scriverebbe un orario")
                print("verificato sulla riga sbagliata.")
                print()
                for r in trovati:
                    print(riga_breve(r))
                print()
                print("Restringi con --data, oppure indica --evento <numero>.")
                return 1
            a.evento = trovati[0]["id"]

        mancano = [n for n, v in (("--evento o --luogo", a.evento),
                                  ("--ora", a.ora),
                                  ("--fonte", a.fonte), ("--url", a.url),
                                  ("--prova", a.prova),
                                  ("--affidabilita", a.affidabilita))
                   if not v]
        if mancano:
            print("ERRORE: mancano " + ", ".join(mancano))
            print()
            print("Un'ora senza fonte non e' un'ora verificata: e' un'ora")
            print("scritta a mano, e fra un mese non si distingue piu' da una")
            print("misura. Per questo servono tutti e sei.")
            print()
            print("Per vedere gli eventi: python3 -m approva_orario --elenca")
            return 1

        # `lead` e' una traccia da seguire, non una prova. Dichiarare
        # un'ora verificata su una traccia significa mettere nel sistema
        # un dato che sembra accertato e non lo e'.
        if a.affidabilita == "lead":
            print("ERRORE: «lead» e' una traccia da verificare, non una prova.")
            print("Non basta per dichiarare un orario. Se la fonte e' solida,")
            print("dille col suo nome: primary, instrumental o documented_media.")
            return 1

        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", a.ora):
            print(f"ERRORE: «{a.ora}» non e' un orario HH:MM fra 00:00 e 23:59.")
            return 1

        ev = trova(conn, a.evento)
        if ev is None:
            print(f"ERRORE: nessun evento con identificativo {a.evento}.")
            print("Per vedere quali ci sono: python3 -m approva_orario --elenca")
            return 1

        if ev["verification_status"] != "verified":
            print(f"ERRORE: l'evento {a.evento} non e' approvato "
                  f"(stato: {ev['verification_status']}).")
            print("Prima si approva l'evento, poi il suo orario.")
            return 1

        if ev["time_verified"] and not a.sovrascrivi:
            print(f"L'evento {a.evento} ha gia' un'ora verificata: "
                  f"{ev['event_time']}.")
            print("Per cambiarla serve --sovrascrivi, e conviene chiedersi")
            print("prima quale delle due fonti sia migliore.")
            return 1

        gia = conn.execute(
            "SELECT 1 FROM event_evidence WHERE event_id = ? AND source_url = ?",
            (a.evento, a.url)).fetchone() is not None

        luogo = ev["location"] + (f" ({ev['province']})" if ev["province"] else "")
        print()
        print("EVENTO " + str(a.evento))
        print("-" * (7 + len(str(a.evento))))
        print(f"  {ev['event_date']}  {luogo}  —  {ev['event_type']}")
        print()
        print(f"  ora adesso    : {ev['event_time'] or '—'}"
              f"  ({'verificata' if ev['time_verified'] else 'non verificata'})")
        print(f"  ora nuova     : {a.ora}  (verificata)")
        print(f"  fonte         : {a.fonte}")
        print(f"  affidabilita' : {a.affidabilita} — {SPIEGA[a.affidabilita]}")
        print(f"  prova         : {a.prova}")
        print(f"  url           : {a.url}")
        if gia:
            print("  nota          : questa fonte e' gia' allegata "
                  "all'evento, non viene duplicata")
        print()

        if not a.conferma:
            print("NIENTE E' STATO SCRITTO. Per scrivere, rilancia lo stesso")
            print("comando aggiungendo --conferma")
            print()
            return 0

        nota = (f"Orario approvato a mano da {a.fonte} "
                f"({a.affidabilita}). {a.url}")
        precedente = (ev["verification_notes"] or "").strip()
        conn.execute(
            """
            UPDATE events
            SET event_time = ?, time_verified = 1, verification_notes = ?
            WHERE id = ?
            """,
            (a.ora, (precedente + "\n" + nota).strip(), a.evento))

        conn.execute(
            """
            INSERT OR IGNORE INTO event_evidence (
                event_id, source_name, source_type, source_url,
                published_at, observed_time_text, evidence, reliability
            ) VALUES (?, ?, 'orario', ?, ?, ?, ?, ?)
            """,
            (a.evento, a.fonte, a.url, a.pubblicato, a.ora, a.prova,
             a.affidabilita))
        conn.commit()

        print("SCRITTO.")
        print()
        print("Adesso rigenera il report perche' il cambiamento arrivi in")
        print("NOWCAST:")
        print()
        print(f"  python3 -m export_bridge --date {ev['event_date']}")
        print()
        print("Lo stato dell'evento restera' «compatibile»: in compare.py")
        print("`confirmed` non e' raggiungibile finche' non viene decisa una")
        print("finestra temporale di validita' a priori. Quello che cambia e'")
        print("che adesso lo SCARTO rispetto all'ETA viene misurato.")
        print()
        return 0

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
