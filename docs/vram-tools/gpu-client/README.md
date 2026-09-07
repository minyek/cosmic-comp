# Owned DMA-BUF import client

Build with `cargo build --locked --manifest-path Cargo.toml`. The binary is
`target/debug/vram-retest-gpu-client`.

```sh
target/debug/vram-retest-gpu-client --timeout 180 \
  --render-node /dev/dri/renderD128 --render-node /dev/dri/renderD129
```

Every node is opened explicitly and must have a sampling tranche in the
compositor's DMA-BUF feedback. The helper allocates a 64×64 GBM buffer with an
advertised format/modifier, exports every plane, and requests an asynchronous
`wl_buffer` import. It requires linux-dmabuf version 6 and sets each buffer's
sampling device to the requested render node's `dev_t`. This selects the server's
GPU explicitly; allocating on a GPU alone does not establish where the compositor
imports the buffer.

An advertised implicit modifier uses GBM's implicit allocation API and preserves
the implicit modifier on the protocol request. Explicit modifier requests must
match the resulting allocation.

All buffers belong to one Wayland connection. The helper retains their GBM
allocations and devices until shutdown. It creates no surface and samples no
pixels: the server's successful import path registers the client on the GPU.
The harness must also observe compositor GPU-client counters before and after
disconnect to establish teardown behavior.

Standard output contains JSON lines:

- `imported`: emitted only after the server's `created` event. `detail` contains
  `render_node` (canonical path), `dev_id`, `format`, `modifier`, and `planes`.
- `ready`: all requested distinct nodes imported successfully; `detail.imports`
  contains each import's evidence.
- `acknowledged`: sent after a `{"command":"destroy"}` stdin line destroys all
  buffers and completes a server round trip. The helper then exits successfully,
  disconnecting its Wayland client.
- `error`: failure, followed by a nonzero exit. There is no partial `ready`.

`--timeout` is mandatory and accepts 1–3600 seconds. A watchdog bounds registry,
feedback, GBM, import, and shutdown waits. Closing stdin without `destroy` fails.
Unavailable nodes, absent protocol version 6, unadvertised target devices, and
rejected imports fail explicitly. A multi-GPU run requires multiple real render
nodes; repeating one node cannot establish multi-GPU cleanup.

```sh
cargo test --locked --offline
cargo clippy --locked --offline --all-targets -- -D warnings
```

Tests cover feedback parsing, device-specific sampling selection, malformed
indices, explicit deadlines, and failed startup. They do not establish successful
hardware import; that requires the desktop user's declared runtime suite.
