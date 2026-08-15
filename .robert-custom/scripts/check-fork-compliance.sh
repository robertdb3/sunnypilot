#!/usr/bin/env bash
#
# Pre-flash check: does this openpilot/sunnypilot checkout modify anything comma bans you for?
#
# Comma's rule, from docs/SAFETY.md in the checkout itself:
#   * Do not disable or nerf driver monitoring
#   * Do not disable or nerf excessive actuation checks
#   * If your fork modifies any of the code in opendbc/safety/:
#       - your fork cannot use the openpilot trademark
#       - your fork must preserve the full safety test suite and all tests must pass,
#         including any new coverage required by the fork's changes
#
# Forking is not the trigger. Those three surfaces are.
#
# Usage:  ./check-fork-compliance.sh [checkout-path]     (default /data/openpilot)
# Exit:   0 = clear   1 = safety code touched, run the suite   2 = stop, protected area touched

set -u

CHECKOUT="${1:-/data/openpilot}"

DM_DIR="openpilot/selfdrive/monitoring"
ACTUATION_FILE="openpilot/selfdrive/selfdrived/helpers.py"
SAFETY_DIR="opendbc_repo/opendbc/safety"

# not a -d test on .git: worktrees and submodules use a .git file, not a directory
if [ ! -d "$CHECKOUT" ] || ! git -C "$CHECKOUT" rev-parse --git-dir >/dev/null 2>&1; then
  echo "error: $CHECKOUT is not a git checkout" >&2
  echo "usage: $0 [checkout-path]   (default /data/openpilot)" >&2
  exit 2
fi

cd "$CHECKOUT" || exit 2

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
echo "checkout: $CHECKOUT"
echo "branch:   $BRANCH"
echo

# --- collect changed files -----------------------------------------------------------------
# working tree (modified, staged, untracked) ...
CHANGED="$(git status --porcelain 2>/dev/null | sed 's/^...//' | sed 's/.* -> //')"

# ... plus any local commits ahead of the branch's origin ref, if we can resolve one offline
BASE=""
CANDIDATES="@{upstream}"
# on a detached HEAD there is no branch name to build an origin ref from
[ "$BRANCH" != "HEAD" ] && CANDIDATES="origin/$BRANCH $CANDIDATES"
for ref in $CANDIDATES; do
  if git rev-parse --verify --quiet "$ref" >/dev/null 2>&1; then
    BASE="$ref"
    break
  fi
done

if [ -n "$BASE" ]; then
  COMMITTED="$(git diff --name-only "$BASE"..HEAD 2>/dev/null)"
  CHANGED="$(printf '%s\n%s\n' "$CHANGED" "$COMMITTED")"
  echo "comparing against: $BASE (plus uncommitted changes)"
else
  if [ "$BRANCH" = "HEAD" ]; then
    echo "note: detached HEAD, so there is no upstream branch to compare against."
  else
    echo "note: no origin ref for '$BRANCH' resolved locally."
    echo "      Run 'git fetch origin $BRANCH' for a full check."
  fi
  echo "      Checking uncommitted changes only -- local commits are NOT covered."
fi
echo

CHANGED="$(printf '%s\n' "$CHANGED" | grep -v '^$' | sort -u)"

if [ -z "$CHANGED" ]; then
  echo "No local modifications found. Nothing to check."
  exit 0
fi

FILE_COUNT="$(printf '%s\n' "$CHANGED" | wc -l | tr -d ' ')"
echo "$FILE_COUNT changed file(s):"
printf '%s\n' "$CHANGED" | sed 's/^/    /'
echo

# --- classify ------------------------------------------------------------------------------
HIT_DM="$(printf '%s\n' "$CHANGED" | grep "^$DM_DIR/" || true)"
HIT_ACTUATION="$(printf '%s\n' "$CHANGED" | grep "^$ACTUATION_FILE$" || true)"
HIT_SAFETY="$(printf '%s\n' "$CHANGED" | grep "^$SAFETY_DIR/" || true)"

STATUS=0

if [ -n "$HIT_DM" ] || [ -n "$HIT_ACTUATION" ]; then
  STATUS=2
  echo "=============================================================================="
  echo " STOP -- protected safety code modified"
  echo "=============================================================================="
  [ -n "$HIT_DM" ] && {
    echo
    echo "  Driver monitoring ($DM_DIR):"
    printf '%s\n' "$HIT_DM" | sed 's/^/      /'
  }
  [ -n "$HIT_ACTUATION" ] && {
    echo
    echo "  Excessive actuation checks ($ACTUATION_FILE):"
    printf '%s\n' "$HIT_ACTUATION" | sed 's/^/      /'
  }
  echo
  echo "  SAFETY.md: \"Do not disable or nerf driver monitoring / excessive actuation checks.\""
  echo "  A ban here is permanent and follows the hardware, even if you sell the device."
  echo "  Revert these before flashing."
  echo
fi

if [ -n "$HIT_SAFETY" ]; then
  [ "$STATUS" -eq 0 ] && STATUS=1
  echo "=============================================================================="
  echo " Panda safety code modified -- allowed, but you now own the test obligation"
  echo "=============================================================================="
  echo
  printf '%s\n' "$HIT_SAFETY" | sed 's/^/      /'
  echo
  echo "  Before flashing, the full safety suite must pass:"
  echo
  echo "      cd $CHECKOUT/$SAFETY_DIR/tests && ./test.sh"
  echo
  echo "  That enforces 100% line coverage (gcovr --fail-under-line=100), so any new"
  echo "  safety lines need new tests. MISRA C:2012 is checked separately:"
  echo
  echo "      cd $CHECKOUT/$SAFETY_DIR/tests/misra && ./test_misra.sh"
  echo
  echo "  A fork that modifies opendbc/safety/ also cannot use the openpilot trademark."
  echo
fi

# --- heuristic: safety-adjacent edits made from unprotected files ----------------------------
OTHER="$(printf '%s\n' "$CHANGED" \
  | grep -v "^$DM_DIR/" \
  | grep -v "^$ACTUATION_FILE$" \
  | grep -v "^$SAFETY_DIR/" || true)"

SUSPECT=""
if [ -n "$OTHER" ]; then
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    case "$f" in *.py|*.c|*.h|*.cc|*.cpp)
      if grep -qEl 'dmonitoring|awarenessStatus|DISTRACTED|ExcessiveActuation' "$f" 2>/dev/null; then
        SUSPECT="$(printf '%s\n%s' "$SUSPECT" "$f")"
      fi
      ;;
    esac
  done <<EOF
$OTHER
EOF
fi

SUSPECT="$(printf '%s\n' "$SUSPECT" | grep -v '^$' || true)"
if [ -n "$SUSPECT" ]; then
  echo "------------------------------------------------------------------------------"
  echo " Hint (not a verdict): these changed files reference driver monitoring or"
  echo " actuation-check symbols from outside the protected paths. Usually harmless --"
  echo " plenty of files legitimately read DM state. Worth a look, expect false positives."
  echo "------------------------------------------------------------------------------"
  printf '%s\n' "$SUSPECT" | sed 's/^/      /'
  echo
fi

# --- verdict ---------------------------------------------------------------------------------
case "$STATUS" in
  0)
    echo "=============================================================================="
    echo " Clear -- no protected code touched"
    echo "=============================================================================="
    echo
    echo "  Everything above is car port / UI / userspace code. Not a ban surface."
    ;;
  1) echo "Verdict: allowed, but run the safety suite before you flash." ;;
  2) echo "Verdict: do not flash." ;;
esac

exit "$STATUS"
