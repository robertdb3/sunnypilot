#!/usr/bin/env python3
"""Add public-fork notices and a reproducible provenance manifest to a candidate checkout."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


MARKER = "<!-- robertdb3-custom-fork-notice -->"
BANNER = f"""{MARKER}
> [!IMPORTANT]
> **Unofficial personal fork.** This is not affiliated with or endorsed by comma.ai or
> SUNNYPILOT LLC. This project uses software from Haibin Wen and SUNNYPILOT LLC and is licensed under a custom license requiring permission for use. See
> [CUSTOM_FORK_NOTICE.md](CUSTOM_FORK_NOTICE.md) before installing.

"""


def git(checkout: Path, *args: str) -> str:
  return subprocess.check_output(["git", "-C", str(checkout), *args], text=True).strip()


def customization_digest(assets: Path) -> str:
  digest = hashlib.sha256()
  roots = (assets / "patches", assets / "ports", assets / "tests", assets / "automation")
  for root in roots:
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
      relative = path.relative_to(assets).as_posix().encode()
      digest.update(len(relative).to_bytes(4, "big"))
      digest.update(relative)
      data = path.read_bytes()
      digest.update(len(data).to_bytes(8, "big"))
      digest.update(data)
  return digest.hexdigest()


def main() -> int:
  if len(sys.argv) != 3:
    print(f"usage: {sys.argv[0]} <candidate-checkout> <customization-assets>", file=sys.stderr)
    return 2

  checkout = Path(sys.argv[1]).resolve()
  assets = Path(sys.argv[2]).resolve()
  upstream_sha = git(checkout, "rev-parse", "HEAD")

  readme = checkout / "README.md"
  text = readme.read_text(encoding="utf-8")
  if MARKER not in text:
    readme.write_text(BANNER + text, encoding="utf-8")

  shutil.copyfile(assets / "automation" / "CUSTOM_FORK_NOTICE.md",
                  checkout / "CUSTOM_FORK_NOTICE.md")
  workflow_dir = checkout / ".github" / "workflows"
  workflow_dir.mkdir(parents=True, exist_ok=True)
  shutil.copyfile(assets / "automation" / "workflows" / "validate-custom-fork.yml",
                  workflow_dir / "validate-custom-fork.yml")

  manifest = {
    "schema": 1,
    "upstream": {
      "repository": "https://github.com/sunnypilot/sunnypilot",
      "branch": "staging",
      "commit": upstream_sha,
    },
    "customization_sha256": customization_digest(assets),
    "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    "release_policy": "manual promotion from custompilot-staging to custompilot-stable",
  }
  (checkout / "CUSTOM_FORK_MANIFEST.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
