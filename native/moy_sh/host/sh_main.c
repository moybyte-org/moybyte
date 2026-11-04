// The shell's host harness: a session over a folder of this machine, fed a
// script line by line as a face feeds it, so the suite (tests/moy_sh/) runs
// the shell exactly as the screen and serial faces do, under the sanitizers.
//
//   sh_main ROOT SCRIPT          run SCRIPT's lines; exit with the shell's status
//   sh_main -t ROOT SCRIPT       a transcript: each prompt, then the line fed
//
// The OS around the session is a stand-in, enough to exercise every path:
//   /proc/1/status, /dev/uart1        virtual files (uart1 takes writes)
//   /bin/hello, /bin/count, /bin/fail, /bin/daemon, /bin/py
//                                     installed commands: hello prints its
//                                     argv, count copies stdin to stdout with a
//                                     line count, fail exits 3, daemon runs in
//                                     the background until killed (pid 40), py
//                                     answers "needs the console"
//   roles perm, notify, python        the console's commands: they echo argv
//                                     (python its stdin too)
//   role service                      answers "needs the console", as every
//                                     role does while the VM is stopped
//   /.interrupt                       Ctrl-C, after the count it holds
//   ps: pid 1 shell, pid 7 achievements; free and df fixed figures; log: the
//   kernel's own moy_log ring, with three lines written at start.

#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "moy_sh.h"
#include "moy_log.h"

static int bg_alive = 0;
static int bg_signal = 0;
static char interrupt_path[1024];

// Ctrl-C, in band: the script writes /.interrupt holding N, and the poll
// after the next N takes it (the shell polls after every command and while
// it waits or sleeps).
static int h_poll(void *ctx) {
    (void)ctx;
    FILE *f = fopen(interrupt_path, "r");
    if (f == NULL) {
        return 0;
    }
    int n = 0;
    if (fscanf(f, "%d", &n) != 1) {
        n = 0;
    }
    fclose(f);
    if (n > 0) {
        f = fopen(interrupt_path, "w");
        fprintf(f, "%d\n", n - 1);
        fclose(f);
        return 0;
    }
    remove(interrupt_path);
    return 1;
}

static void *h_alloc(void *ctx, size_t n) {
    (void)ctx;
    return malloc(n);
}

static void h_free(void *ctx, void *p) {
    (void)ctx;
    free(p);
}

static void h_out(void *ctx, int stream, const char *p, size_t n) {
    (void)ctx;
    fwrite(p, 1, n, stream == 2 ? stderr : stdout);
    fflush(stream == 2 ? stderr : stdout);
}

static char *dup_bytes(const char *p, size_t n) {
    char *q = malloc(n + 1);
    memcpy(q, p, n);
    q[n] = 0;
    return q;
}

static char uart[256];
static size_t uart_n;

static int h_vread(void *ctx, const char *path, char **p, size_t *n) {
    (void)ctx;
    const char *text = NULL;
    if (!strcmp(path, "/proc/1/status")) {
        text = "name: shell\npid: 1\nkind: shell\nstate: running\n";
    } else if (!strcmp(path, "/dev/uart1")) {
        *p = dup_bytes(uart, uart_n);
        *n = uart_n;
        uart_n = 0;
        return 0;
    }
    if (text == NULL) {
        return ENOENT;
    }
    *n = strlen(text);
    *p = dup_bytes(text, *n);
    return 0;
}

static int h_vwrite(void *ctx, const char *path, const char *p, size_t n) {
    (void)ctx;
    if (strcmp(path, "/dev/uart1")) {
        return EACCES;
    }
    if (uart_n + n > sizeof uart) {
        n = sizeof uart - uart_n;
    }
    memcpy(uart + uart_n, p, n);
    uart_n += n;
    return 0;
}

static int h_vlist(void *ctx, const char *dir, moy_sh_ent_fn fn, void *arg) {
    (void)ctx;
    if (!strcmp(dir, "/dev")) {
        return fn(arg, "uart1", 0, 0);
    }
    if (!strcmp(dir, "/proc")) {
        return fn(arg, "1", 1, 0);
    }
    if (!strcmp(dir, "/proc/1")) {
        return fn(arg, "status", 0, 0);
    }
    if (!strcmp(dir, "/bin")) {
        static const char *const names[] = { "count", "daemon", "fail", "hello", "py" };
        for (int i = 0; i < 5; i++) {
            int r = fn(arg, names[i], 0, 0);
            if (r) {
                return r;
            }
        }
        return 0;
    }
    return ENOENT;
}

static int h_lookup(void *ctx, const char *name) {
    (void)ctx;
    return !strcmp(name, "hello") || !strcmp(name, "count") || !strcmp(name, "fail") ||
           !strcmp(name, "daemon") || !strcmp(name, "py");
}

static int h_spawn(void *ctx, moy_sh_t *s, int argc, char **argv, int bg, int *pid) {
    (void)ctx;
    const char *name = argv[0];
    const char *slash = strchr(name, '/');
    if (slash != NULL) {
        if (strncmp(name, "moybyte.demo/", 13) != 0) {
            return MOY_SH_NOT_FOUND;
        }
        name = slash + 1;
    }
    if (!strcmp(name, "hello")) {
        char line[256];
        int n = snprintf(line, sizeof line, "hello:");
        for (int i = 1; i < argc; i++) {
            n += snprintf(line + n, sizeof line - n, " [%s]", argv[i]);
        }
        line[n++] = '\n';
        moy_sh_fd_write(s, 1, line, (size_t)n);
        return 0;
    }
    if (!strcmp(name, "count")) {
        char buf[64];
        long got, lines = 0;
        while ((got = moy_sh_fd_read(s, 0, buf, sizeof buf)) > 0) {
            for (long i = 0; i < got; i++) {
                lines += buf[i] == '\n';
            }
            moy_sh_fd_write(s, 1, buf, (size_t)got);
        }
        int n = snprintf(buf, sizeof buf, "count: %ld lines\n", lines);
        moy_sh_fd_write(s, 2, buf, (size_t)n);
        return 0;
    }
    if (!strcmp(name, "fail")) {
        return 3;
    }
    if (!strcmp(name, "daemon")) {
        if (!bg) {
            return 0;
        }
        bg_alive = 1;
        *pid = 40;
        return 0;
    }
    if (!strcmp(name, "py")) {
        return MOY_SH_NEEDS_VM;
    }
    return MOY_SH_NOT_FOUND;
}

static int h_status(void *ctx, int pid, int *code) {
    (void)ctx;
    if (pid == 40) {
        if (bg_alive) {
            return 0;
        }
        *code = bg_signal;              // its wait status: killed by the signal
        return 1;
    }
    if (pid == 1 || pid == 7) {
        return 0;
    }
    return -1;
}

static int h_kill(void *ctx, int pid, int sig) {
    (void)ctx;
    if (pid == 40 && bg_alive) {
        if (sig != 0 && sig != SIGCONT) {
            bg_alive = 0;
            bg_signal = sig;
        }
        return 0;
    }
    return pid == 1 || pid == 7 ? EPERM : ESRCH;
}

static int h_procs(void *ctx, moy_sh_ps_fn fn, void *arg) {
    (void)ctx;
    moy_sh_ps_t rows[3] = {
        { 1, 0, "shell", "running", "console", 0 },
        { 7, 1, "service", "background", "achievements", 0 },
        { 40, 1, "command", "background", "daemon", 0 },
    };
    for (int i = 0; i < (bg_alive ? 3 : 2); i++) {
        int r = fn(arg, &rows[i]);
        if (r) {
            return r;
        }
    }
    return 0;
}

static const char *const roles[] = { "notify", "perm", "python", "service", NULL };

static int h_role(void *ctx, moy_sh_t *s, int argc, char **argv) {
    (void)ctx;
    if (!strcmp(argv[0], "service")) {
        return MOY_SH_NEEDS_VM;
    }
    char line[256];
    int n = snprintf(line, sizeof line, "role %s:", argv[0]);
    for (int i = 1; i < argc; i++) {
        n += snprintf(line + n, sizeof line - n, " %s", argv[i]);
    }
    line[n++] = '\n';
    moy_sh_fd_write(s, 1, line, (size_t)n);
    if (!strcmp(argv[0], "python")) {
        char buf[64];
        long got;
        while ((got = moy_sh_fd_read(s, 0, buf, sizeof buf)) > 0) {
            moy_sh_fd_write(s, 1, buf, (size_t)got);
        }
    }
    return 0;
}

static int h_mem(void *ctx, uint32_t out[6]) {
    (void)ctx;
    uint32_t m[6] = { 512000, 100000, 8388608, 4000000, 2000000, 1500000 };
    memcpy(out, m, sizeof m);
    return 0;
}

static int h_df(void *ctx, const char *path, uint64_t *total, uint64_t *free) {
    (void)ctx;
    (void)path;
    *total = 1048576;
    *free = 524288;
    return 0;
}

static void h_sleep(void *ctx, int ms) {
    (void)ctx;
    struct timespec ts = { ms / 1000, (ms % 1000) * 1000000L };
    nanosleep(&ts, NULL);
}

static int h_log(void *ctx, moy_sh_t *s, int fd, const char *name, int follow) {
    (void)ctx;
    (void)follow;
    moy_log_cursor_t c = { 0, 0 };
    moy_log_rec_t r;
    char text[512], line[640];
    while (moy_log_read(&c, &r, text, sizeof text)) {
        const char *who = moy_log_name(r.pid);
        if (name != NULL && strstr(who, name) == NULL) {
            continue;
        }
        size_t n = r.len < sizeof text ? r.len : sizeof text;
        // the time varies run to run: the suite reads level, name and text
        int k = snprintf(line, sizeof line, "%s %s/%u %.*s\n", moy_log_level_name(r.level), who,
                         (unsigned)r.pid, (int)n, text);
        moy_sh_fd_write(s, fd, line, (size_t)k);
    }
    return 0;
}

int main(int argc, char **argv) {
    int transcript = 0;
    if (argc > 1 && !strcmp(argv[1], "-t")) {
        transcript = 1;
        argc--;
        argv++;
    }
    if (argc != 3) {
        fprintf(stderr, "usage: sh_main [-t] ROOT SCRIPT\n");
        return 2;
    }
    snprintf(interrupt_path, sizeof interrupt_path, "%s/.interrupt", argv[1]);
    moy_log_init(8192);
    moy_log_say(6, 0, "kernel up");
    uint32_t ach = moy_log_spawn("achievements");
    moy_log_say(4, ach, "a slow callback");
    moy_log_say(6, 1, "desk drawn");

    moy_sh_host_t host = {
        .alloc = h_alloc, .free = h_free, .out = h_out, .poll = h_poll, .root = argv[1],
        .vread = h_vread, .vwrite = h_vwrite, .vlist = h_vlist,
        .lookup = h_lookup, .spawn = h_spawn, .status = h_status, .kill = h_kill,
        .procs = h_procs, .roles = roles, .role = h_role, .mem = h_mem, .df = h_df,
        .reboot = NULL, .sleep_ms = h_sleep, .log = h_log,
    };
    moy_sh_t *s = moy_sh_new(&host, 256 * 1024, "/", "/");
    if (s == NULL) {
        fprintf(stderr, "sh_main: no session\n");
        return 2;
    }
    FILE *f = fopen(argv[2], "r");
    if (f == NULL) {
        fprintf(stderr, "sh_main: %s: %s\n", argv[2], strerror(errno));
        moy_sh_free(s);
        return 2;
    }
    char line[4096];
    int more = 0, r = MOY_SH_DONE;
    char prompt[256];
    while (fgets(line, sizeof line, f) != NULL) {
        if (transcript) {
            moy_sh_prompt(s, more, prompt, sizeof prompt);
            printf("%s%s", prompt, line);
            fflush(stdout);
        }
        r = moy_sh_run(s, line, strlen(line));
        if (r == MOY_SH_EXIT) {
            break;
        }
        more = r == MOY_SH_MORE;
    }
    fclose(f);
    int status = moy_sh_status(s);
    if (more) {
        fprintf(stderr, "sh_main: the script ended inside a command\n");
        status = 2;
    }
    moy_sh_free(s);
    return status;
}
