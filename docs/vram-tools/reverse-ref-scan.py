#!/usr/bin/env python3
"""Reverse-reference search for the cosmic-comp VRAM-leak hunt.

Given a core dump (`gcore`) and the SIGUSR1 census `main ptrs` line, find every
strong/weak holder of each leaked dmabuf's `Arc<DmabufInternal>`. This is the
"ENDGAME" tool the findings log calls for: it names (or rules out) the retainer
of a leaked render-target dmabuf that static analysis + heaptrack cannot see.

How it works
------------
The census `debug_dmabuf_cache_ptrs` prints `id@WxH=0x<dataptr>` where
`dataptr = Arc::as_ptr(&dmabuf)` (the `DmabufInternal` *data* pointer). For an
`Arc<T>` the `ArcInner` base = `dataptr - 0x10` (strong + weak `AtomicUsize`).
  * A strong `Arc` and a `Weak` BOTH store the ArcInner **base**  -> search `dataptr-0x10`.
  * `Arc::into_raw` / `Arc::as_ptr` store the **data** ptr           -> search `dataptr`.
Read the ArcInner header (strong, weak counts) at the base to ground each result:
`strong=N` real owners, `weak=M` -> `M-1` explicit `Weak`s (one implicit while strong>0).

A leaked dmabuf with `strong=1` whose ONLY base-hit is the renderer's weak cache
key (and no other base/data hit anywhere) has NO live owner -> an escaped/forgotten
`Dmabuf` clone (drop never runs), not a retained container.

Usage
-----
  python3 reverse-ref-scan.py /tmp/cc-core.<pid> '<the main ptrs census payload>'

where the payload is everything after `main ptrs DrmNode{...}:` e.g.
  '52@3840x2160=0x55e9... 73@3840x2160=0x55e9... ...'
(quote it). Pass it via a file with --file PATH if it's long.

Validate the method on a known-LIVE buffer first: its strong holder should resolve
to a real `Box<Dmabuf>` in a swapchain slot's UserDataMap.
"""
import struct, mmap, sys, re, argparse

PT_LOAD = 1

class Core:
    def __init__(self, path):
        self.f = open(path, "rb")
        self.mm = mmap.mmap(self.f.fileno(), 0, prot=mmap.PROT_READ)
        self.segs = self._phdrs()
    def _phdrs(self):
        mm = self.mm
        e_phoff = struct.unpack_from("<Q", mm, 0x20)[0]
        e_phes  = struct.unpack_from("<H", mm, 0x36)[0]
        e_phn   = struct.unpack_from("<H", mm, 0x38)[0]
        segs = []
        for i in range(e_phn):
            b = e_phoff + i * e_phes
            if struct.unpack_from("<I", mm, b)[0] != PT_LOAD:
                continue
            off = struct.unpack_from("<Q", mm, b + 0x08)[0]
            va  = struct.unpack_from("<Q", mm, b + 0x10)[0]
            fsz = struct.unpack_from("<Q", mm, b + 0x20)[0]
            if fsz:
                segs.append((va, off, fsz))
        segs.sort()
        return segs
    def u64(self, vaddr):
        for va, off, sz in self.segs:
            if va <= vaddr < va + sz:
                o = off + (vaddr - va)
                return struct.unpack("<Q", self.mm[o:o + 8])[0]
        return None
    def off_to_vaddr(self, off):
        for va, o, sz in self.segs:
            if o <= off < o + sz:
                return va + (off - o)
        return None
    def find_all(self, val):
        n = struct.pack("<Q", val); out = []; s = 0
        while True:
            i = self.mm.find(n, s)
            if i < 0:
                break
            out.append(self.off_to_vaddr(i) or f"foff:{i}")
            s = i + 1
        return out

def parse_ptrs(payload):
    # tokens like  52@3840x2160=0x55e96bc6e370
    out = {}
    for m in re.finditer(r"(\d+)@(\d+)x(\d+)=0x([0-9a-fA-F]+)", payload):
        did = int(m.group(1)); data = int(m.group(4), 16)
        out[did] = (data, f"{m.group(2)}x{m.group(3)}")
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("core")
    ap.add_argument("payload", nargs="?", default="")
    ap.add_argument("--file", help="read the census payload from a file")
    args = ap.parse_args()
    payload = open(args.file).read() if args.file else args.payload
    ptrs = parse_ptrs(payload)
    if not ptrs:
        sys.exit("no 'id@WxH=0xptr' tokens found in payload")
    c = Core(args.core)
    print(f"core PT_LOAD segs={len(c.segs)}  dmabufs={len(ptrs)}\n")
    print(f"{'id':>4} {'size':>11} {'strong':>6} {'weak':>4}  holders (base=Arc/Weak, data=into_raw/as_ptr)")
    for did, (data, size) in sorted(ptrs.items()):
        base = data - 0x10
        strong = c.u64(base); weak = c.u64(base + 8)
        bh = c.find_all(base)
        dh = c.find_all(data)
        sc = f"{strong}" if strong is not None else "?"
        wc = f"{weak}" if weak is not None else "?"
        fb = " ".join(f"0x{x:x}" if isinstance(x, int) else x for x in bh)
        fd = " ".join(f"0x{x:x}" if isinstance(x, int) else x for x in dh)
        print(f"{did:>4} {size:>11} {sc:>6} {wc:>4}  base#{len(bh)}[{fb}]")
        if dh:
            print(f"{'':>30}  data#{len(dh)}[{fd}]")

if __name__ == "__main__":
    main()
