# database.py - storage (SQLite), attendance rules and CSV / Excel export
import csv
import hashlib
import sqlite3
from datetime import date, datetime, timedelta

DUPLICATE_SECONDS = 60             # same person scanning again within 60 s = duplicate


def pin_hash(pin):
    return hashlib.sha256(pin.encode()).hexdigest()


class Database:
    def __init__(self, path="attendance.db"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, id_number TEXT UNIQUE, name TEXT, role TEXT,
                uid TEXT UNIQUE, pin TEXT UNIQUE, photo TEXT, active INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY, user_id INTEGER, ts TEXT, event TEXT,
                status TEXT, method TEXT, credential TEXT);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            INSERT OR IGNORE INTO settings VALUES ('start', '08:00'), ('end', '17:00'),
                                                   ('grace', '10');
        """)
        self.db.commit()
      
    # ---------------- work shift (start, end, grace minutes) ----------------
    def shift(self):
        s = dict(self.db.execute("SELECT key, value FROM settings").fetchall())
        return s["start"], s["end"], int(s["grace"])

    def save_shift(self, start, end, grace):
        for key, value in (("start", start), ("end", end), ("grace", str(grace))):
            self.db.execute("UPDATE settings SET value=? WHERE key=?", (value, key))
        self.db.commit()

    # ---------------- users ----------------
    def users(self):
        return self.db.execute("SELECT * FROM users WHERE active=1 ORDER BY name").fetchall()

    def find(self, uid=None, pin=None, user_id=None):
        if uid:
            sql, value = "uid=?", uid
        elif pin:
            sql, value = "pin=?", pin_hash(pin)
        else:
            sql, value = "id=?", user_id
        return self.db.execute(f"SELECT * FROM users WHERE active=1 AND {sql}",
                               (value,)).fetchone()

    def save_user(self, user_id, id_number, name, role, uid, pin, photo):
        """Raises sqlite3.IntegrityError if the ID number, tag or PIN is already used."""
        pin_value = pin_hash(pin) if pin else None
        if user_id:
            self.db.execute("UPDATE users SET id_number=?, name=?, role=?, uid=?, photo=? "
                            "WHERE id=?", (id_number, name, role, uid or None, photo, user_id))
            if pin:
                self.db.execute("UPDATE users SET pin=? WHERE id=?", (pin_value, user_id))
        else:
            self.db.execute("INSERT INTO users (id_number, name, role, uid, pin, photo) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (id_number, name, role, uid or None, pin_value, photo))
        self.db.commit()

    def remove_user(self, user_id):
        # keeps the attendance history, but the tag and PIN stop working
        self.db.execute("UPDATE users SET active=0, uid=NULL, pin=NULL WHERE id=?", (user_id,))
        self.db.commit()
      
    # ---------------- attendance rules ----------------
    def decide(self, user, now, forced=None):
        """Returns (accepted, event, status). Status is ON_TIME / LATE / OK / EARLY_OUT,
        or the reason for rejection (max 16 characters, to fit the LCD)."""
        last = self.db.execute("SELECT event, ts FROM attendance WHERE user_id=? AND "
                               "event IN ('IN','OUT') AND ts>=? ORDER BY id DESC LIMIT 1",
                               (user["id"], now.strftime("%Y-%m-%d"))).fetchone()
        event = forced or ("OUT" if last and last["event"] == "IN" else "IN")
        if last:
            seconds = (now - datetime.fromisoformat(last["ts"])).total_seconds()
            if seconds < DUPLICATE_SECONDS:
                return False, event, f"Scanned {int(seconds)}s ago"
            if last["event"] == event:
                return False, event, f"Already {event}"
        elif event == "OUT":
            return False, event, "No IN today"
        start, end, grace = self.shift()
        if event == "IN":
            limit = datetime.combine(now.date(), datetime.strptime(start, "%H:%M").time())
            status = "LATE" if now > limit + timedelta(minutes=grace) else "ON_TIME"
        else:
            finish = datetime.combine(now.date(), datetime.strptime(end, "%H:%M").time())
            status = "EARLY_OUT" if now < finish else "OK"
        return True, event, status

    def record(self, user_id, event, status, method, credential, now):
        self.db.execute("INSERT INTO attendance (user_id, ts, event, status, method, credential) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (user_id, now.strftime("%Y-%m-%d %H:%M:%S"), event, status,
                         method, credential))
        self.db.commit()
      
    # ---------------- dashboard counters ----------------
    def summary(self, now):
        today = now.strftime("%Y-%m-%d")
        total = len(self.users())
        first_in = self.db.execute(       # each person's first IN today (MIN picks that row)
            "SELECT a.user_id, a.status, MIN(a.id) FROM attendance a JOIN users u "
            "ON u.id=a.user_id WHERE u.active=1 AND a.event='IN' AND a.ts>=? "
            "GROUP BY a.user_id", (today,)).fetchall()
        present = len(first_in)
        late = sum(1 for r in first_in if r["status"] == "LATE")
        start, _, grace = self.shift()
        cutoff = datetime.combine(now.date(), datetime.strptime(start, "%H:%M").time())
        absent = total - present if now > cutoff + timedelta(minutes=grace) else 0
        return total, present, late, absent

    # ---------------- log viewer + export ----------------
    def logs(self, first, last, user_id=None, event=None):
        sql = ("SELECT a.ts, u.id_number, u.name, u.role, a.event, a.status, a.method, "
               "a.credential FROM attendance a LEFT JOIN users u ON u.id=a.user_id "
               "WHERE a.ts>=? AND a.ts<?")
        args = [first.isoformat(), (last + timedelta(days=1)).isoformat()]
        if user_id:
            sql, args = sql + " AND a.user_id=?", args + [user_id]
        if event:
            sql, args = sql + " AND a.event=?", args + [event]
        return self.db.execute(sql + " ORDER BY a.id DESC", args).fetchall()

    def export(self, rows, path):
        header = ["Timestamp", "ID Number", "Name", "Role", "Event", "Status", "Method",
                  "Credential"]
        data = [[r[k] or "" for k in r.keys()] for r in reversed(rows)]   # oldest first
        if path.endswith(".csv"):
            with open(path, "w", newline="") as f:
                csv.writer(f).writerows([header] + data)
        else:
            from openpyxl import Workbook
            from openpyxl.styles import Font
            book = Workbook()
            sheet = book.active
            sheet.title = "Attendance"
            sheet.append(header)
            for row in data:
                sheet.append(row)
            for cell in sheet[1]:
                cell.font = Font(bold=True)
            for column, width in zip("ABCDEFGH", (20, 12, 22, 10, 8, 16, 9, 12)):
                sheet.column_dimensions[column].width = width
            book.save(path)
        return len(data)


def month_range(day):
    first = day.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return first, last


def today():
    return date.today()
