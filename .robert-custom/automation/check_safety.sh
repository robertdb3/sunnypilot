#!/usr/bin/env bash
# Run opendbc's panda safety obligations against a candidate whose patches change safety code:
# the full safety suite, 100% line coverage, and MISRA C:2012, as opendbc's own CI does
# (opendbc/safety/tests/test.sh and misra/test_misra.sh, minus their uv environment setup).
#
#   check_safety.sh <candidate-checkout>
#
# Needs numpy, pycapnp, cffi, gcovr and comma's cppcheck build in PYTHON_BIN's environment, plus gcc/gcov.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <candidate-checkout>" >&2
  exit 2
fi

OPENDBC=$(cd "$1/opendbc_repo" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
TESTS="$OPENDBC/opendbc/safety/tests"
export PYTHONPATH="$OPENDBC"

cd "$TESTS"
SAFETY_COVERAGE=0 "$PYTHON_BIN" -m unittest discover -s . -q

rm -f libsafety/*.gcda libsafety/*.gcno
SAFETY_COVERAGE=1 "$PYTHON_BIN" -m unittest discover -s . -q
"$PYTHON_BIN" -m gcovr -r "$OPENDBC/opendbc/safety" -d --fail-under-line=100 -e '^libsafety' > /dev/null
echo "safety suite passed with 100% line coverage"

CPPCHECK_DIR=$("$PYTHON_BIN" -c "import cppcheck; print(cppcheck.DIR)")
output=$(mktemp)
"$CPPCHECK_DIR/cppcheck" --inline-suppr -I "$OPENDBC" -I "$(cc -print-file-name=include)" \
  --suppress=missingIncludeSystem --suppress='*:*include/*' --suppressions-list="$TESTS/misra/suppressions.txt" \
  --error-exitcode=2 --check-level=exhaustive --safety --platform=arm32-wchar_t4 \
  -D__GNUC__=9 '-D__has_include_next(x)=0' --std=c11 --enable=all --enable=unusedFunction --addon=misra \
  "$TESTS/misra/main.c" > "$output" 2>&1 || true
# cppcheck does not always set its exit code for MISRA findings, so read the output (as test_misra.sh does)
if grep -e "misra violation" -e "error" -e "style: " "$output"; then
  echo "error: MISRA violations" >&2
  exit 1
fi
echo "MISRA C:2012 clean"
