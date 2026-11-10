// moy_notes: the queue in front of the console's notifications and the
// achievements process (docs/os_2026-11/processes.md sections 7 and 8), so a
// program's moybyte.notify, award and progress answer on its own task, with
// or without the VM (native/moy_app/moy_app.c's rows; a Lua service keeps
// its console calls, which are the VM's).
//
// NOTIFICATIONS. A post queues the notification for the console, which
// takes each (moy_notes_take) into its list and puts its banner up
// (runtime/system/notifications.py; the banner and the list are the
// console's). A post answers the notification's id, from the one counter
// the console's own posts take theirs from too (moy_notes_id), so an id
// names one notification whoever posted it. It is DENIED unless the
// poster's event queue was opened MOY_EVQ_NOTIFY (its `notifications`
// permission), STALE with no queue. MOY_NOTES_WAITING wait at most: past
// that the oldest goes, as the console's list lets its oldest go. The system
// posts too (moy_notes_system: the media service's takeover notice), as the
// shell, under no queue.
//
// BADGES. award(id) and progress(id, value) are messages to the
// achievements process ("award" [id], "progress" [id, value]), COALESCED
// here per process and badge as runtime/system/moybyte.py documented them:
// an award of a badge found sends nothing; a progress keeps the badge's
// newest value and sends it MOY_NOTES_EVERY_MS after the last send at most,
// at once when it reaches the goal the manifest declares (moy_notes_goal,
// which the console hands at the process's open), and at its end
// (moy_notes_end); an unchanged value sends nothing. A send no listener
// took -- refused for BACKPRESSURE, or with the achievements process not
// there, as through a VM stop -- waits and goes again in its turn
// (moy_notes_run, the kernel's frame's job while one waits, and the host's
// scheduler's tick). Each answers what its badge's last send answered: how
// many took it, or -MOY_EVQ_FULL. A value is a whole count (the goal's
// current value); a badge the table has no row left for is sent at once.
//
// Any task may call any function but take and run, the console's; no ISR
// may. Nothing allocates.

#ifndef MOY_NOTES_H
#define MOY_NOTES_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MOY_NOTES_SUMMARY 47u       // a summary's bytes (moy_chrome.c's line, its NUL apart)
#define MOY_NOTES_BODY 200u
#define MOY_NOTES_WAITING 8u        // notifications waiting for the console
#define MOY_NOTES_BADGES 32u        // badges coalesced at once
#define MOY_NOTES_ID 32u            // a badge's id, its NUL apart
#define MOY_NOTES_EVERY_MS 250u     // a badge's sends, one in this at most
#define MOY_NOTES_TO "moybyte.achievements"
#define MOY_NOTES_SYSTEM 1u         // the system's posts' pid, the shell's

typedef struct {
    uint32_t id;
    uint32_t pid;
    char app[48];                   // the poster's app id as it posted (MOY_EVQ_APP)
    char summary[MOY_NOTES_SUMMARY + 1u];
    char body[MOY_NOTES_BODY + 1u];
} moy_notes_note_t;

// The next notification id.
uint32_t moy_notes_id(void);
// Process `pid`'s notification queued: its id, or a moy_evq code negated
// (DENIED, STALE). A newline in either text reads as a space.
int32_t moy_notes_post(uint32_t pid, const char *summary, size_t sn, const char *body,
                       size_t bn);
// The system's notification queued, as the shell: its id, or -MOY_EVQ_FULL.
int32_t moy_notes_system(const char *summary, size_t sn, const char *body, size_t bn);
// How many wait, and the oldest taken into `out`: whether there was one.
uint32_t moy_notes_waiting(void);
bool moy_notes_take(moy_notes_note_t *out);

// Badge `id` (n bytes) of process `pid` has `goal` (1 or more). 0, or a
// moy_evq code negated (FULL: no row left).
int moy_notes_goal(uint32_t pid, const char *id, size_t n, int32_t goal);
int32_t moy_notes_award(uint32_t pid, const char *id, size_t n);
int32_t moy_notes_progress(uint32_t pid, const char *id, size_t n, int32_t value);
// What waits sent where its turn came: the ms to the next turn, UINT32_MAX
// for none.
uint32_t moy_notes_run(void);
// Process `pid` ended: what waits of its badges sent, its rows forgotten.
void moy_notes_end(uint32_t pid);
// Everything forgotten (a test's teardown).
void moy_notes_reset(void);

#endif // MOY_NOTES_H
