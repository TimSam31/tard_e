# NOC Alarm Activity Analyzer

A tool for Network Operation Center engineers to determine which alarms were
supposed to be **active** at a single point in time, or **during a time range**,
based on history and/or active alarm exports from network elements.

## Features

1. **Import** one or more alarm export files (`.xlsx`, `.xls`, `.csv`). Files
   with different column layouts are merged (column union), and a `Source File`
   column records where each row came from.
2. **Query** by either a single point in time, or a time range (start + end).
3. **Range remarks** — for range queries, each matched alarm is annotated to
   show whether it *occurred midway*, *cleared midway*, both, was *still active
   / uncleared*, or *active throughout the window*.
4. **Anchor columns** are located by header name (case-insensitive, with common
   aliases). Only these are needed for the calculation; every other column is
   carried through to the displayed result and the CSV export.
5. **GUI** (Tkinter) shows the filtered result in a table, with a menu to
   **export the output to CSV**.

## Requirements

- Python 3.9+
- [pandas](https://pypi.org/project/pandas/)
- [openpyxl](https://pypi.org/project/openpyxl/) (for reading `.xlsx`)
- Tkinter (bundled with most Python installs; on Debian/Ubuntu install
  `python3-tk`)

```bash
pip install pandas openpyxl
# Debian/Ubuntu only, if Tkinter is missing:
sudo apt-get install python3-tk
```

## Usage

```bash
python alarm_analyzer.py
```

1. **File > Import alarm file(s)...** — select one or more exports.
2. Choose **Single point** or **Time range**.
3. Enter the time(s) as `YYYY-MM-DD HH:MM:SS` (e.g. `2026-01-15 14:30:00`).
   The time portion may be omitted seconds, and a few other common formats are
   accepted automatically.
4. Click **Analyze** to display the alarms active in that period.
5. **File > Export result to CSV...** — save the displayed result.

## Anchor columns

The following headers are required for the calculation. Matching is
case-insensitive and accepts the listed aliases:

| Anchor             | Accepted header spellings                                  | Required |
| ------------------ | ---------------------------------------------------------- | -------- |
| `ME`               | ME                                                         | Yes      |
| `Alarm Code Name`  | Alarm Code Name, Alarm Name, Alarm Code                    | Yes      |
| `Occurrence Time`  | Occurrence Time, Occur Time, Raise Time, Event Time        | Yes      |
| `Clear Time`       | Clear Time, Cleared Time, Recovery Time                    | No\*     |
| `Position`         | Position, Location                                         | Yes      |

\* `Clear Time` is optional. A **blank Clear Time means the alarm is still
active**, and the current time is substituted as its effective clear time for
the calculation. The substituted value is shown in the added
`Effective Clear Time` column.

All remaining columns from the source files are preserved and exported.

## How activity is determined

Each alarm has an active interval `[Occurrence Time, Effective Clear Time]`,
where the effective clear time is the real `Clear Time` if present, otherwise
the current time.

- **Single point `T`** — alarm is kept when `Occurrence ≤ T ≤ Effective Clear`.
- **Time range `[start, end]`** — alarm is kept when its interval overlaps the
  window, i.e. `Occurrence ≤ end` **and** `Effective Clear ≥ start`.

Interval endpoints are treated as **inclusive**.

### `Analysis Remark` values

| Query  | Remark                                                              |
| ------ | ------------------------------------------------------------------ |
| Point  | `Active at query time`                                             |
| Point  | `Active at query time (uncleared / active alarm)`                  |
| Range  | `Active throughout window`                                         |
| Range  | `occurred midway (after window start)`                             |
| Range  | `cleared midway (before window end)`                               |
| Range  | `still active / uncleared`                                         |

For range queries, applicable tags are combined with `; ` (e.g. an alarm that
both appeared and recovered inside the window is marked *occurred midway ...;
cleared midway ...*).

## Output columns

The export/result leads with the anchor and analysis columns, followed by every
other column from the source data:

```
ME, Alarm Code Name, Occurrence Time, Clear Time, Effective Clear Time,
Position, Analysis Remark, <all other original columns...>, Source File
```

## Using the logic programmatically

The analysis core is GUI-independent and can be scripted:

```python
from datetime import datetime
from alarm_analyzer import load_alarms, analyze

df = load_alarms(["history.xlsx", "active.csv"])

# Single point query
result = analyze(df, datetime(2026, 1, 15, 14, 30, 0))

# Time range query
result = analyze(df, datetime(2026, 1, 1), datetime(2026, 2, 1))

result.to_csv("active_alarms.csv", index=False)
```
