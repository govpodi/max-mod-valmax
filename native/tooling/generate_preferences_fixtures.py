"""Pin synthetic preference results from the existing Python implementation."""
from pathlib import Path
from datetime import datetime, timedelta
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "modkit"))
import account_preferences as accounts
import ghost_store

def main():
    rows = []
    for id, name, color in [("10", "Тестовый офис 🚀", "#8b5cf6"), ("2", "Испытание Äß東京", "#0088ff")]:
        row = accounts.defaults(id)
        row.update(displayName=name, color=color)
        row["workHours"].update(enabled=True, days=[7, 2, 5], start="22:30", end="06:15")
        row["ghost"]["hideRead"] = True
        rows.append(row)
    clean = accounts._validated_accounts(rows, "invalid_accounts")
    hours = clean[0]["workHours"]
    times = []
    start = datetime(2026, 10, 12)  # Monday
    for day in range(7):
        for minute in [0, 374, 375, 376, 1349, 1350, 1439]:
            now = start + timedelta(days=day, minutes=minute)
            times.append({"weekday": now.isoweekday(), "minute": minute,
                          "expected": accounts.is_work_time(hours, now)})
    fixture = {"sourceSha256": hashlib.sha256(Path(accounts.__file__).read_bytes()).hexdigest(),
               "input": rows, "validated": clean, "canonical": accounts._canonical(clean).decode(),
               "snapshot": accounts._snapshot(clean), "workTimes": times}
    destination = ROOT / "native/core/tests/fixtures"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "account_preferences.json").write_text(json.dumps(fixture, ensure_ascii=False, indent=2)+"\n",encoding="utf8")
    ghost = {"canonical":ghost_store._canonical(ghost_store.defaults()).decode(),
             "snapshot":ghost_store._snapshot(ghost_store.defaults()),
             "sourceSha256":hashlib.sha256(Path(ghost_store.__file__).read_bytes()).hexdigest()}
    (destination / "ghost_defaults.json").write_text(json.dumps(ghost,indent=2)+"\n",encoding="utf8")
    print(json.dumps({"files":2,"synthetic":True}))

if __name__ == "__main__": main()
