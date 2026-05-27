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

## Windows step-by-step (for beginners)

If you have never run a Python program before, follow these steps exactly. You
only need to do Steps 1–3 once; after that, running the program is just Step 4.

### Step 1 — Install Python

1. Open your web browser and go to <https://www.python.org/downloads/windows/>.
2. Click the big yellow **Download Python 3.x.x** button (any 3.9 or newer is
   fine).
3. Open the file you just downloaded (it will be named something like
   `python-3.12.x-amd64.exe`).
4. **IMPORTANT:** On the first install screen, tick the checkbox at the bottom
   that says **"Add python.exe to PATH"**. This step is easy to miss and the
   program will not work without it.
5. Click **Install Now** and wait for it to finish, then click **Close**.

> Tkinter (the part that draws the window) is included automatically with the
> official Python installer on Windows — you do not need to install it
> separately.

### Step 2 — Open the Command Prompt

1. Press the **Windows key** on your keyboard.
2. Type `cmd` and press **Enter**. A black window titled *Command Prompt*
   opens. This is where you type the commands below.
3. To check Python installed correctly, type the following and press **Enter**:

   ```bat
   python --version
   ```

   You should see something like `Python 3.12.x`. If instead you see an error
   or the Microsoft Store opens, Python was not added to PATH — re-run Step 1
   and make sure the **"Add python.exe to PATH"** box is ticked.

### Step 3 — Install the required add-ons

In the same Command Prompt window, type this and press **Enter**:

```bat
pip install pandas openpyxl
```

Wait until it finishes (you will see lots of text, ending with something like
*Successfully installed ...*). You only need to do this once.

### Step 4 — Run the program

1. Put `alarm_analyzer.py` in an easy-to-find folder, for example your
   **Downloads** folder.
2. In the Command Prompt, move into that folder. For the Downloads folder type:

   ```bat
   cd %USERPROFILE%\Downloads
   ```

   (If you saved it somewhere else, type `cd ` followed by the folder path.)
3. Start the program by typing:

   ```bat
   python alarm_analyzer.py
   ```

4. The **NOC Alarm Activity Analyzer** window opens. Continue with the
   [Usage](#usage) steps below.

> **Tip:** To run it again next time, just repeat Step 2 (open Command Prompt),
> then Step 4. Steps 1 and 3 do not need to be repeated.

### Common problems on Windows

| What you see                                   | What to do                                                                 |
| ---------------------------------------------- | -------------------------------------------------------------------------- |
| `'python' is not recognized ...`               | Python is not on PATH. Reinstall (Step 1) and tick **Add python.exe to PATH**. |
| `'pip' is not recognized ...`                  | Try `python -m pip install pandas openpyxl` instead.                       |
| The Microsoft Store opens when you type `python` | Reinstall from python.org (Step 1) with the PATH box ticked.             |
| `No module named 'pandas'` / `'openpyxl'`      | Re-run Step 3.                                                             |
| Nothing happens / no window appears            | Make sure you are in the right folder (Step 4.2) and the file name is exactly `alarm_analyzer.py`. |

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
