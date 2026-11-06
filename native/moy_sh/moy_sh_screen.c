// moy_sh_screen: the shell's screen face. moy_sh_screen.h has the contract.

#include <stdbool.h>
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

#include "moy_sh_app.h"
#include "moy_sh_board.h"
#include "moy_sh_screen.h"

// The kernel's input table (native/moy_input/moy_input.h): weak, so an image
// or a harness without it links, and its poll then reads no keys.
typedef struct moy_input moy_input_t;
moy_input_t *moy_input_kernel(void) __attribute__((weak));
bool moy_input_holds(const moy_input_t *t, int32_t byte, uint32_t buttons)
    __attribute__((weak));
void moy_input_board_kick(void) __attribute__((weak));

#define CTRL_C 3
#define CANCEL_BUTTONS ((1u << 5) | (1u << 14))   // `b` and `start` (MOY_INPUT_NAMES)
#define POLL_MS 20u
#define YIELD_MS 200u
#define RELEASE_MS 1500u                // the wait for an interrupting key's release

struct moy_sh_screen {
    moy_sh_t *s;
    moy_term_t *t;
    void *(*alloc)(void *ctx, size_t n);
    void (*free)(void *ctx, void *p);
    uint32_t polled, yielded;
    int held;                           // the cancel keys held at the last read
    int by_keys;                        // the run's interrupt was a key press
    int stop;                           // the face's own Ctrl-C
    moy_sh_app_t *app;                  // the text app in front (moy_sh_app.h)
    int ctrl;                           // the input types control keys
    moy_sh_runner_t *runner;
    int ended, result;                  // a parked run's end, not yet taken
    char root[MOY_SH_SCREEN_ROOT];
};

static void screen_out(void *ctx, int stream, const char *p, size_t n) {
    (void)stream;
    moy_sh_screen_t *f = ctx;
    moy_term_write(f->t, p, n);
}

static int keys_held(void) {
    if (moy_input_kernel == NULL || moy_input_holds == NULL) {
        return 0;
    }
    moy_input_t *k = moy_input_kernel();
    if (k == NULL) {
        return 0;
    }
    if (moy_input_board_kick != NULL) {
        moy_input_board_kick();
    }
    return moy_input_holds(k, CTRL_C, CANCEL_BUTTONS) ? 1 : 0;
}

static int screen_poll(void *ctx) {
    moy_sh_screen_t *f = ctx;
    if (f->stop) {
        f->stop = 0;
        return 1;
    }
    uint32_t now = NOW_MS();
    if (now - f->polled < POLL_MS) {
        return 0;
    }
    f->polled = now;
    int held = keys_held();
    int hit = held && !f->held;
    f->held = held;
    f->by_keys |= hit;
    if (now - f->yielded >= YIELD_MS) {
        f->yielded = now;
        moy_sh_board_yield();
    }
    return hit;
}

static void *t_alloc(void *ctx, size_t n) {
    moy_sh_screen_t *f = ctx;
    return f->alloc(NULL, n);
}

static void t_free(void *ctx, void *p) {
    moy_sh_screen_t *f = ctx;
    f->free(NULL, p);
}

static int run_here(moy_sh_screen_t *f, const char *text, size_t n, int parks);

static int run_line(void *ctx, const char *text, size_t n) {
    return run_here(ctx, text, n, 0);
}

static void screen_done(void *ctx, int r) {
    moy_sh_screen_t *f = ctx;
    f->ended = 1;
    f->result = r;
}

static int screen_app(void *ctx, moy_sh_t *s, int argc, char **argv) {
    moy_sh_screen_t *f = ctx;
    if (f->app != NULL) {
        static const char busy[] = ": an app is open\n";
        moy_sh_fd_write(s, 2, argv[0], strlen(argv[0]));
        moy_sh_fd_write(s, 2, busy, sizeof busy - 1);
        return 1;
    }
    if (moy_sh_app_find(argv[0]) == NULL) {
        return MOY_SH_NOT_FOUND;
    }
    int cols, rows, status;
    moy_term_size(f->t, &cols, &rows);
    f->app = moy_sh_app_open(s, cols, rows, f->ctrl, run_line, f, argc, argv, &status);
    return status;
}

static void screen_size(void *ctx, int *cols, int *rows) {
    moy_sh_screen_t *f = ctx;
    moy_term_size(f->t, cols, rows);
}

moy_sh_screen_t *moy_sh_screen_new(int cols, int rows, const char *root) {
    moy_sh_host_t h;
    moy_sh_board_host(&h, NULL, screen_out, screen_poll);
    h.app = screen_app;
    h.size = screen_size;
    moy_sh_screen_t *f = h.alloc(NULL, sizeof *f);
    if (f == NULL) {
        return NULL;
    }
    memset(f, 0, sizeof *f);
    f->alloc = h.alloc;
    f->free = h.free;
    h.ctx = f;
    f->runner = moy_sh_board_runner(&h, screen_done);
    if (root != NULL) {
        size_t n = strlen(root);
        if (n >= sizeof f->root) {
            h.free(NULL, f);
            return NULL;
        }
        memcpy(f->root, root, n + 1);
        h.root = f->root;
    }
    f->t = moy_term_new(cols, rows, rows + MOY_SH_SCREEN_LINES, t_alloc, t_free, f);
    if (f->t == NULL) {
        moy_sh_board_runner_end(f->runner);
        h.free(NULL, f);
        return NULL;
    }
    f->s = moy_sh_new(&h, MOY_SH_ARENA, "/", "/");
    if (f->s == NULL) {
        moy_sh_board_runner_end(f->runner);
        moy_term_free(f->t);
        h.free(NULL, f);
        return NULL;
    }
    moy_sh_board_go_home(f->s, 0);
    return f;
}

void moy_sh_screen_free(moy_sh_screen_t *f) {
    if (f == NULL) {
        return;
    }
    moy_sh_screen_end_app(f);
    moy_sh_board_runner_end(f->runner);
    moy_sh_free(f->s);
    moy_term_free(f->t);
    f->free(NULL, f);
}

int moy_sh_screen_run(moy_sh_screen_t *f, const char *text, size_t n) {
    return run_here(f, text, n, 1);
}

void moy_sh_screen_inline(moy_sh_screen_t *f) {
    moy_sh_board_runner_inline(f->runner);
}

int moy_sh_screen_busy(const moy_sh_screen_t *f) {
    return moy_sh_board_busy(f->s);
}

int moy_sh_screen_ended(moy_sh_screen_t *f, int *r) {
    if (!f->ended) {
        return 0;
    }
    f->ended = 0;
    *r = f->result;
    return 1;
}

static int run_here(moy_sh_screen_t *f, const char *text, size_t n, int parks) {
    if (moy_sh_board_busy(f->s)) {
        return MOY_SH_BUSY;
    }
    f->held = keys_held();
    f->by_keys = 0;
    f->polled = NOW_MS();
    f->yielded = f->polled;
    int r = parks ? moy_sh_board_run(f->s, text, n) : moy_sh_run(f->s, text, n);
    if (r == MOY_SH_BUSY) {
        return r;
    }
    // a key that interrupted is let go before the frame merges again, so its
    // press is no edge there (Mic is the console's key, B its back)
    uint32_t t0 = NOW_MS();
    while (f->by_keys && keys_held() && NOW_MS() - t0 < RELEASE_MS) {
        moy_sh_board_yield();
    }
    return r;
}

void moy_sh_screen_interrupt(moy_sh_screen_t *f) {
    f->stop = 1;
}

moy_sh_t *moy_sh_screen_session(moy_sh_screen_t *f) {
    return f->s;
}

moy_term_t *moy_sh_screen_term(moy_sh_screen_t *f) {
    return f->t;
}

moy_sh_app_t *moy_sh_screen_app(const moy_sh_screen_t *f) {
    return f->app;
}

void moy_sh_screen_end_app(moy_sh_screen_t *f) {
    moy_sh_app_t *a = f->app;
    f->app = NULL;
    moy_sh_app_close(a);
}

void moy_sh_screen_set_ctrl(moy_sh_screen_t *f, int ctrl) {
    f->ctrl = ctrl != 0;
    if (f->app != NULL) {
        moy_sh_app_set_ctrl(f->app, f->ctrl);
    }
}
