// moy_screen_slot: a program's screen handed to the console, per process
// (its pid): what a compiled app or game in the Player presents
// (`moy_screen` and `moy_present`: an ELF game's rows, native/moy_elf/moy_os.c,
// runtime/run/elf_run.py; a WASI app's moybyte.os imports,
// native/moy_wasm/moy_wasi.c, runtime/run/wasi_run.py).
//
// THE SLOT is w x h pixels of `depth` bytes (1 palette indices, 2 RGB565),
// or a MIXED slot (MOY_SCREEN_SLOT_MIXED, a game built from a compiled
// cart's source: sdk/src/moycart.c) of two bytes a pixel whose every frame
// is either, by its size, in the kernel's memory, opened by the program's runner on the VM's task as
// the program starts and closed at its end. Its frame has one writer at a
// time: FREE, the program's present copies its screen in (WRITING) and makes
// it READY; the console's frame call takes a READY one (TAKEN) and keeps it
// until a present waits with a newer one: a frame call lets it go (FREE)
// then and waits for that one, past its `max_ms`, so between frame calls the
// slot holds the last frame taken, which the console may show again from
// its memory (runtime/run/elf_run.py's `kept`). A stepped build lets it go
// at the call's start, since its program presents after the call returns.
// A present while the frame is not FREE waits for it, in slices, and
// answers EINTR once the slot is closed.
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
#define MOY_SCREEN_SLOT_MIXED 3         // a depth: indices or RGB565, frame by frame
#define MOY_SCREEN_SLOT_IN 16           // input words a frame message carries at most

// The VM's task. Open: 0, or an errno negated (ENOSPC every slot another's,
// EINVAL a size or depth refused, ENOMEM). Close: the slot freed, a present
// waiting on it answered EINTR.
int moy_screen_slot_open(uint32_t pid, int w, int h, int depth);
void moy_screen_slot_close(uint32_t pid);

// Any task: the slot's size and depth (a MIXED slot's MIXED): 0, or -ENODEV
// for none.
int moy_screen_slot_size(uint32_t pid, int *w, int *h, int *depth);

// The program's thread: its screen, `n` bytes (w * h * depth; a MIXED
// slot's w * h indices or 2 * w * h RGB565), copied into the slot, waiting
// while the last is not let go. 0, or an errno negated (ENODEV no slot,
// EINVAL a size not the slot's, EINTR the slot closed).
int moy_screen_slot_present(uint32_t pid, const void *px, size_t n);

// The VM's task, once a frame: the frame message posted unless one waits
// unread (its arguments followed by the `n_in` words at `in`), then up to
// `max_ms` waiting for a present (the frame taken let go to it), the
// program's hops served meanwhile (0 where no task can block: stepped). A
// mask: MOY_SCREEN_SLOT_SENT where the message was posted,
// MOY_SCREEN_SLOT_SHOWN where a newer frame was presented and taken, and
// MOY_SCREEN_SLOT_DIRECT with SHOWN where a MIXED slot's frame is RGB565;
// -ENODEV for no slot.
#define MOY_SCREEN_SLOT_SENT 1
#define MOY_SCREEN_SLOT_SHOWN 2
#define MOY_SCREEN_SLOT_DIRECT 4
int moy_screen_slot_frame(uint32_t pid, uint32_t held, uint32_t pressed, uint32_t ms,
                          const int32_t *in, int n_in, uint32_t max_ms);
// Any task: a hop of process `pid` queued (moy_vmq_hop): the frame call
// waiting on its slot, if one has a slot, woken to serve it.
void moy_screen_slot_kick(uint32_t pid);
// The slot's pixels, for its life (between frame calls, the last frame a
// call took), `depth` the bytes a pixel it holds (a MIXED slot's 2); NULL
// for none.
const uint8_t *moy_screen_slot_pixels(uint32_t pid, int *w, int *h, int *depth);

// THE TOOLKIT'S ANSWERS. An app drawing with the toolkit it links tells,
// after each frame it draws, what the console's input owner asks a run's
// toolkit (moy_ui_wasm.h's MOY_UI_TOLD_*: a field in focus, a cancel or a
// back it would take); the console reads the last told, and its cancel and
// back go in by the next frame message's MOY_OS_FRAME_IN_ASK word.
// Tell: the program's thread, 0 or -ENODEV. Told: any task, 0 for no slot.
int moy_screen_slot_tell(uint32_t pid, uint32_t answers);
uint32_t moy_screen_slot_told(uint32_t pid);

#endif // MOY_SCREEN_SLOT_H
