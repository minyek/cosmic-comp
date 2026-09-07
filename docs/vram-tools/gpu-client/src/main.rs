use gbm::{BufferObject, BufferObjectFlags, Device, Modifier};
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs::{File, OpenOptions},
    io::{self, BufRead, Read, Write},
    os::{
        fd::{AsFd, AsRawFd},
        unix::fs::{FileTypeExt, MetadataExt},
    },
    path::PathBuf,
    sync::mpsc,
    time::Duration,
};
use vram_retest_gpu_client::{
    Format, Tranche, decode_formats, decode_indices, export_format, sampling_candidates,
};
use wayland_client::{
    Connection, Dispatch, QueueHandle, WEnum, delegate_noop, event_created_child,
    globals::{GlobalListContents, registry_queue_init},
    protocol::{wl_buffer, wl_registry},
};
use wayland_protocols::wp::linux_dmabuf::zv1::client::{
    zwp_linux_buffer_params_v1::{self, ZwpLinuxBufferParamsV1},
    zwp_linux_dmabuf_feedback_v1::{self, ZwpLinuxDmabufFeedbackV1},
    zwp_linux_dmabuf_v1::ZwpLinuxDmabufV1,
};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn emit(event: &str, detail: Value) {
    println!(
        "{}",
        json!({"event": event, "pid": std::process::id(), "detail": detail})
    );
    let _ = io::stdout().flush();
}

struct Options {
    nodes: Vec<PathBuf>,
    timeout: u64,
}

fn options() -> Result<Options> {
    let mut args = std::env::args().skip(1);
    let mut nodes = Vec::new();
    let mut timeout = None;
    while let Some(option) = args.next() {
        let value = args.next().ok_or("every option requires a value")?;
        match option.as_str() {
            "--render-node" => nodes.push(PathBuf::from(value)),
            "--timeout" if timeout.is_none() => timeout = Some(value.parse::<u64>()?),
            _ => return Err(format!("unsupported or duplicate option: {option}").into()),
        }
    }
    let timeout = timeout.ok_or("--timeout is required")?;
    if nodes.is_empty() || !(1..=3600).contains(&timeout) {
        return Err("require --render-node and --timeout in 1..3600 seconds".into());
    }
    Ok(Options { nodes, timeout })
}

#[derive(Default)]
struct Feedback {
    formats: Vec<Format>,
    tranches: Vec<Tranche>,
    device: Option<u64>,
    sampling: bool,
    indices: Vec<usize>,
    done: bool,
}

impl Feedback {
    fn update(&mut self, event: zwp_linux_dmabuf_feedback_v1::Event) -> Result<()> {
        use zwp_linux_dmabuf_feedback_v1::Event;
        match event {
            Event::FormatTable { fd, size } => {
                if size == 0 || u64::from(size) > (u64::from(u16::MAX) + 1) * 16 {
                    return Err("invalid format table size".into());
                }
                let mut bytes = vec![0; size as usize];
                File::from(fd).read_exact(&mut bytes)?;
                self.formats = decode_formats(&bytes)?;
            }
            Event::TrancheTargetDevice { device } => {
                self.device = Some(libc::dev_t::from_ne_bytes(
                    device
                        .try_into()
                        .map_err(|_| "invalid feedback device width")?,
                ));
            }
            Event::TrancheFlags {
                flags: WEnum::Value(flags),
            } => {
                self.sampling =
                    flags.contains(zwp_linux_dmabuf_feedback_v1::TrancheFlags::Sampling);
            }
            Event::TrancheFlags { .. } => return Err("unknown feedback tranche flags".into()),
            Event::TrancheFormats { indices } => self.indices.extend(decode_indices(&indices)?),
            Event::TrancheDone => {
                self.tranches.push(Tranche {
                    device: self.device.take().ok_or("tranche without target device")?,
                    sampling: std::mem::take(&mut self.sampling),
                    indices: std::mem::take(&mut self.indices),
                });
            }
            Event::Done => self.done = true,
            _ => {}
        }
        Ok(())
    }
}

#[derive(Default)]
struct State {
    feedback: Feedback,
    created: Option<wl_buffer::WlBuffer>,
    failed: bool,
    error: Option<String>,
}

struct Import {
    buffer: wl_buffer::WlBuffer,
    _allocation: BufferObject<()>,
    _device: Device<File>,
    evidence: Value,
}

fn allocate(device: &Device<File>, candidates: &[Format]) -> Result<(BufferObject<()>, Format)> {
    let mut failures = Vec::new();
    for candidate in candidates {
        let Ok(format) = gbm::Format::try_from(candidate.code) else {
            continue;
        };
        let modifier = Modifier::from(candidate.modifier);
        let allocation = if modifier == Modifier::Invalid {
            device.create_buffer_object(64, 64, format, BufferObjectFlags::RENDERING)
        } else {
            device.create_buffer_object_with_modifiers2(
                64,
                64,
                format,
                std::iter::once(modifier),
                BufferObjectFlags::RENDERING,
            )
        };
        match allocation {
            Ok(buffer) => {
                let actual = Format {
                    code: buffer.format() as u32,
                    modifier: buffer.modifier().into(),
                };
                if let (Ok(wire_format), true) = (
                    export_format(*candidate, actual),
                    (1..=4).contains(&buffer.plane_count()),
                ) {
                    return Ok((buffer, wire_format));
                }
                failures.push(format!("unadvertised allocation result {actual:?}"));
            }
            Err(error) => failures.push(error.to_string()),
        }
    }
    Err(format!(
        "GBM allocation failed for advertised candidates: {}",
        failures.join("; ")
    )
    .into())
}

fn open_node(path: PathBuf) -> Result<(PathBuf, u64, Device<File>)> {
    let path = path.canonicalize()?;
    let file = OpenOptions::new().read(true).write(true).open(&path)?;
    let metadata = file.metadata()?;
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or("invalid render node name")?;
    if !metadata.file_type().is_char_device()
        || !name.starts_with("renderD")
        || !name[7..].bytes().all(|byte| byte.is_ascii_digit())
    {
        return Err("--render-node must name a DRM render node".into());
    }
    Ok((path, metadata.rdev(), Device::new(file)?))
}

fn run() -> Result<()> {
    let options = options()?;
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(options.timeout));
        emit(
            "error",
            json!({"message": "GPU client deadline expired before destroy"}),
        );
        std::process::exit(1);
    });
    let connection = Connection::connect_to_env()?;
    let (globals, mut queue) = registry_queue_init::<State>(&connection)?;
    let handle = queue.handle();
    let dmabuf: ZwpLinuxDmabufV1 = globals.bind(&handle, 6..=6, ())?;
    let feedback = dmabuf.get_default_feedback(&handle, ());
    let mut state = State::default();
    while !state.feedback.done {
        queue.blocking_dispatch(&mut state)?;
        if let Some(error) = state.error.take() {
            return Err(error.into());
        }
    }
    feedback.destroy();
    let mut seen = BTreeSet::new();
    let mut imports = Vec::new();
    for path in options.nodes {
        let (path, dev_id, device) = open_node(path)?;
        if !seen.insert(dev_id) {
            return Err("duplicate physical render node".into());
        }
        let candidates =
            sampling_candidates(dev_id, &state.feedback.formats, &state.feedback.tranches)?;
        let (allocation, wire_format) = allocate(&device, &candidates)?;
        let modifier = wire_format.modifier;
        let params = dmabuf.create_params(&handle, ());
        params.set_sampling_device((dev_id as libc::dev_t).to_ne_bytes().to_vec());
        for plane in 0..allocation.plane_count() {
            let fd = allocation.fd_for_plane(plane as i32)?;
            params.add(
                fd.as_fd(),
                plane,
                allocation.offset(plane as i32),
                allocation.stride_for_plane(plane as i32),
                (modifier >> 32) as u32,
                modifier as u32,
            );
        }
        params.create(
            allocation.width() as i32,
            allocation.height() as i32,
            allocation.format() as u32,
            zwp_linux_buffer_params_v1::Flags::empty(),
        );
        while state.created.is_none() && !state.failed {
            queue.blocking_dispatch(&mut state)?;
            if let Some(error) = state.error.take() {
                return Err(error.into());
            }
        }
        params.destroy();
        let buffer = state
            .created
            .take()
            .ok_or_else(|| format!("compositor rejected dmabuf for {}", path.display()))?;
        let evidence = json!({"render_node": path, "dev_id": dev_id, "format": allocation.format() as u32, "modifier": modifier, "planes": allocation.plane_count()});
        emit("imported", evidence.clone());
        imports.push(Import {
            buffer,
            _allocation: allocation,
            _device: device,
            evidence,
        });
    }
    emit(
        "ready",
        json!({"imports": imports.iter().map(|item| &item.evidence).collect::<Vec<_>>() }),
    );
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        for line in io::stdin().lock().lines() {
            if sender.send(line).is_err() {
                break;
            }
        }
    });
    loop {
        queue.dispatch_pending(&mut state)?;
        match receiver.try_recv() {
            Ok(line) => {
                let command: Value = serde_json::from_str(&line?)?;
                if command.get("command").and_then(Value::as_str) != Some("destroy") {
                    return Err("expected JSON destroy command".into());
                }
                for imported in &imports {
                    imported.buffer.destroy();
                }
                queue.roundtrip(&mut state)?;
                emit("acknowledged", json!({"command": "destroy"}));
                return Ok(());
            }
            Err(mpsc::TryRecvError::Disconnected) => {
                return Err("stdin closed before destroy".into());
            }
            Err(mpsc::TryRecvError::Empty) => {}
        }
        connection.flush()?;
        if let Some(guard) = queue.prepare_read() {
            let mut descriptor = libc::pollfd {
                fd: connection.as_fd().as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            };
            // SAFETY: descriptor is one initialized pollfd; connection owns its fd.
            let result = unsafe { libc::poll(&mut descriptor, 1, 20) };
            if result < 0 {
                return Err(io::Error::last_os_error().into());
            }
            if result > 0 {
                guard.read()?;
            }
        }
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

impl Dispatch<ZwpLinuxDmabufFeedbackV1, ()> for State {
    fn event(
        state: &mut Self,
        _: &ZwpLinuxDmabufFeedbackV1,
        event: zwp_linux_dmabuf_feedback_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let Err(error) = state.feedback.update(event) {
            state.error = Some(error.to_string());
        }
    }
}

impl Dispatch<ZwpLinuxBufferParamsV1, ()> for State {
    fn event(
        state: &mut Self,
        _: &ZwpLinuxBufferParamsV1,
        event: zwp_linux_buffer_params_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match event {
            zwp_linux_buffer_params_v1::Event::Created { buffer } => state.created = Some(buffer),
            zwp_linux_buffer_params_v1::Event::Failed => state.failed = true,
            _ => {}
        }
    }
    event_created_child!(State, ZwpLinuxBufferParamsV1, [0 => (wl_buffer::WlBuffer, ())]);
}

delegate_noop!(State: ignore ZwpLinuxDmabufV1);
delegate_noop!(State: ignore wl_buffer::WlBuffer);

fn main() {
    if let Err(error) = run() {
        emit("error", json!({"message": error.to_string()}));
        std::process::exit(1);
    }
}
