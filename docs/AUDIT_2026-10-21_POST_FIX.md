# Post-fix performance review — 2026-10-21

Read-only audit of weeks 1–6 (kickoffs through 2026-10-20T00:15Z) asking one
question: did the 2026-10-18/19 selection fixes stop the totals bleed, and what
is lagging now. No code was changed for this review.

> **Superseded in scope (2026-10-22).** The season line below is the **pooled**
> basis — every priced reference, PLAYABLE and NO_VALUE together. That pooled
> headline was retired as the advertised record on 2026-10-22; the bettable
> record is PLAYABLE-only (**89-92-1, -10.80u, -5.93%**, 182 refs) and the
> pooled figure is now printed as a labelled diagnostic. The cuts, cohorts and
> argument in this document are unaffected. See
> `docs/INTEGRITY_AUDIT_2026-10-22.md`.

## Verdict

- Season (pooled over all 263 priced refs): **137-124-2, -14.86u, -5.7% ROI** (flat 1u at printed odds). *Corrected 2026-10-22: this line read 138-124-1 — the NYG@WAS 27-27 tie is a push, not a win; the unit and ROI figures were already right.*
- The bleed is **totals**, not the board as a whole: totals -11.16u (-12.8%) against spreads **+1.95u (+2.2%)**.
- Inside totals, the bleed is **PLAYABLE totals**: -12.66u (-17.3%, n=73). Totals the board *rejected* made **+1.50u (+10.7%, n=14)**.
- Both fixes landed 14 refs ago (n=14 post-fix: 10-4, +3.69u). Direction is right; the sample is far too small to call it fixed.
- The largest unresolved defect is not calibration: **the forecast path has no EV/edge ceiling**, so a mispriced line ships as a maximum-confidence PLAYABLE.

## Basis and method

Grading basis is board/snapshot print-time attribution, not the weekly artifacts:

- Ledger `outputs/ledger/forecast_ledger.jsonl`, 201 prints, last 2026-10-19T23:15:37Z.
- One row per (forecast_id, family, side, line) from the **latest print before kickoff**; `outputs/snapshots/<game-date>/<fid>.json` picks override the board when a pre-kickoff snapshot exists (mirrors `latest_snapshot_before` in `grade_forecast.py`).
- Graded against `data/raw/games.csv` finals, flat 1u, `decimal - 1` on wins.
- Tie = push (the `grade_forecast.py` convention); NFL moneyline ties are refunds at most books.
- Cohorts: **pre-fix** < 2026-10-18T18:47:03Z (first v3 totals print), **post-fix** ≥ that timestamp.

263 rows graded: 249 pre-fix, 14 post-fix. Status here is the forecast-path tier (`PLAYABLE` / `NO_VALUE`) — Core/Lean/Watch live in the retired legacy board and appear nowhere in this path.

Reproduction scripts used for this audit (VM-local, not committed): `/tmp/nfl_build2.py` (row builder) and `/tmp/nfl_cuts3.py` (cuts).

### Why the season number moves

Two grading corrections matter and both are now applied:

- **WAS@SF spread** printed a dead Polymarket rung (`SF +17.5`; real line -7.5). At the printed line it grades a win (+0.98u); at the true line SF won by 6 and it is a **loss (-1.00u)**. Snapshot directories are keyed by *game date*, so a script that rebuilds the snapshot path from `kickoff_utc[:10]` (a 00:15Z Monday kickoff rolls into the next UTC day) silently misses the repair and scores this as a win. The repo grader scans, so it is not affected — but any ad-hoc script is.
- **NYG@WAS moneyline** was a 27-27 tie; graded as a push.

Net effect versus a builder that had the tie as a loss and missed the WAS repair: -14.86u instead of -15.86u. The official week-6 artifact (`grade_weekly_2026w06_20261020_073002.json`, 21-17, +0.32u priced) predates both line repairs and Monday's game; the honest week-6 figure is **20-18, -1.68u** (BUF@LV -1.00, WAS@SF -1.00).

## Season cuts (n=263)

- moneyline: 88, 52-35-1, **-5.66u (-6.4%)**
- spread: 88, 46-41-1, **+1.95u (+2.2%)**
- total: 87, 39-48-0, **-11.16u (-12.8%)**

By status:

- PLAYABLE: 182, 89-92-1, **-10.80u (-5.9%)**
  - PLAYABLE/moneyline: 36, 20-16, **+0.20u (+0.5%)** — fine, no action
  - PLAYABLE/spread: 73, 38-34-1, +1.67u (+2.3%)
  - PLAYABLE/total: 73, 31-42, **-12.66u (-17.3%)**
- NO_VALUE: 81, 48-32-1, -4.07u (-5.0%)
  - NO_VALUE/moneyline: 52, 32-19-1, -5.86u — the filter is on the right side of its own line
  - NO_VALUE/total: 14, 8-6, **+1.50u (+10.7%)** — the filter is on the *wrong* side here

By week:

- w1: -4.30u · w2: -6.08u · w3: +1.52u · w4: +0.22u · w5: -4.54u (20-20-2) · w6: -1.68u (20-18)

w6 detail: totals 8-4 **+3.52u**, spread 5-8 -3.16u, moneyline 7-6 -2.04u.

## Pre-fix vs post-fix

Pre-fix (n=249): 127-121-1, **-18.56u (-7.5%)**

- moneyline 83, 48-34-1, -6.49u · spread 83, 43-39-1, +0.98u · total 83, 36-47, **-13.05u (-15.7%)**
- PLAYABLE/total 69, 28-41, **-14.55u (-21.1%)** · under 33, 12-21, **-9.88u (-29.9%)**

Post-fix (n=14): 10-4, **+3.69u (+26.4%)**

- moneyline 5, 4-1, +0.83u · spread 5, 3-2, +0.97u · total 4, 3-1, **+1.89u**
- PLAYABLE 8, 5-3, +1.23u · PLAYABLE/total 4, 3-1, +1.89u · PLAYABLE/spread 2, 0-2, -2.00u
- The literal 2026-10-19 window (prints ≥ 00:00Z, WAS@SF only, n=3) is **+0.19u**. The four post-fix totals are ARI@LA, BUF@LV, DAL@GB, WAS@SF — all unders; the single loss is BUF.

Two of the four post-fix totals were unders the earlier board had suppressed, so the gate has never produced a graded suppression we can score: `UNDER_GAP_BELOW_FLOOR` fired 3× total (ARI@LA 18:47Z gap 1.7; LAC@KC 18:47Z and 19:37Z, gaps 1.5/2.6) and has not fired since. ARI@LA under was gated at 18:47Z and re-emitted as PLAYABLE at 19:37Z, where it won (+0.94u).

### Is +1.89u over 4 totals meaningful? No.

- Pre-fix totals per bet: mean -0.157u, sd 0.964, se 0.106 (n=83).
- Post-fix totals: mean +0.473u, sd 0.852, se 0.426 (n=4).
- Difference 0.63u ± 0.44 se → t ≈ 1.4, one-sided p ≈ 0.08. Consistent with the fix working; equally consistent with luck.

## Totals deep dive

Side: over 50, 24-26, -3.17u (-6.3%); **under 37, 15-22, -7.99u (-21.6%)**; pre-fix under 33, 12-21, **-9.88u (-29.9%)**. The gate targeted exactly the losing side, which is why four clean post-fix unders is the right first read.

Edge bands (pre-fix totals) — **no monotone relationship between edge and outcome**:

- negative edge: 7, 4-3, +0.82u
- 0–3%: 13, 7-6, +0.59u
- **3–6%: 13, 4-9, -5.40u (-41.5%)**
- 6–10%: 27, 11-16, -5.29u
- 10%+: 23, 10-13, -3.78u

Post-fix: 6–10% n=1 +0.94u; 10%+ n=3 +0.95u.

This is the important negative result: on totals the edge score carries no usable ranking, and the board's *rejections* (NO_VALUE/total +10.7%) beat its acceptances. Raising the totals edge floor is therefore not the obvious next lever — the score itself is the suspect.

## Data and process health

- **No on-VM scheduler.** `nfl-props-board.timer` and `nfl-props-grade.timer` are disabled/inactive (last runs Sep 8–10); no crontab, `atq`, or tmux entry. Boards still arrived for the Oct 18–19 slate (15:00Z, 18:47Z, 19:37Z, 22:02Z, 23:15Z) and the weekly grade at 07:30Z Oct 20, so an external trigger is live. There is **no freshness alarm**: if that trigger stops, the failure looks like an off-season.
- **No week-7 board yet** (last ledger print 2026-10-19T23:15:37Z). Week 7 opens 2026-10-22.
- **The injury feed will be stale for week 7.** `data/processed/personnel.json` mtime 2026-10-20T00:48:39Z (delta_check exit 0, WAS@SF debits applied); `.bak` is 2026-10-11. No ESPN pull since Monday night.
- **The v3 totals version is stable.** `nfl-total-ridge-v3` first appears 18:47Z Oct 18 and both 19:37Z boards carry it for every game. The ledger shows no v2 regression.
- **Dispersion is global, not game-specific.** `margin_sd` = 13.197 and `total_sd` = 13.325 on all 263 rows (state-level `resid_sd`), so the 1.15 residual scale is a single flat multiplier — no game gets a tighter or wider distribution than another.
- **The absurd-edge defect is live.** 7 refs carry edge ≥ 0.25, all PLAYABLE, e.g. WAS@SF spread `p_model=0.9769`, `edge=+0.4719`, `ev=0.9345`; BUF@LV spread `p_model=0.9785`. Their combined P&L is **+1.55u** (4-3) — the group that should never ship is the only profitable subgroup, and that profit is dead-line luck. Excluding them: 256 refs, -17.42u (-6.8%). `nfl_props/forecast.py` has no EV or edge ceiling; `nfl_props/tiers.py:21 EV_MAX=0.08` guards only the retired legacy board.
- **Manual line repairs are invisible to the artifacts.** `snapshots/2026-10-18/g:2026_06_BUF_LV.json` was rewritten 2026-10-20 14:41 — after the week-6 grade; `snapshots/2026-10-19/g:2026_06_WAS_SF.json` at 23:58 with a `.bak`. The BUF file carries a `line_correction_note`; the WAS file carries no note at all, only the `.bak`. Neither correction is in the ledger, so the public board and the artifact disagree about what was played.
- **Discord delivery is healthy.** `[forecast] discord ok` on all three Oct 18–19 boards, `[grade_forecast] weekly discord ok` Oct 20; no 403 or Cloudflare entries in `logs/` since 16425d9.
- **Price source is not a lever.** bovada 180, -9.41u (-5.2%); polymarket 82, -5.45u (-6.7%). The week-6 polymarket collapse (+6.19u vs -4.89u) does not survive at season scale — it was one week.
- **Two-source prices look worse, but confounded.** `n_sources=2`: 123, -12.22u (-9.9%); `n_sources=1`: 102, -1.96u (-1.9%). Two-source rows skew toward games where Polymarket quotes a line, so this is a market-type confound, not a pricing insight. Observation only.

## Ranked candidates

1. **No EV/edge ceiling on the forecast path** (correctness, highest value). A 47% edge with `p_model=0.98` is not a betting opinion, it is a broken line — and today it becomes the board's most confident PLAYABLE. Add a hard ceiling (reject or annotate at |edge| ≳ 15–20% or |ev| ≳ 0.30, logging `DEAD_LINE_SUSPECT`); the same guard doubles as a detector for residual Polymarket ladder bugs. Evidence: 7 refs ≥ 0.25 edge, 2 of them needing manual repair, netting +1.55u of luck that cannot repeat now that 4dbe8d7 is in.
2. **Residual dead-line risk in Polymarket spread resolution** (n=2 graded, both repaired by hand, both pre-4dbe8d7). The sign+distance bound in `_resolve_line` (≤1.0 pt against Bovada) plus the liquidity/bid filter cut most of it, but DFS/ladder rungs can still land inside 1.0 pt on the wrong rung. Unmeasurable until a post-fix spread misprices; the cheap proxy is candidate 1's ceiling-hit count per board, target 0.
3. **The totals edge score looks uninformative** (the substantive one). Non-monotone bands, NO_VALUE/total +10.7% against PLAYABLE/total -17.3%, worst band the 3–6% middle. Before touching thresholds, test whether the totals edge is predictive at all (rank correlation of edge against outcome units over n=87); tightening a noise score only shrinks n.
4. **Totals bleed: direction right, unproven.** Four post-fix refs is nothing. Decision rule for the next review: 40 graded post-fix totals (≈3–4 weeks at 12/week) put a 90% band of ±0.25u per bet around the post-fix mean; if cumulative post-fix totals are worse than ≈ -8u over those 40, the bleed survived the fix.
5. **Gate effectiveness is unmeasured.** 3 firings ever, all on the Oct 18 boards, and the one game it gated then re-emitted (ARI@LA under) won. Nothing to act on; have the gate emit a machine-readable suppression record (family, side, line, gap) so a later review can score hypothetical outcomes instead of counting log lines.
6. **The weekly report hides the bleed.** The `priced` ROI line pools NO_VALUE with PLAYABLE, so a -17.3% PLAYABLE/totals block reads as a -5.7% board. A status-split ROI line is a reporting fix only.
7. **Operational:** week 7 has no board, the injury feed is 4–5 days stale, and the external trigger has no freshness alarm. Refresh personnel before the next board; add a stale-artifact assertion at grade time.
8. **No action on moneyline PLAYABLE** (+0.20u, n=36) — the only family/status block that is behaving.

## What would change this answer

- **n=40 graded post-fix totals** (≈3–4 weeks): enough to falsify "the gate plus the 1.15 scale fixed the under bleed" (cumulative worse than ≈ -8u ⇒ not fixed).
- **n≈230 post-fix totals** (≈19 weeks): enough for 80% power to separate the post-fix rate from the pre-fix -15.7%. The program will realistically never have that, so decisions must be made on the block test above, not on significance.
- **A post-fix spread misprice** (edge ≥ 20% with no manual repair): would mean candidate 2's risk is still live and 4dbe8d7 is incomplete.
- **A shift in PLAYABLE/spread** (currently +1.67u, n=73): it is the block keeping the season near break-even. If it turns, the board is negative on every family.

## Fix landed: EV/edge ceiling (2026-10-21)

Candidate 1 shipped. Commits: `32a9a33` (failing tests), `2b51cbd`
(guard). Full suite `224 tests, OK` (215 before + 9 new); no other
threshold, tier, or gate touched.

### What shipped

- `nfl_props/forecast.py`: `EDGE_CEILING = 0.20`, `EV_CEILING = 0.30`
  (`NFL_EDGE_CEILING`, `NFL_EV_CEILING` env overrides),
  `DEAD_LINE_SUSPECT = "DEAD_LINE_SUSPECT"`, and
  `apply_edge_ceiling(ref, game)`.
- Applied to every `fc.references` at the end of `build_game_forecast`.
  A ref that is currently `PLAYABLE` and has `|edge| >= 0.20` or
  `|ev| >= 0.30` becomes `NO_VALUE`, keeps its prices/probabilities, and
  gets `DEAD_LINE_SUSPECT` appended to `note`. Nothing is dropped from
  the board artifact or the ledger, so the hit count stays observable.
- Each demotion logs
  `[edge-ceiling] <game> <family> <side> line=... p_model=... edge=+0.0000 ev=+0.0000 DEAD_LINE_SUSPECT`
  to stderr at build time.

Status is assigned in `nfl_props/references.py` (`status_for`,
`build_reference`), which the forecast path shares with the retired
legacy board, so the guard was placed at the forecast build chokepoint
(`build_game_forecast`) instead of in the shared status function.
`references.py` is untouched; legacy tiers and the UNDER gap gate are
unchanged.

### Dry run (read-only, no writes)

Replayed 5 board artifacts (Oct 18 15:00Z, 18:47Z, 19:37Z; Oct 19
22:02Z, 23:15Z) plus the 36 picks in `outputs/snapshots/2026-10-18`
and `2026-10-19` through `apply_edge_ceiling` on the VM. Scratch
script `/tmp/nfl_edge_ceiling_dryrun.py`, not committed; no artifact,
snapshot, or ledger file was written.

- 15:00Z: 33 rows, 4 demoted
- 18:47Z: 12 rows, 3 demoted
- 19:37Z: 12 rows, 3 demoted
- 22:02Z: 3 rows, 2 demoted
- 23:15Z: 3 rows, 2 demoted
- snapshots: 36 rows, 6 demoted
- **Total: 99 rows replayed, 20 demotions, 0 status changes that were
  not a ceiling hit.**

Nine `(game, family, side, line)` keys demoted (7 logical refs; the
repaired snapshot lines are separate keys):

- ARI @ LA spread home 14.5 — edge +0.4707, ev +0.9321
- BUF @ LV spread away 21.5 and -5.5 (repaired line, same model
  inputs) — edge +0.4785, ev +0.9569
- WAS @ SF spread home 17.5 and -7.5 (repaired line) — edge +0.4719,
  ev +0.9345
- WAS @ SF total under 51.5 — edge +0.2313, ev +0.4010
- DAL @ GB total under 53.5 — edge +0.1749, ev +0.3570
- BUF @ LV total under 54.5 — edge +0.1563, ev +0.3035
- NYJ @ NE spread home -7.5 — edge +0.1757, ev +0.3699

The audit's edge >= 0.25 set is 7 unique graded refs, all `PLAYABLE`.
All 7 demote under the guard: 5 of them (MIN@CHI under, PHI@CHI ML
home, GB@TB over, MIA@MIN spread, LAC@SEA spread) were checked
directly against their own board artifacts in a second scratch pass;
BUF@LV and WAS@SF were covered by the Oct 18-19 replay in both the
original and repaired line form.

### The "exactly the absurd set" check does not hold literally

The step-3 criterion was "exactly the refs from the audit's edge >= 0.25
set, and nothing else." With the approved 0.20/0.30 thresholds, the
Oct 18-19 replay also demotes 4 refs below the audit's 0.25 line:

- NYJ @ NE spread home -7.5 — ev +0.3699 (edge +0.1757)
- DAL @ GB total under 53.5 — ev +0.3570 (edge +0.1749)
- BUF @ LV total under 54.5 — ev +0.3035 (edge +0.1563)
- WAS @ SF total under 51.5 — edge +0.2313, ev +0.4010

The first three trip only the EV ceiling; WAS@SF under trips both the
0.20 edge ceiling and the EV ceiling. No row that fails both ceilings
changed status (`unexpected=0`). This is the approved thresholds doing
what they say, not a wiring leak - but it means the guard removes more
than the audit's absurd-edge group, and those four rows were graded
PLAYABLE on live boards. Flagged rather than tuned; the constants are
as decided.

### Monitoring

Per-board `[edge-ceiling]` lines are the dead-line metric; target is 0
outside known manually repaired lines. A hit on a freshly built board
with no repair is the signal that residual Polymarket spread resolution
(audit candidate 2) is still live.
