# moy_uitree: the UI engine in C

`moy_uitree.h` is the API and its header comment the overview: a page a build
function describes, held in a node store, drawn by walking it through
native/moy_ui's verbs. `runtime/uitree/uitree_c.py` is its Python front end;
`runtime/host/moy_uitree_binding.py` binds it on the host, `modmoy_uitree.c`
on MicroPython. A Lua app's pages reach it through `native/moycore/moycore_uitree.h`
(its store as a Lua object) and `runtime/run/uitree_engine.lua`, the Lua
layer's front end.

| file | holds |
|---|---|
| `moy_uitree.c` | the store, state, models, actions, the build calls, events, the trace |
| `moy_uitree_lang.c` | templates and conditions, compiled at the call that names them |
| `moy_uitree_walk.c` | the frame: the walk, focus, hints, dialogs, the placed layout |
| `moy_uitree_aot.c` | a page compiled ahead of time: `uit_save`, `uit_load` |
| `moy_uitree_run.c` | a page's frame on a run (`uit_run_frame`): the walk over the RUN face's context and sink, a Lua or compiled app's |

## A compiled page

A build reads no state and asks the engine nothing (B01-B03), so the store as
a build's lowering leaves it is a function of the page's module alone: every
size class, input kind, desk or handheld and font scale is a template or a
condition the walk evaluates. `tools/uitree_compile.py` checks a module with
every reference check, builds and lowers each page function on the host's
engine, and saves the store as a blob; a board loads it into a new store
(`uit_load`) with no build, no check and no lowering. The variant key is
empty: no build depends on a layout value.

The blob, every number little-endian at its own width:

| at | bytes | what |
|---|---|---|
| 0 | 4 | `MUIT` |
| 4 | 2 | the format, `UIT_FORMAT` (2) |
| 6 | 2 | the header's length, `UIT_BLOB_HEAD` (32) |
| 8 | 4 | the layout hash of the engine that wrote it (`uit_layout_hash`) |
| 12 | 4 | the source hash: FNV-1a over the page's module and the engine's sources (`tools/uitree_compile.py`) |
| 16 | 4 | FNV-1a over every byte after the header |
| 20 | 4 | the blob's length |
| 24 | 4 | the front end's part: its offset |
| 28 | 4 | and its length |
| 32 | 1 + n | the page's id (`"1 rows_page"`), then the variant, each a length byte and its bytes; then one byte, 1 where the store is placed (`uit_set_placed`: the Editor's pages) |

Then the store's sections, each a count (4 bytes) and its records, each record
its fields in the order `moy_uitree_aot.c`'s field tables list them, at their
widths: the pool (bytes); the slots (a slot's declaration, a byte, and its
model's fields where the byte is 1); the actions; the label tables; the
templates; the template ops; the conditions; their terms; the nodes; the
props; the dialogs' buttons (2 bytes each); then the six marks (where the
declarations end in the pool, templates, ops, conditions and terms, and the
page id's pool offset). Last, the front end's part, text lines: `S` the
states in slot order, `T` their type codes, `A` the actions in their order,
`D` each canvas's draw function (`node:name`), `G` each canvas's ground role,
`X` the rest of what the front end keeps beside the store once a page is
lowered, as one literal (`uitree_c.CEngine.front`): each canvas's pointer
action, each popover's list and whether a field of its own takes keys, the
document fields the page reads, each text editor, and each panes, pane and
legacy node's stand-in (its props, a function by its name in the page's
module), and `O` what each state's slot keeps by index, in the store's
order (`uitree_aot.orders`): a model's fields (`Model.order`, sorted), a
document's row's (`doc`, then `DOC_ORDER`, sorted), an enum's values, None
for the rest. A document is its state's slot (a row_of) and its hidden
model's (`NAME#doc`), both in `S`.

The layout hash folds the format, every field table's widths, every kind's
name and the last value of each numbering a record holds. A blob whose
format, layout, length, checksum, page or placing is not this engine's is refused by
name (`UIT_E_BLOB`), never read; `uitree_aot.load` refuses one naming a state
or action its module does not declare, a state of another type, or a
state whose fields or values this Python orders otherwise than `O`, and the
front end then builds the page as it would with no blob.

Slots are bound by name: a board's dicts keep another order than the host's,
so the store's slot order is the blob's and the front end's `_names` follow
it. Nothing the store numbers takes a dict's order: fields go in sorted
order, and `O` refuses a blob whose numbering this Python would read
otherwise. `tests/test_uitree_aot.py` loads every page on the boards'
MicroPython and holds its frame to the built one, and on the host walks
every declaration dict the other way. The image's frozen `uitree_pages` module holds each blob with its front
part already read (`uitree_aot.entry`), its names a frozen tuple, and the
blob itself bare of that part (`uitree_aot.bare`: its length and checksum
made again), so the front is in the image once. A board
build that takes this module writes it into its staged modules
(`moybyte_stage_uitree_pages` in `tools/esp32_build_lib.sh`, which runs
`tools/uitree_compile.py --staged` over the console's pages, `SYSTEM`); where
the host cannot compile, the image carries none and every page builds on the
device. The host and the browser carry none either: their pages build.

**What stays in RAM.** A load copies the blob into the store (the compiler prints
each blob's size). After `uit_build_end` the nodes, props, templates, ops, conditions,
terms, actions, labels and pool are not written again by a visit, so they
could be read from flash in place; the slots are written by every visit
(values, versions, the lists' syncs). Reading in place would need a blob in
the board's own struct layout (padding and a double's alignment differ
between a 64-bit host, the board model and the boards) and arrays the store
neither frees nor grows, to save the PSRAM the kept trees hold
(`tests/test_uitree_c_memory.py` pins it): it is not done.

**A page with no blob** (a cart's app outside the image) builds on the device
as before. A cart's page can get one later at install: compiled by the
console's own engine, stored beside the cart, keyed by the firmware version as
well as the layout hash, since a later engine may read the same layout
differently.
