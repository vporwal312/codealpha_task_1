# Task 1: Data Redundancy Removal System

## Overview
A Python system that checks every incoming record against the existing database, classifies it, and appends only unique, verified records. Duplicates and invalid data never reach the database.

**Files**
- `dedup_system.py` – the system (validation, classification, storage, audit log)
- `demo.py` – runs 9 sample records through the system
- `records.db` – SQLite database produced by the demo (swap for any cloud DB)

Run: `python3 demo.py`

## How the task requirements are met

| Requirement | Implementation |
|---|---|
| Identify and classify data as redundant or false positive | `DedupEngine.classify()` returns `UNIQUE`, `REDUNDANT`, `FALSE_POSITIVE` or `INVALID` with a reason |
| Validation mechanism for new data vs existing data | `validate()` checks required fields, email format and phone length. `classify()` then compares the record with existing data (exact hash + fuzzy match) |
| Prevent duplicate data entering the cloud database | Three layers: exact fingerprint lookup, fuzzy candidate comparison, and a `UNIQUE(fingerprint)` constraint in the DB as a final guard against concurrent writers |
| Append only unique and verified entries | `process()` inserts only `UNIQUE` and verified `FALSE_POSITIVE` records. `REDUNDANT` and `INVALID` are rejected |
| Accuracy and efficiency | Normalization avoids missed duplicates, indexed "blocking" queries avoid full table scans, and every decision is stored in an `audit_log` table |

## Pipeline
```
Incoming record
   |
   v
1. Validate ----------- fails --> INVALID (rejected)
   |
   v
2. Normalize (lowercase, trim spaces, last 10 phone digits)
   |
   v
3. Exact check: SHA-256 fingerprint exists? --yes--> REDUNDANT (rejected)
   |
   v
4. Fuzzy check against indexed candidates (same email / phone / name prefix)
   |-- shared email or phone AND similar name (>= 0.80) --> REDUNDANT (rejected)
   |-- looks similar but email/phone differ             --> FALSE_POSITIVE (distinct person, appended)
   |-- no match                                         --> UNIQUE (appended)
   v
5. INSERT with UNIQUE(fingerprint) constraint (final guard)
   v
6. Write decision to audit_log
```

## Definitions
- **Redundant**: the record already exists, exactly or as a near duplicate (typo, spacing, casing, same email/phone).
- **False positive**: the record was flagged as similar to an existing one (e.g. same name) but verification shows it is a different entity because the identifiers (email/phone) differ. It is kept, not dropped.
- **Invalid**: fails validation (missing field, bad email, short phone).
- **Unique**: no match at all.

## Demo results
| # | Input | Class | Appended |
|---|---|---|---|
| 1 | John Smith, john.smith@mail.com | UNIQUE | Yes |
| 2 | Priya Sharma | UNIQUE | Yes |
| 3 | "john  smith " / JOHN.SMITH@mail.com (formatting differs) | REDUNDANT (exact after normalizing) | No |
| 4 | Jon Smith, same email and phone (typo) | REDUNDANT (near duplicate, sim 0.95) | No |
| 5 | John Smith, different email and phone | FALSE_POSITIVE | Yes |
| 6 | Invalid email | INVALID | No |
| 7 | Missing name | INVALID | No |
| 8 | Priya Sharma again | REDUNDANT | No |
| 9 | Anil Kumar | UNIQUE | Yes |

Result: 9 records in, 4 stored, 3 duplicates blocked, 2 invalid rejected.

## Moving to a real cloud database
Only the `Storage` class touches the database. To use PostgreSQL, MySQL, Firestore or DynamoDB, re-implement `exists()`, `candidates()`, `insert()`, `log()` and keep a unique index on `fingerprint`. The engine logic stays unchanged.

## Assumptions and tuning
- Records are assumed to have `name`, `email`, `phone`. Change `REQUIRED_FIELDS` and `normalize()` for your actual data.
- Thresholds `NAME_SAME_PERSON` (0.80) and `NAME_LOOKS_SIMILAR` (0.90) can be tuned.
