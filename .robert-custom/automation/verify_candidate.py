#!/usr/bin/env python3
"""Fail closed on licensing, packaging, safety-boundary, or provenance regressions."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ACKNOWLEDGMENT = (
  "This project uses software from Haibin Wen and SUNNYPILOT LLC and is licensed under a "
  "custom license requiring permission for use."
)
PROTECTED_PREFIXES = (
  "openpilot/selfdrive/monitoring/",
  "opendbc_repo/opendbc/safety/",
  "panda/",
)
PROTECTED_EXACT = {
  "openpilot/selfdrive/selfdrived/helpers.py",
  "system/hardware/tici/agnos.json",
  "system/hardware/tici/agnos.py",
}


def git(checkout: Path, *args: str) -> str:
  return subprocess.check_output(["git", "-C", str(checkout), *args], text=True).strip()


def fail(message: str) -> None:
  raise SystemExit(f"candidate verification failed: {message}")


def main() -> int:
  if len(sys.argv) != 3:
    print(f"usage: {sys.argv[0]} <candidate-checkout> <upstream-commit>", file=sys.stderr)
    return 2

  checkout = Path(sys.argv[1]).resolve()
  base = sys.argv[2]
  changed = set(filter(None, git(checkout, "diff", "--name-only", base).splitlines()))

  protected = sorted(
    path for path in changed
    if path in PROTECTED_EXACT or any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES)
  )
  if protected:
    fail("protected safety/OS paths changed:\n  " + "\n  ".join(protected))

  forbidden_schema = sorted(
    path for path in changed
    if path.startswith("openpilot/cereal/") and path != "openpilot/cereal/custom.capnp"
  )
  if forbidden_schema:
    fail("stock cereal schema changed instead of custom.capnp:\n  " + "\n  ".join(forbidden_schema))

  if not (checkout / "prebuilt").is_file():
    fail("prebuilt marker is missing")

  for license_name in ("LICENSE", "LICENSE.md"):
    if license_name in changed:
      fail(f"upstream {license_name} was modified")
    if not (checkout / license_name).is_file():
      fail(f"upstream {license_name} is missing")

  for visible_file in ("README.md", "CUSTOM_FORK_NOTICE.md"):
    path = checkout / visible_file
    if not path.is_file() or ACKNOWLEDGMENT not in path.read_text(encoding="utf-8"):
      fail(f"required sunnypilot acknowledgment missing from {visible_file}")

  manifest_path = checkout / "CUSTOM_FORK_MANIFEST.json"
  manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  if manifest.get("upstream", {}).get("commit") != base:
    fail("manifest upstream commit does not match candidate base")
  digest = manifest.get("customization_sha256", "")
  if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
    fail("manifest customization digest is invalid")

  if not changed:
    fail("candidate contains no customization changes")

  print(f"verified {len(changed)} customized files against upstream {base[:12]}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
