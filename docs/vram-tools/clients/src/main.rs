use cosmic_protocols::{
    toplevel_info::v1::client::{zcosmic_toplevel_handle_v1, zcosmic_toplevel_info_v1},
    toplevel_management::v1::client::zcosmic_toplevel_manager_v1,
};
use serde_json::{Value, json};
use std::{
    io::{self, BufRead, Write},
    os::fd::{AsFd, AsRawFd},
    sync::mpsc,
    time::{Duration, Instant},
};
use vram_retest_client::Evidence;
use wayland_client::{
    Connection, Dispatch, QueueHandle, delegate_noop,
    globals::{GlobalListContents, registry_queue_init},
    protocol::{
        wl_buffer, wl_callback, wl_compositor, wl_pointer, wl_registry, wl_seat, wl_shm,
        wl_shm_pool, wl_surface,
    },
};
use wayland_protocols::ext::foreign_toplevel_list::v1::client::{
    ext_foreign_toplevel_handle_v1, ext_foreign_toplevel_list_v1,
};
use wayland_protocols::{
    wp::{
        cursor_shape::v1::client::{wp_cursor_shape_device_v1, wp_cursor_shape_manager_v1},
        pointer_constraints::zv1::client::{zwp_locked_pointer_v1, zwp_pointer_constraints_v1},
    },
    xdg::{
        activation::v1::client::{xdg_activation_token_v1, xdg_activation_v1},
        shell::client::{xdg_surface, xdg_toplevel, xdg_wm_base},
    },
};

fn emit(event: &str, detail: Value) {
    println!(
        "{}",
        json!({"event":event,"pid":std::process::id(),"detail":detail})
    );
    io::stdout().flush().expect("stdout flush failed");
}

struct State {
    evidence: Evidence,
    surface: wl_surface::WlSurface,
    shell_surface: xdg_surface::XdgSurface,
    toplevel: xdg_toplevel::XdgToplevel,
    buffer: Option<wl_buffer::WlBuffer>,
    shm: wl_shm::WlShm,
    pointer: wl_pointer::WlPointer,
    seat: wl_seat::WlSeat,
    constraints: Option<zwp_pointer_constraints_v1::ZwpPointerConstraintsV1>,
    constraint: Option<zwp_locked_pointer_v1::ZwpLockedPointerV1>,
    shapes: Option<wp_cursor_shape_device_v1::WpCursorShapeDeviceV1>,
    activation: Option<xdg_activation_v1::XdgActivationV1>,
    token: Option<xdg_activation_token_v1::XdgActivationTokenV1>,
    serial: u32,
    width: i32,
    height: i32,
    running: bool,
    quitting: bool,
    app_id: String,
    info: Option<zcosmic_toplevel_info_v1::ZcosmicToplevelInfoV1>,
    management: Option<zcosmic_toplevel_manager_v1::ZcosmicToplevelManagerV1>,
    managed: Option<zcosmic_toplevel_handle_v1::ZcosmicToplevelHandleV1>,
    capabilities: Vec<u32>,
}

impl State {
    fn command(&mut self, command: &str, qh: &QueueHandle<Self>) -> Result<(), String> {
        self.evidence.validate(command)?;
        match command {
            "lock" => {
                let manager = self
                    .constraints
                    .as_ref()
                    .ok_or("missing zwp_pointer_constraints_v1")?;
                self.constraint = Some(manager.lock_pointer(
                    &self.surface,
                    &self.pointer,
                    None,
                    zwp_pointer_constraints_v1::Lifetime::Persistent,
                    qh,
                    (),
                ));
                self.evidence.constraint = true;
            }
            "hint" => {
                if self.width <= 80 || self.height <= 80 {
                    return Err("surface is too small for cursor hint (80,80)".into());
                }
                self.constraint
                    .as_ref()
                    .ok_or("missing constraint")?
                    .set_cursor_position_hint(80.0, 80.0);
                self.surface.commit();
            }
            "unlock" => {
                self.constraint
                    .take()
                    .ok_or("missing constraint")?
                    .destroy();
                self.evidence.constraint = false;
                self.evidence.locked = false;
            }
            "shape-default" | "shape-pointer" => {
                let shape = if command == "shape-default" {
                    wp_cursor_shape_device_v1::Shape::Default
                } else {
                    wp_cursor_shape_device_v1::Shape::Pointer
                };
                self.shapes
                    .as_ref()
                    .ok_or("missing wp_cursor_shape_manager_v1")?
                    .set_shape(self.serial, shape);
            }
            "minimize" => self.toplevel.set_minimized(),
            "sticky" | "unsticky" | "unminimize" | "focus" => {
                let manager = self
                    .management
                    .as_ref()
                    .ok_or("missing COSMIC toplevel management")?;
                let handle = self
                    .managed
                    .as_ref()
                    .ok_or("owned COSMIC toplevel not discovered")?;
                let capability = match command {
                    "sticky" | "unsticky" => 7,
                    "unminimize" => 4,
                    _ => 2,
                };
                if !self.capabilities.contains(&capability) {
                    return Err("required COSMIC management capability absent".into());
                }
                match command {
                    "sticky" => manager.set_sticky(handle),
                    "unsticky" => manager.unset_sticky(handle),
                    "unminimize" => manager.unset_minimized(handle),
                    _ => manager.activate(handle, &self.seat),
                }
            }
            "fullscreen" => self.toplevel.set_fullscreen(None),
            "unfullscreen" => self.toplevel.unset_fullscreen(),
            "activate-token" => {
                if self.token.is_some() {
                    return Err("activation token already pending".into());
                }
                let token = self
                    .activation
                    .as_ref()
                    .ok_or("missing xdg_activation_v1")?
                    .get_activation_token(qh, ());
                token.set_app_id(self.app_id.clone());
                token.set_surface(&self.surface);
                if self.evidence.focused {
                    token.set_serial(self.serial, &self.seat);
                }
                token.commit();
                self.token = Some(token);
            }
            "destroy" | "quit" => {
                if let Some(constraint) = self.constraint.take() {
                    constraint.destroy();
                }
                if let Some(token) = self.token.take() {
                    token.destroy();
                }
                self.toplevel.destroy();
                self.shell_surface.destroy();
                self.surface.destroy();
                self.quitting = true;
            }
            _ => return Err(format!("unsupported command: {command}")),
        }
        emit("submitted", json!({"command":command}));
        Ok(())
    }

    fn draw(&mut self, qh: &QueueHandle<Self>) -> Result<(), Box<dyn std::error::Error>> {
        let size = self
            .width
            .checked_mul(self.height)
            .and_then(|n| n.checked_mul(4))
            .ok_or("buffer size overflow")?;
        let mut file = tempfile::tempfile()?;
        let pixel = [0x60, 0xa0, 0x30, 0xff];
        let pixels = pixel.repeat((size / 4) as usize);
        file.write_all(&pixels)?;
        let pool = self.shm.create_pool(file.as_fd(), size, qh, ());
        let buffer = pool.create_buffer(
            0,
            self.width,
            self.height,
            self.width * 4,
            wl_shm::Format::Argb8888,
            qh,
            (),
        );
        pool.destroy();
        if let Some(old) = self.buffer.replace(buffer.clone()) {
            old.destroy();
        }
        self.surface.attach(Some(&buffer), 0, 0);
        self.surface.damage(0, 0, self.width, self.height);
        self.surface.commit();
        Ok(())
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let seconds: u64 = std::env::args()
        .nth(1)
        .ok_or("usage: vram-retest-client TIMEOUT_SECONDS")?
        .parse()?;
    if seconds == 0 || seconds > 3600 {
        return Err("timeout must be 1..3600 seconds".into());
    }
    let deadline = Instant::now() + Duration::from_secs(seconds);
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(seconds));
        emit("error", json!({"message":"client deadline expired"}));
        std::process::exit(1);
    });
    let conn = Connection::connect_to_env()?;
    let (globals, mut queue) = registry_queue_init::<State>(&conn)?;
    let qh = queue.handle();
    let compositor: wl_compositor::WlCompositor = globals.bind(&qh, 1..=4, ())?;
    let shell: xdg_wm_base::XdgWmBase = globals.bind(&qh, 1..=1, ())?;
    let seat: wl_seat::WlSeat = globals.bind(&qh, 1..=5, ())?;
    let pointer = seat.get_pointer(&qh, ());
    let shape_manager: Option<wp_cursor_shape_manager_v1::WpCursorShapeManagerV1> =
        globals.bind(&qh, 1..=1, ()).ok();
    let surface = compositor.create_surface(&qh, ());
    let shell_surface = shell.get_xdg_surface(&surface, &qh, ());
    let toplevel = shell_surface.get_toplevel(&qh, ());
    let _foreign: Option<ext_foreign_toplevel_list_v1::ExtForeignToplevelListV1> =
        globals.bind(&qh, 1..=1, ()).ok();
    let app_id = format!("vram-retest-{}", std::process::id());
    toplevel.set_app_id(app_id.clone());
    toplevel.set_title(app_id.clone());
    let mut state = State {
        evidence: Evidence::default(),
        surface,
        shell_surface,
        toplevel,
        buffer: None,
        shm: globals.bind(&qh, 1..=1, ())?,
        pointer: pointer.clone(),
        seat,
        constraints: globals.bind(&qh, 1..=1, ()).ok(),
        constraint: None,
        shapes: shape_manager.map(|m| m.get_pointer(&pointer, &qh, ())),
        activation: globals.bind(&qh, 1..=1, ()).ok(),
        token: None,
        serial: 0,
        width: 320,
        height: 240,
        running: true,
        quitting: false,
        app_id,
        info: globals.bind(&qh, 2..=3, ()).ok(),
        management: globals.bind(&qh, 3..=4, ()).ok(),
        managed: None,
        capabilities: Vec::new(),
    };
    state.surface.commit();
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        for line in io::stdin().lock().lines() {
            match line {
                Ok(line) => {
                    if sender.send(line).is_err() {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    });
    while state.running {
        if Instant::now() >= deadline {
            return Err("client deadline expired before destroy/quit".into());
        }
        queue.dispatch_pending(&mut state)?;
        loop {
            match receiver.try_recv() {
                Ok(line) => {
                    let request: Value = serde_json::from_str(&line)?;
                    let command = request
                        .get("command")
                        .and_then(Value::as_str)
                        .ok_or("expected JSON command string")?;
                    state.command(command, &qh)?;
                    conn.display().sync(&qh, command.to_owned());
                    if state.quitting {
                        break;
                    }
                }
                Err(mpsc::TryRecvError::Empty) => break,
                Err(mpsc::TryRecvError::Disconnected) if !state.quitting => {
                    return Err("stdin closed before destroy/quit".into());
                }
                Err(_) => break,
            }
        }
        conn.flush()?;
        if !state.running {
            break;
        }
        if let Some(guard) = queue.prepare_read() {
            let mut fd = libc::pollfd {
                fd: conn.as_fd().as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            };
            let result = unsafe { libc::poll(&mut fd, 1, 20) };
            if result < 0 {
                return Err(io::Error::last_os_error().into());
            }
            if result > 0 {
                guard.read()?;
            }
        }
    }
    emit(
        "complete",
        json!({"coverage_verdict":"requires compositor and harness evidence"}),
    );
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        emit("error", json!({"message":error.to_string()}));
        std::process::exit(1);
    }
}

impl Dispatch<wl_registry::WlRegistry, GlobalListContents> for State {
    fn event(
        _: &mut Self,
        _: &wl_registry::WlRegistry,
        _: wl_registry::Event,
        _: &GlobalListContents,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}
impl Dispatch<wl_callback::WlCallback, String> for State {
    fn event(
        state: &mut Self,
        _: &wl_callback::WlCallback,
        _: wl_callback::Event,
        command: &String,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        emit("acknowledged", json!({"command":command}));
        if command == "quit" || command == "destroy" {
            state.running = false;
        }
    }
}
impl Dispatch<xdg_wm_base::XdgWmBase, ()> for State {
    fn event(
        _: &mut Self,
        proxy: &xdg_wm_base::XdgWmBase,
        event: xdg_wm_base::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let xdg_wm_base::Event::Ping { serial } = event {
            proxy.pong(serial);
        }
    }
}
impl Dispatch<xdg_surface::XdgSurface, ()> for State {
    fn event(
        state: &mut Self,
        proxy: &xdg_surface::XdgSurface,
        event: xdg_surface::Event,
        _: &(),
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        if let xdg_surface::Event::Configure { serial } = event {
            proxy.ack_configure(serial);
            if let Err(error) = state.draw(qh) {
                emit("error", json!({"message":error.to_string()}));
                std::process::exit(1);
            }
            let first = !state.evidence.configured;
            state.evidence.configured = true;
            emit(
                if first { "ready" } else { "configured" },
                json!({"app_id":state.app_id,"width":state.width,"height":state.height,"constraints":state.constraints.is_some(),"cursor_shapes":state.shapes.is_some(),"activation":state.activation.is_some()}),
            );
        }
    }
}
impl Dispatch<xdg_toplevel::XdgToplevel, ()> for State {
    fn event(
        state: &mut Self,
        _: &xdg_toplevel::XdgToplevel,
        event: xdg_toplevel::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match event {
            xdg_toplevel::Event::Configure {
                width,
                height,
                states,
            } => {
                if width > 0 {
                    state.width = width;
                }
                if height > 0 {
                    state.height = height;
                }
                emit(
                    "toplevel-state",
                    json!({"states":states.as_chunks::<4>().0.iter().map(|b| u32::from_ne_bytes(*b)).collect::<Vec<_>>()}),
                );
            }
            xdg_toplevel::Event::Close => {
                emit("error", json!({"message":"unexpected compositor close"}));
                std::process::exit(1);
            }
            _ => {}
        }
    }
}
impl Dispatch<wl_pointer::WlPointer, ()> for State {
    fn event(
        state: &mut Self,
        _: &wl_pointer::WlPointer,
        event: wl_pointer::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match event {
            wl_pointer::Event::Enter {
                serial,
                surface_x,
                surface_y,
                ..
            } => {
                state.evidence.focused = true;
                state.serial = serial;
                emit("pointer-enter", json!({"x":surface_x,"y":surface_y}));
            }
            wl_pointer::Event::Leave { .. } => {
                state.evidence.focused = false;
                emit("pointer-leave", json!({}));
            }
            wl_pointer::Event::Motion {
                surface_x,
                surface_y,
                ..
            } => emit("pointer-motion", json!({"x":surface_x,"y":surface_y})),
            _ => {}
        }
    }
}
impl Dispatch<zwp_locked_pointer_v1::ZwpLockedPointerV1, ()> for State {
    fn event(
        state: &mut Self,
        _: &zwp_locked_pointer_v1::ZwpLockedPointerV1,
        event: zwp_locked_pointer_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match event {
            zwp_locked_pointer_v1::Event::Locked => {
                state.evidence.locked = true;
                emit("locked", json!({}));
            }
            zwp_locked_pointer_v1::Event::Unlocked => {
                state.evidence.locked = false;
                emit("unlocked", json!({}));
            }
            _ => {}
        }
    }
}
impl Dispatch<xdg_activation_token_v1::XdgActivationTokenV1, ()> for State {
    fn event(
        state: &mut Self,
        _: &xdg_activation_token_v1::XdgActivationTokenV1,
        event: xdg_activation_token_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let xdg_activation_token_v1::Event::Done { .. } = event {
            emit("activation-token-done", json!({}));
            if let Some(token) = state.token.take() {
                token.destroy();
            }
        }
    }
}
delegate_noop!(State: ignore wl_compositor::WlCompositor);
delegate_noop!(State: ignore wl_surface::WlSurface);
delegate_noop!(State: ignore wl_shm::WlShm);
delegate_noop!(State: ignore wl_shm_pool::WlShmPool);
delegate_noop!(State: ignore wl_buffer::WlBuffer);
delegate_noop!(State: ignore wl_seat::WlSeat);
delegate_noop!(State: ignore zwp_pointer_constraints_v1::ZwpPointerConstraintsV1);
delegate_noop!(State: ignore wp_cursor_shape_manager_v1::WpCursorShapeManagerV1);
delegate_noop!(State: ignore wp_cursor_shape_device_v1::WpCursorShapeDeviceV1);
delegate_noop!(State: ignore xdg_activation_v1::XdgActivationV1);
delegate_noop!(State: ignore zcosmic_toplevel_info_v1::ZcosmicToplevelInfoV1);

impl Dispatch<ext_foreign_toplevel_list_v1::ExtForeignToplevelListV1, ()> for State {
    fn event(
        _: &mut Self,
        _: &ext_foreign_toplevel_list_v1::ExtForeignToplevelListV1,
        _: ext_foreign_toplevel_list_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
    wayland_client::event_created_child!(State, ext_foreign_toplevel_list_v1::ExtForeignToplevelListV1, [0 => (ext_foreign_toplevel_handle_v1::ExtForeignToplevelHandleV1, ())]);
}
impl Dispatch<ext_foreign_toplevel_handle_v1::ExtForeignToplevelHandleV1, ()> for State {
    fn event(
        state: &mut Self,
        proxy: &ext_foreign_toplevel_handle_v1::ExtForeignToplevelHandleV1,
        event: ext_foreign_toplevel_handle_v1::Event,
        _: &(),
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        match event {
            ext_foreign_toplevel_handle_v1::Event::AppId { app_id }
                if app_id == state.app_id && state.managed.is_none() =>
            {
                if let Some(info) = &state.info {
                    state.managed = Some(info.get_cosmic_toplevel(proxy, qh, ()));
                    emit("managed", json!({"app_id":state.app_id}));
                }
            }
            ext_foreign_toplevel_handle_v1::Event::Closed => proxy.destroy(),
            _ => {}
        }
    }
}
impl Dispatch<zcosmic_toplevel_handle_v1::ZcosmicToplevelHandleV1, ()> for State {
    fn event(
        _: &mut Self,
        _: &zcosmic_toplevel_handle_v1::ZcosmicToplevelHandleV1,
        event: zcosmic_toplevel_handle_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let zcosmic_toplevel_handle_v1::Event::State { state } = event {
            emit(
                "managed-state",
                json!({"states":state.as_chunks::<4>().0.iter().map(|b| u32::from_ne_bytes(*b)).collect::<Vec<_>>()}),
            );
        }
    }
}
impl Dispatch<zcosmic_toplevel_manager_v1::ZcosmicToplevelManagerV1, ()> for State {
    fn event(
        state: &mut Self,
        _: &zcosmic_toplevel_manager_v1::ZcosmicToplevelManagerV1,
        event: zcosmic_toplevel_manager_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let zcosmic_toplevel_manager_v1::Event::Capabilities { capabilities } = event {
            state.capabilities = capabilities
                .as_chunks::<4>()
                .0
                .iter()
                .map(|b| u32::from_ne_bytes(*b))
                .collect();
            emit(
                "management-capabilities",
                json!({"capabilities":state.capabilities}),
            );
        }
    }
}
