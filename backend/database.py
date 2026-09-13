import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "nowcast_verify.db"

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_date TEXT NOT NULL,
            event_time TEXT,
            location TEXT NOT NULL,
            province TEXT,
            region TEXT,
            event_type TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_url TEXT,
            evidence TEXT,
            verification_status TEXT DEFAULT 'reported',
            time_verified INTEGER DEFAULT 0,
            verification_notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()

    print(f"Database pronto: {DB_PATH}")

if __name__ == "__main__":
    init_db()
