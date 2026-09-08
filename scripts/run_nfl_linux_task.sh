#!/usr/bin/env bash
set -u

TASK=${1:?task name is required}
PROJECT_DIR=${PROJECT_DIR:-"$HOME/nfl_props"}
ENV_FILE=${NFL_PROPS_ENV_FILE:-"$HOME/.config/nfl_props/env"}
PYTHON_EXE=${NFL_PROPS_PYTHON_EXE:-"$PROJECT_DIR/.venv/bin/python"}
LOG_DIR="$PROJECT_DIR/logs"
LOCK_FILE=${NFL_PROPS_LOCK_FILE:-"$HOME/.local/state/nfl_props/run.lock"}

mkdir -p "$LOG_DIR" "$(dirname "$LOCK_FILE")"

if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

export TZ=${TZ:-America/Detroit}
export PYTHONUNBUFFERED=1

if [[ ! -x "$PYTHON_EXE" ]]; then
    printf 'Project Python interpreter is unavailable: %s\n' "$PYTHON_EXE" >&2
    exit 1
fi

case "$TASK" in
    board)
        LOG_FILE="$LOG_DIR/nfl_board.log"
        TIMEOUT=45m
        COMMAND=("$PYTHON_EXE" run_board.py --discord)
        REQUIRED_SECRET=NFL_DISCORD_WEBHOOK_URL
        ;;
    grade)
        LOG_FILE="$LOG_DIR/nfl_grade.log"
        TIMEOUT=90m
        # Refresh the new week even when there are no completed games to grade.
        COMMAND=(bash -c 'grade_exit=0; "$1" grade.py || grade_exit=$?; "$1" -m nfl_props.cli refresh-data && "$1" -m nfl_props.cli build && "$1" -m nfl_props.cli rebuild-state && "$1" -m nfl_props.cli rebuild-state-v2; refresh_exit=$?; if (( grade_exit != 0 )); then exit "$grade_exit"; fi; exit "$refresh_exit"' _ "$PYTHON_EXE")
        REQUIRED_SECRET=
        ;;
    *)
        printf 'Unknown task: %s\n' "$TASK" >&2
        exit 2
        ;;
esac

exec >>"$LOG_FILE" 2>&1
printf '%s  Starting %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$TASK"

if [[ -n "$REQUIRED_SECRET" && -z "${!REQUIRED_SECRET:-}" ]]; then
    printf '%s  FAILED: %s is not configured in %s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$REQUIRED_SECRET" "$ENV_FILE"
    exit 1
fi

cd "$PROJECT_DIR" || exit 1
exec 9>"$LOCK_FILE"
printf '%s  Waiting for the shared task lock\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')"
flock 9
printf '%s  Running: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "${COMMAND[*]}"

timeout --signal=TERM --kill-after=2m "$TIMEOUT" "${COMMAND[@]}"
EXIT_CODE=$?
printf '%s  Finished %s with exit code %s\n' \
    "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$TASK" "$EXIT_CODE"
exit "$EXIT_CODE"
