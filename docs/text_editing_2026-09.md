# Text on the console: one editor, three doors (2026-09)

Owner decisions of 2026-09-07, recorded so the six steps below are built to one
design and not re-argued step by step. Status lives in the issues; this file says
what IS decided.

## The decision

Notes is the one text app. Writer's shell process goes; what it had that Notes
lacks (undo through the op-history core, clipboard, wrap, the T-Deck's text-mode
keyboard) moves INTO the shell as an editor handle a cart draws through, the way
`make_layer` exposes the layer engine. The kid edits the skin, never the engine.

Docs are plain Markdown files in one flat vault so a PC with Obsidian opens the
same folder unchanged. The file is the interface; sync is a folder moving over
the existing RPC or the card, not an app feature.

## Three roots, three doors

Roots a person touches: PROJECTS (each cart a folder), NOTES (the vault, one
flat folder -- `.md` by default, and whatever else a person names), and the
drawings the Files app owns.

| door | path | what opens |
|---|---|---|
| make a game | Launcher → cart → EDIT → tab ladder | the Code tab is one of the cart's scripts — its main file unless another is picked (#89): highlight by runtime, parse gate on the debounce, hard exits always write (SYNTAX badge), PLAY, crash-to-code |
| write | Launcher → Notes | the vault list (whole names, mode badges), NEW → a name prompt, one file open with the SELECT/COPY/CUT/PASTE/UNDO/REDO toolbar over it |
| any file | Launcher → Files → a root | routed by what it is (below) |

**The Files router**, in order: a `.moy` folder opens the project Editor, never a
listing; any script in a cart's own load order — `main.py`/`main.lua` and every
other entry in its `sources` (#89) — opens the Editor's Code tab (PLAY, the
journal and crash-to-code live there); an image opens Paint -- including a
cart's own `images/*.moyimg`, edited in place on the project kind, and one
Paint has no editor for opens READ-ONLY rather than being refused, because a
picture always has somewhere to open; any other text file
(`.md`, `.json`, `.txt`, a script `.py`/`.lua`) opens the editor in the mode for
its extension. A project's own files as files are reached only through the Config
tab's ADVANCED row, so the loader stays in the loop.

**Everything the router opens comes BACK to Files**, on the shelf it was left
on. Files is a cart app, so a note/project/drawing REPLACES it, and the console
records who it is returning to: `_run_caller` for a run (the Notes cart),
`_project_return` for a project file (which returns through the loader), and
`_app_return` for a jump into another app's own surface (Paint, a project's
Editor) -- popped by `Workstation._go_home_or_back`, which is what every exit
gesture ends in. HOME is still home, and going home clears the return.

**Modes** are the only thing that differs per file: Markdown (wrap, headings and
checkboxes rendered, `[[note]]` tappable, `![[drawing]]` inline), code (the
per-runtime highlighter and parse gate), JSON (pretty-printed at indent 2 on
open, saved as shown; soft save refuses an invalid document; a hard exit writes
it with an INVALID badge and the loader re-validates on the next open), plain
text.

**WRAP is prose, PAN is structure.** Markdown and plain text soft-wrap, because
a sentence that runs off a 320px screen is unreadable. Code and JSON do not:
their LINES mean something, so a long line pans sideways instead -- the Code
tab's gesture, drag or trackball, one rung down.

## What the vault holds, and what a name means

**Every file in the vault is listed, under its whole name, with its mode's
badge.** `.md` is the ONE extension a vault name may leave off, because a bare
name IS a note; `todo.txt`, `data.json`, `hi.py` and `hi.lua` keep theirs,
because the extension is what picks the mode and what stops two files shadowing
each other. The store's `VAULT_EXTS` is that set and `text_modes.badge_for_kind`
is the label.

**NEW asks for a name** (Notes' prompt; the Files rename field's pattern) and
the name decides the mode: a bare name is Markdown, an extension is honoured.
That is the only thing on the console that MAKES a `.txt` or a `.json` -- before
it, nothing did, and the vault listed neither. The store slugs and unique-ifies
what was typed (`moy_carts.free_file_name`), so NEW never overwrites.

## Scripts

A script is a cart with no folder: a bare `.py` or `.lua` in the vault, listed
under its whole name like every other vault file (a
separate `scripts/` root waits for the terminal's `run`, #115). RUN (from Files, later `run name.py` at the terminal prompt) wraps
it on the fly — a synthesized manifest of `type: "script"`, the same portable
subset every cart gets, a TEXT CONSOLE as its surface (`print` to scrollback,
`input` on the prompt's line editor). Default grants are `files`, `prefs` and the
console; `carts` and the network go through the capability gate (#120). The
terminal (#115) inherits all of it: its `run` is this, its REPL is the script
surface with a live prompt, its scrollback is the console.

## The board split

The T-Deck is the writing device (keyboard, trackball as caret, the symbol
palette for the keys it lacks). **The trackball is the caret in a cart's editor
handle too, not only in the Code tab**: `Workstation.nav` is the one place that
decides, and it answers whether it spent the roll so the board loop can give the
same pulses to the cursor when nothing holds a caret.

**That keyboard has no Ctrl**, so the handle's `Ctrl+C/X/V/Z/Y/A` are a host
convenience and never the way a note is edited on a board. The device path is a
TOOLBAR -- SELECT, COPY, CUT, PASTE, UNDO, REDO -- carrying the Code tab's own
labels and its `select_sticky` gesture, because it is the row a kid's hand has
already learned. On the P4 and the Guitions long-form typing goes
through a BLE keyboard or the phone's keyboard in the web view; on glass they
read, tick checkboxes, make short edits and use the Config cards. No on-screen
QWERTY is planned.

## The six steps, each shippable alone

1. Notes' files become `.md` in one flat vault. **No migration**: the reader
   is strict, so a `.moytext` left on a developer's card is simply not a
   document any more (CLAUDE.md's no-migration policy).
2. The editor mode table and the Files router.
3. The editor handle verb; Notes rebuilt as the thin cart over it. **Built as
   `open_editor(name)` on the `files` grant** (`runtime/editor_handle.py`): the
   handle is identified by `(kind, name)` through the Files role, the cart draws
   it into a rect and forwards taps, and the SHELL feeds the focused handle the
   keyboard — because the switch that takes the keyboard is also the one that
   swallows the byte that caused it, and no cart can make that call.
4. Writer's shell process removed. **Deleted, not deprecated**: the app, its
   identity cart and its layer are out of the tree, and the launcher's text
   door is Notes over the handle.
5. The Config tab's ADVANCED files row, with JSON mode's rule. **Built as a
   router over the project KIND** (`moy_carts.PROJECT_KIND`, `project:<folder>`,
   written through `_write_atomic` and journaled into the project's own #111
   journal): a text file goes through the same request door as Notes and comes
   BACK to the Config tab, which is what keeps the loader in the loop — and
   because JSON mode writes an invalid manifest on a hard exit by design,
   `moy_carts.load` now RECOVERS such a cart instead of dropping it, so the
   project stays on the shelf carrying the reason and the row leads back to the
   file that broke it. **A cart's own SCRIPT does not take this door** (#89):
   the Code tab holds a *file* (SPEC.md §4's `sources`), so every entry in the
   cart's load order routes there, where PLAY, the journal and crash-to-code
   are. This row is also where the one **NEW SCRIPT** verb lives, offered only
   on a cart whose runtime loads more than `main`; the Code tab's file chip is
   a switcher and has no create.
6. The script runner with the text console surface.

Related: #108 (user files), #181 (apps are carts), #112 (cart-facing undo —
SETTLED by step 3: the handle carries `undo`/`redo` over the op-history, so
there is no cart-facing undo VERB and none is wanted), #114/#115 (the
terminal), #120 (the capability gate).
