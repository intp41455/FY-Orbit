import sqlite3
import sys

if len(sys.argv) < 3:
    print("Usage: db_test.py <db_path> <write|verify>")
    sys.exit(1)

db_path = sys.argv[1]
action = sys.argv[2]

con = sqlite3.connect(db_path)
cur = con.cursor()

if action == "write":
    cur.execute("CREATE TABLE IF NOT EXISTS _lifecycle_test (id TEXT PRIMARY KEY, content TEXT)")
    cur.execute("INSERT OR REPLACE INTO _lifecycle_test VALUES (?, ?)", ("verify-key-01", "desktop-persistence-check-ok"))
    con.commit()
    print("WRITE_OK")
elif action == "verify":
    row = cur.execute("SELECT content FROM _lifecycle_test WHERE id = ?", ("verify-key-01",)).fetchone()
    if row and row[0] == "desktop-persistence-check-ok":
        print("DATA_OK")
    else:
        print(f"DATA_FAIL: {row}")

con.close()
