# Documentation map

The project overview and build instructions live in [`README.md`](../README.md).
Area guides describe workflows in their project's directory; the Fedora RPM
guide is [`packaging/rpm/README.md`](../packaging/rpm/README.md).

Active specifications, requirements and plans belong in `docs/design/`,
`docs/requirements/` and `docs/plans/`. After a change ships, move its planning
documents to `docs/archive/<feature-slug>/` for ticketless work or
`docs/archive/<parent-id>/` for ticketed work. Keep each archived document's
original history with `git mv` and update links when moving it.

The completed Fedora RPM design is archived at
[`rpm-packaging-spec.md`](archive/rpm-packaging/rpm-packaging-spec.md).

The verified local Fedora RPM build is recorded in
[`docs/reports/rpm-packaging-acceptance.md`](reports/rpm-packaging-acceptance.md).
