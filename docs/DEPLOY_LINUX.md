# Linux deployment — Azure VM, systemd timers

Production moved here 2026-09-07 when the Windows box was retired. Same
product semantics as the Windows deploy (daily board, Tuesday grade + v1/v2
rebuild, flat 1u, Discord Core-only), Linux-native scheduling.

**Deployed and verified 2026-09-08 04:00 UTC:** units installed, both timers
`enabled` with correct ET→UTC conversion (grade Tue 13:00 UTC = 09:00
Detroit, board 15:00 UTC = 11:00 Detroit); manual `systemctl start` of both
services returned exit 0; board run graded 16 games (Core=0, Lean=5,
Watch=143) and wrote history; grade run reported
`graded 0 | pending 18 | unmatched 0` with the migrated 35-snapshot history
and rebuilt v1+v2 state at `pbp_match_rate=1.0000`. The old tmux scheduler
experiment was removed at the same time.

| Item | Value |
|---|---|
| Host | Azure VM, Ubuntu 22.04, 2 vCPU, ~1 GB RAM, disk UTC timezone |
| SSH from Mac | `ssh -i ~/Downloads/RunThemScripts_key.pem azureuser@130.131.0.6` |
| Repo | `~/nfl_props` (git@github.com:colemason6524/nfl_props.git) |
| Python | system 3.10; venv at `.venv` |
| Schedule | board daily 11:00, grade+rebuild Tue 09:00 — both America/Detroit |
| Config | `~/.config/nfl_props/env` (TZ, NFL_SEND_DISCORD, NFL_DISCORD_WEBHOOK_URL) |
| Logs | `~/nfl_props/logs/nfl_board.log`, `logs/nfl_grade.log` + `journalctl` |
| Lock | `~/.local/state/nfl_props/run.lock` (flock — board/grade never overlap) |

## Lightweight by design

- Raw pbp (`data/raw`, ~250 MB) stays on the VM: the Tuesday rebuild reads it.
  Everything else is code + small parquet state + history JSON.
- One oneshot service per job; journald captures stdout, the runner also
  appends to `logs/` for grepping. No agents, no tmux, no cron.
- `Persistent=true` timers replay missed runs after downtime; a shared flock
  prevents board/grade overlap.

## 1. One-time setup

```bash
git clone git@github.com:colemason6524/nfl_props.git ~/nfl_props
cd ~/nfl_props
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p ~/.config/nfl_props && printf 'TZ=America/Detroit\n' > ~/.config/nfl_props/env
# add NFL_SEND_DISCORD=true + NFL_DISCORD_WEBHOOK_URL only when a channel exists
.venv/bin/python -m nfl_props.cli refresh-data   # ~250 MB; 2026 pbp 404s until Week 1
.venv/bin/python -m nfl_props.cli build
.venv/bin/python -m nfl_props.cli rebuild-state
.venv/bin/python -m nfl_props.cli rebuild-state-v2
.venv/bin/python -m unittest discover -s tests
.venv/bin/python run_board.py                    # manual smoke
.venv/bin/python grade.py --no-refresh           # pending is a pass
```

Migrate grading history from the old host before the first in-season grade:

```bash
# from the Mac (canonical copy lives here)
rsync -av outputs/history/ azureuser@130.131.0.6:nfl_props/outputs/history/
```

`grade.py` keeps only the latest pre-kickoff snapshot per (matchup, market,
side), so a superset of snapshots is always safe.

## 2. systemd install (after `git pull` brings `scripts/systemd/`)

```bash
sudo cp ~/nfl_props/scripts/systemd/nfl-props-*.service \
        ~/nfl_props/scripts/systemd/nfl-props-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nfl-props-board.timer nfl-props-grade.timer
systemctl list-timers --no-pager | grep nfl-props
```

## 3. Verification (order matters)

```bash
systemctl list-timers --no-pager | grep nfl-props      # both timers pending
sudo systemctl start nfl-props-board.service           # manual trigger
journalctl -u nfl-props-board.service -n 50 --no-pager # full board output
tail -5 ~/nfl_props/logs/nfl_board.log                 # ends with exit=0
ls -t ~/nfl_props/outputs/history/ | head -1           # same-day snapshot
sudo systemctl start nfl-props-grade.service
tail -8 ~/nfl_props/logs/nfl_grade.log                 # grade_exit=0 refresh_exit=0
```

Success: board logs `bovada games: N (ml=... sp=... gt=... tt=...)` with N>0,
`tt=0` before game week is coverage not failure; grade reports
`graded X | pending Y | unmatched 0` and the weekly rebuild lines.

## 4. Updating the deployment

```bash
ssh -i ~/Downloads/RunThemScripts_key.pem azureuser@130.131.0.6 \
  "cd ~/nfl_props && git pull --ff-only"
```

Tasks need no changes after a pull unless `requirements.txt` changed (rerun
pip install) or `nfl_props/version.py` bumped the model version (run a manual
`rebuild-state` + `rebuild-state-v2`).

## 5. Troubleshooting

| Symptom | Check |
|---|---|
| Timer not firing | `systemctl list-timers`; `Persistent=true` replays after downtime |
| Service red | `systemctl status` + `journalctl -u nfl-props-*.service` |
| `exit=1` in `logs/nfl_board.log` | env file missing `NFL_DISCORD_WEBHOOK_URL` while board runs `--discord` |
| Empty board | `outputs/diagnostics/bovada_coverage_*.json` — fetch metadata vs no games posted |
| Stale `as_of` mid-season | Tuesday grade task didn't run; check `logs/nfl_grade.log` |
| `unmatched > 0` on grade | team alias or date drift; stop and fix before trusting grades |
