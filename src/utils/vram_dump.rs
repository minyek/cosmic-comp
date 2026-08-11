// SPDX-License-Identifier: GPL-3.0-only

//! SIGUSR1-triggered census of cosmic-comp's resource-holding data structures.
//!
//! Send `SIGUSR1` to the compositor process to dump per-output and global
//! counts of capture sessions, offscreen renderbuffers, zoom states, and
//! pending activations. Compare two dumps over a workflow to spot containers
//! that grow without bound — the leak signature.
//!
//! Counts only; no byte-level VRAM accounting (NVIDIA's driver does not
//! expose per-allocation sizes). The signal handler stores a flag in
//! async-signal-safe fashion; the actual walk runs from the main event loop.
//!
//! The dump is emitted at `warn!` on purpose: a release build defaults the log
//! filter to `cosmic_comp=warn`, so an `warn!` census is silently dropped
//! unless the operator remembered to raise `RUST_LOG`. A SIGUSR1 dump is a
//! rare, deliberate request, so logging it at `warn!` makes it visible without
//! any environment fiddling.

use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};

use smithay::output::Output;
use tracing::warn;

use crate::shell::{Shell, Workspace, zoom::OutputZoomState};
use crate::state::State;
use crate::wayland::handlers::image_copy_capture::{
    SessionCensus, pending_frame_count, session_census, session_census_user_data,
};

static DUMP_REQUESTED: AtomicBool = AtomicBool::new(false);

extern "C" fn sigusr1_handler(_: libc::c_int) {
    DUMP_REQUESTED.store(true, Ordering::SeqCst);
}

/// Install the SIGUSR1 handler. Without this, SIGUSR1 would terminate the
/// process (default disposition).
pub fn install_sigusr1_handler() {
    // SAFETY: handler only mutates an atomic, which is async-signal-safe.
    unsafe {
        let handler = sigusr1_handler as extern "C" fn(libc::c_int);
        libc::signal(libc::SIGUSR1, handler as libc::sighandler_t);
    }
    warn!("SIGUSR1 dumper installed: `kill -USR1 <pid>` to dump VRAM census");
}

/// Called once per event-loop iteration. Cheap when no dump is pending
/// (single atomic load).
pub fn drain_dump_requests(state: &State) {
    if !DUMP_REQUESTED.swap(false, Ordering::SeqCst) {
        return;
    }
    dump_resource_census(state);
}

fn dump_resource_census(state: &State) {
    let common = &state.common;
    warn!("=== VRAM/resource census (SIGUSR1) ===");

    let shell = common.shell.read();
    warn!(
        "pending_windows={}, pending_layers={}, pending_activations={}, override_redirect_windows={}, idle_inhibiting_surfaces={}",
        shell.pending_windows.len(),
        shell.pending_layers.len(),
        shell.pending_activations.len(),
        shell.override_redirect_windows.len(),
        common.idle_inhibiting_surfaces.len(),
    );
    warn!("iced_elements={}", crate::utils::iced::live_count());

    dump_shell(&shell);

    dump_gl_object_counters();

    dump_workload_counters();

    dump_renderer_cache_sizes();

    // No-op unless COSMIC_GL_DEBUG recorded messages; surfaces the GL
    // allocate/delete imbalance that the per-message trace lines lose to
    // journald rate-limiting.
    crate::utils::gl_debug::dump_histogram();
}

/// Process-wide GL object accounting from the smithay fork: live counts per
/// object class (created − destroyed) and the deferred-destruction queue
/// depth per resource type (queued − drained). A live count that grows while
/// the window set is constant names the leaking class; a growing queue depth
/// means a context's cleanup queue is not being drained.
fn dump_gl_object_counters() {
    // Signed differences: a negative value means a destroy path fired whose
    // creation site is not instrumented — a coverage gap worth knowing about,
    // not a reason to panic the compositor on under/overflow.
    fn live(created: u64, destroyed: u64) -> i64 {
        created as i64 - destroyed as i64
    }

    let c = smithay::backend::renderer::gles::vram_counters();
    warn!(
        "GL live objects: textures={} egl_images={} renderbuffers={} framebuffers={} buffers={}",
        live(c.textures_created, c.textures_destroyed),
        live(c.egl_images_created, c.egl_images_destroyed),
        live(c.renderbuffers_created, c.renderbuffers_destroyed),
        live(c.framebuffers_created, c.framebuffers_destroyed),
        live(c.buffers_created, c.buffers_destroyed),
    );
    warn!(
        "GL cleanup queue depth: texture={} framebuffer={} renderbuffer={} egl_image={} mapping={} program={} sync={}",
        live(c.queued_texture, c.drained_texture),
        live(c.queued_framebuffer, c.drained_framebuffer),
        live(c.queued_renderbuffer, c.drained_renderbuffer),
        live(c.queued_egl_image, c.drained_egl_image),
        live(c.queued_mapping, c.drained_mapping),
        live(c.queued_program, c.drained_program),
        live(c.queued_sync, c.drained_sync),
    );
    warn!("GL raw counters: {c:?}");
}

fn dump_workload_counters() {
    let c = crate::utils::workload_counters::workload_counters();
    warn!(
        "workload counters: pointer_motions={} workspace_activations={} zoom_changes={}",
        c.pointer_motions, c.workspace_activations, c.zoom_changes,
    );
}

/// Per-renderer `dmabuf_cache` length recorded by each render thread after its
/// last cleanup (see [`crate::utils::renderer_cache_probe`]). The global GL
/// counters above give the *total* live object counts but not *which* renderer
/// holds them; this per-instance length localises a pinned-import leak to one
/// renderer — a `dmabuf_cache` that grows without bound names it.
fn dump_renderer_cache_sizes() {
    warn!(
        "live surface threads={} (expect == connected output surfaces; higher means stranded threads leaking renderer+swapchain+postprocess)",
        crate::utils::renderer_cache_probe::live_surface_threads(),
    );
    for (label, dmabuf_cache) in crate::utils::renderer_cache_probe::snapshot() {
        warn!("renderer caches {label}: dmabuf_cache={dmabuf_cache}");
    }
    for (label, detail) in crate::utils::renderer_cache_probe::details_snapshot() {
        warn!("renderer cache detail {label}: {detail}");
    }
    // Escaped-clone backtraces (COSMIC_DMABUF_TRACE only; empty otherwise). Each
    // unique site on its own line so journald never truncates it; cross-reference
    // the ids against `main ptrs` — the rare site whose ids are the leaked orphans
    // is where the never-dropped clone was made.
    for (label, backtrace) in crate::utils::renderer_cache_probe::clone_sites_snapshot() {
        warn!("dmabuf {label}:\n{backtrace}");
    }
    // Live `EGLImage` allocation sites (COSMIC_DMABUF_TRACE only; empty otherwise).
    // A surviving handle is a leaked EGLImage; the dominant group's call site names
    // where the leak is born. Largest group sorts last, so it is the final line.
    for (label, backtrace) in crate::utils::renderer_cache_probe::egl_image_sites_snapshot() {
        warn!("{label}:\n{backtrace}");
    }
}

fn dump_shell(shell: &Shell) {
    let outputs: Vec<Output> = shell.outputs().cloned().collect();
    warn!("outputs={}", outputs.len());

    let mut totals = SessionCensus::default();
    let mut total_pending_frames = 0usize;
    let mut total_zoom_states = 0usize;

    for output in &outputs {
        let census = session_census_user_data(output.user_data());
        let pending = pending_frame_count(output.user_data());
        let zoom = output.user_data().get::<Mutex<OutputZoomState>>().is_some();
        totals.sessions += census.sessions;
        totals.cursor_sessions += census.cursor_sessions;
        totals.offscreen_renderbuffers += census.offscreen_renderbuffers;
        total_pending_frames += pending;
        if zoom {
            total_zoom_states += 1;
        }
        warn!(
            "  output {:?}: sessions={}, cursor_sessions={}, pending_frames={pending}, offscreen={}, zoom={zoom}",
            output.name(),
            census.sessions,
            census.cursor_sessions,
            census.offscreen_renderbuffers,
        );
    }

    let workspaces: Vec<&Workspace> = shell.workspaces.spaces().collect();
    let mut total_minimized = 0usize;
    let mut ws_totals = SessionCensus::default();
    for ws in &workspaces {
        total_minimized += ws.minimized_windows.len();
        let c = session_census(&ws.image_copy);
        ws_totals.sessions += c.sessions;
        ws_totals.cursor_sessions += c.cursor_sessions;
        ws_totals.offscreen_renderbuffers += c.offscreen_renderbuffers;
    }
    warn!(
        "workspaces={}, minimized_windows={}, ws_sessions={}, ws_cursor_sessions={}, ws_offscreen={}",
        workspaces.len(),
        total_minimized,
        ws_totals.sessions,
        ws_totals.cursor_sessions,
        ws_totals.offscreen_renderbuffers,
    );

    // Walk every CosmicSurface across all workspaces (mapped, minimized,
    // fullscreen). A surface that outlives its window or whose user-data
    // accumulates dead sessions shows up as a non-shrinking offscreen count
    // here.
    let mut surface_count = 0usize;
    let mut surf_totals = SessionCensus::default();
    let add = |s: &crate::shell::CosmicSurface, totals: &mut SessionCensus, count: &mut usize| {
        *count += 1;
        let c = session_census_user_data(s.user_data());
        totals.sessions += c.sessions;
        totals.cursor_sessions += c.cursor_sessions;
        totals.offscreen_renderbuffers += c.offscreen_renderbuffers;
    };
    for ws in &workspaces {
        for mapped in ws.mapped() {
            for (surface, _) in mapped.windows() {
                add(&surface, &mut surf_totals, &mut surface_count);
            }
        }
        for min in &ws.minimized_windows {
            for surface in min.windows() {
                add(&surface, &mut surf_totals, &mut surface_count);
            }
        }
        for fs in &ws.fullscreen_surfaces {
            add(&fs.surface, &mut surf_totals, &mut surface_count);
        }
    }
    warn!(
        "toplevels={surface_count}, surface_sessions={}, surface_cursor_sessions={}, surface_offscreen={}",
        surf_totals.sessions, surf_totals.cursor_sessions, surf_totals.offscreen_renderbuffers,
    );

    warn!(
        "TOTALS (outputs only): sessions={}, cursor_sessions={}, pending_frames={total_pending_frames}, offscreen_renderbuffers={}, output_zoom_states={total_zoom_states}",
        totals.sessions, totals.cursor_sessions, totals.offscreen_renderbuffers,
    );
}
