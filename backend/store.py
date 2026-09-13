"""SQLite metadata adapter. Keep SQL at this boundary when introducing DynamoDB.
Local files contain synthetic demo data only. Production encryption/retention is not implemented.
"""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .catalog import COMPONENTS, FOUNDATIONS, INITIAL_GRANTS, discovery_components
from .repository import SQLiteRepository

class Store:
    def __init__(self, path: str, seed_personas=True):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.tx() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, persona TEXT NOT NULL, csrf TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS components(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS foundations(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS catalog_history(id INTEGER PRIMARY KEY, resource TEXT, body TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS grants(persona TEXT, component TEXT, PRIMARY KEY(persona,component));
            CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY, owner TEXT, workspace TEXT, current_version INTEGER, created REAL);
            CREATE TABLE IF NOT EXISTS versions(agent TEXT, version INTEGER, digest TEXT, body TEXT, created REAL, PRIMARY KEY(agent,version));
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, agent TEXT, version INTEGER, requester TEXT, idem TEXT, stage TEXT, result TEXT, created REAL, updated REAL, deadline REAL, attempts INTEGER DEFAULT 0, UNIQUE(agent,requester,idem));
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, stage TEXT, detail TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, requester TEXT, workspace TEXT, component TEXT, reason TEXT, status TEXT, decision TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS general_requests(id TEXT PRIMARY KEY, requester TEXT, workspace TEXT, summary TEXT, details TEXT, status TEXT, resolution TEXT, created REAL, updated REAL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, action TEXT, resource TEXT, detail TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, body TEXT);
            ''')
            for component in COMPONENTS:
                db.execute("INSERT OR IGNORE INTO components VALUES (?,?)", (component["id"], json.dumps(component)))
            # Real Bedrock discovery rows (from cached ListFoundationModels). These
            # drive the Models tab from real fields; they are discoverable-only and
            # carry no grant/execution state.
            for component in discovery_components():
                db.execute("INSERT OR IGNORE INTO components VALUES (?,?)", (component["id"], json.dumps(component)))
            for foundation in FOUNDATIONS:
                db.execute("INSERT OR IGNORE INTO foundations VALUES (?,?)", (foundation["id"], json.dumps({**foundation, "approved": True})))
            seeded = db.execute("SELECT 1 FROM settings WHERE key='seeded'").fetchone()
            if not seeded:
                for persona, components in (INITIAL_GRANTS.items() if seed_personas else []):
                    db.executemany("INSERT INTO grants VALUES (?,?)", [(persona, c) for c in components])
                db.execute("INSERT INTO settings VALUES ('seeded','true')")
                db.execute("INSERT INTO settings VALUES ('policy',?)", (json.dumps({"version": 1, "require_judge": False, "minimum_score": 1}),))

    @contextmanager
    def tx(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        try:
            yield SQLiteRepository(db)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
