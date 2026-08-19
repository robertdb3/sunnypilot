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

for number in 0008 0009 0010 0011; do
  patch=$(find "$ASSETS/patches" -maxdepth 1 -type f -name "$number-*.patch" -print)
  if [ "$(printf '%s\n' "$patch" | grep -c .)" -ne 1 ]; then
    echo "error: expected exactly one $number patch" >&2
    exit 2
  fi
  apply_one "$patch"
done

# Roll out independently reviewable safety stages. All source patches can be maintained and
# tested on master while the install branch advances only when the preceding stage has passed its
# parked and driving validation.
stage=$(sed -n '1p' "$ASSETS/release-stage")
case "$stage" in
  tailscale) last_patch=0012 ;;
  visual) last_patch=0013 ;;
  speed) last_patch=0014 ;;
  *) echo "error: unknown release stage: $stage" >&2; exit 2 ;;
esac

for number in 0012 0013 0014; do
  if [ "$number" -gt "$last_patch" ]; then
    continue
  fi
  patch=$(find "$ASSETS/patches" -maxdepth 1 -type f -name "$number-*.patch" -print)
  if [ "$(printf '%s\n' "$patch" | grep -c .)" -ne 1 ]; then
    echo "error: expected exactly one $number patch" >&2
    exit 2
  fi
  apply_one "$patch"
done

git -C "$CANDIDATE" diff --check
"$PYTHON_BIN" "$ASSETS/automation/prepare_candidate.py" "$CANDIDATE" "$ASSETS"
