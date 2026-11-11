// moy_sh_job: a shell line run with no face (docs/os_2026-11/between_apps.md
// section 3: cron's and at's jobs, runtime/system/cron.py): a session of its
// own over the board's host table (moy_sh_board.h) in its home, whose output
// goes into the OS log under the job's pid, a record a line (stdout at info,
// stderr at err; an empty line none; a line longer than MOY_SH_JOB_LINE is
// cut into records of that length), its stdin at its end and no screen for
// a text app.
//
// A RUN is the session's runner's, as the screen face's is: it holds the
// caller's task while it computes and parks where it waits (`sleep`, a role
// command between its steps), when moy_sh_job_run answers MOY_SH_JOB_PARKED
// and the frames go on; moy_sh_job_ended gives its exit status once it ends.
// A line that does not end (an open quote, an `if` with no `fi`) is dropped
// with a line at err and status 2. moy_sh_job_interrupt is Ctrl-C at its
// next poll.
//
// Its memory is the board table's `alloc` (PSRAM): the record, the line's
// copy and the session's arena (MOY_SH_ARENA), all given back by
// moy_sh_job_free, which writes a line still held first.

#ifndef MOY_SH_JOB_H
#define MOY_SH_JOB_H

#include <stddef.h>
#include <stdint.h>

#define MOY_SH_JOB_LINE 240             // a log record's bytes at most
#define MOY_SH_JOB_PARKED (-1)          // moy_sh_job_run: the run waits

typedef struct moy_sh_job moy_sh_job_t;

// A job of process `pid` in its home; `root` names the files tree off a board
// (NULL: the board table's). NULL when memory refuses.
moy_sh_job_t *moy_sh_job_new(uint32_t pid, const char *root);
void moy_sh_job_free(moy_sh_job_t *j);
// Run `n` bytes of shell (a line, its newline included): the exit status, or
// MOY_SH_JOB_PARKED while the run waits; MOY_SH_JOB_PARKED too while a run
// is parked, which nothing else may enter.
int moy_sh_job_run(moy_sh_job_t *j, const char *text, size_t n);
// 1 once, when a parked run has ended, with its exit status in *status.
int moy_sh_job_ended(moy_sh_job_t *j, int *status);
void moy_sh_job_interrupt(moy_sh_job_t *j);

#endif // MOY_SH_JOB_H
