# app.py - Smart RFID Attendance System: kiosk logic + Tkinter GUI.   Run:  python3 app.py
import os
import queue
import sqlite3
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from database import Database, month_range, today

try:
    from PIL import Image, ImageTk                 # shows JPG/PNG photos
except ImportError:
    Image = None

FOLDER = os.path.dirname(os.path.abspath(__file__))
STANDBY_AFTER_MS = 30000                          # nobody near for 30 s -> standby
COLORS = {"ON_TIME": "green", "OK": "green", "LATE": "orange", "EARLY_OUT": "orange"}


class App(tk.Tk):
    def __init__(self, hardware=True):
        super().__init__()
        self.title("Smart RFID Attendance System")
        self.geometry("1100x650")
        self.db = Database(os.path.join(FOLDER, "attendance.db"))
        self.events = queue.Queue()               # hardware threads -> GUI
        if hardware:
            from hardware import InputThread, OutputThread
            self.out = OutputThread(self.events)
            InputThread(self.events).start()
            self.out.start()
        self.state = "STANDBY"                    # STANDBY / READY / RESULT
        self.mode = tk.StringVar(value="AUTO")    # AUTO (sensor) / MANUAL (GUI)
        self.present = False
        self.pin = ""
        self.forced = None                        # IN / OUT chosen with keypad A / B
        self.enrolling = False                    # True while waiting for a new tag
        self.jobs = {}                            # running after() timers

        top = tk.Frame(self, bg="#202124")
        top.pack(fill="x")
        tk.Label(top, text="Smart RFID Attendance", fg="white", bg="#202124",
                 font=("Arial", 16, "bold")).pack(side="left", padx=10, pady=8)
        self.status_lbl = tk.Label(top, fg="white", bg="#1a73e8", font=("Arial", 11, "bold"))
        self.status_lbl.pack(side="left", padx=10)
        self.clock = tk.Label(top, fg="white", bg="#202124", font=("Arial", 13))
        self.clock.pack(side="right", padx=10)
        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=6, pady=6)
        for name, build in (("Dashboard", self.build_dashboard), ("Users", self.build_users),
                            ("Logs", self.build_logs), ("Control", self.build_control)):
            frame = ttk.Frame(tabs, padding=10)
            tabs.add(frame, text=name)
            build(frame)
        self.sleep()
        self.refresh()
        self.after(50, self.check_events)
        self.after(1000, self.tick)

    # ======================= kiosk logic =======================
    def output(self, lcd=None, led=None, beep=None, backlight=None):
        if hasattr(self, "out"):
            self.out.send(lcd, led, beep, backlight)

    def timer(self, name, ms, function):
        """Start (or restart) a named timer that runs in the GUI thread."""
        if name in self.jobs:
            self.after_cancel(self.jobs[name])
        self.jobs[name] = self.after(ms, function)

    def check_events(self):
        """Runs every 50 ms: handles everything the hardware threads have sent."""
        while not self.events.empty():
            kind, value = self.events.get()
            if kind == "card":
                self.card(value)
            elif kind == "key":
                self.key(value)
            elif kind == "presence":
                self.presence(value)
            elif kind == "distance":
                self.info["Distance"].config(text=f"{value:.0f} cm")
            elif kind == "error":
                self.info["Last error"].config(text=value[:60])
        self.after(50, self.check_events)

    def tick(self):
        now = datetime.now()
        self.clock.config(text=now.strftime("%a %d %b %Y  %H:%M:%S"))
        if self.state == "READY" and not self.pin:
            self.output(lcd=("Tap card / PIN", f"{self.forced or 'AUTO':5}{now:%H:%M:%S}"))
        if now.second == 0:
            self.refresh()                        # absences change as time passes
        self.after(1000, self.tick)

    def set_state(self, state):
        self.state = state
        self.status_lbl.config(text=f"  {self.mode.get()} | {state}  ",
                               bg="#1a73e8" if self.mode.get() == "AUTO" else "#e37400")

    def wake(self):
        if self.state == "STANDBY":
            self.output(backlight=True, beep="click")
        self.set_state("READY")
        self.pin = ""
        self.output(lcd=("Tap card / PIN", ""), led="blue")
        self.timer("idle", STANDBY_AFTER_MS, self.idle_check)

    def sleep(self):
        self.set_state("STANDBY")
        self.output(lcd=("", ""), led="standby", backlight=False)

    def idle_check(self):
        if self.mode.get() == "AUTO" and not self.present and self.state == "READY":
            self.sleep()
        elif self.state != "STANDBY":
            self.timer("idle", STANDBY_AFTER_MS, self.idle_check)

    def presence(self, near):
        self.present = near
        self.info["Presence"].config(text="Person near" if near else "Nobody")
        if near and self.mode.get() == "AUTO" and self.state == "STANDBY":
            self.wake()

    def card(self, uid):
        self.info["Last card"].config(text=uid)
        if self.enrolling:                        # User tab is waiting for a new tag
            self.enrolling = False
            self.form["Tag"].set(uid)
            self.scan_btn.config(text="Scan tag")
            self.output(lcd=("Tag captured", uid), led="green", beep="ok")
            return
        if self.state == "STANDBY":
            if self.mode.get() == "MANUAL":
                return                            # manual mode: only the GUI wakes it
            self.wake()
        user = self.db.find(uid=uid)
        if user:
            self.check_in(user, "RFID", uid)
        else:
            self.reject(None, "Unknown card", "RFID", uid)

    def key(self, k):
        if self.state == "STANDBY":
            if self.mode.get() == "MANUAL":
                return
            self.wake()
        self.output(beep="click")
        self.timer("idle", STANDBY_AFTER_MS, self.idle_check)
        if k in "AB":                             # A = check-in, B = check-out
            self.forced = "IN" if k == "A" else "OUT"
            self.output(lcd=(f"Mode: CHECK-{self.forced}", "Tap card / PIN"))
            self.timer("forced", 10000, lambda: setattr(self, "forced", None))
        elif k == "C":
            self.forced = None
        elif k in "0123456789" and len(self.pin) < 6:
            self.pin += k
        elif k == "*":
            self.pin = self.pin[:-1]
        elif k == "D":
            self.pin = ""
        elif k == "#":
            pin, self.pin = self.pin, ""
            user = self.db.find(pin=pin) if len(pin) >= 4 else None
            if user:
                self.check_in(user, "PIN", "****")
            else:
                self.reject(None, "Wrong PIN", "PIN", "****")
            return
        if self.pin:
            self.output(lcd=("Enter PIN, # =OK", "PIN: " + "*" * len(self.pin)))

    def check_in(self, user, method, credential):
        now = datetime.now()
        forced = self.forced or (self.direction.get() if self.mode.get() == "MANUAL" and
                                 self.direction.get() != "Auto" else None)
        accepted, event, status = self.db.decide(user, now, forced)
        if not accepted:
            self.reject(user, status, method, credential)
            return
        self.db.record(user["id"], event, status, method, credential, now)
        self.forced = None
        greeting = "Welcome," if event == "IN" else "Goodbye,"
        page2 = (f"ID:{user['id_number']}", f"{now:%H:%M:%S} {status.replace('_', ' ')}")
        self.output(lcd=(f"{greeting} {user['name'].split()[0]}", f"{event} - ACCEPTED"),
                    led="green", beep="ok")
        self.show_result(user, event, status, now, COLORS.get(status, "green"), page2)

    def reject(self, user, reason, method, credential):
        now = datetime.now()
        self.forced = None
        self.db.record(user["id"] if user else None, "DENIED", reason, method, credential, now)
        who = user["id_number"] if user else credential
        self.output(lcd=("ACCESS DENIED", reason), led="red", beep="error")
        page2 = (f"ID:{who}", f"{now:%H:%M:%S} DENIED")
        self.show_result(user, "DENIED", reason, now, "red", page2)

    def show_result(self, user, event, status, now, color, page2):
        self.set_state("RESULT")
        self.timer("page2", 1500, lambda: self.output(lcd=page2))
        self.timer("result", 3000, self.wake)     # back to READY after 3 s
        self.card_name.config(text=user["name"] if user else "Unknown", fg=color)
        values = (user["id_number"] if user else "-", user["role"] if user else "-",
                  event, now.strftime("%Y-%m-%d %H:%M:%S"), status)
        for label, value in zip(self.card_fields, values):
            label.config(text=value)
        self.card_fields[4].config(fg=color)
        self.photo_img = None
        if user and user["photo"] and Image and os.path.exists(user["photo"]):
            picture = Image.open(user["photo"])
            picture.thumbnail((130, 160))
            self.photo_img = ImageTk.PhotoImage(picture)
        if self.photo_img:                        # size in pixels when showing a picture
            self.photo.config(image=self.photo_img, text="", width=130, height=160)
        else:                                     # size in characters when showing text
            self.photo.config(image="", text="No photo", width=18, height=9)
        self.refresh()

    # ======================= GUI tabs =======================
    def build_dashboard(self, tab):
        row = ttk.Frame(tab)
        row.pack(fill="x")
        self.counters = []
        for i, (title, color) in enumerate((("TOTAL", "#1a73e8"), ("PRESENT", "green"),
                                            ("LATE", "orange"), ("ABSENT", "red"))):
            box = tk.Frame(row, bg="white", bd=1, relief="solid")
            box.grid(row=0, column=i, sticky="ew", padx=5)
            row.columnconfigure(i, weight=1)
            tk.Label(box, text=title, bg="white", font=("Arial", 11, "bold")).pack(pady=(8, 0))
            number = tk.Label(box, text="0", bg="white", fg=color, font=("Arial", 32, "bold"))
            number.pack(pady=(0, 8))
            self.counters.append(number)
        card = tk.Frame(tab, bg="white", bd=1, relief="solid")
        card.pack(fill="x", pady=10, padx=5)
        self.photo = tk.Label(card, text="No photo", bg="#e8eaed", width=18, height=9)
        self.photo.pack(side="left", padx=10, pady=10)
        info = tk.Frame(card, bg="white")
        info.pack(side="left", fill="both", expand=True)
        self.card_name = tk.Label(info, text="Waiting for scan", bg="white",
                                  font=("Arial", 20, "bold"))
        self.card_name.grid(row=0, column=0, columnspan=2, sticky="w", pady=5)
        self.card_fields = []
        for r, name in enumerate(("ID Number", "Role", "Event", "Time", "Status"), start=1):
            tk.Label(info, text=name, bg="white", fg="gray").grid(row=r, column=0, sticky="w")
            value = tk.Label(info, text="-", bg="white", font=("Arial", 12, "bold"))
            value.grid(row=r, column=1, sticky="w", padx=10)
            self.card_fields.append(value)
        self.recent = self.make_table(tab, ("Time", "Name", "ID Number", "Event", "Status",
                                            "Method"), 8)

    def build_users(self, tab):
        self.user_table = self.make_table(tab, ("ID Number", "Name", "Role", "Tag", "PIN"), 8,
                                          side="left")
        self.user_table.bind("<<TreeviewSelect>>", self.load_user)
        form = ttk.LabelFrame(tab, text=" User details ", padding=10)
        form.pack(side="left", fill="y", padx=10)
        self.form = {name: tk.StringVar() for name in ("ID Number", "Name", "Role", "Tag",
                                                       "PIN", "Photo")}
        self.user_id = None
        for r, name in enumerate(self.form):
            ttk.Label(form, text=name).grid(row=r, column=0, sticky="w", pady=3)
            if name == "Role":
                widget = ttk.Combobox(form, textvariable=self.form[name], state="readonly",
                                      values=("Student", "Employee", "Admin", "Guest"))
            else:
                widget = ttk.Entry(form, textvariable=self.form[name], width=26,
                                   show="*" if name == "PIN" else "")
            widget.grid(row=r, column=1, pady=3, sticky="w")
        self.scan_btn = ttk.Button(form, text="Scan tag", command=self.scan_tag)
        self.scan_btn.grid(row=3, column=2, padx=4)
        ttk.Button(form, text="Browse", command=self.pick_photo).grid(row=5, column=2, padx=4)
        buttons = ttk.Frame(form)
        buttons.grid(row=6, column=0, columnspan=3, pady=10)
        for text, command in (("New", self.new_user), ("Save", self.save_user),
                              ("Remove", self.remove_user)):
            ttk.Button(buttons, text=text, command=command).pack(side="left", padx=3)
        shift = ttk.LabelFrame(form, text=" Work shift ", padding=8)
        shift.grid(row=7, column=0, columnspan=3, sticky="ew", pady=10)
        self.shift_vars = [tk.StringVar(value=v) for v in map(str, self.db.shift())]
        for c, (name, var) in enumerate(zip(("Start", "End", "Grace min"), self.shift_vars)):
            ttk.Label(shift, text=name).grid(row=0, column=c)
            ttk.Entry(shift, textvariable=var, width=8).grid(row=1, column=c, padx=3)
        ttk.Button(shift, text="Save shift", command=self.save_shift).grid(row=2, column=0,
                                                                         columnspan=3, pady=5)
        self.new_user()

    def build_logs(self, tab):
        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        self.log_from = tk.StringVar(value=today().isoformat())
        self.log_to = tk.StringVar(value=today().isoformat())
        self.log_user, self.log_event = tk.StringVar(value="All"), tk.StringVar(value="All")
        ttk.Label(bar, text="From").pack(side="left")
        ttk.Entry(bar, textvariable=self.log_from, width=11).pack(side="left", padx=3)
        ttk.Label(bar, text="To").pack(side="left")
        ttk.Entry(bar, textvariable=self.log_to, width=11).pack(side="left", padx=3)
        self.log_user_box = ttk.Combobox(bar, textvariable=self.log_user, width=22,
                                         state="readonly")
        self.log_user_box.pack(side="left", padx=3)
        ttk.Combobox(bar, textvariable=self.log_event, width=8, state="readonly",
                     values=("All", "IN", "OUT", "DENIED")).pack(side="left", padx=3)
        ttk.Button(bar, text="Show", command=self.show_logs).pack(side="left", padx=3)
        self.log_table = self.make_table(tab, ("Timestamp", "ID Number", "Name", "Role",
                                               "Event", "Status", "Method"), 14)
        export = ttk.Frame(tab)
        export.pack(fill="x")
        ttk.Label(export, text="Export report for the From date:").pack(side="left")
        for text, period, kind in (("Daily CSV", "day", "csv"), ("Daily Excel", "day", "xlsx"),
                                   ("Monthly CSV", "month", "csv"),
                                   ("Monthly Excel", "month", "xlsx")):
            ttk.Button(export, text=text, command=lambda p=period, k=kind: self.export(p, k)
                       ).pack(side="left", padx=3)

    def build_control(self, tab):
        box = ttk.LabelFrame(tab, text=" Mode ", padding=8)
        box.pack(fill="x")
        ttk.Radiobutton(box, text="Automated - the distance sensor wakes / sleeps the kiosk",
                        variable=self.mode, value="AUTO", command=self.mode_changed
                        ).pack(anchor="w")
        ttk.Radiobutton(box, text="Manual override - control the kiosk from these buttons",
                        variable=self.mode, value="MANUAL", command=self.mode_changed
                        ).pack(anchor="w")
        manual = ttk.LabelFrame(tab, text=" Manual override ", padding=8)
        manual.pack(fill="x", pady=8)
        ttk.Button(manual, text="Wake kiosk", command=self.manual(self.wake)).grid(row=0, column=0)
        ttk.Button(manual, text="Standby", command=self.manual(self.sleep)).grid(row=0, column=1)
        ttk.Label(manual, text="Scan direction").grid(row=1, column=0, pady=4)
        self.direction = ttk.Combobox(manual, values=("Auto", "IN", "OUT"), width=8,
                                      state="readonly")
        self.direction.set("Auto")
        self.direction.grid(row=1, column=1)
        self.mark_user = ttk.Combobox(manual, width=26, state="readonly")
        self.mark_user.grid(row=2, column=0, columnspan=2, pady=4)
        ttk.Button(manual, text="Check IN", command=lambda: self.mark("IN")).grid(row=2, column=2)
        ttk.Button(manual, text="Check OUT", command=lambda: self.mark("OUT")).grid(row=2, column=3)
        tests = ttk.LabelFrame(tab, text=" Output tests ", padding=8)
        tests.pack(fill="x")
        for i, (text, args) in enumerate((("LED green", {"led": "green"}),
                                          ("LED red", {"led": "red"}),
                                          ("LED blue", {"led": "blue"}),
                                          ("Beep OK", {"beep": "ok"}),
                                          ("Beep error", {"beep": "error"}))):
            ttk.Button(tests, text=text, command=lambda a=args: self.output(**a)
                       ).grid(row=0, column=i, padx=2)
        self.lcd_text = tk.StringVar()
        ttk.Entry(tests, textvariable=self.lcd_text, width=18).grid(row=1, column=0, columnspan=2)
        ttk.Button(tests, text="Send to LCD", command=lambda: self.output(
            lcd=(self.lcd_text.get()[:16], ""), backlight=True)).grid(row=1, column=2, pady=4)
        live = ttk.LabelFrame(tab, text=" Live hardware status ", padding=8)
        live.pack(fill="x", pady=8)
        self.info = {}
        for r, name in enumerate(("Distance", "Presence", "Last card", "Last error")):
            ttk.Label(live, text=name).grid(row=r, column=0, sticky="w")
            self.info[name] = ttk.Label(live, text="-", font=("Arial", 11, "bold"))
            self.info[name].grid(row=r, column=1, sticky="w", padx=10)

    def make_table(self, parent, columns, height, side="top"):
        table = ttk.Treeview(parent, columns=columns, show="headings", height=height)
        for c in columns:
            table.heading(c, text=c)
            table.column(c, width=160 if c == "Timestamp" else 110)
        table.pack(side=side, fill="both", expand=True)
        return table

    # ======================= GUI actions =======================
    def refresh(self):
        for label, value in zip(self.counters, self.db.summary(datetime.now())):
            label.config(text=value)
        self.recent.delete(*self.recent.get_children())
        for r in self.db.logs(today(), today())[:20]:
            self.recent.insert("", "end", values=(r["ts"][11:], r["name"] or "(unknown)",
                                                  r["id_number"] or r["credential"],
                                                  r["event"], r["status"], r["method"]))
        self.user_table.delete(*self.user_table.get_children())
        self.user_names = {}
        for u in self.db.users():
            self.user_table.insert("", "end", iid=u["id"], values=(
                u["id_number"], u["name"], u["role"], u["uid"] or "-", "set" if u["pin"] else "-"))
            self.user_names[f"{u['name']} ({u['id_number']})"] = u["id"]
        self.log_user_box.config(values=["All"] + list(self.user_names))
        self.mark_user.config(values=list(self.user_names))

    def mode_changed(self):
        self.set_state(self.state)
        if self.mode.get() == "AUTO":
            self.direction.set("Auto")
            if self.present and self.state == "STANDBY":
                self.wake()

    def manual(self, action):
        """Wake / Standby buttons only work in manual override mode."""
        return lambda: action() if self.mode.get() == "MANUAL" else messagebox.showinfo(
            "Manual override", "Switch to Manual override mode first.")

    def mark(self, event):
        user_id = self.user_names.get(self.mark_user.get())
        if self.mode.get() != "MANUAL" or not user_id:
            messagebox.showinfo("Manual override", "Choose Manual override mode and a user.")
            return
        self.forced = event
        self.check_in(self.db.find(user_id=user_id), "MANUAL", "operator")

    def scan_tag(self):
        self.enrolling = True
        self.scan_btn.config(text="Tap card now...")
        self.output(lcd=("ENROLL: tap new", "card or fob..."), led="blue", backlight=True)

    def pick_photo(self):
        path = filedialog.askopenfilename(filetypes=[("Pictures", "*.jpg *.jpeg *.png")])
        if path:
            self.form["Photo"].set(path)

    def new_user(self):
        self.user_id = None
        for var in self.form.values():
            var.set("")
        self.form["Role"].set("Student")

    def load_user(self, event=None):
        selected = self.user_table.selection()
        if selected:
            u = self.db.find(user_id=int(selected[0]))
            self.user_id = u["id"]
            for name, key in (("ID Number", "id_number"), ("Name", "name"), ("Role", "role"),
                              ("Tag", "uid"), ("Photo", "photo")):
                self.form[name].set(u[key] or "")
            self.form["PIN"].set("")

    def save_user(self):
        f = {name: var.get().strip() for name, var in self.form.items()}
        if not f["ID Number"] or not f["Name"]:
            messagebox.showwarning("Missing", "ID number and name are required.")
            return
        if f["PIN"] and not (f["PIN"].isdigit() and 4 <= len(f["PIN"]) <= 6):
            messagebox.showwarning("PIN", "The PIN must be 4 to 6 digits.")
            return
        try:
            self.db.save_user(self.user_id, f["ID Number"], f["Name"], f["Role"], f["Tag"],
                              f["PIN"], f["Photo"])
        except sqlite3.IntegrityError:
            messagebox.showwarning("Not saved", "That ID number, tag or PIN is already used.")
            return
        self.new_user()
        self.refresh()

    def remove_user(self):
        if self.user_id and messagebox.askyesno("Remove", "Remove this user?"):
            self.db.remove_user(self.user_id)
            self.new_user()
            self.refresh()

    def save_shift(self):
        start, end, grace = (v.get().strip() for v in self.shift_vars)
        try:
            datetime.strptime(start, "%H:%M"), datetime.strptime(end, "%H:%M"), int(grace)
        except ValueError:
            messagebox.showwarning("Shift", "Use HH:MM for times (e.g. 08:00) and a number "
                                            "for grace minutes.")
            return
        self.db.save_shift(start, end, int(grace))
        messagebox.showinfo("Shift", "Work shift saved.")
        self.refresh()

    def dates(self):
        try:
            return (datetime.strptime(self.log_from.get(), "%Y-%m-%d").date(),
                    datetime.strptime(self.log_to.get(), "%Y-%m-%d").date())
        except ValueError:
            messagebox.showwarning("Date", "Type dates like 2026-09-25.")

    def show_logs(self):
        dates = self.dates()
        if dates:
            event = None if self.log_event.get() == "All" else self.log_event.get()
            rows = self.db.logs(*dates, self.user_names.get(self.log_user.get()), event)
            self.log_table.delete(*self.log_table.get_children())
            for r in rows:
                self.log_table.insert("", "end", values=[r[k] or "" for k in r.keys()][:7])

    def export(self, period, kind):
        dates = self.dates()
        if not dates:
            return
        first, last = (dates[0], dates[0]) if period == "day" else month_range(dates[0])
        os.makedirs(os.path.join(FOLDER, "exports"), exist_ok=True)
        label = str(first) if period == "day" else f"{first:%Y-%m}"
        name = f"attendance_{label}.{kind}"
        path = os.path.join(FOLDER, "exports", name)
        count = self.db.export(self.db.logs(first, last), path)
        messagebox.showinfo("Exported", f"{count} records saved to\n{path}")


if __name__ == "__main__":
    App().mainloop()
