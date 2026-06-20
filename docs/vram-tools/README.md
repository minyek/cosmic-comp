# VRAM-leak capture scripts

Canonical copies of the two user-run capture scripts for the VRAM-leak hunt.
They live in the repo so a reboot wiping `/tmp` (or a `dnf` reinstall) never
again forces reconstructing them from prose. See
[`../vram-leak-runbook.md`](../vram-leak-runbook.md) for the full procedure.

- **`vram-watch.sh`** — `flock`-guarded single-instance sampler. Logs
  cosmic-comp's own `nvidia-smi` VRAM row every 60 s to `/tmp/vram.csv`
  (`YYYY-MM-DD-HH:MM:SS,<MiB>`) and fires a SIGUSR1 census at start + hourly.
- **`leak-snapshot.sh`** — point-in-time snapshot into
  `/tmp/leak-snapshot-<ts>/`: census (via SIGUSR1 → journal), `nvidia-smi`,
  open-fd count, dma-buf fdinfo inventory + summary (pinned client buffers vs
  compositor-internal GL objects), `/proc/<pid>/status` `Vm*` lines, and the
  journal. The whole dir is `chmod -R a+rX` for the two-actor hand-off.

## Two-actor deploy

The compositor runs as `andy` (uid 1000); the agent as `agent` (uid 1001) and
**cannot** signal the compositor or read its journal. So **andy runs the
scripts**; the agent only authors/redeploys them. After any reboot, redeploy to
`/tmp` and run:

```bash
cp docs/vram-tools/*.sh /tmp/ && chmod a+rx /tmp/*.sh   # redeploy
setsid /tmp/vram-watch.sh & disown                      # sampler + hourly census
sh /tmp/leak-snapshot.sh                                # one snapshot
```

## Prerequisite: the running compositor must be OUR instrumented build

`SIGUSR1` and `GL_KHR_debug` only do anything if the live `/usr/bin/cosmic-comp`
is the instrumented counter build, **not** the Fedora distro RPM (a `dnf`
operation can silently overwrite it — observed 2026-06-13). Verify before
trusting any capture:

```bash
# our build contains these strings; the distro RPM contains none of them
strings /usr/bin/cosmic-comp | grep -E 'VRAM/resource census|SIGUSR1 dumper installed'
rpm -Vf /usr/bin/cosmic-comp   # a '5' flag on the binary = NOT the pristine RPM (good — it's ours)
```

If absent: `sudo make install` from the repo (copies `target/release/cosmic-comp`),
then log out / back in so the new binary is the running compositor.
