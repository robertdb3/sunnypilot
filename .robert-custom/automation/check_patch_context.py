#!/usr/bin/env python3
"""Warn about patch context lines that upstream is likely to churn.

Every line of context in a patch is a line upstream can break us on, even though we do not
change it. That is how the 0006 ui_state hunk broke three times in three days: a trailing
context line sat on `get_active_source(...)`, upstream reworked that call, and the whole
nightly candidate went red.

This runs before apply_candidate.sh and reports two things:

  excess context  Context lines beyond what is needed to locate the hunk uniquely. Pure risk,
                  no benefit -- they can be deleted without changing what the patch does.

  churned context Context lines that upstream actually changed between two snapshots. Needs
                  --baseline; see the note on baselines below.

Advisory by default: it prints findings and exits 0 so a warning never turns the nightly red
on its own. Pass --strict to exit non-zero when anything is reported.

Baselines: upstream `staging` is a single force-pushed orphan commit, so it carries no history
to diff against. The usable baseline is a previous pristine snapshot that this fork still
references -- e.g. the root commit of `custompilot-staging`. Without --baseline only the
excess-context check runs, which needs no history at all.

usage: check_patch_context.py <candidate-checkout> <customization-assets>
                              [--baseline REV] [--near N] [--strict] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Same order apply_candidate.sh replays them in; the ports patch slots in after 0006.
PATCH_ORDER = ("0001", "0002", "0003", "0004", "0005", "0006",
               "PORT", "0008", "0009", "0010", "0011")

# git apply rejects a hunk that ends with no trailing context, so one line is the floor.
MIN_EDGE_CONTEXT = 1


def git(repo: Path, *args: str) -> tuple[int, str]:
  proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
  return proc.returncode, proc.stdout


def patch_files(assets: Path) -> list[tuple[str, Path]]:
  ordered = []
  for tag in PATCH_ORDER:
    if tag == "PORT":
      found = sorted(assets.glob("ports/*/*.patch"))
    else:
      found = sorted(assets.glob(f"patches/{tag}-*.patch"))
    if len(found) != 1:
      raise SystemExit(f"error: expected exactly one {tag} patch, found {len(found)}")
    ordered.append((tag, found[0]))
  return ordered


def parse_hunks(text: str):
  """Yield (path, header, body) where body is a list of (tag, content), tag in ' ', '-', '+'."""
  hunks, path, header, body, is_new = [], None, None, None, False
  for line in text.split("\n"):
    if line.startswith("diff --git "):
      if body is not None:
        hunks.append((path, header, body))
      path, body, is_new = line.split(" b/")[-1], None, False
    elif line.startswith("new file mode"):
      is_new = True
    elif line.startswith("@@"):
      if body is not None:
        hunks.append((path, header, body))
      header, body = line, (None if is_new else [])
    elif body is not None:
      if line.startswith("\\"):
        continue
      if line == "":
        body.append((" ", ""))
      elif line[0] in " -+":
        body.append((line[0], line[1:]))
      else:
        hunks.append((path, header, body))
        body = None
  if body is not None:
    hunks.append((path, header, body))
  return [h for h in hunks if h[2] is not None]


def locate(haystack: list[str], needle: list[str]) -> list[int]:
  if not needle:
    return []
  span = len(needle)
  return [i for i in range(len(haystack) - span + 1) if haystack[i:i + span] == needle]


def changed_lines(repo: Path, baseline: str, path: str) -> set[int]:
  """1-based line numbers in the current file that differ from the baseline snapshot."""
  code, out = git(repo, "diff", "-U0", baseline, "HEAD", "--", path)
  if code != 0:
    return set()
  touched: set[int] = set()
  for match in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", out, re.M):
    start, count = int(match.group(1)), int(match.group(2) or 1)
    touched.update(range(start, start + count))
  return touched


def analyse(checkout: Path, assets: Path, baseline: str | None, near: int) -> list[dict]:
  tree: dict[str, list[str]] = {}
  # Parallel to tree[path]: each simulated line's 1-based number in the checkout, or None for
  # a line one of our own patches inserted. Earlier patches shift the simulated file, so a
  # context line has to be mapped back before it can be compared against upstream's diff.
  origin: dict[str, list[int | None]] = {}
  findings: list[dict] = []
  # Lines our own earlier patches insert. They show up as context in later patches, but they
  # are ours, not upstream's, so upstream churn cannot strand them.
  ours: set[str] = set()

  def content(path: str) -> list[str] | None:
    if path not in tree:
      target = checkout / path
      if not target.exists():
        return None
      tree[path] = target.read_text().split("\n")
      origin[path] = list(range(1, len(tree[path]) + 1))
    return tree[path]

  for tag, patch in patch_files(assets):
    text = patch.read_text()
    for path, header, body in parse_hunks(text):
      current = content(path)
      if current is None:
        continue
      preimage = [c for t, c in body if t in " -"]
      spots = locate(current, preimage)
      # Where this hunk sits, and where each of its lines sits within it. Without this a
      # context line gets measured against the nearest identical text anywhere in the file.
      anchor = spots[0] if len(spots) == 1 else None
      preimage_at, seen = {}, 0
      for i, (marker, _line) in enumerate(body):
        if marker in " -":
          preimage_at[i] = seen
          seen += 1

      first = next((i for i, (t, _) in enumerate(body) if t != " "), None)
      last = next((i for i in range(len(body) - 1, -1, -1) if body[i][0] != " "), None)
      if first is None:
        continue
      lead, trail = body[:first], body[last + 1:]

      def unique(drop_lead: int, drop_trail: int) -> bool:
        end = len(body) - drop_trail if drop_trail else len(body)
        sub = body[drop_lead:end]
        return len(locate(current, [c for t, c in sub if t in " -"])) == 1

      excess_trail = 0
      for k in range(1, max(0, len(trail) - MIN_EDGE_CONTEXT) + 1):
        if unique(0, k):
          excess_trail = k
        else:
          break
      excess_lead = 0
      for k in range(1, max(0, len(lead) - MIN_EDGE_CONTEXT) + 1):
        if unique(k, 0):
          excess_lead = k
        else:
          break

      if excess_lead or excess_trail:
        findings.append({"kind": "excess-context", "patch": tag, "path": path, "hunk": header,
                         "excess_lead": excess_lead, "excess_trail": excess_trail,
                         "detail": f"{excess_lead + excess_trail} context line(s) removable "
                                   f"(lead {excess_lead}, trail {excess_trail})"})

      if baseline:
        code, old = git(checkout, "show", f"{baseline}:{path}")
        touched = changed_lines(checkout, baseline, path)
        old_lines = set(old.split("\n")) if code == 0 else None
        for index, (marker, text_line) in enumerate(body):
          if marker != " " or not text_line.strip():
            continue
          if text_line in ours:
            continue
          where = "lead" if index < first else ("trail" if index > last else "interior")
          if old_lines is None:
            continue
          if text_line not in old_lines:
            findings.append({"kind": "churned-context", "patch": tag, "path": path,
                             "hunk": header, "where": where, "line": text_line.strip()[:100],
                             "detail": "upstream changed this exact context line since the baseline"})
          elif touched and anchor is not None:
            at = origin[path][anchor + preimage_at[index]]
            gap = min((abs(at - t) for t in touched), default=None) if at else None
            if gap is not None and gap <= near:
              findings.append({"kind": "near-churn", "patch": tag, "path": path, "hunk": header,
                               "where": where, "line": text_line.strip()[:100],
                               "detail": f"{gap} line(s) from code upstream changed since the baseline"})

      if anchor is not None:                   # keep the simulated tree and its map in step
        omap, rebuilt, cursor = origin[path], [], anchor
        for marker, _line in body:
          if marker == " ":
            rebuilt.append(omap[cursor])
            cursor += 1
          elif marker == "-":
            cursor += 1
          else:
            rebuilt.append(None)
        tree[path] = current[:anchor] + [c for t, c in body if t in " +"] + \
                     current[anchor + len(preimage):]
        origin[path] = omap[:anchor] + rebuilt + omap[anchor + len(preimage):]

    for path, _header, body in parse_hunks(text):   # register files this patch creates
      if path not in tree and not (checkout / path).exists():
        tree[path] = [c for t, c in body if t in " +"]
        origin[path] = [None] * len(tree[path])
      for marker, text_line in body:
        if marker == "+":
          ours.add(text_line)

  return findings


def main() -> int:
  parser = argparse.ArgumentParser(add_help=True)
  parser.add_argument("checkout", type=Path)
  parser.add_argument("assets", type=Path)
  parser.add_argument("--baseline", help="a previous pristine upstream snapshot to diff against")
  parser.add_argument("--near", type=int, default=3, help="flag context within N lines of churn")
  parser.add_argument("--strict", action="store_true", help="exit non-zero when anything is found")
  parser.add_argument("--json", action="store_true")
  args = parser.parse_args()

  if args.baseline:
    code, _ = git(args.checkout, "cat-file", "-e", f"{args.baseline}^{{commit}}")
    if code != 0:
      print(f"warning: baseline {args.baseline} is not in this checkout; "
            "skipping the churn check", file=sys.stderr)
      args.baseline = None

  findings = analyse(args.checkout, args.assets, args.baseline, args.near)

  if args.json:
    print(json.dumps(findings, indent=2))
  elif not findings:
    print("patch context: nothing to report")
  else:
    order = {"churned-context": 0, "near-churn": 1, "excess-context": 2}
    for item in sorted(findings, key=lambda f: (order[f["kind"]], f["patch"])):
      print(f"[{item['kind']}] {item['patch']} {item['path']}")
      print(f"    {item['hunk']}")
      if "line" in item:
        print(f"    {item['line']}")
      print(f"    -> {item['detail']}")
    print(f"\n{len(findings)} finding(s). Context lines are lines upstream can break us on "
          "without us changing them.")

  return 1 if (findings and args.strict) else 0


if __name__ == "__main__":
  raise SystemExit(main())
