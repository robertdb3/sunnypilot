#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <candidate-checkout> <customization-assets>" >&2
  exit 2
fi

CANDIDATE=$(cd "$1" && pwd)
ASSETS=$(cd "$2" && pwd)
MANIFEST="$CANDIDATE/CUSTOM_FORK_MANIFEST.json"
PYTHON_BIN=${PYTHON_BIN:-python3}

if [ ! -f "$MANIFEST" ]; then
  echo "error: candidate has no provenance manifest" >&2
  exit 2
fi

BASE=$("$PYTHON_BIN" -c 'import json,sys; print(json.load(open(sys.argv[1]))["upstream"]["commit"])' "$MANIFEST")
git -C "$CANDIDATE" cat-file -e "$BASE^{commit}"

"$PYTHON_BIN" "$ASSETS/automation/verify_candidate.py" "$CANDIDATE" "$BASE" "$ASSETS"

link="$ASSETS/sunnypilot"
if [ -e "$link" ] || [ -L "$link" ]; then
  unlink "$link"
fi
ln -s "$CANDIDATE" "$link"
trap 'unlink "$link" 2>/dev/null || true' EXIT

"$PYTHON_BIN" -m unittest discover -s "$ASSETS/tests" -p 'test_*.py' -v

python_files=()
while IFS= read -r file; do
  python_files+=("$CANDIDATE/$file")
done < <(git -C "$CANDIDATE" diff --name-only "$BASE" -- '*.py')

if [ "${#python_files[@]}" -gt 0 ]; then
  "$PYTHON_BIN" -m py_compile "${python_files[@]}"
fi

git -C "$CANDIDATE" diff --check "$BASE"
