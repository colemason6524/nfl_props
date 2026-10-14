# Smart Delta Alerts — Spec

**Status:** Draft for Cole's review. No implementation yet.
**Date:** 2026-10-13

## Problem

Boards print 1-2x daily with full-slate outlook. Late injuries/scratches (inactives ~90 min pre-kickoff, NBA late scratches ~30 min pre-tip) can materially change pick value. Current one-off silent verifications catch mispricings but don't surface actionable deltas to Cole.

## Solution

Smart delta alerts: targeted updates posted to the same Discord channel as the original board, only when late news materially changes something.

## Trigger Logic

An alert fires when **any** of these are true after recompute with late injury data:

1. **Edge shift > 3%** — a pick's model edge moves more than 3 points in either direction
2. **Play/no-play flip** — a pick crosses the playable threshold (in either direction)
3. **Key player news** — a star or high-impact player is confirmed in/out, regardless of model delta (transparency)

**Alert on both directions:** "Expected out but plays" is just as important as "expected in but out."

**Final status only:** If a player moves questionable → doubtful → out across the day, note the movement but alert on the confirmed pre-game status. Questionable→out is an update, not a correction.

**Verify before output:** Sources should agree before posting. Don't alert on single-source rumors.

**No alert when:**
- Edge shift < 3% and no flip (noise)
- News is already priced in the board (no delta)
- Only role-player news with minimal impact

## Two-Tier Alert System

**Tier 1 — Delta Alert** (small change):
- Edge shift >3%, single pick flip, role player news
- Short format, just the delta
- Original board remains valid

**Tier 2 — Re-board** (large change):
- Trigger: projected margin moves **>5 points** (NBA equivalent per sport)
- The game prediction is fundamentally different — spread, total, props all move
- Repost that game's full prediction block with new numbers
- Example: star scratched 30 min before tip, entire game repriced

## Batching

- **One combined post** per check window covering all impacted picks
- If a single game triggers Tier 2, it gets its own re-board block within the combined post

## Per-Sport Check Timing

Checks run AFTER the relevant injury/inactive information is available:

| Sport | Check Times | Source |
|-------|-------------|--------|
| NFL (Sun) | 11:30am, 2:30pm, 6:45pm ET | Official inactives per window |
| NFL (TNF/MNF) | 7:15pm ET | Official inactives |
| NBA | ~6:30pm ET (early tips), ~9:00pm ET (late tips) | Injury report + late scratches |
| NHL | ~45 min before puck drop, batched | Goalie confirmations |
| CFB (Sat) | Per wave (11am, 3pm, 7pm ET) | Availability reports |
| WNBA | ~1hr before tip | Injury report |
| MLB | ~1hr before first pitch | Lineup cards |
| Soccer | ~1hr before kickoff | Team news |

## Pipeline

For each check:
1. Pull latest injury/inactive data (existing pipelines)
2. Recompute affected game projections with updated availability
3. Compare vs. the printed board: edge delta, play/no-play status
4. If trigger threshold met → format and post alert
5. If no trigger → stay silent, log to diary

## Alert Format

Short, delta-only. Posted to the same Discord channel as the board.

```
🔄 UPDATE 6:45pm ET — Bills @ Rams

Josh Allen (ankle) INACTIVE
Bills -3.5 → -1.0 | Edge 6.1% → 1.2%
Was: PLAYABLE → Now: NO PLAY
```

```
🔄 UPDATE 9:00pm ET — Lakers @ Warriors

LeBron James (rest) OUT — late scratch
Lakers +4.5 → +8.0 | Edge 4.2% → -1.5%
Was: PLAYABLE → Now: NO PLAY
```

```
🔄 UPDATE 2:30pm ET — Chiefs @ Eagles

No material changes. All picks stand.
```
*(Only posted if there were pre-game questions — otherwise silent)*

## Delivery

- **Same channel** as the sport's board (NFL update in NFL channel, etc.)
- Posted as a follow-up message, not a new board
- Timestamped so it's clear when the info landed
- Silent when nothing material changed

## Implementation Pieces

1. **Recompute-and-compare module** (per sport): takes latest injury data, recomputes projections, diffs vs. printed board
2. **Trigger evaluator**: applies the 3% / flip / key-player rules
3. **Alert formatter**: builds the Discord message from the delta
4. **Scheduler**: per-sport check times as one-off or recurring crons
5. **Board state store**: need to know what was printed to compute deltas (board artifact with timestamp)

## Open Questions

- NBA: how to handle the 5:30pm ET injury report vs. actual late scratches? Two-stage check?
- Should alerts also cover line movement (not just injuries)? Currently scoped to injuries only.
- Per-sport Tier 2 threshold equivalents (5 pts NBA → ? NFL, ? NHL, ? MLB)

## Resolved (per Cole 2026-10-13)

- **Scope:** Alert on picks impacted. If in/out moves anything, flag it.
- **Direction:** Both. "Expected out but plays" = "expected in but out."
- **Status changes:** Alert on final confirmed status. Note movement history.
- **Verification:** Sources should agree before posting. Updates ≠ corrections.
- **Batching:** One combined post per check window.
- **Grading:** Most up-to-date wins. "No play" by tip = graded as no play.
- **Tier 2 threshold:** 5-point margin move triggers re-board.
- **Sports (phase 1):** NFL, NBA, NHL (highest impact, best data).

## Not In Scope

- Rebuilding full boards (existing board pipeline unchanged)
- Pre-game predictions for games not on the board
- Automated bet placement
