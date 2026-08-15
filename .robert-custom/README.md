# custompilot maintenance assets

These files maintain the unofficial `robertdb3/sunnypilot` fork for a 2018 Subaru Outback
Preglobal on comma 3X.

## Branches

- `master` contains upstream sunnypilot plus this private-to-the-project maintenance machinery.
- `custompilot-staging` is generated from the latest installable upstream `staging` snapshot.
- `custompilot-stable` is the only branch intended for installation on the comma.

The installation URL is:

```text
https://install.sunnypilot.ai/fork/robertdb3/custompilot-stable
```

`custompilot-stable` is never updated by the scheduled workflow. A passing candidate produces a
one-click GitHub comparison link. Promotion requires a human to review the upstream diff, open the
pull request, and merge it deliberately.

## Automated update sequence

`.github/workflows/update-candidate.yml` runs daily and can also be started manually. It:

1. Fetches `sunnypilot/sunnypilot:staging`.
2. Requires the upstream `prebuilt` marker used by installable comma 3X snapshots.
3. Applies patches `0001` through `0006`, the narrow prebuilt Params compatibility port, then
   patches `0007` through `0010`.
4. Rejects patch conflicts and whitespace errors.
5. Rejects changes to driver monitoring, excessive-actuation checks, `opendbc/safety/`, panda,
   AGNOS, upstream license files, or stock cereal schemas.
6. Runs the focused regression suite and compiles every changed Python file.
7. Records the exact upstream commit and customization digest in `CUSTOM_FORK_MANIFEST.json`.
8. Publishes `custompilot-staging` and supplies a manual PR comparison link only when the upstream
   commit or customization stack actually changed.

Textual conflict freedom is not treated as proof of runtime compatibility. The runbook documents
real staging failures that merged cleanly but used incompatible cereal services, Cap'n Proto
fields, manager arguments, and prebuilt Params metadata. The PR remains a manual safety gate.

## Contents

- `patches/`: ordered customization patches.
- `ports/`: compatibility code required by the pinned prebuilt layout.
- `tests/`: laptop/CI regression tests for the customized behavior.
- `automation/`: candidate construction, validation, notices, and workflow templates.
- `scripts/`: additional fork-compliance checks.
- `notes/device-journey-runbook.md`: authoritative history, rationale, traps, and recovery steps.
- `tools/`: parked diagnostics and visualization helpers.

## Licensing and status

This is an unofficial, public, open-source, personal, noncommercial fork. It is not affiliated
with or endorsed by comma.ai or SUNNYPILOT LLC.

This project uses software from Haibin Wen and SUNNYPILOT LLC and is licensed under a custom license requiring permission for use.

The upstream `LICENSE` and `LICENSE.md` must remain unchanged. See
`automation/CUSTOM_FORK_NOTICE.md` for the complete fork notice. Commercial, for-profit, or
closed-source use of sunnypilot-authored material requires permission from its author(s).

AI tools assisted with portions of the changes. Commit messages disclose that assistance, and the
maintainer remains responsible for understanding, reviewing, and testing every published change.
