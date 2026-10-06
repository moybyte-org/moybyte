# moy_store — the store, native

The store's volume seam and crash-safe write in C
(`docs/kernel_store_2026-10.md` §3, §5; #224), linked by every image: the
boards, the browser and the desktop MicroPython. `runtime/moy_fs.py` delegates
every read and write to it wherever the module imports; CPython (the
simulator, the tools, the tests) keeps the Python twin, the reference the C is
held to.

| file | what it is |
|---|---|
| `moy_vol.h`, `moy_vol.c` | the volume seam: oofatfs, littlefs2 and POSIX behind one interface; a store call borrows the VM's own instance |
| `moy_fs.h`, `moy_fs.c` | the crash-safe write (marker, stamped backup, file in place), its recovering reader, the claim, and the plain file verbs |
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
against the twin through ctypes, the desktop MicroPython against CPython) and
`tests/test_store_on_vfs.py` (the store on FAT and littlefs volumes under the
desktop MicroPython, every write the C store's). The harness is
`tools/moy_index_spike.py --component fs sanitize`.
