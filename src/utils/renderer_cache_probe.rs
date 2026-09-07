// SPDX-License-Identifier: GPL-3.0-only

//! Process-global registry of each render thread's GLES dmabuf-cache size, for
//! the SIGUSR1 VRAM census.
//!
//! cosmic-comp runs one `GlesRenderer` per output surface thread plus the
//! main-thread renderer, each with a private `dmabuf_cache` of imported client
//! dmabufs (one `GlesTexture` + `EGLImage` apiece; since dmabuf render targets
//! are now bound as textures, render-target imports live here too). A VRAM leak
//! of pinned imports lives in exactly one of these, but the process-global GL
//! lifecycle counters cannot say which renderer holds it, and the surface
//! renderers run on threads the census (main thread) cannot reach directly.
//!
//! Each thread therefore records its own renderer's `dmabuf_cache` length here
//! right after draining; the census reads the latest snapshot. A label whose
//! length grows without bound names the leaking renderer.

use std::collections::BTreeMap;
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};

/// Latest `dmabuf_cache` length per renderer label.
static CACHE_SIZES: Mutex<BTreeMap<String, usize>> = Mutex::new(BTreeMap::new());

#[cfg(test)]
mod lifecycle_tests {
    use super::*;

    #[test]
    fn retiring_thread_removes_its_cache_observations() {
        let marker = SurfaceThreadHandle::new();
        record("surface[fixture] gpu", 3);
        record_detail("compositor[fixture]", "slots".into());
        drop(marker);
        assert!(
            !snapshot()
                .iter()
                .any(|(label, _)| label.contains("fixture"))
        );
        assert!(
            !details_snapshot()
                .iter()
                .any(|(label, _)| label.contains("fixture"))
        );
    }
}

/// Live surface render threads. Each surface thread holds a
/// [`SurfaceThreadHandle`] for its whole lifetime, so a count above the number
/// of connected output surfaces means threads are being stranded — detached on
/// disconnect/reconfigure without ever exiting — leaking their renderer,
/// swapchain and postprocess offscreens (the dominant pinned full-screen
/// buffers). The per-label cache registry above cannot reveal this because a
/// stranded thread and its replacement share an output label.
static LIVE_SURFACE_THREADS: AtomicUsize = AtomicUsize::new(0);

/// RAII marker a surface thread keeps on its stack for its whole lifetime; the
/// census counts these to detect stranded threads. The count drops only when
/// the thread function actually returns, so a thread stuck before its `End`
/// handler keeps counting.
pub struct SurfaceThreadHandle(());

impl SurfaceThreadHandle {
    pub fn new() -> Self {
        LIVE_SURFACE_THREADS.fetch_add(1, Ordering::Relaxed);
        SurfaceThreadHandle(())
    }
}

impl Default for SurfaceThreadHandle {
    fn default() -> Self {
        Self::new()
    }
}

impl Drop for SurfaceThreadHandle {
    fn drop(&mut self) {
        let owner = format!("@{:?}]", std::thread::current().id());
        CACHE_SIZES
            .lock()
            .unwrap()
            .retain(|key, _| !key.contains(&owner));
        CACHE_DETAILS
            .lock()
            .unwrap()
            .retain(|key, _| !key.contains(&owner));
        LIVE_SURFACE_THREADS.fetch_sub(1, Ordering::Relaxed);
    }
}

/// Number of live surface render threads.
pub fn live_surface_threads() -> usize {
    LIVE_SURFACE_THREADS.load(Ordering::Relaxed)
}

/// Record the `dmabuf_cache` length for the renderer identified by `label`
/// (e.g. `"surface[DP-2]"`, `"main[/dev/dri/card1]"`), overwriting that label's
/// previous value so the registry holds each renderer's most recent
/// post-cleanup size.
pub fn record(label: impl Into<String>, dmabuf_cache_len: usize) {
    if let Ok(mut map) = CACHE_SIZES.lock() {
        map.insert(scoped_label(label.into()), dmabuf_cache_len);
    }
}

/// Every recorded renderer's latest `dmabuf_cache` length, sorted by label.
pub fn snapshot() -> Vec<(String, usize)> {
    CACHE_SIZES
        .lock()
        .map(|map| map.iter().map(|(k, v)| (k.clone(), *v)).collect())
        .unwrap_or_default()
}

/// Latest per-label detail line (cache report / compositor slot-ids) for the
/// VRAM-leak census. Separate from [`CACHE_SIZES`] so the cheap length probe is
/// unaffected.
static CACHE_DETAILS: Mutex<BTreeMap<String, String>> = Mutex::new(BTreeMap::new());

/// Record a detail line for `label`, overwriting the previous one.
pub fn record_detail(label: impl Into<String>, detail: String) {
    if let Ok(mut map) = CACHE_DETAILS.lock() {
        map.insert(scoped_label(label.into()), detail);
    }
}

fn scoped_label(label: String) -> String {
    if label.starts_with("surface[") || label.starts_with("compositor[") {
        label.replacen(']', &format!("@{:?}]", std::thread::current().id()), 1)
    } else {
        label
    }
}

pub fn clear_main() {
    CACHE_SIZES
        .lock()
        .unwrap()
        .retain(|key, _| !key.starts_with("main "));
    CACHE_DETAILS
        .lock()
        .unwrap()
        .retain(|key, _| !key.starts_with("main ") && !key.starts_with("compositor_map "));
}

/// Every recorded detail line, sorted by label.
pub fn details_snapshot() -> Vec<(String, String)> {
    CACHE_DETAILS
        .lock()
        .map(|map| map.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
        .unwrap_or_default()
}

/// Format a [`debug_dmabuf_cache_report`](smithay's `GlesRenderer`) result into a
/// compact per-size histogram: each distinct buffer size with the count and the
/// `id:strong-count` of every alive entry, plus the dead-entry tally. A
/// render-target-sized group that keeps growing across cycles — with old ids
/// persisting and strong-count ≥ 1 — is the leak.
pub fn format_cache_report(alive: &[(i32, i32, u64, usize, String)], dead: usize) -> String {
    let mut by_size: BTreeMap<(i32, i32), Vec<(u64, usize)>> = BTreeMap::new();
    for (w, h, id, sc, _origin) in alive {
        by_size.entry((*w, *h)).or_default().push((*id, *sc));
    }
    let mut parts = Vec::new();
    for ((w, h), mut entries) in by_size {
        entries.sort_unstable();
        let ids = entries
            .iter()
            .map(|(id, sc)| format!("{id}:sc{sc}"))
            .collect::<Vec<_>>()
            .join(",");
        parts.push(format!("{w}x{h}×{} [{ids}]", entries.len()));
    }
    format!("alive={} dead={dead} {{{}}}", alive.len(), parts.join("; "))
}

/// Group the alive cache entries by their `Dmabuf` creation-path signature, so the
/// census reveals what the leaked full-output buffers actually are — stranded
/// render-target `export()` clones vs leaked client-buffer imports. Entries below
/// the size threshold carry an empty signature (reported as `<small>`).
pub fn format_cache_origins(alive: &[(i32, i32, u64, usize, String)]) -> String {
    let mut by_origin: BTreeMap<&str, Vec<u64>> = BTreeMap::new();
    for (_w, _h, id, _sc, origin) in alive {
        let key = if origin.is_empty() { "<small>" } else { origin };
        by_origin.entry(key).or_default().push(*id);
    }
    let mut groups = by_origin.into_iter().collect::<Vec<_>>();
    groups.sort_by_key(|(_, ids)| std::cmp::Reverse(ids.len()));
    groups
        .into_iter()
        .map(|(origin, mut ids)| {
            ids.sort_unstable();
            let n = ids.len();
            let ids_str = ids
                .iter()
                .map(|id| id.to_string())
                .collect::<Vec<_>>()
                .join(",");
            format!("{n}× [{ids_str}] {origin}")
        })
        .collect::<Vec<_>>()
        .join(" | ")
}

/// Latest grouped escaped-clone sites for the VRAM census. The `Dmabuf` clone
/// registry is process-global and rebuilt every census, so this is replaced
/// wholesale each time (never merged) — a stale grouping must not linger.
static CLONE_SITES: Mutex<Vec<(String, String)>> = Mutex::new(Vec::new());

/// Replace the recorded escaped-clone sites — `(label, backtrace)` — wholesale.
pub fn set_clone_sites(sites: Vec<(String, String)>) {
    if let Ok(mut v) = CLONE_SITES.lock() {
        *v = sites;
    }
}

/// Every recorded escaped-clone site, in census-ready (rarest-first) order.
pub fn clone_sites_snapshot() -> Vec<(String, String)> {
    CLONE_SITES.lock().map(|v| v.clone()).unwrap_or_default()
}

/// Group live `Dmabuf` clone backtraces (from smithay's `debug_dmabuf_clone_sites`)
/// by identical reduced call site, so the escaped-clone location stands out as the
/// rare signature whose buffer ids match the leaked `main ptrs` orphans. Returns
/// `(label, reduced backtrace)` per unique site, rarest first — a `strong_count==1`
/// orphan contributes a single survivor, so its site sorts to the top.
pub fn group_clone_sites(sites: &[(u64, String)]) -> Vec<(String, String)> {
    let mut by_site: BTreeMap<String, Vec<u64>> = BTreeMap::new();
    for (debug_id, backtrace) in sites {
        by_site
            .entry(reduce_clone_backtrace(backtrace))
            .or_default()
            .push(*debug_id);
    }
    let mut groups: Vec<(String, Vec<u64>)> = by_site.into_iter().collect();
    groups.sort_by_key(|(_, ids)| ids.len());
    groups
        .into_iter()
        .map(|(site, mut ids)| {
            ids.sort_unstable();
            let ids_str = ids
                .iter()
                .map(|id| id.to_string())
                .collect::<Vec<_>>()
                .join(",");
            (format!("clone-site n={} ids=[{ids_str}]", ids.len()), site)
        })
        .collect()
}

/// Recorded live `EGLImage` allocation sites — grouped `(label, backtrace)`,
/// rebuilt wholesale each census, mirroring [`CLONE_SITES`].
static EGL_IMAGE_SITES: Mutex<Vec<(String, String)>> = Mutex::new(Vec::new());

/// Replace the recorded `EGLImage` allocation sites — `(label, backtrace)` — wholesale.
pub fn set_egl_image_sites(sites: Vec<(String, String)>) {
    if let Ok(mut v) = EGL_IMAGE_SITES.lock() {
        *v = sites;
    }
}

/// Every recorded live `EGLImage` allocation site, in census-ready (rarest-first) order.
pub fn egl_image_sites_snapshot() -> Vec<(String, String)> {
    EGL_IMAGE_SITES
        .lock()
        .map(|v| v.clone())
        .unwrap_or_default()
}

/// Group live `EGLImage` backtraces (from smithay's `debug_egl_image_sites`) by
/// identical reduced call site. A handle created but never destroyed leaves a
/// surviving entry, so the site that accounts for the bulk of survivors is the
/// leak. Returns `(label, reduced backtrace)` per unique site, largest group last
/// so the dominant leak site is the final, most visible line.
pub fn group_egl_image_sites(sites: &[(usize, String)]) -> Vec<(String, String)> {
    let mut by_site: BTreeMap<String, usize> = BTreeMap::new();
    for (_handle, backtrace) in sites {
        *by_site
            .entry(reduce_clone_backtrace(backtrace))
            .or_default() += 1;
    }
    let mut groups: Vec<(String, usize)> = by_site.into_iter().collect();
    groups.sort_by_key(|(_, n)| *n);
    groups
        .into_iter()
        .map(|(site, n)| (format!("egl-image-site n={n}"), site))
        .collect()
}

/// Reduce a full `Backtrace` string to its meaningful frames — drop std/core/
/// alloc/backtrace runtime noise, keep the smithay/cosmic-comp call chain with
/// `file:line`, capped so a census line stays within journald's field limit.
fn reduce_clone_backtrace(backtrace: &str) -> String {
    let mut out: Vec<String> = Vec::new();
    let mut pending: Option<String> = None;
    for line in backtrace.lines() {
        let trimmed = line.trim();
        if let Some((_, sym)) = trimmed
            .split_once(": ")
            .filter(|(idx, _)| !idx.is_empty() && idx.bytes().all(|b| b.is_ascii_digit()))
        {
            if let Some(p) = pending.take() {
                out.push(p);
            }
            let sym = sym.split("::h").next().unwrap_or(sym);
            let skip = sym.starts_with("std::")
                || sym.starts_with("core::")
                || sym.starts_with("alloc::")
                || sym.contains("backtrace::")
                || sym.contains("rust_begin")
                || sym.contains("__rust")
                || sym.contains("call_once")
                || sym == "main";
            pending = if skip { None } else { Some(sym.to_string()) };
            continue;
        }
        if let (Some(p), Some(rest)) = (pending.as_mut(), trimmed.strip_prefix("at ")) {
            p.push_str(" (");
            p.push_str(rest);
            p.push(')');
            out.push(pending.take().unwrap());
        }
        if out.len() >= 24 {
            break;
        }
    }
    if let Some(p) = pending.take() {
        out.push(p);
    }
    if out.is_empty() {
        "<no frames>".into()
    } else {
        out.join("\n    ")
    }
}
