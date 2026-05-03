"""
Build backend/db/sample.sqlite from scratch.

Re-runnable: opens the existing file (or creates one), drops every table, then
recreates schema and inserts seed rows. Stdlib only (sqlite3).

Run:
    python3 backend/db/seed_sample_db.py

Schema satisfies the MVWF_PERMIT report (Q_PERMIT, Q_ORG, Q_SIGNATURE).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent / "sample.sqlite"


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

DROP_SQL = """
DROP TABLE IF EXISTS DEQ_Image_XRef;
DROP TABLE IF EXISTS DEQ_Image;
DROP TABLE IF EXISTS System_Parameters;
DROP TABLE IF EXISTS Program;
DROP TABLE IF EXISTS Site_Affil_Type;
DROP TABLE IF EXISTS Address_History_Program_XRef;
DROP TABLE IF EXISTS Address_History;
DROP TABLE IF EXISTS Organization;
DROP TABLE IF EXISTS Counties;
DROP TABLE IF EXISTS Geo_Loc_Info;
DROP TABLE IF EXISTS Site;
DROP TABLE IF EXISTS Site_Affiliation;
DROP TABLE IF EXISTS Application;
DROP TABLE IF EXISTS Status_Type;
DROP TABLE IF EXISTS Permit;
DROP TABLE IF EXISTS Permit_Type;
"""

CREATE_SQL = """
CREATE TABLE Permit_Type (
    Perm_Type_Id    INTEGER PRIMARY KEY,
    Perm_Type_Name  TEXT NOT NULL,
    Perm_Type_Desc  TEXT
);

CREATE TABLE Status_Type (
    Stat_Type_Id    INTEGER PRIMARY KEY,
    Stat_Type_Name  TEXT NOT NULL
);

CREATE TABLE Permit (
    Perm_Num        TEXT PRIMARY KEY,
    Perm_Name       TEXT,
    Perm_Type_Id    INTEGER,
    Stat_Type_Id    INTEGER,
    Perm_Eff_Date   TEXT,
    Perm_Exp_Date   TEXT,
    Status_Date     TEXT,
    Prog_Id         INTEGER
);

CREATE TABLE Application (
    App_Id          INTEGER PRIMARY KEY,
    Perm_Num        TEXT,
    Site_Affil_Id   INTEGER,
    App_Name        TEXT,
    Prog_Id         INTEGER
);

CREATE TABLE Site_Affiliation (
    Site_Affil_Id           INTEGER PRIMARY KEY,
    Site_Id                 INTEGER,
    Org_Id                  INTEGER,
    Site_Affil_Type_Id      INTEGER,
    Prog_Id                 INTEGER,
    Site_Affil_Start_Date   TEXT,
    Site_Affil_End_Date     TEXT
);

CREATE TABLE Site (
    Site_Id     INTEGER PRIMARY KEY,
    Site_Name   TEXT
);

CREATE TABLE Geo_Loc_Info (
    Site_Id             INTEGER PRIMARY KEY,
    Cnty_Id             INTEGER,
    Addr_Strt_1         TEXT,
    Addr_Strt_2         TEXT,
    Addr_City           TEXT,
    Addr_St             TEXT,
    Addr_Zip            TEXT,
    Addr_Zip_Plus_4     TEXT
);

CREATE TABLE Counties (
    Cnty_Id     INTEGER PRIMARY KEY,
    Cnty_Nm     TEXT
);

CREATE TABLE Organization (
    Org_Id      INTEGER PRIMARY KEY,
    Busn_Nm     TEXT,
    Frst_Nm     TEXT,
    MI          TEXT,
    Lst_Nm      TEXT
);

CREATE TABLE Address_History (
    ADRH_No                 INTEGER PRIMARY KEY,
    Org_Id                  INTEGER,
    AdTyp_Cd                TEXT,
    ADRH_Strt_1             TEXT,
    ADRH_Strt_2             TEXT,
    ADRH_City               TEXT,
    ADRH_St                 TEXT,
    ADRH_Zip_Cd             TEXT,
    ADRH_Zip_Plus_Four      TEXT,
    ADRH_Pri_Phn_No         TEXT
);

CREATE TABLE Address_History_Program_XRef (
    Org_Id      INTEGER,
    AdTyp_Cd    TEXT,
    ADRH_No     INTEGER,
    Prog_Id     INTEGER
);

CREATE TABLE Site_Affil_Type (
    Site_Affil_Type_Id      INTEGER PRIMARY KEY,
    Site_Affil_Type_Name    TEXT
);

CREATE TABLE Program (
    Prog_Id     INTEGER PRIMARY KEY,
    Prog_Name   TEXT
);

CREATE TABLE System_Parameters (
    SParm_Nm        TEXT PRIMARY KEY,
    SParm_Num_Val   REAL,
    SParm_Char_Val  TEXT
);

CREATE TABLE DEQ_Image (
    Image_Id    INTEGER PRIMARY KEY,
    Image       BLOB
);

CREATE TABLE DEQ_Image_XRef (
    Image_Id    INTEGER,
    Org_Id      INTEGER,
    Prog_Id     INTEGER
);
"""


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

PERMIT_TYPES = [
    (1, "MVWF", "Motor Vehicle Wrecking Facility"),
    (2, "GEN",  "Generic"),
]

STATUS_TYPES = [
    (10, "JVA"),
    (11, "PEND"),
    (12, "EXP"),
]

PROGRAMS = [
    (1, "JV"),
    (2, "AQ"),
]

SITE_AFFIL_TYPES = [
    (100, "Owner"),
    (101, "Operator"),
    (102, "Permittee"),
]

COUNTIES = [
    (1, "Yellowstone"),
    (2, "Gallatin"),
    (3, "Missoula"),
]

ORGANIZATIONS = [
    (1001, "Big Sky Auto Recyclers", None,    None, None),
    (1002, "Yellowstone Salvage LLC",  None,    None, None),
    (1003, None,                       "John",  "A",  "Doe"),
    (1004, None,                       "Jane",  "M",  "Smith"),
]

SITES = [
    (5001, "Big Sky Auto Yard"),
    (5002, "Gallatin Salvage Yard"),
    (5003, "Missoula Vehicle Recycling"),
]

GEO_LOC = [
    (5001, 1, "1234 Industrial Way",   None,         "Billings",   "MT", "59101", "1001"),
    (5002, 2, "789 Frontage Road",     "Suite B",    "Bozeman",    "MT", "59715", "0123"),
    (5003, 3, "456 Reserve St",        None,         "Missoula",   "MT", "59801", "4567"),
]

ADDRESS_HISTORY = [
    (9001, 1001, "M", "PO Box 100",        None,        "Billings",  "MT", "59103", "0100", "4065551234"),
    (9002, 1002, "M", "PO Box 200",        None,        "Bozeman",   "MT", "59771", "0200", "4065552345"),
    (9003, 1003, "M", "100 Main St",       "Apt 5",     "Missoula",  "MT", "59802", "0500", "4065553456"),
    (9004, 1004, "M", "250 Oak Avenue",    None,        "Billings",  "MT", "59102", "1234", "4065554567"),
]

ADDRESS_HISTORY_XREF = [
    (1001, "M", 9001, 1),
    (1002, "M", 9002, 1),
    (1003, "M", 9003, 1),
    (1004, "M", 9004, 1),
]

PERMITS = [
    ("MVWF-1001", "Big Sky Auto Wrecking",     1, 10, "2025-03-15", "2030-03-14", "2026-01-10", 1),
    ("MVWF-1002", "Yellowstone Salvage Site",  1, 10, "2025-06-01", "2030-05-31", "2026-01-22", 1),
    ("MVWF-1003", "Doe Family Junk Yard",       1, 10, "2025-09-12", "2030-09-11", "2026-02-05", 1),
    ("MVWF-1004", "Smith Auto Recyclers",       1, 10, "2026-01-05", "2031-01-04", "2026-02-15", 1),
    ("MVWF-1005", "Big Sky Auxiliary Yard",     1, 10, "2026-02-01", "2031-01-31", "2026-02-20", 1),
]

APPLICATIONS = [
    (7001, "MVWF-1001", 6001, "Big Sky Auto Wrecking App",     1),
    (7002, "MVWF-1002", 6002, "Yellowstone Salvage Site App",  1),
    (7003, "MVWF-1003", 6003, "Doe Family Junk Yard App",      1),
    (7004, "MVWF-1004", 6004, "Smith Auto Recyclers App",      1),
    (7005, "MVWF-1005", 6005, "Big Sky Auxiliary Yard App",    1),
]

SITE_AFFILIATIONS = [
    (6001, 5001, 1001, 102, 1, "2025-03-01", None),
    (6002, 5002, 1002, 102, 1, "2025-05-15", None),
    (6003, 5003, 1003, 102, 1, "2025-09-01", None),
    (6004, 5001, 1004, 102, 1, "2025-12-20", None),
    (6005, 5002, 1001, 102, 1, "2026-01-20", None),
]

SYSTEM_PARAMETERS = [
    ("K_Prog_Id_JV",         1.0,  None),
    ("K_AdTyp_Mailing",      None, "M"),
    ("K_Stat_JVA",           None, "JVA"),
    ("MVWF_REPORT_SIGNER",   None, "JOHN DOE"),
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


def main() -> str:
    """Build the database. Returns absolute path of the resulting sqlite file."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _zero_journals()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        # journal_mode=MEMORY keeps the rollback journal in RAM, so we don't
        # produce a sidecar file that the sandbox might refuse to delete.
        conn.execute("PRAGMA journal_mode=MEMORY")
        conn.executescript(DROP_SQL)
        conn.executescript(CREATE_SQL)
        cur = conn.cursor()
        cur.executemany("INSERT INTO Permit_Type VALUES (?,?,?)", PERMIT_TYPES)
        cur.executemany("INSERT INTO Status_Type VALUES (?,?)", STATUS_TYPES)
        cur.executemany("INSERT INTO Program VALUES (?,?)", PROGRAMS)
        cur.executemany("INSERT INTO Site_Affil_Type VALUES (?,?)", SITE_AFFIL_TYPES)
        cur.executemany("INSERT INTO Counties VALUES (?,?)", COUNTIES)
        cur.executemany("INSERT INTO Organization VALUES (?,?,?,?,?)", ORGANIZATIONS)
        cur.executemany("INSERT INTO Site VALUES (?,?)", SITES)
        cur.executemany("INSERT INTO Geo_Loc_Info VALUES (?,?,?,?,?,?,?,?)", GEO_LOC)
        cur.executemany("INSERT INTO Address_History VALUES (?,?,?,?,?,?,?,?,?,?)", ADDRESS_HISTORY)
        cur.executemany("INSERT INTO Address_History_Program_XRef VALUES (?,?,?,?)", ADDRESS_HISTORY_XREF)
        cur.executemany("INSERT INTO Permit VALUES (?,?,?,?,?,?,?,?)", PERMITS)
        cur.executemany("INSERT INTO Application VALUES (?,?,?,?,?)", APPLICATIONS)
        cur.executemany("INSERT INTO Site_Affiliation VALUES (?,?,?,?,?,?,?)", SITE_AFFILIATIONS)
        cur.executemany("INSERT INTO System_Parameters VALUES (?,?,?)", SYSTEM_PARAMETERS)
        conn.commit()
    finally:
        conn.close()
    return str(DB_PATH)


if __name__ == "__main__":
    path = main()
    size = os.path.getsize(path)
    print(f"Wrote {path} ({size} bytes)")
