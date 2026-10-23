# Fedora RPM build acceptance

The `cosmic-comp-rpm-release` job completed with `rc=0` and `result=clean`
(2026-10-23). Its journal reports the output path, EVR, and source commit at
lines 2083–2086. It produced
`target/rpm-build/cosmic-comp.rpm` from commit
`f8729fb882ab8cdb3a95f6f919cfccfd1610dcce`.

Package inspection confirmed name `cosmic-comp`, x86_64, EVR
`1.11.0^20261023.99999999.29.gf8729fb882ab-1.fc45`, runtime requirement
`cosmic-icon-theme >= 1.11.0`, recommendation `cosmic-session`, executable
`/usr/bin/cosmic-comp`, two COSMIC defaults, and license files. The executable
contains the full source SHA. `rpmdev-vercmp` confirmed this EVR is newer than
the installed Fedora 45 snapshot
`1.11.0^git20261020.8cb1802-1.fc45`.

The build log reports an empty `%changelog` warning for
`%source_date_epoch_from_changelog`; it did not prevent RPM creation. No system
packages were installed and no system configuration changed.
