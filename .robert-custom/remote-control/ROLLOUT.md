# Tailscale and voice-control rollout

This procedure is intentionally staged. The current value in `release-stage` is authoritative.
All device work is parked/offroad unless a driving-validation step explicitly says otherwise.

## Stage 1: Tailscale only

1. Promote a candidate built with `release-stage=tailscale` to `custompilot-stable`, install it,
   and confirm the device commit/branch and a clean worktree.
2. Confirm Scene3D, UI, `card`, `controlsd`, `selfdrived`, and `manager` health before installation.
3. Copy the tailnet policy entries from `tailnet-policy.hujson`, replacing the owner login. Preserve
   unrelated existing policy entries. Create a one-use, preauthorized `tag:comma` auth key.
4. On the parked comma, run:

       cd /data/openpilot
       openpilot/sunnypilot/system/tailscale/install.sh
       openpilot/sunnypilot/system/tailscale/bootstrap.sh
       umask 077
       printf '%s' '<ONE_USE_KEY>' > /data/custompilot/tailscale/enroll.key
       openpilot/sunnypilot/system/tailscale/enroll.sh '<OWNER_LOGIN>' /data/custompilot/tailscale/enroll.key

   `enroll.sh` removes the one-use key only after `tailscale up` succeeds. It leaves visual and
   speed flags false.
5. Native mode uses `ssh comma@comma3x`. Userspace mode uses the private TCP Serve forward on
   port 2222. From the Mac, prove HTTPS and SSH both work before relying on Tailscale. If either
   userspace forward fails, stop; do not alter AGNOS or the kernel.
6. Prove the policy denies another tailnet identity, prove hotspot/local SSH still works, reboot,
   and prove Tailscale starts even if manager is stopped.
7. Record before/after idle CPU, memory, temperature and traffic. After an ordinary drive, use the
   existing service-rate tools for `modelV2`, `radarState`, `carState`, `card`, and `controlsd`.

## Stage 2: visual commands

1. Change `release-stage` to `visual`, build/validate, and use a separate protected promotion PR.
2. Set `visual_enabled` to true in `/data/custompilot/commands.json`; keep `speed_enabled` false.
3. Build only the visual Shortcuts in `SHORTCUTS.md` and exercise every mapping parked with
   ignition on, including cellular-to-Tailscale and iPhone-hotspot-to-comma paths.
4. Confirm UI health and no new crash log before a normal drive.

## Stage 3: confirmed speed commands

1. Change `release-stage` to `speed`, build/validate, and use another protected promotion PR.
2. Keep speed disabled until parked rejection tests pass, then set `speed_enabled` true.
3. First road validation is one 5 mph change with a passenger operating/observing the phone.
   Test a physical-button cancellation and “Comma resume speed assist.”
4. Expand to multi-step changes only after logs show the expected coarse-tap count, cluster
   convergence, no oscillation, normal service rates, and no alerts.

## Recovery

- Set both feature flags false to stop voice mutations without changing branches.
- Stop Tailscale with `/data/custompilot/tailscale/bin/tailscale --socket=/data/custompilot/tailscale/run/tailscaled.sock down`.
- Hotspot/local SSH and the protected `custompilot-stable` rollback remain independent of this
  feature. Never use Tailscale availability as a prerequisite for openpilot startup.
