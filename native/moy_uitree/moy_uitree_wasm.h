// The UI engine's wasm rows: the `uitree` rows of native/os_calls.json a
// compiled app imports from `moybyte.app` (`uitree_<verb>`, named by
// native/moy_app/moy_app_wasm.c's table; the app's header is
// sdk/include/moybyte/uitree.h). A compiled app is its own front end: it
// declares its states and actions, builds its page with the engine's generic
// build calls, draws it each frame on the run's canvas and runs a handler for
// each action the engine hands it -- the same store, walk and trace a Python
// or Lua app's pages reach (moy_uitree.h).
//
// One store a run, made by the run's first row and dropped when the run ends
// (moy_uitree_wasm_end) or the next run opens (moycore_ui_snap's serial), as
// the toolkit's RUN face keeps one context a run (moy_ui_run.h).
//
// Every text, list and model crosses by value at the call (a pointer and a
// length; a list's items NUL-separated in one text), and the engine keeps
// nothing that points into the module's memory. A read is copied into the
// buffer the app hands with its capacity and answers the whole length.
//
// The rows, beyond the uit_* call each makes:
//   frame(flags)  uit_run_frame on the run's canvas with the hints band and
//                 the trace on; `flags` is 0 (no flag is defined yet). The
//                 engine's trace lines go to the OS log as "[trace] " lines,
//                 the Python and Lua layers' format.
//   open()        the visit opens (uit_open): the values set before it are
//                 the app's start and are not traced, every write after is.
//                 An action declared as the hook `opened` (UIT_A_HOOK) is
//                 the first one `next` hands out after it.
//   next(ev)      the next action to run, its index (-1: none): ev[0] its
//                 model slot, ev[1] its row (-1 each where it acts on none).
//                 An action that is off now is traced as skipped and not
//                 handed out; a control's write is not an event here (the
//                 slot holds it: read it with get_*). Every set_* until the
//                 next `next` or `frame` is traced as the action's write
//                 (a model's as the layers trace a set: rows added, gone,
//                 changed and moved).
//   error(out)    the first refusal of the run as one line,
//                 "CODE uitree_<row>(<where>): <message>". A refusal is
//                 latched: every later row of the run answers it again,
//                 and it is logged once.
//
// An answer below 0 is moy_uitree.h's UIT_E_*.

#ifndef MOY_UITREE_WASM_H
#define MOY_UITREE_WASM_H

#include <stdint.h>

#include "moy_uitree_wasm_rows.h"

// The run is over: its store and what the app kept beside it are freed. The
// module that drives a compiled run calls it as the run ends (weak there: an
// image without this engine has none).
void moy_uitree_wasm_end(void);

#endif
