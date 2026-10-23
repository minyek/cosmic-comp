#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat >&2 <<'EOF'
Usage: git-version.sh [--rpm-version|--metadata]

Refreshes the configured upstream's tags (or origin when upstream is absent),
then reports the nearest reachable epoch-* tag and HEAD metadata.
  --rpm-version  Print only the RPM Version value.
  --metadata     Print tab-separated: version, base version, tag, distance,
                 full commit SHA, and commit Unix timestamp.
EOF
}

mode="${1:---rpm-version}"
if [[ "$#" -gt 1 || ( "$mode" != "--rpm-version" && "$mode" != "--metadata" ) ]]; then
    usage
    exit 2
fi

git -C "$(pwd)" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
    printf 'error: run this command inside a Git checkout of cosmic-comp\n' >&2
    exit 1
}

remote=""
if git remote get-url upstream >/dev/null 2>&1; then
    remote=upstream
elif git remote get-url origin >/dev/null 2>&1; then
    remote=origin
fi

refresh_tags() {
    if [[ -z "$remote" ]]; then
        printf 'warning: no upstream or origin remote; using locally available tags\n' >&2
        return 1
    fi

    if ! GIT_TERMINAL_PROMPT=0 git fetch --tags "$remote"; then
        printf 'warning: could not refresh tags from %s; checking locally available tags\n' "$remote" >&2
        return 1
    fi
}

refresh_tags || true

describe_tag() {
    git describe --tags --match 'epoch-*' --long --abbrev=12 HEAD 2>/dev/null
}

description=""
if ! description="$(describe_tag)"; then
    if [[ "$(git rev-parse --is-shallow-repository)" == true && -n "$remote" ]]; then
        if ! GIT_TERMINAL_PROMPT=0 git fetch --unshallow --tags "$remote"; then
            printf 'warning: could not unshallow history from %s\n' "$remote" >&2
        fi
        description="$(describe_tag || true)"
    fi
fi

if [[ -z "$description" ]]; then
    printf 'error: no reachable epoch-* Git tag could be found. Fetch tags and sufficient history, then retry. The RPM version is never taken from Cargo.toml.\n' >&2
    exit 1
fi

if [[ ! "$description" =~ ^(epoch-.+)-([0-9]+)-g([[:xdigit:]]+)$ ]]; then
    printf 'error: cannot parse Git description: %s\n' "$description" >&2
    exit 1
fi

tag="${BASH_REMATCH[1]}"
distance="${BASH_REMATCH[2]}"
short_commit="${BASH_REMATCH[3]}"
tag_version="${tag#epoch-}"
base_version="${tag_version//-/\~}"

if [[ ! "$base_version" =~ ^[0-9][0-9A-Za-z.+~]*$ ]]; then
    printf 'error: tag %s does not contain a supported RPM-compatible version\n' "$tag" >&2
    exit 1
fi

commit="$(git rev-parse HEAD)"
if [[ "${commit:0:${#short_commit}}" != "$short_commit" ]]; then
    printf 'error: Git description commit %s does not match HEAD %s\n' "$short_commit" "$commit" >&2
    exit 1
fi

tag_commit="$(git rev-parse "${tag}^{commit}")"
commit_epoch="$(git show -s --format=%ct HEAD)"
max_epoch="$(
    {
        git show -s --format=%ct "$tag_commit"
        git log --format=%ct "${tag_commit}..HEAD"
    } | awk 'NR == 1 || $1 > max { max = $1 } END { if (NR > 0) print max }'
)"
if [[ -z "$max_epoch" ]]; then
    printf 'error: could not read committer timestamps for %s..HEAD\n' "$tag" >&2
    exit 1
fi
watermark="$(date -u -d "@$max_epoch" +%Y%m%d)"
rpm_version="${base_version}^${watermark}.99999999.${distance}.g${short_commit}"

if [[ "$mode" == "--metadata" ]]; then
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$rpm_version" "$base_version" "$tag" "$distance" "$commit" "$commit_epoch"
else
    printf '%s\n' "$rpm_version"
fi
