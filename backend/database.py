import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "nowcast_verify.db"


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with connect() as conn:

        # EVENTI REALMENTE OSSERVATI
        conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_date TEXT NOT NULL,
            event_time TEXT,
            location TEXT NOT NULL,
            province TEXT,
            region TEXT,
            latitude REAL,
            longitude REAL,
            event_type TEXT NOT NULL,
            severity TEXT,
            source_name TEXT NOT NULL,
            source_url TEXT,
            evidence TEXT,
            verification_status TEXT NOT NULL DEFAULT 'reported',
            time_verified INTEGER NOT NULL DEFAULT 0,
            verification_notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # BOLLETTINI / PREVISIONI UFFICIALI
        conn.execute("""
        CREATE TABLE IF NOT EXISTS official_forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            bulletin_type TEXT NOT NULL,
            bulletin_date TEXT NOT NULL,
            issued_at TEXT,
            valid_from TEXT,
            valid_to TEXT,
            area TEXT,
            event TEXT,
            precipitation_level TEXT,
            severity TEXT,
            certainty TEXT,
            description TEXT,
            source_url TEXT NOT NULL,
            source_identifier TEXT,
            source_hash TEXT,
            collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # ALERT GENERATI DA NOWCAST
        conn.execute("""
        CREATE TABLE IF NOT EXISTS nowcast_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id TEXT,
            issued_at TEXT NOT NULL,
            predicted_time TEXT,
            location TEXT NOT NULL,
            province TEXT,
            region TEXT,
            latitude REAL,
            longitude REAL,
            event_type TEXT NOT NULL,
            probability REAL,
            lead_minutes REAL,
            message TEXT,
            raw_data TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # CONFRONTO TRA ALERT ED EVENTI OSSERVATI
        conn.execute("""
        CREATE TABLE IF NOT EXISTS comparisons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER,
            event_id INTEGER,
            result TEXT NOT NULL CHECK (
                result IN (
                    'confirmed',
                    'false_positive',
                    'missed',
                    'unverifiable'
                )
            ),
            time_difference_minutes REAL,
            distance_km REAL,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(alert_id) REFERENCES nowcast_alerts(id),
            FOREIGN KEY(event_id) REFERENCES events(id)
        )
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_date
        ON events(event_date)
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_location
        ON events(location)
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_alerts_issued
        ON nowcast_alerts(issued_at)
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_forecasts_date
        ON official_forecasts(bulletin_date)
        """)

        conn.commit()


if __name__ == "__main__":
    init_db()
    print(f"Database pronto: {DB_PATH}")
