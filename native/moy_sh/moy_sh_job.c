// moy_sh_job: a shell line run with no face. moy_sh_job.h has the contract.

#include <stdint.h>
#include <string.h>

#if __has_include("esp_timer.h")
#include "esp_timer.h"
#define NOW_MS() ((uint32_t)(esp_timer_get_time() / 1000))
#else
#include <time.h>
static uint32_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000 + ts.tv_nsec / 1000000);
}
#define NOW_MS() now_ms()
#endif

#include "moy_log.h"
#include "moy_sh_board.h"
#include "moy_sh_job.h"

#define YIELD_MS 200u
#define ROOT_MAX 256u
#define UNENDED_STATUS 2

struct moy_sh_job {
    moy_sh_t *s;
    void *(*alloc)(void *ctx, size_t n);
    void (*free)(void *ctx, void *p);
    moy_sh_runner_t *runner;
    uint32_t pid;
    uint32_t yielded;
    int stop;                           // the job's Ctrl-C
    int ended, result;                  // a parked run's end, not yet taken
    char *text;                         // the line a run holds, until it ends
    size_t held[2];                     // stdout's and stderr's bytes not yet a record
    char line[2][MOY_SH_JOB_LINE];
    char root[ROOT_MAX];
};

static void put(moy_sh_job_t *j, int k) {
    if (j->held[k] > 0) {
        moy_log_write(k ? MOY_LOG_ERR : MOY_LOG_INFO, j->pid, j->line[k], j->held[k]);
        j->held[k] = 0;
    }
}

static void job_out(void *ctx, int stream, const char *p, size_t n) {
    moy_sh_job_t *j = ctx;
    int k = stream == 2 ? 1 : 0;
    for (size_t i = 0; i < n; i++) {
        if (p[i] == '\n') {
            put(j, k);
            continue;
        }
        if (j->held[k] == MOY_SH_JOB_LINE) {
            put(j, k);
        }
        j->line[k][j->held[k]++] = p[i];
    }
}

static int job_poll(void *ctx) {
    moy_sh_job_t *j = ctx;
    if (j->stop) {
        j->stop = 0;
        return 1;
    }
    uint32_t now = NOW_MS();
    if (now - j->yielded >= YIELD_MS) {
        j->yielded = now;
        moy_sh_board_yield();
    }
    return 0;
}

static int status_of(moy_sh_job_t *j, int r) {
    if (r == MOY_SH_MORE) {
        static const char said[] = "the line does not end";
        moy_sh_cancel(j->s);
        put(j, 0);
        put(j, 1);
        moy_log_write(MOY_LOG_ERR, j->pid, said, sizeof said - 1);
        return UNENDED_STATUS;
    }
    return moy_sh_status(j->s);
}

static void job_done(void *ctx, int r) {
    moy_sh_job_t *j = ctx;
    j->ended = 1;
    j->result = r;
}

moy_sh_job_t *moy_sh_job_new(uint32_t pid, const char *root) {
    moy_sh_host_t h;
    moy_sh_board_host(&h, NULL, job_out, job_poll);
    moy_sh_job_t *j = h.alloc(NULL, sizeof *j);
    if (j == NULL) {
        return NULL;
    }
    memset(j, 0, sizeof *j);
    j->alloc = h.alloc;
    j->free = h.free;
    j->pid = pid;
    h.ctx = j;
    if (root != NULL) {
        size_t n = strlen(root);
        if (n >= sizeof j->root) {
            h.free(NULL, j);
            return NULL;
        }
        memcpy(j->root, root, n + 1);
        h.root = j->root;
    }
    j->runner = moy_sh_board_runner(&h, job_done);
    j->s = moy_sh_new(&h, MOY_SH_ARENA, "/", "/");
    if (j->s == NULL) {
        moy_sh_board_runner_end(j->runner);
        h.free(NULL, j);
        return NULL;
    }
    moy_sh_board_go_home(j->s, 0);
    return j;
}

void moy_sh_job_free(moy_sh_job_t *j) {
    if (j == NULL) {
        return;
    }
    moy_sh_board_runner_end(j->runner);
    put(j, 0);
    put(j, 1);
    moy_sh_free(j->s);
    if (j->text != NULL) {
        j->free(NULL, j->text);
    }
    j->free(NULL, j);
}

int moy_sh_job_run(moy_sh_job_t *j, const char *text, size_t n) {
    if (moy_sh_board_busy(j->s)) {
        return MOY_SH_JOB_PARKED;
    }
    if (j->text != NULL) {
        j->free(NULL, j->text);
    }
    j->text = j->alloc(NULL, n + 1);
    if (j->text == NULL) {
        return UNENDED_STATUS;
    }
    memcpy(j->text, text, n);
    j->text[n] = '\0';
    j->ended = 0;
    j->yielded = NOW_MS();
    int r = moy_sh_board_run(j->s, j->text, n);
    if (r == MOY_SH_BUSY) {
        return MOY_SH_JOB_PARKED;
    }
    return status_of(j, r);
}

int moy_sh_job_ended(moy_sh_job_t *j, int *status) {
    if (!j->ended) {
        return 0;
    }
    j->ended = 0;
    *status = status_of(j, j->result);
    return 1;
}

void moy_sh_job_interrupt(moy_sh_job_t *j) {
    j->stop = 1;
}
