// The exit statuses the OS gives a process it refuses or ends, on every tier
// and every face (docs/os_2026-11/processes.md section 1): the shell's, 126
// for a command refused its start and 128 + the signal for one ended. The
// process table (moy_proc.h) owns them; every C copy includes this header,
// and Python reads them from the module moy_log (NOT_RUN, INTERRUPTED,
// TRAPPED, KILLED: modmoy_log.c, the host's runtime/host/moy_log.py through
// moy_log_exits).

#ifndef MOY_PROC_EXIT_H
#define MOY_PROC_EXIT_H

#define MOY_PROC_NOT_RUN 126            // refused its start
#define MOY_PROC_INTERRUPTED 130        // Ctrl-C (SIGINT)
#define MOY_PROC_TRAPPED 134            // a compiled module trapped (SIGABRT)
#define MOY_PROC_KILLED 137             // its budget, kill, or a stop past its time (SIGKILL)

#endif
