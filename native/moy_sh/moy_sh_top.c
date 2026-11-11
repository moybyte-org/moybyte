// moy_sh_top: `top`, the processes a second at a time, a full-screen app
// (moy_sh_app.h) over the rows `ps` reads (the host table's `procs`: the
// kernel's, and across the ROLE door the console's process table with the
// task manager's CPU and memory, runtime/shell/sh_door.py) and the figures
// `free` reads (`mem`). One implementation on every face: with the VM
// stopped (the recovery floor) it shows what `ps` shows there, the kernel,
// the console stopped and the run in front.
//
// THE KEYS: up and down pick a row, k ends the one picked (SIGTERM, as
// `kill` sends and the task manager's Stop does), q, Esc, ^C or ^X quits.
// The face's key row holds Kill and Quit for an input that has no letters.

#include <stdio.h>
#include <string.h>

#include "moy_sh_app.h"

#define TOP_ROWS 32                     // the processes it shows at most
#define TOP_EVERY_MS 1000u
#define SIGTERM_ 15
#define TITLE "\x1b[0;36m"
#define DIM "\x1b[2m"
#define PLAIN "\x1b[0m"

typedef struct {
    int pid;
    int32_t cpu;
    uint32_t mem;
    char kind[10], state[12], name[28];
} top_row_t;

typedef struct {
    top_row_t row[TOP_ROWS];
    int n, sel, sel_pid;
    uint32_t mem[6];
    int have_mem;
    uint32_t last;
    int fetched;
    char msg[64];
} top_t;

static void copy(char *d, size_t cap, const char *s) {
    snprintf(d, cap, "%s", s != NULL ? s : "");
}

static int take(void *arg, const moy_sh_ps_t *p) {
    top_t *t = arg;
    if (t->n >= TOP_ROWS) {
        return 1;
    }
    top_row_t *r = &t->row[t->n++];
    r->pid = p->pid;
    r->cpu = p->cpu;
    r->mem = p->mem;
    copy(r->kind, sizeof r->kind, p->kind);
    copy(r->state, sizeof r->state, p->state);
    copy(r->name, sizeof r->name, p->name);
    return 0;
}

static void fetch(moy_sh_app_t *a, top_t *t) {
    const moy_sh_host_t *h = moy_sh_host(moy_sh_app_session(a));
    t->n = 0;
    if (h->procs != NULL) {
        h->procs(h->ctx, take, t);
    }
    t->have_mem = h->mem != NULL && h->mem(h->ctx, t->mem) == 0;
    t->sel = 0;
    for (int i = 0; i < t->n; i++) {
        if (t->row[i].pid == t->sel_pid) {
            t->sel = i;
        }
    }
    t->sel_pid = t->n ? t->row[t->sel].pid : 0;
    t->fetched = 1;
}

static void kb(char *out, size_t cap, uint32_t free, uint32_t total) {
    unsigned f = (unsigned)free, t = (unsigned)total;
    if (t >= 4u * 1024u * 1024u) {
        snprintf(out, cap, "%u.%u/%u.%u MB", f >> 20, ((f >> 10) & 1023u) * 10u / 1024u,
                 t >> 20, ((t >> 10) & 1023u) * 10u / 1024u);
    } else {
        snprintf(out, cap, "%u/%u KB", f >> 10, t >> 10);
    }
}

static void top_draw(moy_sh_app_t *a, void *st) {
    top_t *t = st;
    int cols, rows;
    moy_sh_app_size(a, &cols, &rows);
    if (!t->fetched) {
        fetch(a, t);
    }
    char b[160];
    moy_sh_app_write(a, "\x1b[0m\x1b[2J\x1b[H" TITLE);
    snprintf(b, sizeof b, "top  %d processes", t->n);
    moy_sh_app_clip(a, b, cols);
    moy_sh_app_write(a, PLAIN "\x1b[2;1H" DIM);
    if (t->have_mem) {
        char in[32], ps[32];
        kb(in, sizeof in, t->mem[1], t->mem[0]);
        kb(ps, sizeof ps, t->mem[3], t->mem[2]);
        snprintf(b, sizeof b, "free  internal %s  psram %s", in, t->mem[2] ? ps : "-");
        moy_sh_app_clip(a, b, cols);
    }
    int wide = cols >= 52;              // a narrow screen leaves KIND out
    moy_sh_app_write(a, "\x1b[3;1H");
    moy_sh_app_clip(a, wide ? "  PID KIND     STATE       CPU     MEM NAME" : "  PID STATE     CPU    MEM NAME",
         cols);
    moy_sh_app_write(a, PLAIN);
    int h = rows - 4;
    int first = t->sel >= h ? t->sel - h + 1 : 0;
    for (int i = first; i < t->n && i - first < h; i++) {
        const top_row_t *r = &t->row[i];
        char cpu[16], mem[16];
        if (r->cpu < 0) {
            copy(cpu, sizeof cpu, "-");
        } else {
            snprintf(cpu, sizeof cpu, "%d.%d%%", (int)(r->cpu / 10), (int)(r->cpu % 10));
        }
        if (r->mem == 0) {
            copy(mem, sizeof mem, "-");
        } else {
            snprintf(mem, sizeof mem, "%uK", (unsigned)((r->mem + 1023u) >> 10));
        }
        snprintf(b, sizeof b, "\x1b[%d;1H%s", 4 + i - first, i == t->sel ? TITLE : "");
        moy_sh_app_write(a, b);
        if (wide) {
            snprintf(b, sizeof b, "%c%4d %-8s %-10s %5s %7s %s", i == t->sel ? '>' : ' ', r->pid,
                     r->kind, r->state, cpu, mem, r->name);
        } else {
            snprintf(b, sizeof b, "%c%4d %-8.8s %5s %6s %s", i == t->sel ? '>' : ' ', r->pid,
                     r->state, cpu, mem, r->name);
        }
        moy_sh_app_clip(a, b, cols);
        moy_sh_app_write(a, PLAIN);
    }
    snprintf(b, sizeof b, "\x1b[%d;1H" DIM, rows);
    moy_sh_app_write(a, b);
    if (t->msg[0]) {
        moy_sh_app_clip(a, t->msg, cols);
    } else if (moy_sh_app_ctrl(a)) {
        moy_sh_app_clip(a, "up/down pick  k end it  q quit", cols);
    }
    moy_sh_app_write(a, PLAIN "\x1b[?25l");
}

static int top_open(moy_sh_app_t *a, void *st, int argc, char **argv) {
    (void)argv;
    top_t *t = st;
    if (argc > 1) {
        moy_sh_fd_write(moy_sh_app_session(a), 2, "usage: top\n", 11);
        return 2;
    }
    t->sel_pid = -1;
    return 0;
}

static int top_tick(moy_sh_app_t *a, void *st, uint32_t ms) {
    top_t *t = st;
    if (t->last != 0 && ms - t->last < TOP_EVERY_MS) {
        return 0;
    }
    t->last = ms ? ms : 1;
    fetch(a, t);
    top_draw(a, t);
    return 1;
}

static void end_one(moy_sh_app_t *a, top_t *t) {
    if (t->n == 0) {
        return;
    }
    const moy_sh_host_t *h = moy_sh_host(moy_sh_app_session(a));
    int pid = t->row[t->sel].pid;
    int r = h->kill != NULL ? h->kill(h->ctx, pid, SIGTERM_) : 38;
    if (r == 0) {
        snprintf(t->msg, sizeof t->msg, "ended %d", pid);
    } else {
        snprintf(t->msg, sizeof t->msg, "%d: %s", pid, r == 1 ? "not permitted"
                 : r == 3 ? "no such process" : "cannot be ended");
    }
    fetch(a, t);
}

static int top_keys(moy_sh_app_t *a, void *st, const char *p, size_t n) {
    top_t *t = st;
    const char *end = p + n;
    int k;
    while ((k = moy_sh_app_key(&p, end)) >= 0) {
        t->msg[0] = 0;
        switch (k) {
            case 'q':
            case 'Q':
            case 0x1b:
            case 3:
            case 0x18:
                return MOY_SH_APP_END;
            case MOY_SH_KEY_UP:
            case MOY_SH_KEY_DOWN:
                t->sel += k == MOY_SH_KEY_UP ? -1 : 1;
                t->sel = t->sel < 0 ? 0 : t->sel >= t->n ? (t->n ? t->n - 1 : 0) : t->sel;
                t->sel_pid = t->n ? t->row[t->sel].pid : 0;
                break;
            case 'k':
            case 'K':
                end_one(a, t);
                break;
            default:
                break;
        }
    }
    top_draw(a, t);
    return MOY_SH_APP_GO;
}

static const char *const TOP_KEYS[] = {"Kill", "k", "Quit", "q", NULL};

const moy_sh_app_ops_t moy_sh_top_app = {
    .name = "top",
    .state = sizeof(top_t),
    .full = 1,
    .text = 0,
    .keys = TOP_KEYS,
    .open = top_open,
    .keys_in = top_keys,
    .draw = top_draw,
    .tick = top_tick,
};
