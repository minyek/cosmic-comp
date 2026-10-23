Name:           cosmic-comp
Version:        %{rpm_version}
Release:        1%{?dist}
Summary:        COSMIC Wayland compositor
License:        (0BSD OR Apache-2.0 OR MIT) AND (Apache-2.0 OR Apache-2.0 WITH LLVM-exception OR MIT) AND (Apache-2.0 OR BSD-2-Clause OR MIT) AND (Apache-2.0 OR BSD-3-Clause OR MIT) AND (Apache-2.0 OR BSD-3-Clause) AND (Apache-2.0 OR CC0-1.0 OR MIT) AND (Apache-2.0 OR GPL-2.0-only) AND (Apache-2.0 OR LGPL-2.1-or-later OR MIT) AND (Apache-2.0 OR MIT OR Zlib) AND (Apache-2.0 OR MIT) AND (LGPL-3.0-or-later OR MIT) AND (MIT OR Unlicense) AND Apache-2.0 AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND CC0-1.0 AND GPL-3.0-only AND ISC AND MIT AND MPL-2.0 AND Unicode-3.0 AND Zlib
URL:            https://github.com/pop-os/cosmic-comp
Source0:        cosmic-comp.tar.gz

BuildRequires:  cargo-rpm-macros >= 25
BuildRequires:  rustc
BuildRequires:  lld
BuildRequires:  cargo
BuildRequires:  wayland-devel
BuildRequires:  libxkbcommon-devel
BuildRequires:  libglvnd-devel
BuildRequires:  libseat-devel
BuildRequires:  libinput-devel
BuildRequires:  mesa-libgbm-devel
BuildRequires:  pixman-devel
BuildRequires:  libdisplay-info-devel
BuildRequires:  just

Requires:       libseat%{?_isa}
Requires:       libwayland-server%{?_isa}
Requires:       xorg-x11-server-Xwayland
Requires:       cosmic-icon-theme >= %{rpm_base_version}
Recommends:     cosmic-session

%description
COSMIC Wayland compositor built from the committed source in the local checkout.

%prep
%autosetup -n cosmic-comp
test "$(sed -n '1p' .rpm-source-metadata)" = "%{source_git_hash}"
test "$(sed -n '2p' .rpm-source-metadata)" = "%{source_commit_epoch}"
%cargo_prep -N
cat vendor-config.toml >> .cargo/config.toml

%build
export CARGO_HOME="%{_topdir}/cargo-home"
mkdir -p "$CARGO_HOME"
export GIT_HASH="%{source_git_hash}"
export VERGEN_GIT_SHA="%{source_git_hash}"
export SOURCE_DATE_EPOCH="%{source_commit_epoch}"
export VERGEN_GIT_COMMIT_DATE="$(date -u -d "@${SOURCE_DATE_EPOCH}" +%Y-%m-%d)"
export GIT_CEILING_DIRECTORIES="%{_topdir}"
just build-release --offline --frozen
%{cargo_license_summary}
%{cargo_license} > LICENSE.dependencies
%{cargo_vendor_manifest}

%install
just rootdir=%{buildroot} prefix=%{_prefix} install

%files
%{_bindir}/cosmic-comp
%{_datadir}/cosmic/com.system76.CosmicSettings.Shortcuts/v1/defaults
%{_datadir}/cosmic/com.system76.CosmicSettings.WindowRules/v1/tiling_exception_defaults
%license LICENSE
%license LICENSE.dependencies
%license cargo-vendor.txt
