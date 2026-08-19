# custompilot maintenance assets

These files maintain the unofficial `robertdb3/sunnypilot` fork for a 2018 Subaru Outback
Preglobal on comma 3X.

## Branches

- `master` contains upstream sunnypilot plus this private-to-the-project maintenance machinery.
- `custompilot-staging` is generated from the latest installable upstream `staging` snapshot, then
  committed as a child of the current `custompilot-stable` tip so its promotion PR is mergeable.
- `custompilot-stable` is the only branch intended for installation on the comma.

The installation URL is:

```text
https://install.sunnypilot.ai/fork/robertdb3/custompilot-stable
```

`custompilot-stable` is never updated by the scheduled workflow. A passing candidate produces a
one-click GitHub comparison link. Promotion requires a human to review the upstream diff, open the
pull request, and merge it deliberately.

The updater pushes candidates with GitHub's workflow token. GitHub can mark the resulting PR event
as `action_required` instead of starting another workflow recursively. In that case, manually run
**Validate custom fork** on `custompilot-staging`; never bypass or remove the required `validate`
check. After promotion, an unchanged staging tip that is already an ancestor of stable is treated
as current so the daily updater does not create provenance-only churn.

## Automated update sequence

`.github/workflows/update-candidate.yml` runs daily and can also be started manually. It:

1. Fetches `sunnypilot/sunnypilot:staging`.
2. Requires the upstream `prebuilt` marker used by installable comma 3X snapshots.
3. Applies patches `0001` through `0006`, the narrow prebuilt Params compatibility port, then
   patches `0008` through `0011`, then the active remote-command release stage. (`0007` was
   retired once upstream fixed the same defect; see symptom 12 in the runbook.)
4. Rejects patch conflicts and whitespace errors.
5. Rejects changes to driver monitoring, excessive-actuation checks, `opendbc/safety/`, panda,
   AGNOS, upstream license files, or stock cereal schemas.
6. Runs the focused regression suite and compiles every changed Python file.
7. Records the exact upstream commit and customization digest in `CUSTOM_FORK_MANIFEST.json`.
8. Commits the validated candidate tree on top of the current stable tip; the manifest still
   records the exact upstream base used for construction and validation.
9. Publishes `custompilot-staging` and supplies a manual PR comparison link only when the upstream
   commit, customization stack, or candidate lineage actually changed.

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

## Remote-command release stages

`.robert-custom/release-stage` is the explicit rollout gate:

- `tailscale` applies patch `0012` only. It adds the pinned, fail-open private-network bootstrap;
  this is the current stage.
- `visual` additionally applies patch `0013`, the authenticated loopback command service and UI
  controls.
- `speed` additionally applies patch `0014`, the confirmed ICBM absolute-speed override.

Advancing that one file creates a new candidate and must use the ordinary protected promotion PR.
Do not skip a stage. The `/data/custompilot/commands.json` feature flags are a second, device-local
gate and default to false after enrollment.

## Licensing and status

This is an unofficial, public, open-source, personal, noncommercial fork. It is not affiliated
with or endorsed by comma.ai or SUNNYPILOT LLC.

This project uses software from Haibin Wen and SUNNYPILOT LLC and is licensed under a custom license requiring permission for use.

The upstream `LICENSE` and `LICENSE.md` must remain unchanged. See
`automation/CUSTOM_FORK_NOTICE.md` for the complete fork notice. Commercial, for-profit, or
closed-source use of sunnypilot-authored material requires permission from its author(s).

AI tools assisted with portions of the changes. Commit messages disclose that assistance, and the
maintainer remains responsible for understanding, reviewing, and testing every published change.
