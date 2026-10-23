# cosmic-comp Fedora RPM packaging spec

## Goal

Build a Fedora 45 RPM from this checkout so a local fork build can be installed
or updated with DNF5. The RPM version comes from the nearest reachable
`epoch-*` Git tag and this checkout's current commit.

## Scope and constraints

- Add RPM packaging and concise build/install instructions inside this
  repository.
- Derive package versions from Git metadata. `Cargo.toml` currently reports
  `1.0.0`, while this checkout has `epoch-1.11.0`; Cargo metadata is not a
  version source for the RPM.
- Use Fedora 45's current `cosmic-comp` recipe for build dependencies, runtime
  dependencies, licenses, binary and default resources.
- Build from committed Git `HEAD`, including the fork's committed changes.
- Add `.rpm-source-metadata` to `Source0` with the source commit SHA on line one
  and its Unix committer timestamp on line two. Verify both against `HEAD` and
  pass the SHA as `GIT_HASH` after extraction without `.git`. The timestamp is
  provenance; the version helper uses the maximum committer timestamp in the
  selected tag's history for its date watermark.
- Since the RPM work tree is nested under the checkout, set
  `GIT_CEILING_DIRECTORIES` to the RPM topdir during compilation so Git cannot
  discover the checkout's parent `.git` from the extracted archive.
- Keep the RPM topdir, source/vendor staging, Cargo home/cache/target and RPM
  temporary directory under `target/rpm-build`; install payload files only into
  RPM's buildroot. Fetching missing tags may update this checkout's Git
  metadata.
- Do not install build packages, alter system configuration, or change
  compositor behavior as part of implementation or verification. A narrow
  `build.rs` change is in scope because the current script replaces a supplied
  `GIT_HASH` with an empty value when Git launches but cannot resolve `HEAD`.

## Decisions and alternatives

### RPM version

| Option | Pros | Cons |
| --- | --- | --- |
| Nearest Git tag plus a date-watermark and local sequence suffix for every build | Preserves the release base, orders after Fedora's date-based snapshots (including same-day snapshots), orders successive commits, records the exact commit, and keeps compatible `1.11.0` requirements satisfied. | Requires a reachable `epoch-*` tag; a future-dated Git commit can pin the watermark high. |
| Nearest Git tag plus commit distance only | Strictly orders commits along the same tagged history. | Sorts below Fedora's date-based Git snapshots, so DNF may reject the local update. |
| Read `Cargo.toml` | Simple and available in the source tree. | Incorrect here: it reports `1.0.0` and can make a compatible checkout look too old to dependent RPMs. |
| Use only the nearest tag | Simple and reflects upstream releases. | Fork commits after that tag all have the same EVR, so DNF cannot distinguish successive local snapshots. |

**Recommendation:** refresh tags from `upstream` before resolving the nearest
tag on every build, falling back to `origin` when `upstream` is absent. If the
refresh fails but a reachable local tag exists, report the failure and use that
tag. If no reachable tag remains, deepen or unshallow a shallow checkout and
retry. If no suitable tag is then reachable, stop with an actionable message
and never fall back to `Cargo.toml`.

Always use
`<tag-version>^<max-commit-date-UTC>.99999999.<distance>.g<commit>`,
including distance zero. Put the date immediately after `^`: placing `git`
before it sorts below Fedora's date-first snapshot on the same day. The date is
the maximum Git committer timestamp among
the tag commit and its descendants through `HEAD`; this watermark cannot move
backward as commits are added, and the increasing distance orders commits when
the watermark is unchanged. The numeric local marker sorts above Fedora's
seven-character hexadecimal Git-hash suffix for a same-date snapshot. This
makes the current local checkout newer than the Fedora 45 package already
installed here, while preserving RPM's `>= 1.11.0` comparison. Normalize
semver prerelease tags by replacing their `-` separator with RPM's `~`, so
`epoch-1.0.0-beta.9` becomes `1.0.0~beta.9` before the snapshot suffix. If no
reachable `epoch-*` tag exists after that retry, stop without inventing a
version.

### Build source and installation layout

| Option | Pros | Cons |
| --- | --- | --- |
| Archive this checkout's committed `HEAD`, vendor its locked Cargo dependencies in a staged copy, and build/install through RPM's buildroot | Includes the fork's committed changes, keeps builds reproducible against the lockfile, and confines generated artifacts to `target/rpm-build` and installed paths to RPM-owned files. | Uncommitted source changes are excluded; initial vendoring may use the network. |
| Download the upstream release archive | Matches Fedora's release source and simplifies source acquisition. | Omits fork-only commits and local fixes. |
| Install with the project's `just install` directly | Reuses the project install recipe. | Writes into the host filesystem instead of the RPM buildroot and cannot produce a safely installable package. |

**Recommendation:** build from the checkout's Git archive, vendor dependencies
only in a staged copy, and use `just` with the RPM buildroot for installation.
Fix `build.rs` to set `GIT_HASH` only when `git rev-parse HEAD` exits
successfully, and to leave an explicitly supplied value intact otherwise. This
small build-only guard is preferable to fabricating `.git` metadata in the
source archive: it keeps the packaged binary's hash accurate without teaching
the RPM builder to impersonate a Git checkout. The current behavior silently
overwrites a valid supplied hash with an empty string when Git is present but
the source is not a repository.
The spec must include Fedora 45's build requirements (`cargo-rpm-macros >= 25`,
`rustc`, `lld`, `cargo`, `wayland-devel`, `libxkbcommon-devel`,
`libglvnd-devel`, `libseat-devel`, `libinput-devel`, `mesa-libgbm-devel`,
`pixman-devel`, `libdisplay-info-devel`, and `just`) and runtime requirements
(`libseat%{?_isa}`, `libwayland-server%{?_isa}`,
`xorg-x11-server-Xwayland`, and `cosmic-icon-theme >=` the normalized tag
version), plus `Recommends: cosmic-session`. Its payload is
`/usr/bin/cosmic-comp`,
`/usr/share/cosmic/com.system76.CosmicSettings.Shortcuts/v1/defaults`,
`/usr/share/cosmic/com.system76.CosmicSettings.WindowRules/v1/tiling_exception_defaults`,
`/usr/share/licenses/cosmic-comp/LICENSE`,
`/usr/share/licenses/cosmic-comp/LICENSE.dependencies`, and
`/usr/share/licenses/cosmic-comp/cargo-vendor.txt`. Session files remain with
the existing COSMIC session package.

## Acceptance criteria

1. A helper obtains the nearest reachable `epoch-*` tag and current commit,
   normalizes semver prerelease tags for RPM, and derives
   `<tag-version>^<max-commit-date-UTC>.99999999.<distance>.g<commit>` for
   every build, including distance zero. It does not read `Cargo.toml` for the
   RPM version.
2. With source based on `epoch-1.11.0` or a newer tag, the full package EVR
   compares as at least `1.11.0`; the current checkout's EVR compares newer
   than the installed Fedora 45 `cosmic-comp` and a Fedora date-first snapshot
   with the same date; increasing post-tag commit
   distances compare in increasing order, including when a descendant has an
   earlier committer timestamp than its ancestors.
3. Before resolving the nearest tag, the build attempts to refresh tags from
   `upstream`, or `origin` when `upstream` is absent. If refresh fails, it
   reports the failure and uses a reachable local tag if present. A shallow
   checkout is deepened/unshallowed when no nearest tag can be determined. If
   no tag is available after that, the build fails with an actionable message
   and does not invent a version.
4. The local RPM is built from this checkout's committed `HEAD`, including
   fork commits. `.rpm-source-metadata` records the commit SHA and Unix
   committer timestamp; the build verifies both and passes the SHA as `GIT_HASH`.
   In the extracted source archive, Git cannot discover the parent checkout;
   `build.rs` preserves the supplied SHA when it cannot resolve `HEAD`.
   The RPM appears at `target/rpm-build/cosmic-comp.rpm`. Uncommitted changes
   are excluded and documented.
5. The Fedora 45 spec declares the exact build/runtime dependencies and
   payload paths above. The RPM topdir, Cargo home/cache/target, sources and
   temporary files stay under `target/rpm-build`; `%install` writes only into
   `%{buildroot}`.
6. Project documentation gives dependency preparation, build, artifact
   location and DNF5 install/update commands, with a tag failure explanation.

## Verification

- Compare full EVRs with `rpmdev-vercmp`: a distance-zero build versus
  the installed package EVR from `rpm -q`, a same-date build versus a
  Fedora-style `^<date>git<hash>` snapshot, a compatible snapshot versus
  `1.11.0`, increasing distances with
  equal and regressed committer dates, and a newer tagged release versus the
  prior base's snapshot.
- Parse the spec with `rpmspec --parse` from the checkout root, using the same
  version helper and `HEAD` as the RPM build. Check the normalized version and
  minimum icon-theme dependency.
- Build the local RPM without installing it. Confirm the source archive's
  recorded SHA and committer timestamp equal `HEAD` and that the recorded SHA
  is passed as `GIT_HASH`; inspect the complete EVR and implicit `cosmic-comp`
  provide, exact required/recommended dependencies, and payload/license paths
  with `rpm -qp`.
- Confirm builds and Cargo caches remain under `target/rpm-build`, and that
  source checkout files outside that ignored build directory remain unchanged.

## Tasks

1. Add the Git version helper, the Fedora 45 RPM spec/build driver, and the
   narrow `build.rs` guard required to preserve the source commit hash.
2. Document dependency preparation, local build, artifact location and DNF5
   installation, with a link from the root build instructions.
3. Run the version, spec and package acceptance checks without installing the
   resulting package or changing system configuration.

## Documentation impact

The root `README.md` has the project's build instructions and should point to
the new `packaging/rpm/README.md`. The new packaging README is the local guide
for this RPM workflow. No architecture or runtime behavior changes.

## Implementation results

- `git-version.sh` derived versions from `epoch-1.11.0`; the exact-tag case
  emitted distance zero, and a backdated descendant kept the tag date
  watermark while increasing distance to one. It normalized
  `epoch-1.12.0-rc.1` to `1.12.0~rc.1`, which sorts below stable `1.12.0` but
  above the prerelease tag itself. The helper rejected an untagged checkout
  and used a reachable local tag after a simulated fetch failure.
- `rpmspec --parse` accepted the generated Version, icon-theme minimum and
  Fedora dependencies. `rpmdev-vercmp` placed the local snapshot above the
  installed Fedora 45 RPM, the same-date Fedora snapshot and `1.11.0`; a
  higher local distance sorted above a lower one.
- The local RPM build completed on Fedora 45. `rpm -qp` confirmed the implicit
  `cosmic-comp` provide, runtime requirements, `cosmic-session` recommendation,
  executable, two defaults and three license files. The executable contains
  the full SHA recorded in `.rpm-source-metadata`.
- `just check --offline --frozen` exited zero after the build-script lint fix.
  It still reports existing pedantic Clippy warnings in project code and a
  Cargo future-incompatibility notice for `proc-macro-error2`.
- The RPM build reported only the empty-changelog warning from the local spec.
  The RPM was not installed and no system configuration was changed.
