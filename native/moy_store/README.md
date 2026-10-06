# moy_store — the store, native

The store in C (`docs/kernel_store_2026-10.md`; #224), linked by every image:
the boards, the browser and the desktop MicroPython. `runtime/moy_fs.py`,
`runtime/moy_carts.py` and `runtime/moy_store_base.py` delegate to it wherever
the module imports; CPython (the simulator, the tools, the tests) keeps the
Python twins, the reference the C is held to.

| file | what it is |
|---|---|
| `moy_vol.h`, `moy_vol.c` | the volume seam: oofatfs, littlefs2 and POSIX behind one interface, and a working folder (`moy_vol_enter`: FAT's own, restored whole on leave; a prefix elsewhere); a store call borrows the VM's own instance |
| `moy_fs.h`, `moy_fs.c` | the crash-safe write (marker, stamped backup, file in place), its recovering reader, the claim, and the plain file verbs |
| `moy_load.h` | loading a cart whole (`moy_cat_load`, in `moy_cat.c`, the same reader as the entry): its scripts, config, flags, sheet, sounds, map, blocks, images and scenes into one PSRAM arena, freed once the binding has built `load`'s dict |
| `moy_journal.h`, `moy_journal.c` | the journal: `journal.jsonl` appended one json.dumps line a commit, the snapshot first (a claimed publish backup where the stamp matches), the cursor map last; undo and redo by file scope; compaction to 64 entries and 512 KiB; #136's list, snapshot and restore; the graduation rider's manifest write |
| `moy_pack.h`, `moy_pack.c` | a cart as it travels: the `.moy` archive (the browser's `zipStore`/`unzip` codec; stored entries written, stored and deflated read), the wire's skip rule (`moy_sync._skip` calls it), and adopt, a staged cart moved into place by one rename (Get Carts installs through it) |
| `moy_card.c` | the card volume, owned: the store's FATFS (a VfsFat object in PSRAM) over a NATIVE block device with the 32-sector read cache, mounted for Python at `/sd` (`moy_store.card`), its sectors from `moy_sd_card_io` on a board or a Python driver on the host; `card_stats` is the tests' check and fault switch |
| `moy_arena.h` | a store call's scratch, freed at once |
| `moy_seed.h`, `moy_seed.c` | the seed: a packed roster blob inflated (the image's uzlib) into PSRAM and written as `moybyte.<slug>.moy`; a re-seed keeps the kid's saves and config in place and publishes the manifest last, so a cut seed reads as the older version, or as none |
| `moy_cat.h`, `moy_cat.c` | the catalogue: the shelf's scan of a root (one listing, then per cart folder one enter, one listing and the files it shows), one cart's entry, and the whole-folder verbs (remove, copy) |
| `modmoy_store.c` | the MicroPython binding, module `moy_store`: the mount-table borrow (VfsFat's `FATFS`, VfsLfs2's `lfs2_t` behind a layout check, VfsPosix at `/`), every call under `nlr_push`, scratch and the marker cache in PSRAM on a board |
| `moy_store_host.c` | the host's imports over malloc and POSIX, for the ctypes binding |
| `fuzz_fs.c` | the power-cut matrix (`--matrix`) and the seeded fuzz over oofatfs on a RAM card and littlefs2 on a RAM flash, under ASan and UBSan |
| `host/py/mpconfig.h` | the FAT settings oofatfs reads, for the host builds |
| `micropython.cmake`, `micropython.mk` | the boards', and the desktop's and browser's, builds |

Two rules the matrix found and the store keeps: an EMPTY unstamped backup is
refused (FAT lands a new file's entry before its bytes), and on littlefs a
claim across folders declines, so the journal writes its own copy (littlefs
2.11 cut between the two commits of a cross-folder rename drops the source
folder's other entries).

The nets: `tests/test_moy_store.py` (the matrix and the fuzz, the C over POSIX
against the twin through ctypes, the desktop MicroPython against CPython),
`tests/test_store_native.py` (the shelf and moy_json on the desktop MicroPython
against CPython over every cart and generated ones) and
`tests/test_store_on_vfs.py` (the store on FAT and littlefs volumes under the
desktop MicroPython, every write the C store's). The harness is
`tools/moy_index_spike.py --component fs sanitize`.
