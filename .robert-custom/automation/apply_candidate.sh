#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <candidate-checkout> <customization-assets>" >&2
  exit 2
fi

CANDIDATE=$(cd "$1" && pwd)
ASSETS=$(cd "$2" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}

if ! git -C "$CANDIDATE" rev-parse --verify HEAD >/dev/null 2>&1; then
  echo "error: candidate is not a git checkout: $CANDIDATE" >&2
  exit 2
fi

if [ ! -f "$CANDIDATE/prebuilt" ]; then
  echo "error: upstream staging is not an installable prebuilt snapshot" >&2
  exit 2
fi

apply_one() {
  local patch=$1
  echo "checking ${patch#$ASSETS/}"
  git -C "$CANDIDATE" apply --check --whitespace=error-all "$patch"
  git -C "$CANDIDATE" apply --whitespace=error-all "$patch"
}

for number in 0001 0002 0003 0004 0005 0006; do
  patch=$(find "$ASSETS/patches" -maxdepth 1 -type f -name "$number-*.patch" -print)
  if [ "$(printf '%s\n' "$patch" | grep -c .)" -ne 1 ]; then
    echo "error: expected exactly one $number patch" >&2
    exit 2
  fi
  apply_one "$patch"
done

# The comma 3X staging branch is prebuilt: params_keys.h is source documentation, while the
# shipped libparams_c remains native code from the upstream build. This narrow compatibility
# layer supplies metadata only for our five keys and preserves strict rejection of all others.
apply_one "$ASSETS/ports/staging-30a9cdc/0001-prebuilt-custom-params-compat.patch"

for number in 0008 0009 0010 0011 0015 0016 0017 0018 0019 0020; do
  patch=$(find "$ASSETS/patches" -maxdepth 1 -type f -name "$number-*.patch" -print)
  if [ "$(printf '%s\n' "$patch" | grep -c .)" -ne 1 ]; then
    echo "error: expected exactly one $number patch" >&2
    exit 2
  fi
  apply_one "$patch"
done

# Panda safety code is compiled into the panda, so a patch that changes it only takes effect in
# rebuilt firmware. build_panda_firmware.sh first proves it reproduces upstream's firmware exactly.
if ! git -C "$CANDIDATE" diff --quiet HEAD -- opendbc_repo/opendbc/safety ':(exclude)opendbc_repo/opendbc/safety/tests'; then
  bash "$ASSETS/automation/build_panda_firmware.sh" install "$CANDIDATE" "$(git -C "$CANDIDATE" rev-parse HEAD)"
fi

git -C "$CANDIDATE" diff --check
"$PYTHON_BIN" "$ASSETS/automation/prepare_candidate.py" "$CANDIDATE" "$ASSETS"
