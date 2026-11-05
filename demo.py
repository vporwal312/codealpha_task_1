from collections import Counter
from dedup_system import DedupEngine, Storage

incoming = [
    {"name": "John Smith",   "email": "john.smith@mail.com",  "phone": "+91 98765 43210"},  # unique
    {"name": "Priya Sharma", "email": "priya@mail.com",       "phone": "9123456780"},        # unique
    {"name": "john  smith ", "email": "JOHN.SMITH@mail.com",  "phone": "9876543210"},        # exact dup after normalizing
    {"name": "Jon Smith",    "email": "john.smith@mail.com",  "phone": "9876543210"},        # near duplicate (typo)
    {"name": "John Smith",   "email": "john.s2@other.com",    "phone": "9000011111"},        # same name, different person -> false positive
    {"name": "Rahul Verma",  "email": "not-an-email",         "phone": "9999988888"},        # invalid email
    {"name": "",             "email": "x@y.com",              "phone": "9999988888"},        # missing name
    {"name": "Priya Sharma", "email": "priya@mail.com",       "phone": "9123456780"},        # exact duplicate
    {"name": "Anil Kumar",   "email": "anil@mail.com",        "phone": "9812345670"},        # unique
]

engine = DedupEngine(Storage("records.db"))
results = engine.process_batch(incoming)

print(f"{'#':<3}{'CLASS':<16}{'APPENDED':<10}REASON")
for i, (rec, r) in enumerate(zip(incoming, results), 1):
    print(f"{i:<3}{r.classification:<16}{str(r.inserted):<10}{r.reason}")

print("\nSummary:", dict(Counter(r.classification for r in results)))
print("\nRecords now in database:")
for row in engine.store.all_records():
    print("  ", row)
