# Integrity audit — the NFL performance record (NHL-class defects)

Date: 2026-10-22. Scope: `~/nfl_props` (VM), branch `main`, HEAD `28647cd`.
Method: read-only inspection of the live tree, the live artifacts under
`outputs/`, the systemd user units and `~/dashboard/config/projects.json`; the
two headline numbers were recomputed from source on the VM (`/tmp/nfl_recompute.py`,
which reproduces `grade_forecast.py` row for row) rather than quoted from any
document. Reference case: `~/nhl_props/docs/LEDGER_RECONCILIATION_2026-10-22.md`.

Status of this document: §1–§7 are the assessment (steps 1–3), §8 lists the
fixes that actually landed (steps 2 and 4), §9 is what is deliberately left
open.

---

## 1. Verdict

**FINDINGS.** All three NHL defect classes are present in this project. None of
them is a data-corruption problem: every graded row is internally consistent,
the grader reproduces exactly, and there is no second price basis to reconcile.
The defects are about **what gets called the record**:

1. **Pooling (present, fixed).** The advertised season figure pooled 81
   `NO_VALUE` references — rows the board explicitly declined to recommend —
   into the headline. Pooled: **137-124-2, -14.86u**. Bettable: **89-92-1,
   -10.80u**. The headline overstated the loss by 4.06u and, worse, attributed
   it to bets nobody was told to place.
2. **A frozen writer/reader pair (present, fixed).** `grade.py` grades
   `outputs/history/`, which stopped being written on 2026-09-12. It still ran,
   and it still printed a W-L/units "record" covering 2026-08-13..2026-09-12 —
   an out-of-season record wearing the name of the season record.
3. **Competing records (present, partly fixed).** The week-6 weekly artifact
   the dashboard serves says 21-17, +0.32u; the same week now grades 20-18,
   -1.68u. Five earlier weekly artifacts agree with the machine grade exactly,
   so this is one stale file, not a systematic split. It is left in place
   pending a decision (§9.1) because replacing a number that already went to
   Discord is a publication choice, not a bug fix.

What is *not* wrong: the record source, the grader, the price basis, and the
weekly artifacts for weeks 1–5.

---

## 2. The map

### 2.1 Official record

`outputs/forecast_history/nfl_forecast_*.json` — 37 files, one per board run,
newest `nfl_forecast_20261022T231507Z.json`. Each file is a full board: every
game's forecasts plus the references (moneyline / spread / total) with captured
price, status and notes.

Writer: `nfl_props/forecast_output.py::export_history`, called from
`run_forecast_board.py` on every non-`--no-export` run. No pruning, no rotation.

### 2.2 Graders

- `grade_forecast.py::grade_all` + `render_report` → season record
  (`outputs/forecast_backtests/grade_forecast_<stamp>.{txt,json}`).
- `grade_forecast.py::grade_week` + `render_weekly_recap` → one week
  (`grade_weekly_<season>w<NN>_<stamp>.{txt,json}`), plus the Discord recap and
  the `grade_weekly_delivered.json` ledger of weeks already posted.

Both share one selection rule: **the latest pre-kickoff forecast per game**,
with `_with_snapshot_pick` / `nfl_props/snapshots.py::latest_snapshot_before`
overriding the board's pick when a pre-kickoff snapshot exists. Finals come
from `data/raw/games.csv`; a scored game is graded only when its
`(season, week)` matches the target.

### 2.3 Consumers

- **Discord**: the Tuesday weekly recap (`sports-nfl-weekly-grade.timer`,
  Tue 03:30 ET via `grade_forecast.py --weekly --discord`). Idempotent through
  `grade_weekly_delivered.json`.
- **Dashboard** (`~/dashboard/config/projects.json`, project `nfl_props`): one
  report entry, `outputs/forecast_backtests/grade_weekly_*.txt`, kind `text`,
  `active: true`. It renders the newest weekly *text* artifact — nothing else.
  The season report is not surfaced.
- **Humans reading `docs/`**: `AUDIT_2026-10-21_POST_FIX.md`,
  `WEEKLY_REFLECTIONS.md`, `README.md`, `OPERATIONS.md`.

There is no machine consumer that parses the season report's prose; the only
reader of that file shape is a person. That is why the step-2 reformat was safe
(§8).

### 2.4 Headline definition (exact, after the fix)

Rows counted: one per **priced reference** of each **chosen game**, where a
chosen game is the latest pre-kickoff forecast for that game, board picks
overridden by any pre-kickoff snapshot. A reference is priced when a decimal
was captured at print time. Result from the final score: win / loss / push.

- **PLAYABLE references** = the board's recommendation (`status == PLAYABLE`).
  This is the record: **the only rows a reader was told to stake**.
- **NO_VALUE references** = priced rows the board declined. Reported on their
  own line, never inside the record.
- **Pooled** = everything, both statuses together. Printed as a labelled
  diagnostic only.

Price basis: flat **1u at the captured price**, `decimal - 1` on a win, `-1.00`
on a loss, `0.00` on a push. ROI = units / **all references in the cohort**
(pushes stay in the denominator; `-10.80 / 182 = -5.93%`).

Season to date: 2026 weeks 1–6, kickoffs through 2026-10-22T23:15Z. 89 chosen
games, **263 priced references**, 1 pending game (NE @ CHI, week 7, kickoff
2026-10-23T00:15Z — not graded). Statuses present: `PLAYABLE`, `NO_VALUE`, both
carrying the forecast-path tier; no Core/Lean/Watch anywhere in this path.

---

## 3. Both headline numbers

### 3.1 Season

| Basis | Record | Units | ROI | Refs |
| --- | --- | --- | --- | --- |
| **PLAYABLE (the record)** | **89-92-1** | **-10.80u** | **-5.93%** | 182 |
| NO_VALUE (declined) | 48-32-1 | -4.07u | -5.02% | 81 |
| Pooled (as previously advertised) | 137-124-2 | -14.86u | -5.65% | 263 |

### 3.2 By family

| Family | Pooled | PLAYABLE | NO_VALUE |
| --- | --- | --- | --- |
| moneyline | 52-35-1, -5.66u (-6.43%), n=88 | **20-16, +0.20u (+0.56%)**, n=36 | 32-19-1, -5.86u (-11.27%), n=52 |
| spread | 46-41-1, +1.95u (+2.22%), n=88 | **38-34-1, +1.67u (+2.29%)**, n=73 | 8-7, +0.28u (+1.87%), n=15 |
| total | 39-48, -11.16u (-12.83%), n=87 | **31-42, -12.66u (-17.34%)**, n=73 | 8-6, +1.50u (+10.71%), n=14 |

Two things this table says that the pooled table hides:

- **The moneyline record flips sign.** Pooled moneyline is -5.66u; the moneylines
  the board actually recommended are +0.20u. The entire pooled moneyline loss is
  in rows the board rejected.
- **Totals are the only bettable bleed, and they are worse than pooled.**
  -12.66u on PLAYABLE totals vs -11.16u pooled: the rejected totals *made*
  +1.50u. Consistent with `AUDIT_2026-10-21_POST_FIX.md`.

### 3.3 By week

| Week | Pooled | PLAYABLE |
| --- | --- | --- |
| 1 | 21-21-0, -4.30u | 12-19, -7.19u |
| 2 | 24-24-0, -6.08u | 16-17, -3.03u |
| 3 | 27-21-0, +1.52u | 18-13, +4.03u |
| 4 | 25-20-0, +0.22u | 19-13, +4.05u |
| 5 | 20-20-2, -4.54u | 12-16-1, -5.09u |
| 6 | 20-18-0, -1.68u | 12-14, -3.57u |

Weeks 3 and 4 were positive on the bettable record and only flat-to-slightly-up
pooled; weeks 1 and 6 were worse than pooled. The per-week view is the honest
one: this is a losing season so far, driven by totals, not a uniformly bad board.

### 3.4 Why the number has moved before

Two grading corrections are already inside both columns, and they are why older
figures on disk differ: the WAS@SF spread originally printed a dead Polymarket
rung (`SF +17.5` for a real -7.5; a win at the printed line, a loss at the true
line) and the NYG@WAS moneyline was a 27-27 tie that must grade as a push, not
a win. Pooled was -12.87u before those repairs and -14.86u after.

---

## 4. Class 1 — pooling non-recommended rows into the record

**Present. Was in the season report; now fixed.**

Before this audit, `render_report` printed family breakdowns over all priced
references and **no season total at all** — the number a reader quoted was
whatever they summed, and every quoted figure in `docs/` was the pooled one
(`AUDIT_2026-10-21_POST_FIX.md`: "Season: 263 graded refs, … -14.86u, -5.7%
ROI"). Nothing in the output said the 81 `NO_VALUE` rows inside that figure were
rows the board had rejected. That is the NHL pattern exactly: an all-rows line
standing in for the bettable record.

The weekly path was already clean — `render_weekly_recap` has printed the priced
record and a separate PLAYABLE/NO_VALUE split since the v2 grading rollout — so
this was a season-report-only defect.

---

## 5. Class 2 — orphan and frozen writer/reader pairs

### 5.1 `outputs/history/` + `grade.py` — frozen for 40 days

`outputs/history/` holds 2026 v1/v2 board artifacts; the newest is
`nfl_board_20260912T145628Z.json`. Its only writer is `run_board.py` (the retired
price-screened board), its only reader is `grade.py`. Neither is reachable from
production: `scripts/run_nfl_linux_task.sh` maps `grade` and `weekly-grade` to
`grade_forecast.py`; there is no timer, no CLI subcommand, no test and no doc
entry point that runs `run_board.py` or `grade.py`.

So `grade.py` kept working, alone, on data nobody produces any more — and
printed a "record" with 2026-08-13..2026-09-12 scope. This is the NHL dead-ledger
defect in miniature.

Fixed (§8): `grade.py` refuses to run and says why. The grading helpers survive
so the frozen artifacts stay reproducible.

### 5.2 `outputs/ledger/forecast_ledger.jsonl` — write-only, no reader

202 rows, appended by `run_forecast_board.py` and `scripts/pregame_refresh.py`
on every board. No code reads it (only tests mock `append_ledger`). It is
**not** the record and **not** a source of truth; it is a print log that will
grow forever. Already labelled in `append_ledger`'s docstring (§8); no other
action taken — deleting a log that costs nothing to keep is not a fix.

### 5.3 Snapshots: a `wave` / `delta` snapshot tier that has never run

`outputs/snapshots/` holds 15 files across 4 day directories (14 live snapshots
plus `2026-10-19/g:2026_06_WAS_SF.json.bak`, the pre-repair copy of the WAS@SF
line), **all with top-level `source: "early"`** — 15/15. The snapshot model
expects later-tier snapshots
(`wave`, `delta`) to override the early pick before kickoff — the mechanism is
what keeps grading honest when a line moves. It has never received one.

Two reasons, both scheduling gaps rather than code defects:

- **`delta_check` has no timer.** `scripts/run_nfl_linux_task.sh delta_check`
  (→ `nfl_props.delta.run_delta_check`, the pre-kickoff injury delta sweep) is
  implemented, documented and never scheduled. `nfl_props/delta.py` has never
  run unattended.
- **The Sunday pregame refresh is disabled.**
  `sports-nfl-pregame-refresh.timer` (Sun 09:30 ET) is `disabled`,
  `ActiveState=inactive`, `LastTriggerUSec=` empty — it has never fired. The
  Monday variant does run: `sports-nfl-mnf-refresh.timer` (Mon 19:15 ET) fires
  `sports-nfl-pregame-refresh.service`.

Per `nfl_props/snapshots.py`, retention is intentionally manual
(`cleanup_snapshots` is never scheduled): pruning would change past grading,
since grading reads the snapshot as the print-time line.

### 5.4 `outputs/backtests/` — empty directory

`BACKTESTS_DIR` is referenced only by the retired `grade.py` export path.
Zero files. Harmless; left as is.

---

## 6. Class 3 — two files claiming to be the record

### 6.1 The week-6 weekly artifact (open — see §9.1)

| Week | Artifact on disk | Machine grade now | Agrees? |
| --- | --- | --- | --- |
| 1 | 21-21-0, -4.30u (n=42) | 21-21-0, -4.30u | yes |
| 2 | 24-24-0, -6.08u (n=48) | 24-24-0, -6.08u | yes |
| 3 | 27-21-0, +1.52u (n=48) | 27-21-0, +1.52u | yes |
| 4 | 25-20-0, +0.22u (n=45) | 25-20-0, +0.22u | yes |
| 5 | 20-20-2, -4.54u (n=42) | 20-20-2, -4.54u | yes |
| 6 | **21-17-0, +0.32u** (n=38) | **20-18-0, -1.68u** | **no** |

`grade_weekly_2026w06_20261020_073002.{txt,json}` was written at
2026-10-20T07:30Z, before the pre-kickoff line repairs landed. Same 38
references, one of them grades the other way round; the week moves 2.00u.

It matters more than a stale file normally would, because the dashboard's single
report glob (`outputs/forecast_backtests/grade_weekly_*.txt`) resolves to
*newest*, and newest is that file. **The dashboard is currently showing 21-17,
+0.32u for week 6.** The truth is 20-18, -1.68u.

### 6.2 `docs/WEEKLY_REFLECTIONS.md` — hand-maintained tables

Weeks 1–3 carry hand-computed pooled ROI tables that no longer match the machine
record (week 3's journal entry, for instance, reports the week at +1.52u pooled
with a separate PLAYABLE cut — correct as written, but read as "the record" by
anyone skimming). Fixed by a scope note: journal, pooled basis, machine artifact
wins, not retroactively rewritten (§8).

### 6.3 `docs/AUDIT_2026-10-21_POST_FIX.md` — one arithmetic slip

Its verdict line said "263 graded refs, **138-124-1**". The pooled record is
**137-124-2** — the NYG@WAS tie is a push. Units (-14.86u) and ROI were already
right, so the error was in the W-L column only. Corrected in place with a dated
note, plus a banner that its season line is pooled and no longer the advertised
headline (§8).

### 6.4 What is *not* a competing record

- `outputs/ledger/forecast_ledger.jsonl` — no reader (§5.2).
- `outputs/diagnostics/*` — coverage/diagnostic reports, no record semantics.
- The Discord board — point-in-time projections, graded later by the artifacts.
- `outputs/forecast_backtests/grade_forecast_*.{txt,json}` — each is a dated
  snapshot of the same computation; the newest one is the season record. They do
  not disagree with each other by construction, only across grading-code
  versions, which is why the file name carries a stamp.

---

## 7. Documentation and legacy audit (step 3)

Fixed in `80ee3ad` and `28647cd`:

- `docs/ARCHITECTURE.md` described the **legacy v1/v2 system only** — `run_board.py`
  / `grade.py` / `board.py` / `tiers.py` — and never mentioned the live
  forecast-first path. Rewritten around the live modules; legacy demoted to its
  own section.
- `docs/OPERATIONS.md` timer table was wrong (it presented the disabled Sunday
  pregame refresh as the Sunday driver) and said nothing about what the record
  counts. Corrected; PLAYABLE scope stated; scheduling gaps named explicitly
  rather than silently fixed; tests line corrected to
  `.venv/bin/python -m pytest -q`.
- `docs/NEXT_CHECKIN.md` superseded banner; `README.md` grade bullet states the
  PLAYABLE scope; `docs/AGENT_INTRO_PROMPT.md`, `docs/PLAN.md`, `docs/HANDOFF.md`
  no longer point a new reader or agent at `run_board.py` / `grade.py`.
- `nfl_props/forecast_output.py::append_ledger` docstring now says plainly:
  write-only print log, not a record, no reader.
- `nfl_props/snapshots.py` claimed "Retained 30 days, then cleaned up". There is
  no such cleanup and no such schedule; replaced with the truth (manual pruning
  by design, because pruning changes past grading).

Still stale, deliberately left (documented, not fixed):

- `docs/DEPLOY_WINDOWS.md` — retired host, referenced as RETIRED from
  `OPERATIONS.md`.
- `docs/PLAN.md` and `docs/HANDOFF.md` remain historical journals; only their
  live-path pointers were corrected.
- Dashboard report note "Current report may predate the grading-v2 rollout…" is
  itself stale (v2 shipped in week 4).

Dead code paths, for the record: `run_board.py` (no caller), `nfl_props/board.py::
screen_games` (only `run_board.py` calls it; `apply_single_side` is still tested),
`grade.py::_legacy_main` (kept deliberately, §8). `nfl_props/tiers.py` is still
imported by two test modules and is therefore live test surface, not dead code.

---

## 8. Fixes landed

- **`9e0420c` — `fix(grade): scope season record to PLAYABLE references`.**
  `grade_all` now emits a `headline` block (`_ref_bundle`, `_season_headline`,
  scope string); `render_report` leads with the PLAYABLE record, prints NO_VALUE
  on its own line, prints pooled as a labelled diagnostic, and relabels the
  family breakdowns "pooled over all priced refs". New `tests/test_season_grade.py`
  (6 tests).
- **`80ee3ad` — `docs: make the record scope and the live schedules match the code`.**
  The step-3 documentation set above.
- **`28647cd` — `fix(record): retire the frozen v1/v2 grader, label the derived records`.**
  `grade.py::main()` refuses with exit 2 and names the live command;
  `_legacy_main()` and the grading helpers are preserved for reproducibility;
  `tests/test_legacy_grade_retired.py` (3 tests) pins the refusal and the
  preserved surface. Scope notes on `WEEKLY_REFLECTIONS.md` and
  `AUDIT_2026-10-21_POST_FIX.md`, plus the 138-124-1 → 137-124-2 correction.

Nothing live depended on anything changed: the season report's prose has no
machine reader, no timer or script invokes `grade.py`, and no test imported its
`main()` (a new test now does).

---

## 9. Open items — decisions, not bugs

1. **The week-6 artifact.** Publishing a corrected `grade_weekly_2026w06_*`
   would fix the dashboard and would also silently revise a number already
   posted to Discord on 2026-10-20. Options: (a) regenerate with
   `.venv/bin/python grade_forecast.py --weekly --week 6 --no-discord` and post
   a one-line correction to Discord; (b) leave it and let week 7's recap carry a
   correction note; (c) leave it and note the stale week in the dashboard entry.
   Recommended: (a) with the correction line — the honest number should be the
   one on the wall. Not done here because it changes a published figure.
2. **`delta_check` has no timer, and the Sunday pregame refresh is disabled.**
   Either scheduling is a live-behaviour change (extra Discord posts, extra board
   runs), so it is Cole's call. Note the `Persistent=false` catch-up hazard from
   the timers' own history: enabling `sports-nfl-pregame-refresh.timer` on a
   Sunday after 09:30 ET will fire immediately.
3. **`sports-nfl-board.timer` carries four `OnCalendar` specs** (Thu 19:15,
   Sun 11:00, Sun 15:37, Mon 18:00 ET) with `Persistent=false`. Verified from
   `systemctl show … -p TimersCalendar`: all four have real next elapses, so the
   multi-schedule fan-out is correct systemd behaviour, not a defect. Last fire
   Thu 2026-10-22 23:15Z. `list-timers` only shows the next one — that is the
   display, not a bug.
4. **`run_board.py`** — the dead v1/v2 writer. Left in place: it writes to the
   now-unread `outputs/history/`, so it is harmless, and deleting a legacy entry
   point is a housekeeping call. `grade.py` no longer reads what it writes.
5. **`scripts/run_nfl_linux_task.sh.bak`** — untracked stray on the VM, not in
   `git status` history, not referenced. Left untouched; it is Cole's file to
   delete or keep.
6. **Carried forward from the 2026-10-21 review (not this audit's scope):** the
   forecast path still has no EV/edge ceiling, so a mispriced line ships as a
   maximum-confidence PLAYABLE. That is the largest outstanding performance
   defect and it is untouched by anything here.

---

## 10. Verification

- `tests/test_legacy_grade_retired.py` — 3 passed.
- Full suite on the VM: **257 passed in 5.48s** (`.venv/bin/python -m pytest -q`),
  up from 254 before this step.
- Live refusal: `.venv/bin/python grade.py` → exit 2 with the retirement notice.
- Live season recompute reproduces the grader exactly:
  `grade_forecast.py` (run `20261022_234402`) prints
  `PLAYABLE 89-92-1 -10.80u`, `NO_VALUE 48-32-1 -4.07u`, `pooled 137-124-2 -14.86u`.
- Weekly cross-check: `.venv/bin/python grade_forecast.py --weekly --week 6
  --no-discord --no-export` → 20-18, -1.68u (matches §3.3, contradicts the stale
  artifact in §6.1).
- `bash`-level checks: `outputs/history` newest file 2026-09-12; snapshot
  provenance 15/15 `early`; `sports-nfl-pregame-refresh.timer` disabled with no
  `LastTriggerUSec`; no `delta` timer unit and no crontab entry.

---

## 11. What "clean" would look like

The record is now one number with one definition, written by one grader, and the
dead reader that could impersonate it is retired. What remains is not integrity
work: publish the corrected week-6 figure (or accept the stale one knowingly),
decide whether the late-tier snapshot/injury refresh should actually run, and
deal with the missing EV ceiling — which is the thing that will still be costing
money when everything in this document is forgotten.
