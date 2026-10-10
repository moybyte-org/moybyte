# The native store — sprint 1b's design (2026-10)

**What this is.** The design sprint 1b of `docs/native_kernel_2026-09.md` (#224)
builds, written before any of it is: the file system the native store stands
on, what crosses and in which slices, what the open store issues need from it,
its memory, and how it runs in the browser and on the Zero. The kernel's
language is C (owner, 2026-10-06), and 1b follows sprint 2, whose handle tables
it takes (`docs/kernel_spine_2026-10.md` §2). Where the sprint stands, and every
measurement, are #224's.

**The carve it builds on** (dev `5c0e19e`, `c2903da`, `32ece56`):
`runtime/moy_catalogue.py` is the store's interface, carts by handle;
`runtime/moy_index.py` is the handle table and `native/moy_index/` its C twin;
`runtime/project_store.py`, `runtime/boot_carts.py` and `runtime/moyimg.py` are
split out. The scan reads each cart folder from one listing, from inside it
(`e253dc2`); the boot's seed runs after it and decides from its entries'
versions (slice 0), and the Zero's answers "is it there" from one listing
(`aa9ee94`).
`tests/test_catalogue.py`, `tests/test_moy_index.py`,
`tests/test_store_on_vfs.py` and the store trace in
`tests/test_semantic_traces.py` pin it; the crossing swaps implementations
under them.

## 1. The file system under the store

**Decision: the store speaks the file-system libraries the image already links
— oofatfs on a card, littlefs2 on internal flash, POSIX in the browser and on
the host — through one seam, `moy_vol` (§3). A volume has exactly one instance
of its library. The store borrows the VM's instance for the length of a call
until a volume is the kernel's: the card's in 1b's last slice (§10), the
internal flash's in sprint 3. The store does not change when it is.**

- **(a), at the library level.** Not the VFS's object API (`mp_vfs_open`,
  stream objects, `ilistdir` tuples), which allocates on the gc heap and raises
  through nlr: the store finds the mount that owns a path in MicroPython's mount
  table and calls the library on the instance inside it, an `fs_user_mount_t`'s
  `FATFS` or a `VfsLfs2`'s `lfs2_t`. Those are the calls it makes on the
  kernel's instance later. The pointer never outlives the call (§4.3 of the
  kernel doc).
- **(c), the kernel's instance of the same library**, never a second copy. For
  FAT: an `fs_user_mount_t` the kernel allocates in PSRAM over a native block
  device (stm32's `sdcard_init_vfs` shape), which Python mounts at `/sd`, so
  `VfsFat` and the store share one `FATFS`. SOURCE: a scratch probe (2026-10-06)
  linked oofatfs with MicroPython's FAT diskio and block-device glue, every VM
  entry stubbed to abort; on a block device flagged `MP_BLOCKDEV_FLAG_NATIVE`
  with no ioctl method and no `sync` it ran mount, mkdir, list, chdir, open,
  read, write, close and rename with no VM call. Only `f_mkfs` asks the VM, and
  the console never formats a card. For littlefs: the kernel's `lfs2_t` with C
  callbacks over `esp_partition`. `VfsLfs2` cannot wrap it (its callbacks call
  the block device's methods, and its `cur_dir` is a gc buffer), so Python
  reaches a kernel-owned littlefs volume through a VFS type of the kernel's.
- **(b), ESP-IDF's VFS: rejected.** IDF's fatfs and oofatfs both define a
  global `f_open`, with different signatures, so one image cannot link both;
  IDF builds fatfs with `FF_FS_RPATH 0`, which removes the relative opens the
  scan stands on; IDF ships no littlefs. (b) means dropping `VfsFat`, porting
  `VfsPosix` to a VFS with no working directory, and a second littlefs to read
  the P4s' and the Zero's stores.

| target | volumes | borrowed | owned below the VM |
|---|---|---|---|
| T-Deck | card, FAT/exFAT, `moy_sd` attached to the panel's SPI2 | `VfsFat` over `_NativeSDBlockDev` (Python, with its sector cache) | slice 8: the kernel's `FATFS` over a C block device, `moy_sd`'s attach and the cache in C |
| Guition S3 | card on SPI3; with no card, littlefs `/moy` | `VfsFat` over `machine.SDCard`; `VfsLfs2` | slice 8: the kernel's card driver on SPI3, initialised once and never torn down, with the cache this board never had; sprint 3: the kernel's `lfs2_t` (`native/moy_store/moy_kvfs.c`, landed 2026-10-08) |
| Waveshare P4, Guition P4 | card on SDMMC slot 0; internal littlefs `/moy` | as the Guition S3 | slice 8: the card, with the cache; the internal littlefs when a P4 stops its VM (§10 question 5 of the kernel doc) |
| Zero | internal littlefs | `VfsLfs2` | the kernel's `lfs2_t` (`native/moy_store/moy_kvfs.c`, 2026-10-08) |
| browser · host | MEMFS under `VfsPosix` · the disk | POSIX on the same paths | — |

**The card volume is 1b's last slice** (owner, 2026-10-06), taken from
sprint 3's SD gate. The store's code and both its interfaces (`moy_catalogue`
above, `moy_vol` below) are the same over a borrowed and an owned instance, so
slices 0 to 7 need nothing from it; the slice changes who allocates the card's
instance and what its block device is. It puts the T-Deck's read cache (dev
`638661e`, Python today) in C under every console board's card, so the Guition
S3 and the P4s read their cards through it too. It also meets, ahead of sprint
3, the constraint that the owned card volume lands before an S3 stops its VM
with a card mounted (`machine.SDCard`'s finaliser frees its SPI host at the
sweep). The internal flash volumes stay sprint 3's, with the mounts in the stop
inventory (§4.4 of the kernel doc), and the owned littlefs volume lands before
sprint 4 runs a cart VM-free off the Guition S3's internal store.

**The T-Deck's shared host** (`.claude/rules/boards.md`). Until slice 8 every
store call that reaches the card runs inside the Python storage gate
(`with_sd_live`, the console's `_with_sd`), as the Python store's do. The owned
volume keeps `moy_sd`'s lifecycle: attach once after the panel, no teardown
between operations, `deinit` only after a failed attach. Whether the flush
fence becomes per card transaction (the C block device against `moy_flush`'s
`sync()`) or stays per session is slice 8's, under
`native/moy_flush/moy_flush.c`'s header.

## 2. What crosses, and where it lands

| component | Python twin | C |
|---|---|---|
| the volume seam | `runtime/moy_store_base.py`'s directory primitives | `native/moy_store/moy_vol.h` |
| the card volume (slice 8) | `device/moybyte_sd.py`'s block device and its read cache (T-Deck); `machine.SDCard` under `VfsFat` (Guition S3, the P4s) | `moy_vol`'s owned FAT backend: the kernel's `FATFS` over a C block device with the read cache |
| the crash-safe write | `runtime/moy_fs.py` | `native/moy_store/moy_fs.h` |
| the index | `runtime/moy_index.py` | `native/moy_index/moy_index.h`, over `moy_htab` |
| the catalogue, the covers' facts, create / duplicate / delete | `runtime/moy_carts.py`'s entry, `_each`, `_sheet_icon`, `create` and siblings | `native/moy_store/moy_cat.h` |
| the seed | `runtime/moy_seed.py` | `native/moy_store/moy_seed.h` |
| loading a cart | `moy_carts.load` | `native/moy_store/moy_load.h` |
| the journal | `runtime/moy_journal.py` | `native/moy_store/moy_journal.h` |
| reading manifests and journal lines | `json` | the settings store's scanner, as `native/moy_spine/moy_json.h`: one body for both |

`native/moy_store/` builds three ways, as `native/moy_index/` does: a
MicroPython usermod (boards, browser, unix MicroPython), a host library for
ctypes, and a fuzz driver. Each crossing deletes the twin it replaces; CPython
(the simulator, the tools, the tests) then reaches the C through ctypes.

**What stays Python after 1b, by decision.** The builders (`Project`,
`SpriteSheet`, `TileMap`, the flags table): Python objects for the Editor and
the Python Player. `project_store`'s commit verbs, which ask the block compiler
and Storybook (apps) and now publish and journal through C. The cover cache's
decodes, LRU, jobs and frame budget: presentation memory, crossing with the
layer pool in sprint 3. `runtime/cart_files.py`, rebased onto the C `moy_vol`
and `moy_fs` (it crosses when sprint 4 needs it). The user-files layer stayed
Python here and crossed in sprint 5 (`native/moy_store/moy_ufiles.h`,
`docs/kernel_appabi_2026-10.md` §8 step 7). The
shelf's roster (`runtime/cart_manager.py`). The sync wire and the webhost
(sprint 3). The embedded floor, a no-card board's read-only built-ins (sprint 4
if a VM-free cart needs it). The sibling stores (wifi, achievements, the shared
sheet, the icons), writing through C `moy_fs`; `system.json` is sprint 2's.

## 3. The volume seam

    moy_vol_t *moy_vol_at(const char *path, const char **rest);   // the volume owning path
    int  moy_vol_list(moy_vol_t *, const char *dir, moy_vol_ent_fn, void *);  // name, is_dir, size
    int  moy_vol_enter(moy_vol_t *, const char *dir, moy_vol_here_t *);  // relative names start here
    void moy_vol_leave(moy_vol_t *, moy_vol_here_t *);                   // the cwd as it was
    int  moy_vol_open(moy_vol_t *, const char *name, int mode, moy_vol_file_t *);
    int  moy_vol_read(...), moy_vol_write(...), moy_vol_close(...);
    int  moy_vol_stat(...), moy_vol_mkdir(...), moy_vol_remove(...), moy_vol_rename(...);

- Errors are MicroPython's errno values (FAT through `fresult_to_errno_table`);
  the binding raises `OSError(errno)`. A cart folder that will not read is a
  missing entry, never an error.
- `enter` is the scan fix as a primitive. On FAT it is `f_chdir` on the
  volume's `FATFS`, and `leave` restores every working-directory field
  (`cdir`, and `cdc_*` on exFAT) as one, so Python's cwd is untouched. On
  littlefs and POSIX it is a path prefix.
- `rename` refuses an existing destination on every backend, as FAT does.
- A file's `FIL` or `lfs2_file_t` and its buffers live in the call's arena;
  nothing is open when a store call returns.
- **The borrow's two rules.** A store call runs under `nlr_push`: a sector read
  through the T-Deck's Python block device or `machine.SDCard` can raise, and
  the binding then restores the cwd and frees the call's arena before the
  exception continues. And a `VfsLfs2`'s `lfs2_t` sits at a layout
  `extmod/vfs_lfs.c` keeps private, so every resolve checks that the instance's
  `cfg` points at the mount's own config and refuses loudly otherwise — the
  guard on a MicroPython tag bump. Both leave with the borrow.

## 4. The index

`native/moy_index/` takes its slots from `moy_htab` (spine §2), so its rows
are PSRAM and outlive a VM stop, and a handle the kernel holds stays valid
across one. Its key is the root's id followed by the cart's folder name, not a
path; the ABI, which compares keys bytewise, and its pinned values do not
change. Roots are a small table (`moy_store_root(rid, path)`): the carts store;
on a card with profiles (#131, §8), the family shelf and each kid's carts; a
store on another volume. Only the store composes a cart's path from root and
folder (`runtime/moy_store_base.py`'s `cart_path`, `cart_folder` and
`store_path`; `tests/test_store_paths.py` fails any other module that does),
and a catalogue of a root reconciles that root's rows alone, which replaces
the carve's one-root-at-a-time rule. `handle(path)` stays for callers that
start from a path (the sync wire, Files) and splits it against the roots.

## 5. The crash-safe write

`moy_fs` is `runtime/moy_fs.py`'s publish, identical on the medium: the marker
line, the stamped `.bak`, the file in place, recovery of the prefix case only,
and the backup claimed into the journal by a rename.

    int moy_fs_publish(moy_vol_t *, const char *path, const void *, size_t);
    int moy_fs_read(moy_vol_t *, const char *path, moy_buf_t *);     // with recovery
    int moy_fs_claim(moy_vol_t *, const char *path, const char *dest, moy_stamp_t);

The stamp counts code points as Python's `len(str)` does and takes the crc32
of the UTF-8 bytes, so a file one tier stamps reads whole on every other. The
per-root marker cache is the store's and survives a VM stop.

## 6. The catalogue

    int moy_cat_scan(moy_store_t *, uint8_t rid, moy_cat_entry_fn, void *);
    int moy_cat_row(const moy_store_t *, uint32_t h, const moy_row_t **);   // STALE
    int moy_cat_entry(moy_store_t *, uint32_t h, moy_cat_entry_fn, void *); // read again
    int moy_cat_cover(moy_store_t *, uint32_t h, void *buf, size_t cap, size_t *n);
    int moy_cat_create(...), moy_cat_duplicate(...), moy_cat_delete(moy_store_t *, uint32_t h);

The scan keeps the Python scan's access pattern — one listing of the root, then
per `.moy` folder one `enter`, one listing, and an open of each file the
listing shows — parses the manifest with `moy_json`, normalises it as `_load`
does, lists `scenes/`, and cuts the icon's indices off the top of
`sprites.moygfx`. It calls back with the whole entry; the binding builds the
dict the shelf takes, and parity with the twin is the tests'. Fields the store
does not interpret (`edit`, `palette`, `extensions`, `permissions`,
`writable`, `sources`) cross as JSON text for the binding to parse.

**A row holds what the shelf draws and decides on, nothing else**: folder,
title, author, type, runtime, version, the icon's tile size and indices, the
cover's size, provenance (§8), and a bitmask from the listing (main, sprites,
map, flags, config, scenes, cover, journal; graduated, broken, compiled). The
rest of the entry is handed to the binding and dropped; `moy_cat_entry` reads
the cart again, as `entry(h)` does today.

**`flags` and `cfg` leave the entry for `load`** — 1b's one change to the
interface, made in the carve (§10) under `tests/test_catalogue.py`. Only a
loaded cart reads them (`project_store`'s flags build, the wallpaper after
`rehydrate`), and the flags text is a large share of the shelf's resident
strings in #224's census.

**The covers' facts are the store's.** The listing says whether a cart has
`cover.png` and its size; every store write to a cover and every rescan that
changes one bumps a store-wide cover generation, which the cover cache keys its
invalidation on instead of its own rescan hook. `moy_cat_cover` reads the bytes
and refuses a file over `COVER_MAX_BYTES`. Decodes stay presentation.

## 7. The seed, loading a cart, and the journal

**The seed runs after the scan**, so it reads no manifest: the rows carry each
present built-in's version. It writes each built-in that is absent or older,
keeps `pmem.json` and `config.json` across a re-seed, re-reads only the folders
it wrote, and keeps the retired sweep's generation gate (`retired.ver`). The
roster is the packed blob `tools/gen_device_carts.py` bakes, inflated with the
`uzlib` the image links for `deflate`. Progress calls back once per cart
written, so a boot that writes nothing never repaints.

    int moy_seed_run(moy_store_t *, uint8_t rid, const uint8_t *roster, size_t, moy_seed_fn, void *);

**Loading** reads a cart's entry and every payload's bytes, from one listing,
into one PSRAM arena. In 1b the binding builds `load`'s dict and frees it; in
sprint 4 the cart path keeps the arena and hands moycore its sources, sheet,
map and flags, decoded into runtime tables in C.

    int moy_load(moy_store_t *, uint32_t h, moy_cart_t **);   void moy_cart_free(moy_cart_t *);

**The journal** is `runtime/moy_journal.py`'s on the medium — `journal.jsonl`,
a `cursor.json` that exists only while something is rewound, snapshots claimed
from the spent backup, the 64-entry, 512 KB budget — keyed by handle. An
editor's op batch is JSON text it stores and returns verbatim.

    int moy_journal_append(moy_store_t *, uint32_t h, const char *file, const void *, size_t,
                           int grad, const char *ops, size_t);
    int moy_journal_undo(...), moy_journal_redo(...), moy_journal_can(...), moy_journal_compact(...);
    int moy_journal_list(moy_store_t *, uint32_t h, const char *file, moy_jent_fn, void *);  // #136
    int moy_journal_snap(moy_store_t *, uint32_t h, uint32_t seq, moy_buf_t *);              // #136

## 8. The absorbed issues

Each feature is built once, natively: **built** lands in the slice named;
**shaped** means 1b's data model and interface carry it and the feature comes
later. The manifest is a public format: a spec field goes into moy-spec's
SPEC.md before a store writes it, and Moybyte's own fields go under the
manifest's `moybyte` key (SPEC.md §3.1; `moy_store_base.VENDOR_KEY`), never
loose at the top level. A change to the medium ships as strict readers plus a
seed bump (CLAUDE.md, 2026-09-07).

| issue | what the store carries | 1b | why |
|---|---|---|---|
| #162 namespaced ids | the folder name is the id, `<author>.<name>`, and the index key; create, duplicate and adopt write `"id"` into the manifest, and adopt names the folder from it; `title` is display only. A folder whose manifest has no `id` reads its id from its name, which is the rule, not a migration. A cart a user makes takes `<author>` from a Settings field (§12). The built-ins move into `moybyte` (`moybyte.<name>`) by a seed bump, the old folders retired by the sweep's generation gate | built: slices 1, 3, 4 | it is the index key; moving the key later changes the index twice |
| #131 profiles | the decided layout (§12): a root for the family shelf (`/sd/moybyte/shared/carts`) and one for each kid's carts (`/sd/moybyte/kids/<name>/carts`), on a card only, since a board with no card has one profile; `moybyte.owner` in the manifest and `owner` in the row; a kid's saves at `kids/<name>/saves/<cart-id>`, a path the store composes from (profile, id), shared games included | built: the roots in slice 1, `owner` and the saves' path in slice 3; share (a snapshot copied to the family shelf) and remix (a copy into the kid's root) through adopt and provenance in slice 7 | the shelf is the family root plus the current kid's, so a sibling's carts are absent until shared; Who's playing, the PIN and its `gate(action)` are the spine's and the apps' |
| #136 the time machine | `moy_journal_list` and `moy_journal_snap`; a restore appends the snapshot as a new commit, so history only grows | built: slice 6 | a read of the journal 1b moves; the timeline is the Editor's |
| #127 backup, export, import | `moy_pack(h, history)` writes a `.moy` archive, a zip of the folder (`firmware/web_runner/moy_store.mjs`'s codec is its mirror, pinned by a round trip); `moy_adopt(staging, rid)` moves a staged cart into place with one rename and makes its row | built: slice 7 | Get Carts already stages and renames in Python; import, a gallery install and a remix all go through adopt |
| #122, #125, #123, #195 sharing | provenance in the manifest's `moybyte` object and the row (`moybyte.origin`, `moybyte.source` — the source's id and version — and `moybyte.remix_of`); publish is `moy_pack` without history under the wire's `_skip`; a remix is duplicate plus provenance | provenance and pack built: slice 7; the rest is not the store's | the transport is sprint 3's, the parent gate a setting (sprint 2), the screens apps, the gallery a server in moy-spec's tooling |
| #235 aging, sharding | only the store composes a cart's path (§4), so a shard level under `/moy/carts` is invisible to callers and to the wire; an aged volume compacts a cart by duplicating it to a fresh folder and adopting it over the old | shaped | a littlefs lever (#198), built when #235's numbers ask; FAT for regenerable data and QIO flash are partition and board work |

## 9. Memory

**Everything the store holds is PSRAM** (`moy_htab_host_alloc`, and
`heap_caps(MALLOC_CAP_SPIRAM)` for arenas). A store call runs on its caller's
task with a small frame that does not grow with what it reads (moy_json
recurses only in its depth-capped scanner); the store owns no task, and its
statics are a few pointers. The FAT long-name buffer exists already and is in
sprint 0's baseline; the card's DMA bounce (`moy_sd_card_io`) is internal SRAM
taken for one transfer and given back.

| what | lives | ESTIMATED |
|---|---|---|
| index | the store | 8 B a slot and a 2-byte hash cell, grown by doubling |
| rows | the store | ~100 B fixed, the folder, title and author, the icon's indices (64 B a tile, 1 KiB at most): ~200 B a cart, ~18 KB for 87 carts |
| scan arena | one scan | a read buffer and one folder's listing, a few KB |
| seed arena | one seed | the 32 KiB inflate window (`wbits` 15) and one cart's files |
| a loaded cart | until the binding built its dict; in sprint 4, the run | the cart's files, tens of KB |
| a journal call | one call | one file and one snapshot |
| the owned card volume (slice 8) | the volume | the `FATFS` (~600 B) and a 32-sector cache (16 KiB) |

The rows replace the gc heap's shelf catalogue (#224's census sizes it). Python
keeps its shelf dicts while its VM runs, on the Python heap's budget; with the
VM stopped only the rows remain, inside the kernel's 1 MiB, which on the
Guition S3 leaves about one pooled layer after the C side the census measured
(§6.1) — so the store stays small.

**The 1b gate's figures, and how each is taken:**

- **Resident store PSRAM at most 32 KiB** at the Player's fit check on the
  census's stores, and **every arena 0** there: each is released before a cart
  starts. A `store` field in the census snap (rows, row bytes, arena bytes now
  and at high water), added in slice 1 beside `moy_alloc.live()`.
- **The heap after boot** within §6.1's five areas: `gc.areas()` in the
  `heapcaps` word.
- **Doom on a fresh Guition S3, five boots**, at §6.1's cart-available
  threshold: the Player's fit check under `tools/mem_census.py`'s protocol.
- **Internal SRAM 0 B net** against sprint 0's baseline (the share allows
  4 KiB): free, largest and low-water, internal and DMA-capable, WiFi and BLE
  up, both S3s; the census's `sram` run.
- **No slower seed and scan** than dev `a98a04f` beyond each board's spread;
  **every image above its floor** (§6.1), the Zero's included. Numbers to #224.

## 10. The slices

Each lands on dev alone, passes the host suite, the browser, a board pass on
the four consoles and the Zero's suite, and deletes the Python it replaces.

0. **The carve's second pass** (Python, host only): the index keyed by (root,
   folder) with the root table; a test that fails any caller composing a cart
   path; `flags` and `cfg` out of the entry; the journal by handle; the seed
   after the scan, reading versions from the entries; the store trace extended
   to seed, load, publish, recover and journal append, undo and redo.
1. **The index on `moy_htab`**: PSRAM rows, the root table; the C index becomes
   every image's and the frozen `moy_index.py` leaves. Gate: the index suite
   over every binding, the trace on both object models, the fuzz.
2. **The seam and the crash-safe write**: `moy_vol`'s three backends and the
   borrow, `moy_fs` in C, under which every store write now goes. Gate:
   `moy_fs`'s torn-write tests and `tests/test_store_on_vfs.py` on the C path,
   FAT and littlefs; a host power-cut matrix (oofatfs on a RAM card, littlefs
   on a RAM flash, cut at every block write; every read returns the new file
   whole or the previous one); commit, reboot, intact on both S3s.
3. **The catalogue, the covers' facts, the cart verbs, #162's id rule** (the
   author of a cart a user makes from Settings, §12). Gate:
   the shelf identical, key for key and icon for icon, over every seed, fixture
   and port cart and the broken-manifest cases; `moy_json` fuzzed against
   CPython's `json`; the heap after boot.
4. **The seed, with #162's rename of the built-ins into `moybyte.<name>` as
   its seed bump.** Gate: the
   written tree byte-identical to the Python seed's on the host; a first boot
   on a wiped store and a warm boot on both S3s.
5. **Loading a cart.** Gate: every cart loads whole, identical across
   bindings; the Editor opens and commits; Doom on the Guition S3, five boots.
6. **The journal, with #136's reads** (it can follow slice 2 directly). Gate:
   the journal suites over the binding at every crash point; the receiver's
   journaling through the Zero's sync apply.
7. **Adopt, pack, provenance** (#127, #122, #195), Get Carts installing
   through adopt. Gate: a round trip against the browser's codec; an install on
   glass.
8. **The card volume, owned** (§1; owner, 2026-10-06): on every console
   board's card the kernel's `FATFS` over a C block device with the read
   cache, mounted for Python at `/sd` so `VfsFat` and the store share one
   instance; the T-Deck's `moy_sd` attach in C, the Guition S3's and the P4s'
   card driver initialised once and never torn down; the T-Deck's storage gate
   and flush fence under `native/moy_flush/moy_flush.c`'s header. It deletes
   `device/moybyte_sd.py`'s Python block device. Gate:
   `tests/test_store_on_vfs.py` over the C block device, every sector FatFS is
   handed compared with the card's and a cache that skips the drop failing;
   commit, reboot, intact on both S3s and both P4s; the boot and a live rescan
   no slower than the Python cache's on the T-Deck and than the uncached card
   on the Guition S3.

## 11. The browser, the Zero and the sync wire

**The browser.** `native/moy_store/` builds into `micropython.wasm` as a
usermod, as `native/moy_index/` does. Its volume is POSIX on the worker's MEMFS,
the tree `VfsPosix` shows Python, so the C store and Python's file calls see
one store. Site mode's OPFS persistence and board mode's `POST /sync` both come
from `StoreWatcher`'s sweep of that tree and do not change. The worker is one
thread and its VM never stops.

**The Zero.** It takes the usermod, so every slice is a Zero link gate against
its 256 KiB floor. Its volume is its internal littlefs, borrowed from `VfsLfs2`
until the kernel is its entry (sprint 3). `apply_ops(journal=True)` journals
through the C journal, and a cart-delete from the wire goes through
`delete(h)`, so the index stays true.

**The sync wire stays identical by construction.** The crossing writes the
same files with the same bytes, and the wire is paths and bytes. `_skip` stays
one predicate: C owns it (`moy_store_skip(relpath)`), `moy_sync._skip` calls
it, the JavaScript mirror in `firmware/web_runner/moy_store.mjs` is pinned to
it by a table test, and pack uses it. A wire path names a cart folder relative
to its root: #162 changes folder names, not the grammar, and a shard (#235)
never reaches the wire because the receiver resolves `<id>.moy` through the
store. `apply_ops`' op vocabulary and the webhost's pull listings are sprint
3's and unchanged; their writes go through C `moy_fs`.

## 12. The owner's calls

- **#162's namespace and author** (owner, 2026-10-06). The built-ins'
  namespace is `moybyte`: a built-in's id is `moybyte.<name>`. The author of a
  cart a user makes comes from a Settings field, which defaults to the
  profile's name once profiles exist and to `local` until then.
- **The owned card volume is 1b's last slice** (owner, 2026-10-06), not
  sprint 3's (§1, §10 slice 8): it has to be done anyway, and doing it in 1b
  puts one card instance and its read cache under every board's store.
- **#131's model** (owner, 2026-10-06) is #131's decision comment: one
  folder per kid, a family shelf, optional parental controls; §8 says what the
  store builds for it.
