// moy_tty_int: the state the face's files share (moy_tty.h).

#ifndef MOY_TTY_INT_H
#define MOY_TTY_INT_H

#include <stdint.h>

#include "moy_tty.h"

#define MOY_TTY_WAIT_MS 250             // the face's wait for input: an app's clock, a signal

// A hop waiting for the console's task.
typedef struct {
    void (*fn)(void *);
    void *arg;
    volatile int done;
    uintptr_t waiter;
} moy_tty_hop_t;

// PSRAM, taken at the first setup or hold.
typedef struct {
    const moy_tty_shell_t *shell;
    const char *etc;
    moy_tty_t *volatile face;           // the open face, or NULL
    pid_t next_pid;
    volatile uintptr_t task;            // the face task's
    volatile int holds;                 // MOY_TTY_SSH | MOY_TTY_WEB
    moy_tty_hop_t *volatile hop;        // the hop waiting, or NULL
    volatile uintptr_t tail;            // the console's task, as its tail last ran
    int serving;                        // a hop runs on it now
    moy_tty_stats_t stats;
} moy_tty_state_t;

moy_tty_state_t *moy_tty_state(void);
int moy_tty_setup_state(void);
// On the face task: run the face waiting, if one is; 1 when one ran.
int moy_tty_face_step(moy_tty_state_t *t);

#endif // MOY_TTY_INT_H
