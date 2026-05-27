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
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class AlarmGUI:
        def __init__(self, root):
            self.root = root
            self.root.title("NOC Alarm Activity Analyzer")
            self.root.geometry("1200x650")
            self.raw_df: pd.DataFrame | None = None
            self.result_df: pd.DataFrame | None = None
            self._build()

        def _build(self):
            # --- Menu bar ---
            menubar = tk.Menu(self.root)
            filemenu = tk.Menu(menubar, tearoff=0)
            filemenu.add_command(label="Import alarm file(s)...", command=self.import_files)
            filemenu.add_command(label="Export result to CSV...", command=self.export_csv)
            filemenu.add_separator()
            filemenu.add_command(label="Exit", command=self.root.quit)
            menubar.add_cascade(label="File", menu=filemenu)
            self.root.config(menu=menubar)

            # --- Query controls ---
            ctrl = ttk.LabelFrame(self.root, text="Query")
            ctrl.pack(fill="x", padx=8, pady=6)

            self.mode = tk.StringVar(value="point")
            ttk.Radiobutton(ctrl, text="Single point", value="point",
                            variable=self.mode, command=self._toggle_mode).grid(row=0, column=0, padx=4, pady=4, sticky="w")
            ttk.Radiobutton(ctrl, text="Time range", value="range",
                            variable=self.mode, command=self._toggle_mode).grid(row=0, column=1, padx=4, pady=4, sticky="w")

            ttk.Label(ctrl, text="Start / Point (YYYY-MM-DD HH:MM:SS):").grid(row=1, column=0, padx=4, sticky="e")
            self.start_var = tk.StringVar()
            ttk.Entry(ctrl, textvariable=self.start_var, width=24).grid(row=1, column=1, padx=4, sticky="w")

            self.end_label = ttk.Label(ctrl, text="End (YYYY-MM-DD HH:MM:SS):")
            self.end_label.grid(row=1, column=2, padx=4, sticky="e")
            self.end_var = tk.StringVar()
            self.end_entry = ttk.Entry(ctrl, textvariable=self.end_var, width=24)
            self.end_entry.grid(row=1, column=3, padx=4, sticky="w")

            ttk.Button(ctrl, text="Analyze", command=self.run_analysis).grid(row=1, column=4, padx=10)
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
            state = "normal" if is_range else "disabled"
            self.end_entry.configure(state=state)

        def import_files(self):
            paths = filedialog.askopenfilenames(
                title="Select alarm export file(s)",
                filetypes=[("Alarm exports", "*.xlsx *.xls *.csv"), ("All files", "*.*")],
            )
            if not paths:
                return
            try:
                self.raw_df = load_alarms(list(paths))
                resolve_columns(self.raw_df)  # validate anchors early
            except Exception as exc:
                messagebox.showerror("Import error", str(exc))
                self.raw_df = None
                return
            self.status.config(
                text=f"Imported {len(self.raw_df)} alarm row(s) from {len(paths)} file(s)."
            )
            self._show(self.raw_df)

        def run_analysis(self):
            if self.raw_df is None or self.raw_df.empty:
                messagebox.showwarning("No data", "Import alarm data first.")
                return
            start = parse_datetime(self.start_var.get())
            if start is None:
                messagebox.showerror("Invalid input", "Enter a valid start/point time.")
                return
            end = None
            if self.mode.get() == "range":
                end = parse_datetime(self.end_var.get())
                if end is None:
                    messagebox.showerror("Invalid input", "Enter a valid end time for range query.")
                    return
            try:
                self.result_df = analyze(self.raw_df, start, end)
            except Exception as exc:
                messagebox.showerror("Analysis error", str(exc))
                return
            scope = "point" if end is None else "range"
            self.status.config(
                text=f"{len(self.result_df)} alarm(s) active in {scope} query. "
                     f"Use File > Export to save."
            )
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
