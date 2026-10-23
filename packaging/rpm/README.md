# Build a local Fedora 45 RPM

Run these commands from the `cosmic-comp` checkout. The package is built from
committed `HEAD`; commit any source changes that must be included first.

Install the RPM builder and DNF build dependency plugin:

```sh
sudo dnf5 install rpm-build dnf5-plugins
```

Install the spec's Fedora build dependencies. The helper refreshes upstream
tags and supplies the Git-derived version macros needed to parse the spec:

```sh
IFS=$'\t' read -r RPM_VERSION RPM_BASE_VERSION TAG DISTANCE SOURCE_GIT_HASH SOURCE_COMMIT_EPOCH \
  < <(packaging/rpm/git-version.sh --metadata)
sudo dnf5 builddep \
  --define "rpm_version $RPM_VERSION" \
  --define "rpm_base_version $RPM_BASE_VERSION" \
  --define "source_git_hash $SOURCE_GIT_HASH" \
  --define "source_commit_epoch $SOURCE_COMMIT_EPOCH" \
  --spec packaging/rpm/cosmic-comp.spec
```

Build and install/update the local RPM:

```sh
packaging/rpm/build-rpm.sh
sudo dnf5 install ./target/rpm-build/cosmic-comp.rpm
```

The RPM is written to `target/rpm-build/cosmic-comp.rpm`. The driver keeps the
source archive, vendored crates, Cargo state, RPM build tree and build log under
`target/rpm-build`. Its `%install` stage writes only into the RPM buildroot.

The version comes from the nearest reachable `epoch-*` tag, Git history dates,
commit distance and commit hash. Each build tries to refresh tags from the
`upstream` remote, or `origin` if `upstream` is not configured. If fetching
fails but a suitable local tag is reachable, the helper warns and uses it. If
no reachable tag is available, it tries to unshallow a shallow checkout and
then stops with an error if no tag can be found; it never substitutes the
`Cargo.toml` version. Ensure the tag and its history are available, then rerun
the build.

Uncommitted changes are not included because the build source is created from
`git archive HEAD`.
