#!/usr/bin/env bash
set -euo pipefail

project_root="$(git rev-parse --show-toplevel)"
cd "$project_root"

for command in cargo git rpmbuild tar just; do
    if ! command -v "$command" >/dev/null 2>&1; then
        printf 'error: required command not found: %s\n' "$command" >&2
        exit 1
    fi
done

metadata="$(packaging/rpm/git-version.sh --metadata)"
IFS=$'\t' read -r rpm_version rpm_base_version git_tag distance source_git_hash source_commit_epoch <<<"$metadata"

head_sha="$(git rev-parse HEAD)"
head_epoch="$(git show -s --format=%ct HEAD)"
if [[ "$source_git_hash" != "$head_sha" || "$source_commit_epoch" != "$head_epoch" ]]; then
    printf 'error: Git version metadata does not match checkout HEAD\n' >&2
    exit 1
fi

build_root="$project_root/target/rpm-build"
mkdir -p "$build_root"
mkdir -p "$build_root/cargo-home"
build_topdir="$(mktemp -d "$build_root/build.XXXXXXXX")"
mkdir -p "$build_topdir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS,tmp}

stage_root="$build_topdir/stage"
source_dir="$stage_root/cosmic-comp"
mkdir -p "$source_dir"
git archive --format=tar "$head_sha" | tar -xf - -C "$source_dir"
printf '%s\n%s\n' "$source_git_hash" "$source_commit_epoch" > "$source_dir/.rpm-source-metadata"

printf 'Vendoring locked Cargo dependencies from %s (+%s commits, %s)...\n' \
    "$git_tag" "$distance" "$source_git_hash"
(
    cd "$source_dir"
    CARGO_HOME="$build_root/cargo-home" \
        CARGO_TARGET_DIR="$build_topdir/cargo-target" \
        SOURCE_DATE_EPOCH="$source_commit_epoch" \
        SOURCE_GIT_HASH="$source_git_hash" \
        cargo vendor --locked vendor > vendor-config.toml
)

source_tarball="$build_topdir/SOURCES/cosmic-comp.tar.gz"
tar --sort=name --mtime="@${source_commit_epoch}" --owner=0 --group=0 --numeric-owner \
    -czf "$source_tarball" -C "$stage_root" cosmic-comp

tar -xOf "$source_tarball" cosmic-comp/.rpm-source-metadata > "$build_topdir/metadata-check"
expected_metadata="$(printf '%s\n%s' "$source_git_hash" "$source_commit_epoch")"
if [[ "$(cat "$build_topdir/metadata-check")" != "$expected_metadata" ]]; then
    printf 'error: source archive provenance does not match checkout HEAD\n' >&2
    exit 1
fi

spec="$project_root/packaging/rpm/cosmic-comp.spec"
rpmbuild -bb \
    --define "_topdir $build_topdir" \
    --define "_tmppath $build_topdir/tmp" \
    --define "rpm_version $rpm_version" \
    --define "rpm_base_version $rpm_base_version" \
    --define "source_git_hash $source_git_hash" \
    --define "source_commit_epoch $source_commit_epoch" \
    "$spec" 2>&1 | tee "$build_topdir/build.log"

mapfile -t package_rpms < <(find "$build_topdir/RPMS" -type f -name 'cosmic-comp-*.rpm' ! -name '*debuginfo*' ! -name '*debugsource*')
if [[ "${#package_rpms[@]}" -ne 1 ]]; then
    printf 'error: expected one cosmic-comp RPM, found %s\n' "${#package_rpms[@]}" >&2
    exit 1
fi

artifact="$build_root/cosmic-comp.rpm"
install -m0644 "${package_rpms[0]}" "$artifact"
printf '\nBuilt RPM: %s\n' "$artifact"
printf 'Version:   %s\n' "$rpm_version"
printf 'Commit:    %s\n' "$source_git_hash"
printf 'Build log: %s/build.log\n' "$build_topdir"
