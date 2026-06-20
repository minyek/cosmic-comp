// SPDX-License-Identifier: GPL-3.0-only

//! Optional GL_KHR_debug callback installer for leak hunting.
//!
//! When `COSMIC_GL_DEBUG=1` is set, registers a debug callback on each GL
//! context cosmic-comp creates. NVIDIA emits `Buffer detailed info` messages
//! that include object IDs on every glGen*/glDelete*; the imbalance in
//! allocate vs. delete counts is the leak signature.
//!
//! When `COSMIC_GL_DEBUG_SYNC=1` is also set, makes the callback synchronous and
//! captures a backtrace on every high-severity GL error. Synchronous delivery
//! runs the callback on the thread that issued the offending call, so the
//! backtrace names the exact Rust call site — the one thing the resource census
//! cannot reveal. Slower, for diagnostic sessions only.
//!
//! Every delivered message is also folded into an in-memory histogram keyed by a
//! normalized template (object IDs and addresses collapsed to placeholders), so
//! the allocate-vs-delete imbalance survives even when journald rate-limits and
//! drops the per-message trace lines. The SIGUSR1 resource census dumps the
//! histogram via [`dump_histogram`].
//!
//! No-op (single env-var lookup) when not enabled.

use std::backtrace::Backtrace;
use std::collections::HashMap;
use std::ffi::{CStr, c_void};
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

use smithay::backend::egl;
use smithay::backend::renderer::glow::GlowRenderer;
use tracing::{debug, error, trace, warn};

const GL_DEBUG_OUTPUT: u32 = 0x92E0;
const GL_DEBUG_OUTPUT_SYNCHRONOUS: u32 = 0x8242;
const GL_DONT_CARE: u32 = 0x1100;
const GL_TRUE: u8 = 1;
const GL_DEBUG_SEVERITY_NOTIFICATION: u32 = 0x826B;
const GL_DEBUG_SEVERITY_LOW: u32 = 0x9148;
const GL_DEBUG_SEVERITY_MEDIUM: u32 = 0x9147;
const GL_DEBUG_SEVERITY_HIGH: u32 = 0x9146;
const GL_DEBUG_TYPE_ERROR: u32 = 0x824C;
const GL_DEBUG_TYPE_DEPRECATED_BEHAVIOR: u32 = 0x824D;
const GL_DEBUG_TYPE_UNDEFINED_BEHAVIOR: u32 = 0x824E;
const GL_DEBUG_TYPE_PORTABILITY: u32 = 0x824F;
const GL_DEBUG_TYPE_PERFORMANCE: u32 = 0x8250;
const GL_DEBUG_TYPE_OTHER: u32 = 0x8251;
const GL_DEBUG_TYPE_MARKER: u32 = 0x8268;

type GlDebugProc = extern "system" fn(
    source: u32,
    gltype: u32,
    id: u32,
    severity: u32,
    length: i32,
    message: *const i8,
    user_param: *mut c_void,
);

type GlDebugMessageCallback =
    unsafe extern "system" fn(callback: Option<GlDebugProc>, user_param: *const c_void);
type GlEnable = unsafe extern "system" fn(cap: u32);
type GlDebugMessageControl = unsafe extern "system" fn(
    source: u32,
    gltype: u32,
    severity: u32,
    count: i32,
    ids: *const u32,
    enabled: u8,
);

static INSTALLED: AtomicBool = AtomicBool::new(false);
static SYNC: AtomicBool = AtomicBool::new(false);

/// Cap on distinct templates retained in the histogram. NVIDIA's per-allocation
/// messages normalize to a handful of templates, so this is generous; the cap
/// only bounds memory against an unexpectedly chatty driver.
const MAX_TEMPLATES: usize = 512;
/// Sentinel key counting every unseen template once the table is full, so the
/// dump still signals that templates were dropped rather than silently lying.
const OVERFLOW_KEY: &str = "(template table full)";

static MESSAGE_TOTAL: AtomicU64 = AtomicU64::new(0);
static HISTOGRAM: Mutex<Option<HashMap<String, u64>>> = Mutex::new(None);

fn enabled() -> bool {
    std::env::var_os("COSMIC_GL_DEBUG").is_some_and(|v| v != "0")
}

fn sync_enabled() -> bool {
    std::env::var_os("COSMIC_GL_DEBUG_SYNC").is_some_and(|v| v != "0")
}

/// Install on a freshly-initialized renderer. Cheap when disabled, idempotent
/// across multiple renderers in the same process.
pub fn try_install(renderer: &mut GlowRenderer) {
    if !enabled() {
        return;
    }
    if let Err(err) = renderer.with_context(|_| install_in_current_context()) {
        warn!(?err, "GL_KHR_debug: failed to enter renderer context");
    }
}

fn install_in_current_context() {
    let cb_ptr = unsafe { egl::get_proc_address("glDebugMessageCallback") };
    let cb_ptr = if cb_ptr.is_null() {
        unsafe { egl::get_proc_address("glDebugMessageCallbackKHR") }
    } else {
        cb_ptr
    };
    if cb_ptr.is_null() {
        warn!("GL_KHR_debug: glDebugMessageCallback not available on this driver");
        return;
    }
    let enable_ptr = unsafe { egl::get_proc_address("glEnable") };
    if enable_ptr.is_null() {
        warn!("GL_KHR_debug: glEnable not available (?!)");
        return;
    }
    let control_ptr = unsafe { egl::get_proc_address("glDebugMessageControl") };
    let control_ptr = if control_ptr.is_null() {
        unsafe { egl::get_proc_address("glDebugMessageControlKHR") }
    } else {
        control_ptr
    };

    // SAFETY: function-pointer types match the GL spec exactly. Callback only
    // touches static state and the immutable string message.
    unsafe {
        let set_cb: GlDebugMessageCallback = std::mem::transmute(cb_ptr);
        let enable: GlEnable = std::mem::transmute(enable_ptr);
        set_cb(Some(debug_callback), std::ptr::null());
        enable(GL_DEBUG_OUTPUT);
        if sync_enabled() {
            enable(GL_DEBUG_OUTPUT_SYNCHRONOUS);
            SYNC.store(true, Ordering::SeqCst);
        }
        // Unmute the whole message space. NVIDIA's per-allocation "detailed
        // info" — the allocate/delete imbalance this whole module exists to
        // catch — arrives at NOTIFICATION severity, which the driver's default
        // filter drops. Without this the histogram would never see them.
        if control_ptr.is_null() {
            warn!(
                "GL_KHR_debug: glDebugMessageControl not available; relying on driver default filter (NOTIFICATION messages may be dropped)"
            );
        } else {
            let control: GlDebugMessageControl = std::mem::transmute(control_ptr);
            control(
                GL_DONT_CARE,
                GL_DONT_CARE,
                GL_DONT_CARE,
                0,
                std::ptr::null(),
                GL_TRUE,
            );
        }
    }

    if !INSTALLED.swap(true, Ordering::SeqCst) {
        // warn! so the confirmation survives the quiet capture filter
        // (`cosmic_comp::utils::gl_debug=warn`) — it is the liveness signal the
        // runbook's pre-flight greps for.
        warn!(
            "GL_KHR_debug callback installed (sync={}); set COSMIC_GL_DEBUG=0 to disable",
            sync_enabled()
        );
    }
}

extern "system" fn debug_callback(
    source: u32,
    gltype: u32,
    _id: u32,
    severity: u32,
    _length: i32,
    message: *const i8,
    _user_param: *mut c_void,
) {
    // SAFETY: the GL driver guarantees `message` is a valid NUL-terminated C
    // string for the duration of the callback.
    let msg = unsafe { CStr::from_ptr(message) }.to_string_lossy();
    record_template(severity, &msg);
    let kind = match gltype {
        GL_DEBUG_TYPE_ERROR => "error",
        GL_DEBUG_TYPE_DEPRECATED_BEHAVIOR => "deprecated",
        GL_DEBUG_TYPE_UNDEFINED_BEHAVIOR => "undefined-behavior",
        GL_DEBUG_TYPE_PORTABILITY => "portability",
        GL_DEBUG_TYPE_PERFORMANCE => "performance",
        GL_DEBUG_TYPE_MARKER => "marker",
        GL_DEBUG_TYPE_OTHER => "other",
        _ => "?",
    };
    match severity {
        GL_DEBUG_SEVERITY_HIGH => {
            error!(src = source, kind, "GL: {msg}");
            // In sync mode the callback runs inline on the offending call's
            // thread, so this backtrace points straight at the Rust call site
            // that provoked the error (e.g. a leaked-PBO readback). Async mode
            // batches messages on a driver thread where the stack is unrelated,
            // so only capture when sync was requested.
            if gltype == GL_DEBUG_TYPE_ERROR && SYNC.load(Ordering::SeqCst) {
                error!("GL error call site:\n{}", Backtrace::force_capture());
            }
        }
        GL_DEBUG_SEVERITY_MEDIUM => warn!(src = source, kind, "GL: {msg}"),
        GL_DEBUG_SEVERITY_LOW => debug!(src = source, kind, "GL: {msg}"),
        GL_DEBUG_SEVERITY_NOTIFICATION => trace!(src = source, kind, "GL: {msg}"),
        _ => debug!(src = source, kind, sev = severity, "GL: {msg}"),
    }
}

fn severity_tag(severity: u32) -> &'static str {
    match severity {
        GL_DEBUG_SEVERITY_HIGH => "[high]",
        GL_DEBUG_SEVERITY_MEDIUM => "[med]",
        GL_DEBUG_SEVERITY_LOW => "[low]",
        GL_DEBUG_SEVERITY_NOTIFICATION => "[note]",
        _ => "[?]",
    }
}

/// Collapse the variable parts of a message into placeholders so that, e.g., all
/// "Buffer detailed info: Buffer object 1234 ..." lines fold onto one template.
/// `0x`-prefixed hex addresses become `0xN`; every other run of decimal digits
/// becomes `N`.
fn normalize_message(msg: &str) -> String {
    let mut out = String::with_capacity(msg.len());
    let bytes = msg.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        let b = bytes[i];
        // `0x` hex run: consume the prefix and the trailing hex digits as one
        // unit, emitting `0xN`, so addresses don't survive as decimal `N`s.
        if b == b'0' && i + 1 < bytes.len() && (bytes[i + 1] | 0x20) == b'x' {
            i += 2;
            while i < bytes.len() && bytes[i].is_ascii_hexdigit() {
                i += 1;
            }
            out.push_str("0xN");
            continue;
        }
        if b.is_ascii_digit() {
            while i < bytes.len() && bytes[i].is_ascii_digit() {
                i += 1;
            }
            out.push('N');
            continue;
        }
        // ASCII fast path keeps multi-byte UTF-8 sequences intact: only the
        // leading byte of a sequence is < 0x80, so non-ASCII bytes fall through
        // here and are copied verbatim.
        out.push(b as char);
        i += 1;
    }
    out
}

/// Fold one delivered message into the histogram. Runs on the GL callback,
/// possibly concurrently across contexts; stays panic-free so a poisoned lock or
/// allocation hiccup can never take down the driver thread.
fn record_template(severity: u32, msg: &str) {
    MESSAGE_TOTAL.fetch_add(1, Ordering::Relaxed);

    // A panic here would unwind through C; clearing poison instead lets the next
    // census still read a (possibly partial) table.
    HISTOGRAM.clear_poison();
    let Ok(mut guard) = HISTOGRAM.lock() else {
        return;
    };
    let table = guard.get_or_insert_with(HashMap::new);

    let key = format!("{} {}", severity_tag(severity), normalize_message(msg));
    if let Some(count) = table.get_mut(&key) {
        *count += 1;
    } else if table.len() < MAX_TEMPLATES {
        table.insert(key, 1);
    } else {
        *table.entry(OVERFLOW_KEY.to_string()).or_insert(0) += 1;
    }
}

/// `warn!`-dump the template histogram, busiest first. No-op (one atomic load)
/// when nothing was ever recorded, so the SIGUSR1 census can call it
/// unconditionally even when `COSMIC_GL_DEBUG` was never set.
pub fn dump_histogram() {
    let total = MESSAGE_TOTAL.load(Ordering::Relaxed);
    if total == 0 {
        return;
    }

    HISTOGRAM.clear_poison();
    let Ok(guard) = HISTOGRAM.lock() else {
        return;
    };
    let Some(table) = guard.as_ref() else {
        return;
    };

    let distinct = table.len();
    let mut rows: Vec<(&String, &u64)> = table.iter().collect();
    rows.sort_by(|a, b| b.1.cmp(a.1).then_with(|| a.0.cmp(b.0)));

    warn!("=== GL debug-message histogram ({distinct} templates, {total} messages) ===");
    // Cap output so a runaway driver can't flood the journal; the busiest
    // templates — where any allocate/delete imbalance lives — sort to the top.
    const MAX_LINES: usize = 80;
    for (template, count) in rows.iter().take(MAX_LINES) {
        warn!("  {count} × {template}");
    }
    if distinct > MAX_LINES {
        warn!("  … {} more templates omitted", distinct - MAX_LINES);
    }
}
