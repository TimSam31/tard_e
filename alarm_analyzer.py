"""
Alarm Activity Analyzer
=======================

A tool for Network Operation Center engineers to determine which alarms were
supposed to be ACTIVE at a single point in time, or during a time range, based
on imported history and/or active alarm exports from network elements.

Core logic lives in plain functions (no GUI dependency) so it can be unit
tested / scripted.  A Tkinter GUI on top provides import, query, display and
CSV export.

Anchor columns used for calculation (matched by header, case-insensitive):
    ME, Alarm Code Name, Occurrence Time, Clear Time, Position

Clear Time is optional: a blank Clear Time means the alarm is still ACTIVE, and
the current time (``now``) is substituted as its effective clear time.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import pandas as pd


# --------------------------------------------------------------------------- #
# Core logic (GUI-independent)
# --------------------------------------------------------------------------- #

# Logical anchor name -> list of accepted header spellings (normalized compare).
ANCHOR_ALIASES = {
    "ME": ["me"],
    "Alarm Code Name": ["alarm code name", "alarm name", "alarm code"],
    "Occurrence Time": ["occurrence time", "occur time", "raise time", "event time"],
    "Clear Time": ["clear time", "cleared time", "recovery time"],
    "Position": ["position", "location"],
}

# Anchors that MUST be present for the calculation to work.
REQUIRED_ANCHORS = ["ME", "Alarm Code Name", "Occurrence Time", "Position"]

REMARK_COL = "Analysis Remark"
EFFECTIVE_CLEAR_COL = "Effective Clear Time"
SOURCE_COL = "Source File"

DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
)


def _normalize(text) -> str:
    return str(text).strip().lower()


def resolve_columns(df: pd.DataFrame) -> dict:
    """Map each logical anchor to the real column name found in ``df``.

    Returns a dict {logical_name: actual_column_or_None}.  Raises ValueError if
    a required anchor cannot be located.
    """
    normalized = {_normalize(c): c for c in df.columns}
    colmap: dict[str, str | None] = {}
    for logical, aliases in ANCHOR_ALIASES.items():
        found = None
        for alias in aliases:
            if alias in normalized:
                found = normalized[alias]
                break
        colmap[logical] = found

    missing = [a for a in REQUIRED_ANCHORS if colmap.get(a) is None]
    if missing:
        raise ValueError(
            "Imported data is missing required column(s): "
            + ", ".join(missing)
            + ".\nFound columns: "
            + ", ".join(map(str, df.columns))
        )
    return colmap


def parse_datetime(value) -> pd.Timestamp | None:
    """Parse a single cell into a Timestamp, or None if blank/unparseable."""
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return pd.Timestamp(value)
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "nat", "none"}:
        return None
    for fmt in DATE_FORMATS:
        try:
            return pd.Timestamp(datetime.strptime(text, fmt))
        except ValueError:
            continue
    # Last resort: let pandas guess.
    ts = pd.to_datetime(text, errors="coerce")
    return None if pd.isna(ts) else pd.Timestamp(ts)


def load_alarms(paths) -> pd.DataFrame:
    """Read one or more alarm files (.xlsx/.xls/.csv) and concatenate them.

    All original columns are preserved.  A ``Source File`` column records the
    file each row came from.  Differing column sets across files are unioned.
    """
    if isinstance(paths, str):
        paths = [paths]
    frames = []
    for path in paths:
        ext = os.path.splitext(path)[1].lower()
        if ext in (".xlsx", ".xls"):
            frame = pd.read_excel(path, dtype=str)
        elif ext == ".csv":
            frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        else:
            raise ValueError(f"Unsupported file type: {path}")
        frame.columns = [str(c).strip() for c in frame.columns]
        frame[SOURCE_COL] = os.path.basename(path)
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def analyze(
    df: pd.DataFrame,
    start: datetime,
    end: datetime | None = None,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Return the subset of alarms that were active at ``start`` (point query)
    or that overlapped the window ``[start, end]`` (range query).

    A ``Analysis Remark`` and ``Effective Clear Time`` column are appended.
    For range queries the remark flags alarms that occurred and/or cleared
    midway through the window.

    ``now`` is substituted as the effective clear time of still-active alarms
    (blank Clear Time).  Defaults to the wall clock.
    """
    if df is None or df.empty:
        return df.iloc[0:0].copy() if df is not None else pd.DataFrame()

    colmap = resolve_columns(df)
    occ_col = colmap["Occurrence Time"]
    clr_col = colmap["Clear Time"]  # may be None

    start = pd.Timestamp(start)
    end = pd.Timestamp(end) if end is not None else None
    now = pd.Timestamp(now) if now is not None else pd.Timestamp(datetime.now())
    if end is not None and end < start:
        start, end = end, start

    window_hi = end if end is not None else start

    remarks: list[str] = []
    eff_clears: list = []
    keep: list[bool] = []

    for _, row in df.iterrows():
        occ = parse_datetime(row[occ_col])
        raw_clear = parse_datetime(row[clr_col]) if clr_col else None
        is_active = raw_clear is None
        eff_clear = raw_clear if raw_clear is not None else now

        if occ is None:
            # Cannot place this alarm on the timeline -> exclude but explain.
            keep.append(False)
            remarks.append("Excluded: unparseable Occurrence Time")
            eff_clears.append("")
            continue

        # Active interval is [occ, eff_clear].  Overlaps query window
        # [start, window_hi] when occ <= window_hi and eff_clear >= start.
        overlaps = (occ <= window_hi) and (eff_clear >= start)
        keep.append(overlaps)
        eff_clears.append(eff_clear.strftime("%Y-%m-%d %H:%M:%S"))

        if not overlaps:
            remarks.append("Not active in query period")
            continue

        if end is None:
            # Single point query.
            if is_active:
                remarks.append("Active at query time (uncleared / active alarm)")
            else:
                remarks.append("Active at query time")
        else:
            occurred_midway = occ > start
            cleared_midway = (not is_active) and (eff_clear < end)
            tags = []
            if occurred_midway:
                tags.append("occurred midway (after window start)")
            if cleared_midway:
                tags.append("cleared midway (before window end)")
            if is_active:
                tags.append("still active / uncleared")
            if not tags:
                remarks.append("Active throughout window")
            else:
                remarks.append("; ".join(tags))

    out = df.copy()
    out[EFFECTIVE_CLEAR_COL] = eff_clears
    out[REMARK_COL] = remarks
    out = out[pd.Series(keep, index=out.index)].reset_index(drop=True)

    # Reorder so anchor + analysis columns lead, the rest follow.
    lead = [colmap["ME"], colmap["Alarm Code Name"], occ_col]
    if clr_col:
        lead.append(clr_col)
    lead += [EFFECTIVE_CLEAR_COL, colmap["Position"], REMARK_COL]
    ordered = lead + [c for c in out.columns if c not in lead]
    return out[ordered]


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #

def launch_gui():  # pragma: no cover - requires a display
    import calendar
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class DateTimePicker(ttk.Frame):
        """Visual date/time chooser using spinboxes - no typing of a fixed
        format required.  Year / Month / Day / Hour / Minute / Second each have
        up/down arrows.  ``on_change`` is fired whenever any field changes so the
        owner can re-validate live.
        """

        MONTHS = list(calendar.month_abbr)[1:]  # Jan..Dec

        def __init__(self, master, on_change=None):
            super().__init__(master)
            self._on_change = on_change
            now = datetime.now()

            self.year = tk.IntVar(value=now.year)
            self.month = tk.IntVar(value=now.month)
            self.day = tk.IntVar(value=now.day)
            self.hour = tk.IntVar(value=now.hour)
            self.minute = tk.IntVar(value=now.minute)
            self.second = tk.IntVar(value=0)

            self._spins = []

            def add(label, var, frm, to, width=4, fmt=None):
                ttk.Label(self, text=label).pack(side="left", padx=(6, 1))
                sb = ttk.Spinbox(self, from_=frm, to=to, textvariable=var,
                                 width=width, wrap=True, command=self._changed)
                if fmt:
                    sb.configure(format=fmt)
                sb.pack(side="left")
                var.trace_add("write", lambda *_: self._changed())
                self._spins.append(sb)
                return sb

            add("Y", self.year, 2000, 2100, width=5)
            # Month shown as number but kept simple/robust.
            add("M", self.month, 1, 12, width=3)
            self._day_spin = add("D", self.day, 1, 31, width=3)
            add("H", self.hour, 0, 23, width=3)
            add("Min", self.minute, 0, 59, width=3)
            add("Sec", self.second, 0, 59, width=3)

            ttk.Button(self, text="Now", width=5,
                       command=self.set_now).pack(side="left", padx=(8, 0))

        def _clamp_day(self):
            """Keep the day within the selected month's valid range."""
            try:
                max_day = calendar.monthrange(self.year.get(), self.month.get())[1]
            except (tk.TclError, ValueError):
                return
            self._day_spin.configure(to=max_day)
            if self.day.get() > max_day:
                self.day.set(max_day)

        def _changed(self):
            self._clamp_day()
            if self._on_change:
                self._on_change()

        def set_now(self):
            now = datetime.now()
            self.year.set(now.year)
            self.month.set(now.month)
            self.day.set(now.day)
            self.hour.set(now.hour)
            self.minute.set(now.minute)
            self.second.set(now.second)

        def set_enabled(self, enabled: bool):
            state = "normal" if enabled else "disabled"
            for sb in self._spins:
                sb.configure(state=state)

        def get_datetime(self) -> datetime:
            """Build a datetime from the fields; raises ValueError if invalid."""
            return datetime(
                int(self.year.get()), int(self.month.get()), int(self.day.get()),
                int(self.hour.get()), int(self.minute.get()), int(self.second.get()),
            )

    class AlarmGUI:
        def __init__(self, root):
            self.root = root
            self.root.title("NOC Alarm Activity Analyzer")
            self.root.geometry("1200x720")
            self.files: list[str] = []
            self.raw_df: pd.DataFrame | None = None
            self.result_df: pd.DataFrame | None = None
            self._build()
            self._validate()

        def _build(self):
            # --- Menu bar ---
            menubar = tk.Menu(self.root)
            filemenu = tk.Menu(menubar, tearoff=0)
            filemenu.add_command(label="Add alarm file(s)...", command=self.import_files)
            filemenu.add_command(label="Clear imported files", command=self.clear_files)
            filemenu.add_separator()
            filemenu.add_command(label="Export result to CSV...", command=self.export_csv)
            filemenu.add_separator()
            filemenu.add_command(label="Exit", command=self.root.quit)
            menubar.add_cascade(label="File", menu=filemenu)
            self.root.config(menu=menubar)

            # --- Imported files panel ---
            files_frame = ttk.LabelFrame(self.root, text="Imported files (select multiple at once, or add more)")
            files_frame.pack(fill="x", padx=8, pady=6)
            btns = ttk.Frame(files_frame)
            btns.pack(side="left", fill="y", padx=4, pady=4)
            ttk.Button(btns, text="Add file(s)...", command=self.import_files).pack(fill="x", pady=2)
            ttk.Button(btns, text="Clear", command=self.clear_files).pack(fill="x", pady=2)
            self.files_list = tk.Listbox(files_frame, height=3)
            self.files_list.pack(side="left", fill="both", expand=True, padx=4, pady=4)

            # --- Query controls ---
            ctrl = ttk.LabelFrame(self.root, text="Query")
            ctrl.pack(fill="x", padx=8, pady=6)

            self.mode = tk.StringVar(value="point")
            ttk.Radiobutton(ctrl, text="Single point", value="point",
                            variable=self.mode, command=self._toggle_mode).grid(row=0, column=0, padx=4, pady=4, sticky="w")
            ttk.Radiobutton(ctrl, text="Time range", value="range",
                            variable=self.mode, command=self._toggle_mode).grid(row=0, column=1, padx=4, pady=4, sticky="w")

            ttk.Label(ctrl, text="Start / Point:").grid(row=1, column=0, padx=4, sticky="e")
            self.start_picker = DateTimePicker(ctrl, on_change=self._validate)
            self.start_picker.grid(row=1, column=1, padx=4, pady=2, sticky="w")

            self.end_label = ttk.Label(ctrl, text="End:")
            self.end_label.grid(row=2, column=0, padx=4, sticky="e")
            self.end_picker = DateTimePicker(ctrl, on_change=self._validate)
            self.end_picker.grid(row=2, column=1, padx=4, pady=2, sticky="w")

            self.analyze_btn = ttk.Button(ctrl, text="Analyze", command=self.run_analysis)
            self.analyze_btn.grid(row=1, column=2, rowspan=2, padx=12)

            self.valid_lbl = ttk.Label(ctrl, text="", anchor="w")
            self.valid_lbl.grid(row=3, column=0, columnspan=3, padx=4, sticky="w")

            self._toggle_mode()

            self.status = ttk.Label(self.root, text="No data imported.", anchor="w")
            self.status.pack(fill="x", padx=8)

            # --- Result table ---
            table_frame = ttk.Frame(self.root)
            table_frame.pack(fill="both", expand=True, padx=8, pady=6)
            self.tree = ttk.Treeview(table_frame, show="headings")
            vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
            hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
            self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
            self.tree.grid(row=0, column=0, sticky="nsew")
            vsb.grid(row=0, column=1, sticky="ns")
            hsb.grid(row=1, column=0, sticky="ew")
            table_frame.rowconfigure(0, weight=1)
            table_frame.columnconfigure(0, weight=1)

        def _toggle_mode(self):
            is_range = self.mode.get() == "range"
            self.end_picker.set_enabled(is_range)
            self.end_label.configure(state="normal" if is_range else "disabled")
            self._validate()

        def _validate(self):
            """Live-check the inputs; disables Analyze on invalid range."""
            try:
                start = self.start_picker.get_datetime()
            except (ValueError, tk.TclError):
                self.valid_lbl.config(text="Start date/time is invalid.", foreground="red")
                self.analyze_btn.state(["disabled"])
                return None, None
            if self.mode.get() != "range":
                self.valid_lbl.config(
                    text=f"Point query: {start:%Y-%m-%d %H:%M:%S}", foreground="green")
                self.analyze_btn.state(["!disabled"])
                return start, None
            try:
                end = self.end_picker.get_datetime()
            except (ValueError, tk.TclError):
                self.valid_lbl.config(text="End date/time is invalid.", foreground="red")
                self.analyze_btn.state(["disabled"])
                return None, None
            if end < start:
                self.valid_lbl.config(
                    text="End time is earlier than start time - adjust the values.",
                    foreground="red")
                self.analyze_btn.state(["disabled"])
                return None, None
            if end == start:
                self.valid_lbl.config(
                    text="Start and end are identical - widen the range.",
                    foreground="red")
                self.analyze_btn.state(["disabled"])
                return None, None
            self.valid_lbl.config(
                text=f"Range: {start:%Y-%m-%d %H:%M:%S}  ->  {end:%Y-%m-%d %H:%M:%S}",
                foreground="green")
            self.analyze_btn.state(["!disabled"])
            return start, end

        def import_files(self):
            paths = filedialog.askopenfilenames(
                title="Select one or more alarm export files",
                filetypes=[("Alarm exports", "*.xlsx *.xls *.csv"), ("All files", "*.*")],
            )
            if not paths:
                return
            new = [p for p in paths if p not in self.files]
            self.files.extend(new)
            self._reload()

        def clear_files(self):
            self.files = []
            self.raw_df = None
            self._reload()

        def _reload(self):
            self.files_list.delete(0, tk.END)
            for p in self.files:
                self.files_list.insert(tk.END, os.path.basename(p))
            if not self.files:
                self.raw_df = None
                self.status.config(text="No data imported.")
                self.tree.delete(*self.tree.get_children())
                return
            try:
                self.raw_df = load_alarms(self.files)
                resolve_columns(self.raw_df)  # validate anchors early
            except Exception as exc:
                messagebox.showerror("Import error", str(exc))
                self.raw_df = None
                return
            self.status.config(
                text=f"Imported {len(self.raw_df)} alarm row(s) from {len(self.files)} file(s).")
            self._show(self.raw_df)

        def run_analysis(self):
            if self.raw_df is None or self.raw_df.empty:
                messagebox.showwarning("No data", "Add alarm data first.")
                return
            start, end = self._validate()
            if start is None:
                messagebox.showerror("Invalid input",
                                     "Fix the highlighted date/time problem first.")
                return
            try:
                self.result_df = analyze(self.raw_df, start, end)
            except Exception as exc:
                messagebox.showerror("Analysis error", str(exc))
                return
            scope = "point" if end is None else "range"
            self.status.config(
                text=f"{len(self.result_df)} alarm(s) active in {scope} query. "
                     f"Use File > Export to save.")
            self._show(self.result_df)

        def _show(self, df: pd.DataFrame):
            self.tree.delete(*self.tree.get_children())
            self.tree["columns"] = list(df.columns)
            for col in df.columns:
                self.tree.heading(col, text=col)
                self.tree.column(col, width=140, stretch=False, anchor="w")
            for _, row in df.iterrows():
                self.tree.insert("", "end", values=["" if pd.isna(v) else v for v in row])

        def export_csv(self):
            if self.result_df is None or self.result_df.empty:
                messagebox.showwarning("Nothing to export", "Run an analysis first.")
                return
            path = filedialog.asksaveasfilename(
                title="Export result as CSV",
                defaultextension=".csv",
                filetypes=[("CSV file", "*.csv")],
            )
            if not path:
                return
            self.result_df.to_csv(path, index=False)
            messagebox.showinfo("Exported", f"Saved {len(self.result_df)} row(s) to:\n{path}")

    root = tk.Tk()
    AlarmGUI(root)
    root.mainloop()


if __name__ == "__main__":
    launch_gui()
