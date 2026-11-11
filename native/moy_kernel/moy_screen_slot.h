// moy_screen_slot: a program's screen handed to the console, per process
// (its pid): what a compiled app or game in the Player presents
// (`moy_screen` and `moy_present`: an ELF game's rows, native/moy_elf/moy_os.c,
// runtime/run/elf_run.py; a WASI app's moybyte.os imports,
// native/moy_wasm/moy_wasi.c, runtime/run/wasi_run.py).
//
// THE SLOT is w x h pixels of `depth` bytes (1 palette indices, 2 RGB565),
// in the kernel's memory, opened by the program's runner on the VM's task as
// the program starts and closed at its end. Its frame has one writer at a
// time: FREE, the program's present copies its screen in (WRITING) and makes
// it READY; the console's frame call takes a READY one (TAKEN) and lets it
// go (FREE) at its next call, once it has drawn it. A present while the
// frame is not FREE waits for it, in slices, and answers EINTR once the slot
// is closed.
//
// THE FRAME MESSAGE: each frame call posts the system's "frame" message,
// [held, pressed, ms, words...], to the process unless its queue holds one
// unread (moy_evq_queued), so a slow program is never flooded: the frame
// loop in sdk/include/moybyte/frame.h reads it.
//
// THE PROGRAM'S CALLS. A row the program calls that runs on the VM's task is
// a hop on the VM's queue (moy_vmq_hop), and the VM's task may be the one
// waiting in the frame call: a hop queued kicks the slot of its pid, and the
// frame call serves the queue (moy_vmq_serve) before it waits on, so a
// program's calls cost a frame nothing past their own work.
#ifndef MOY_SCREEN_SLOT_H
#define MOY_SCREEN_SLOT_H

#include <stddef.h>
#include <stdint.h>

#define MOY_SCREEN_SLOTS 4u             // programs with a screen at once
#define MOY_SCREEN_SLOT_IN 13           // input words a frame message carries at most

// The VM's task. Open: 0, or an errno negated (ENOSPC every slot another's,
// EINVAL a size or depth refused, ENOMEM). Close: the slot freed, a present
// waiting on it answered EINTR.
int moy_screen_slot_open(uint32_t pid, int w, int h, int depth);
void moy_screen_slot_close(uint32_t pid);

// Any task: the slot's size and depth: 0, or -ENODEV for none.
int moy_screen_slot_size(uint32_t pid, int *w, int *h, int *depth);

// The program's thread: its screen, `n` bytes (w * h * depth), copied into
// the slot, waiting while the last is not let go. 0, or an errno negated
// (ENODEV no slot, EINVAL a size not the slot's, EINTR the slot closed).
int moy_screen_slot_present(uint32_t pid, const void *px, size_t n);

// The VM's task, once a frame: the frame the last call took let go, the
// frame message posted unless one waits unread (its arguments followed by
// the `n_in` words at `in`), then up to `max_ms` waiting for a present, the
// program's hops served meanwhile (0 where no task can block: stepped). A
// mask: MOY_SCREEN_SLOT_SENT where the message was posted,
// MOY_SCREEN_SLOT_SHOWN where a frame was presented, taken until the next
// call; -ENODEV for no slot.
#define MOY_SCREEN_SLOT_SENT 1
#define MOY_SCREEN_SLOT_SHOWN 2
int moy_screen_slot_frame(uint32_t pid, uint32_t held, uint32_t pressed, uint32_t ms,
                          const int32_t *in, int n_in, uint32_t max_ms);
// Any task: a hop of process `pid` queued (moy_vmq_hop): the frame call
// waiting on its slot, if one has a slot, woken to serve it.
void moy_screen_slot_kick(uint32_t pid);
// The slot's pixels, for its life (what a SHOWN frame call took holds them
// until the next call); NULL for none.
const uint8_t *moy_screen_slot_pixels(uint32_t pid, int *w, int *h, int *depth);

#endif // MOY_SCREEN_SLOT_H
