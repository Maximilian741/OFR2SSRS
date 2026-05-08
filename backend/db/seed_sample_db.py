"""
Build backend/db/sample.sqlite from scratch.

Re-runnable: opens the existing file (or creates one), drops every existing
table (regardless of name), then recreates schema and inserts seed rows.
Stdlib only (sqlite3).

Run:
    python3 backend/db/seed_sample_db.py

The schema is intentionally generic so that translated Oracle Reports SQL
that follows common patterns (orgs joined to sites, sites joined to permits,
addresses, image attachments, etc.) can return rows for live preview without
revealing any customer-specific naming.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent / "sample.sqlite"


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

CREATE_SQL = """
CREATE TABLE Org (
    Org_Id      INTEGER PRIMARY KEY,
    Org_Name    TEXT NOT NULL,
    Email_Addr  TEXT,
    Phone       TEXT,
    Active      INTEGER
);

CREATE TABLE Site (
    Site_Id     INTEGER PRIMARY KEY,
    Site_Name   TEXT NOT NULL,
    Org_Id      INTEGER,
    Created_On  TEXT
);

CREATE TABLE Permit_Type (
    Permit_Type_Id      INTEGER PRIMARY KEY,
    Type_Code           TEXT NOT NULL,
    Type_Description    TEXT
);

CREATE TABLE Permit (
    Permit_Id       INTEGER PRIMARY KEY,
    Permit_Code     TEXT NOT NULL,
    Site_Id         INTEGER,
    Permit_Type_Id  INTEGER,
    Issued_On       TEXT,
    Expires_On      TEXT,
    Status          TEXT
);

CREATE TABLE Address (
    Address_Id  INTEGER PRIMARY KEY,
    Org_Id      INTEGER,
    Street_1    TEXT,
    Street_2    TEXT,
    City        TEXT,
    State       TEXT,
    Zip         TEXT,
    Country     TEXT
);

CREATE TABLE Image_Asset (
    Image_Id    INTEGER PRIMARY KEY,
    MimeType    TEXT,
    Image_Bytes BLOB
);

CREATE TABLE Image_Asset_Ref (
    Image_Id    INTEGER,
    Org_Id      INTEGER,
    Context     TEXT
);

CREATE TABLE Visit_Type (
    Visit_Type_Id       INTEGER PRIMARY KEY,
    Type_Code           TEXT NOT NULL,
    Type_Description    TEXT
);

CREATE TABLE Visit (
    Visit_Id            INTEGER PRIMARY KEY,
    Site_Id             INTEGER,
    Visit_Type_Id       INTEGER,
    Visit_Date          TEXT,
    Inspector_Org_Id    INTEGER,
    Notes               TEXT
);
"""


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

ORGS = [
    (1, "Acme Holdings",        "[email protected]", "(000) 000-0000", 1),
    (2, "Northwind Industries", "[email protected]", "(000) 000-0000", 1),
    (3, "Globex Group",         "[email protected]", "(000) 000-0000", 1),
    (4, "Initech Partners",     "[email protected]", "(000) 000-0000", 1),
    (5, "Umbra Logistics",      "[email protected]", "(000) 000-0000", 0),
]

SITES = [
    (101, "Plant A",                 1, "2021-04-12"),
    (102, "Warehouse 7",              2, "2022-08-03"),
    (103, "Distribution Center 3",    3, "2020-11-30"),
    (104, "Plant B",                  1, "2023-02-17"),
    (105, "Warehouse 12",             4, "2024-06-25"),
]

PERMIT_TYPES = [
    (1, "GEN",  "General operating permit"),
    (2, "STD",  "Standard facility permit"),
    (3, "TMP",  "Temporary permit"),
    (4, "REN",  "Renewal permit"),
    (5, "AUX",  "Auxiliary use permit"),
]

PERMITS = [
    (5001, "P-2026-001", 101, 1, "2024-01-10", "2027-01-09", "ACTIVE"),
    (5002, "P-2026-002", 102, 2, "2023-05-22", "2026-05-21", "ACTIVE"),
    (5003, "P-2026-003", 103, 1, "2022-09-15", "2025-09-14", "EXPIRED"),
    (5004, "P-2026-004", 104, 4, "2025-03-01", "2028-02-29", "PENDING"),
    (5005, "P-2026-007", 105, 3, "2026-02-05", "2026-08-04", "ACTIVE"),
]

ADDRESSES = [
    (9001, 1, "100 Main St",   None,        "Springfield", "ST", "00001", "Country"),
    (9002, 2, "200 Oak Ave",   "Suite 5",   "Oakdale",     "ST", "00002", "Country"),
    (9003, 3, "300 River Rd",  None,        "Riverton",    "ST", "00003", "Country"),
    (9004, 4, "400 Pine Blvd", "Unit B",    "Springfield", "ST", "00004", "Country"),
    (9005, 5, "500 Elm Way",   None,        "Oakdale",     "ST", "00005", "Country"),
]

IMAGE_ASSETS = [
    (7001, "image/png", b"\x89PNG\r\n\x1a\n"),
    (7002, "image/png", b"\x89PNG\r\n\x1a\n"),
    (7003, "image/png", b"\x89PNG\r\n\x1a\n"),
    (7004, "image/png", b"\x89PNG\r\n\x1a\n"),
    (7005, "image/png", b"\x89PNG\r\n\x1a\n"),
]

IMAGE_ASSET_REFS = [
    (7001, 1, "logo"),
    (7002, 2, "logo"),
    (7003, 3, "logo"),
    (7004, 4, "logo"),
    (7005, 5, "signature"),
]

VISIT_TYPES = [
    (1, "ROUT", "Routine check"),
    (2, "AUDT", "Audit"),
    (3, "FOLW", "Follow-up"),
    (4, "INIT", "Initial review"),
    (5, "ANNU", "Annual review"),
]

VISITS = [
    (8001, 101, 1, "2024-06-10", 5, "Routine site check"),
    (8002, 102, 2, "2024-09-22", 5, "Audit completed"),
    (8003, 103, 3, "2025-01-15", 5, "Follow-up scheduled"),
    (8004, 104, 4, "2025-04-08", 5, "Initial review"),
    (8005, 105, 5, "2026-02-20", 5, "Annual review pending"),
]


def _zero_journals() -> None:
    """Truncate any orphan SQLite sidecar files. Some sandboxed mounts (Cowork
    host-fs) deny delete; truncating to 0 bytes neutralizes them without
    unlinking, so SQLite ignores them on the next open."""
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = DB_PATH.with_name(DB_PATH.name + suffix)
        if sidecar.exists():
            try:
                with open(sidecar, "wb") as fh:
                    fh.write(b"")
            except OSError:
                pass


def _drop_all_tables(conn: sqlite3.Connection) -> None:
    """Drop EVERY user table in the database, regardless of name. This makes
    the seed re-runnable across schema renames: any leftover tables from a
    prior schema version are removed before the current schema is created."""
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for (name,) in rows:
        cur.execute(f'DROP TABLE IF EXISTS "{name}"')
    conn.commit()


def main() -> str:
    """Build the database. Returns absolute path of the resulting sqlite file."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _zero_journals()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        # journal_mode=MEMORY keeps the rollback journal in RAM, so we don't
        # produce a sidecar file that the sandbox might refuse to delete.
        conn.execute("PRAGMA journal_mode=MEMORY")
        _drop_all_tables(conn)
        conn.executescript(CREATE_SQL)
        cur = conn.cursor()
        cur.executemany("INSERT INTO Org VALUES (?,?,?,?,?)", ORGS)
        cur.executemany("INSERT INTO Site VALUES (?,?,?,?)", SITES)
        cur.executemany("INSERT INTO Permit_Type VALUES (?,?,?)", PERMIT_TYPES)
        cur.executemany("INSERT INTO Permit VALUES (?,?,?,?,?,?,?)", PERMITS)
        cur.executemany("INSERT INTO Address VALUES (?,?,?,?,?,?,?,?)", ADDRESSES)
        cur.executemany("INSERT INTO Image_Asset VALUES (?,?,?)", IMAGE_ASSETS)
        cur.executemany("INSERT INTO Image_Asset_Ref VALUES (?,?,?)", IMAGE_ASSET_REFS)
        cur.executemany("INSERT INTO Visit_Type VALUES (?,?,?)", VISIT_TYPES)
        cur.executemany("INSERT INTO Visit VALUES (?,?,?,?,?,?)", VISITS)
        conn.commit()
    finally:
        conn.close()
    return str(DB_PATH)


if __name__ == "__main__":
    path = main()
    size = os.path.getsize(path)
    print(f"Wrote {path} ({size} bytes)")
