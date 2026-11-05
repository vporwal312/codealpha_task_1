"""
Data Redundancy Removal System
------------------------------
Classifies incoming records as UNIQUE, REDUNDANT, FALSE_POSITIVE or INVALID
and appends only unique, verified records to the database.

Record schema (assumed): name, email, phone  (edit REQUIRED_FIELDS / normalize()
to fit your real data).

Pipeline per record:
  1. Validate      -> INVALID records are rejected
  2. Normalize     -> canonical form so "A@X.com " == "a@x.com"
  3. Exact check   -> SHA-256 fingerprint match            -> REDUNDANT
  4. Fuzzy check   -> compare with candidate records:
        shared email/phone + similar name                  -> REDUNDANT
        flagged as similar but identifiers differ          -> FALSE_POSITIVE (distinct, appended)
  5. Append        -> only UNIQUE / FALSE_POSITIVE are inserted
  6. DB guard      -> UNIQUE(fingerprint) constraint blocks duplicates even
                      under concurrent writers
Every decision is written to an audit log.

SQLite is used so this runs anywhere. To use a cloud DB (PostgreSQL / MySQL /
Firestore / DynamoDB), only the Storage class needs to be re-implemented.
"""
import hashlib
import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher

UNIQUE, REDUNDANT, FALSE_POSITIVE, INVALID = "UNIQUE", "REDUNDANT", "FALSE_POSITIVE", "INVALID"

REQUIRED_FIELDS = ("name", "email", "phone")
EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")

NAME_SAME_PERSON = 0.80   # name similarity needed to call a shared-identifier match a duplicate
NAME_LOOKS_SIMILAR = 0.90  # name similarity that raises a flag even with different identifiers


# ---------------------------------------------------------------- validation
def validate(rec: dict):
    """Return (ok, reason)."""
    for f in REQUIRED_FIELDS:
        if not str(rec.get(f, "")).strip():
            return False, f"missing field: {f}"
    if not EMAIL_RE.match(str(rec["email"]).strip().lower()):
        return False, "invalid email format"
    if len(re.sub(r"\D", "", str(rec["phone"]))) < 10:
        return False, "invalid phone (needs at least 10 digits)"
    return True, ""


def normalize(rec: dict) -> dict:
    return {
        "name": re.sub(r"\s+", " ", str(rec["name"]).strip().lower()),
        "email": str(rec["email"]).strip().lower(),
        "phone": re.sub(r"\D", "", str(rec["phone"]))[-10:],  # last 10 digits
    }


def fingerprint(n: dict) -> str:
    key = "|".join((n["name"], n["email"], n["phone"]))
    return hashlib.sha256(key.encode()).hexdigest()


def name_similarity(a: str, b: str) -> float:
    # order-insensitive: "smith john" ~ "john smith"
    a, b = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
    return SequenceMatcher(None, a, b).ratio()


# ------------------------------------------------------------------- storage
class Storage:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS records(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, email TEXT, phone TEXT,
            fingerprint TEXT NOT NULL UNIQUE,          -- hard duplicate guard
            created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS ix_email ON records(email);
        CREATE INDEX IF NOT EXISTS ix_phone ON records(phone);
        CREATE INDEX IF NOT EXISTS ix_name3 ON records(substr(name,1,3));
        CREATE TABLE IF NOT EXISTS audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw TEXT, classification TEXT, reason TEXT,
            matched_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        """)

    def exists(self, fp):
        r = self.db.execute("SELECT id FROM records WHERE fingerprint=?", (fp,)).fetchone()
        return r[0] if r else None

    def candidates(self, n):
        """Indexed lookup ('blocking') so we never scan the whole table."""
        return self.db.execute(
            "SELECT id,name,email,phone FROM records "
            "WHERE email=? OR phone=? OR substr(name,1,3)=?",
            (n["email"], n["phone"], n["name"][:3])).fetchall()

    def insert(self, n, fp) -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO records(name,email,phone,fingerprint) VALUES(?,?,?,?)",
            (n["name"], n["email"], n["phone"], fp))
        self.db.commit()
        return cur.rowcount == 1  # False => DB rejected a duplicate

    def log(self, raw, cls, reason, matched_id=None):
        self.db.execute(
            "INSERT INTO audit_log(raw,classification,reason,matched_id) VALUES(?,?,?,?)",
            (str(raw), cls, reason, matched_id))
        self.db.commit()

    def all_records(self):
        return self.db.execute("SELECT id,name,email,phone FROM records").fetchall()


# ------------------------------------------------------------------- engine
@dataclass
class Result:
    classification: str
    reason: str
    matched_id: int = None
    inserted: bool = False


class DedupEngine:
    def __init__(self, storage: Storage):
        self.store = storage

    def classify(self, rec: dict) -> Result:
        ok, why = validate(rec)
        if not ok:
            return Result(INVALID, why)

        n = normalize(rec)
        fp = fingerprint(n)

        mid = self.store.exists(fp)
        if mid:
            return Result(REDUNDANT, "exact duplicate", mid)

        flagged = None
        for cid, cname, cemail, cphone in self.store.candidates(n):
            sim = name_similarity(n["name"], cname)
            same_id = (n["email"] == cemail) or (n["phone"] == cphone)
            if same_id and sim >= NAME_SAME_PERSON:
                return Result(REDUNDANT, f"near duplicate (shared email/phone, name sim {sim:.2f})", cid)
            if same_id:
                flagged = flagged or Result(FALSE_POSITIVE,
                    f"shares email/phone with id {cid} but names differ (sim {sim:.2f})", cid)
            elif sim >= NAME_LOOKS_SIMILAR:
                flagged = flagged or Result(FALSE_POSITIVE,
                    f"similar name to id {cid} (sim {sim:.2f}) but email and phone differ", cid)
        return flagged or Result(UNIQUE, "no match found")

    def process(self, rec: dict) -> Result:
        """Validate -> classify -> append only if unique/verified."""
        res = self.classify(rec)
        if res.classification in (UNIQUE, FALSE_POSITIVE):
            n = normalize(rec)
            res.inserted = self.store.insert(n, fingerprint(n))
            if not res.inserted:  # lost a race with another writer
                res.classification, res.reason = REDUNDANT, "blocked by DB unique constraint"
        self.store.log(rec, res.classification, res.reason, res.matched_id)
        return res

    def process_batch(self, records):
        return [self.process(r) for r in records]
