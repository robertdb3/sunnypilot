#!/usr/bin/env bash
# Rebuild the panda firmware for a candidate whose patches change panda safety code.
#
#   build_panda_firmware.sh install <candidate-checkout> <upstream-commit>
#       build from the candidate's safety code and install the firmware into the candidate
#   build_panda_firmware.sh check <candidate-checkout> <upstream-commit>
#       build from the candidate's safety code and require the candidate's firmware to match
#
# Safety code is compiled into the panda, so a safety patch only takes effect in rebuilt firmware.
# Before building anything of ours, this rebuilds upstream's own firmware from upstream's sources and
# requires it to match the binaries upstream shipped, byte for byte. That proves the toolchain and every
# build input are upstream's, so the only difference in our firmware is our source change. If upstream's
# firmware does not reproduce, nothing is built or installed.
set -euo pipefail

if [ "$#" -ne 3 ] || { [ "$1" != install ] && [ "$1" != check ]; }; then
  echo "usage: $0 install|check <candidate-checkout> <upstream-commit>" >&2
  exit 2
fi

MODE=$1
CANDIDATE=$(cd "$2" && pwd)
BASE=$3
PYTHON_BIN=${PYTHON_BIN:-python3}

# what pandad flashes (panda_h7.bin.signed) and the files built alongside it
FW_FILES=(panda_h7.bin.signed panda_h7/main.bin panda_h7/main.elf)
# main.elf embeds the build directory in its debug info, so only the flashed images are reproducible
CHECK_FILES=(panda_h7.bin.signed panda_h7/main.bin)
# what must reproduce exactly from upstream's sources
GATE_FILES=(panda_h7.bin.signed panda_h7/main.bin bootstub.panda_h7.bin)

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# upstream's sources, exactly as shipped
mkdir -p "$WORK/upstream"
git -C "$CANDIDATE" archive "$BASE" opendbc_repo panda uv.lock | tar -x -C "$WORK/upstream"

# The prebuilt snapshot ships panda's firmware sources but not its build scripts. Take them from the
# panda commit upstream's build used: the sunnypilot master commit named in the staging commit message
# pins it as a submodule.
master=$(git -C "$CANDIDATE" log -1 --format=%B "$BASE" | sed -n 's/.*master commit: \([0-9a-f]\{40\}\).*/\1/p' | head -n1)
if [ -z "$master" ]; then
  echo "error: upstream commit $BASE names no sunnypilot master commit" >&2
  exit 2
fi
git init -q "$WORK/sunnypilot"
git -C "$WORK/sunnypilot" remote add origin https://github.com/sunnypilot/sunnypilot.git
git -C "$WORK/sunnypilot" fetch -q --depth=1 --filter=blob:none origin "$master"
panda_sha=$(git -C "$WORK/sunnypilot" ls-tree FETCH_HEAD panda | awk '$2 == "commit" {print $3}')
panda_url=$(git -C "$WORK/sunnypilot" show FETCH_HEAD:.gitmodules | git config -f - submodule.panda.url)
git clone -q --filter=blob:none "$panda_url" "$WORK/panda"
git -C "$WORK/panda" checkout -q "$panda_sha"
if ! diff -r -q -x obj "$WORK/panda/board" "$WORK/upstream/panda/board"; then
  echo "error: panda $panda_sha's firmware sources differ from upstream's prebuilt snapshot" >&2
  exit 1
fi

# upstream's pinned toolchain
read -r gcc_version scons_version < <("$PYTHON_BIN" - "$WORK/upstream/uv.lock" <<'PY'
import re, sys
lock = open(sys.argv[1]).read()
def version(name):
  return re.search(r'\[\[package\]\]\nname = "%s"\nversion = "([^"]+)"' % re.escape(name), lock).group(1)
print(version("comma-deps-gcc-arm-none-eabi"), version("scons"))
PY
)
"$PYTHON_BIN" -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install -q --disable-pip-version-check \
  "comma-deps-gcc-arm-none-eabi==$gcc_version" "scons==$scons_version"

build() {  # build <opendbc-root> <output-dir>
  rm -rf "$WORK/panda/board/obj"
  mkdir -p "$WORK/panda/board/obj"
  # upstream builds without git metadata, so its version string reads DEV-unknown-DEBUG
  (cd "$WORK/panda" && GIT_DIR=/nonexistent PATH="$WORK/venv/bin:$PATH" PYTHONPATH="$1" \
     "$WORK/venv/bin/scons" -j"$(nproc)" > "$WORK/scons.log" 2>&1) || { cat "$WORK/scons.log" >&2; exit 1; }
  rm -rf "$2"
  cp -R "$WORK/panda/board/obj" "$2"
}

build "$WORK/upstream/opendbc_repo" "$WORK/obj-upstream"
for f in "${GATE_FILES[@]}"; do
  if ! cmp -s "$WORK/obj-upstream/$f" "$WORK/upstream/panda/board/obj/$f"; then
    echo "error: upstream's panda firmware does not reproduce ($f); refusing to build modified safety" >&2
    exit 1
  fi
done
echo "reproduced upstream panda firmware byte for byte (panda ${panda_sha:0:12}, gcc $gcc_version)"

build "$CANDIDATE/opendbc_repo" "$WORK/obj-candidate"
if [ "$MODE" = install ]; then
  for f in "${FW_FILES[@]}"; do
    cp "$WORK/obj-candidate/$f" "$CANDIDATE/panda/board/obj/$f"
  done
else
  for f in "${CHECK_FILES[@]}"; do
    if ! cmp -s "$WORK/obj-candidate/$f" "$CANDIDATE/panda/board/obj/$f"; then
      echo "error: candidate panda firmware ($f) is not the build of its safety code" >&2
      exit 1
    fi
  done
fi
if [ "$MODE" = install ]; then
  echo "installed panda firmware built from the candidate's safety code"
else
  echo "candidate panda firmware is the build of its safety code"
fi
