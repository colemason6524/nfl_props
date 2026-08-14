# Windows deployment — SSH, clone, smoke tests, scheduled runs

Production convention (same as tennis_props / mlb_props): **Mac develops,
Windows runs.** Repo lives at `C:\Users\muski\nfl_props`, tasks run from the
`.venv`, changes arrive via `git pull` over SSH.

| Item | Value |
|---|---|
| SSH from Mac | `ssh windows` (alias for `colemason41@100.77.131.65`) |
| Repo path | `C:\Users\muski\nfl_props` |
| GitHub | `https://github.com/colemason6524/nfl_props` |
| Python | Windows `python` (3.13.x observed on this box); venv at `.venv\Scripts\python.exe` |
| Logs | `C:\Users\muski\nfl_props\logs\` |
| Discord | OFF until `NFL_DISCORD_WEBHOOK_URL` + `SEND_DISCORD=true` are set |

Path note: in an SSH (non-interactive cmd) session, quote paths and prefer
`cd /d C:\Users\muski\nfl_props &&` prefixes, exactly like the tennis flow.

## 1. SSH in and clone

```bash
# from the Mac
ssh windows
```

Then on Windows:

```bat
cd /d C:\Users\muski
git clone https://github.com/colemason6524/nfl_props.git
cd nfl_props
```

If the repo is (or becomes) **private**, HTTPS clone will prompt for
credentials Task Scheduler can't provide. Two options, per the playbook:

- Easiest: Git Credential Manager (bundled with Git for Windows) — clone
  once interactively, credentials persist for later `git pull`.
- SSH deploy key (tennis pattern): generate a key on Windows, add it as a
  **deploy key on this one repo**, and remember deploy keys are single-repo.
  If GitHub says "key already in use", it's already a deploy key on another
  repo — use a distinct key plus an SSH config alias (see
  `tennis_props/docs/PLAYBOOK.md`, ops cheat sheet).

## 2. Environment setup

```bat
cd /d C:\Users\muski\nfl_props
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
```

## 3. Data bootstrap (one-time, ~400 MB download)

```bat
.venv\Scripts\python -m nfl_props.cli refresh-data
.venv\Scripts\python -m nfl_props.cli build
.venv\Scripts\python -m nfl_props.cli rebuild-state
```

## 4. Smoke tests (run in order; expected results listed)

Run all of these before creating any scheduled task. Each step must pass
before the next matters.

### 4.1 Interpreter + deps

```bat
.venv\Scripts\python -c "import pandas, pyarrow, numpy; print('deps ok')"
```

Expect: `deps ok`.

### 4.2 Data downloaded and canonical store built

```bat
.venv\Scripts\python -c "import json; d=json.load(open('outputs/diagnostics/build_diagnostics.json')); print('match rate', d['pbp_match_rate'], '| team-game rows', d['team_game_rows'])"
```

Expect: `match rate 1.0` (or ≥ 0.999) and roughly 7,000+ team-game rows.
A low match rate means pbp files are missing — rerun `refresh-data` and
check its log output. Before the season's first game, `pbp 2026
unavailable yet ... skipping` is **normal**.

### 4.3 Model state

```bat
.venv\Scripts\python -c "import json; s=json.load(open('data/processed/live_state.json')); print('as_of', s['as_of'], '| teams', len(s['teams']), '| shrink', s['prob_shrink'])"
```

Expect: 32 teams, an `as_of` equal to the last completed game date, and a
`prob_shrink` dict with four markets.

### 4.4 Live board (network test against Bovada)

```bat
.venv\Scripts\python run_board.py
```

Expect on stderr/console:

- `[board] bovada games: N (ml=... sp=... gt=... tt=...)` with N > 0 when
  games are posted (N=16 per regular-season week; preseason varies)
- a projections table for every game, then Core/Lean/Watch sections
- `[board] history -> outputs\history\nfl_board_*.json`
- `tt=0` before game week is coverage, not failure (team totals post late)

An extra `[board] filtered N stale / M missing-kickoff games` line is normal
when a cached board contains already-started games — they are dropped, not
offered. `outputs\diagnostics\bovada_coverage_*.json` carries the full fetch
metadata + team-total parse diagnostics for game-week verification. Run the
unit tests once after clone: `.venv\Scripts\python -m unittest discover -s tests`.

Then confirm the export exists:

```bat
dir outputs\history\nfl_board_*.json
```

### 4.5 Grade (pending is a pass)

```bat
.venv\Scripts\python grade.py
```

Expect before any games finish: `graded 0 | pending K | unmatched 0`, plus a
`v2 shadow projections: graded 0 | pending N | unmatched 0` line when the v2
state/history exists.
`unmatched > 0` is a real problem (team alias or date matching) — stop and
investigate before scheduling. After a completed week, expect graded rows,
a by-tier/by-market summary, v1/v2 points/margin/total MAE, and a report in
`outputs\backtests\`.

### 4.6 Task wrappers exactly as Task Scheduler will run them

```bat
C:\Windows\System32\cmd.exe /c ""C:\Users\muski\nfl_props\scripts\run_nfl_board_task.cmd""
type logs\nfl_board.log

C:\Windows\System32\cmd.exe /c ""C:\Users\muski\nfl_props\scripts\run_nfl_grade_task.cmd""
type logs\nfl_grade.log
```

Expect each log to end with `exit=0` (board) / `grade_exit=0
refresh_exit=0` (grade). This catches PATH/venv/cwd problems that only
appear under Task Scheduler's environment.

### 4.7 Optional: remote one-liner from the Mac

```bash
ssh windows "cd /d C:\Users\muski\nfl_props && .venv\Scripts\python run_board.py --no-refresh-odds"
```

## 5. Task Scheduler setup

Create two tasks (Task Scheduler → Create Task, not Basic Task):

**NFL board — daily**

- Trigger: daily 11:00 AM (lines move all week; one capture/day is enough)
- Program/script: `C:\Windows\System32\cmd.exe`
- Add arguments: `/c ""C:\Users\muski\nfl_props\scripts\run_nfl_board_task.cmd""`
- Start in: `C:\Users\muski\nfl_props`
- Run whether user is logged on or not: enabled for unattended runs
- Run with highest privileges: enabled

**NFL grade + weekly rebuild — Tuesday**

- Trigger: weekly, Tuesday 9:00 AM (after MNF stats/results land)
- Same program/arguments pattern with `run_nfl_grade_task.cmd`
- This task grades the finished week AND refreshes data / rebuilds the
  model state for the new week. It is required, not optional — without it
  the board runs on stale ratings all season.

In-season calendar note: Thu/Sun/Mon games all resolve by Tuesday morning,
so one weekly grade task covers everything. Keep the board task running
daily Tue–Sun; Monday runs are fine too (it will just show the MNF game).

## 6. Discord (later, when the channel exists)

```powershell
setx NFL_DISCORD_WEBHOOK_URL "https://discord.com/api/webhooks/..."
setx SEND_DISCORD "true"
```

Close and reopen the shell after `setx`; the `.cmd` wrappers also read
`HKCU\Environment` directly so the scheduled task picks the values up
without a re-login. Webhook is NFL-specific on purpose — never reuse
another sport's webhook (wrong-channel lesson from the playbook).

Test manually once: `.venv\Scripts\python run_board.py --discord`
(sends only if Core plays exist; empty Core sends nothing and exits 0).

## 7. Updating the deployment

```bash
# from the Mac
ssh windows "cd /d C:\Users\muski\nfl_props && git pull"
```

Scheduled tasks need no changes after a pull unless `requirements.txt`
changed — then rerun the pip install from step 2. If `nfl_props/version.py`
bumped the model version, run a manual `rebuild-state` so the change takes
effect before the next scheduled grade task would do it anyway.

## 8. Troubleshooting quick table

| Symptom | Likely cause / fix |
|---|---|
| Wrapper log ends with nonzero exit | run the same cmd.exe line manually (4.6) and read the full log |
| `bovada games: 0` | fetch failed or no pregame events; check `outputs\diagnostics\bovada_coverage_*.json`; fail-open cache logs `using cached` |
| `state as of` frozen mid-season | Tuesday grade task not running; check `logs\nfl_grade.log` |
| `git pull` prompts for credentials | credential manager not initialized (private repo); see step 1 |
| Board works manually, fails scheduled | task's Start-in directory wrong, or env var set for a different user account |
| pbp 404 for current season | normal until nflverse publishes the season's first pbp file |
