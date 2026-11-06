// moy_ssh_sh: the shell an SSH face runs on a board -- a moy_sh session over
// the board's host table (native/moy_sh/moy_sh_board.h), its terminal the
// face's socket, its Ctrl-C the face's poll.

#include <stdlib.h>
#include <string.h>

#include "moy_sh.h"
#include "moy_sh_board.h"
#include "moy_ssh.h"
#include "moy_ssh_face.h"

typedef struct {
    moy_ssh_face_t *f;
    moy_sh_t *s;
    int polls;
} sh_t;

static void sh_out(void *ctx, int stream, const char *p, size_t n) {
    (void)stream;
    moy_ssh_face_out(((sh_t *)ctx)->f, p, n);
}

// The face's Ctrl-C, and now and then a tick for the tasks below the loop's.
static int sh_poll(void *ctx) {
    sh_t *h = ctx;
    if ((++h->polls & 63) == 0) {
        moy_sh_board_yield();
    }
    return moy_ssh_face_poll(h->f);
}

static void *sh_open(moy_ssh_face_t *f) {
    sh_t *h = moy_ssh_calloc(1, sizeof *h);
    if (h == NULL) {
        return NULL;
    }
    h->f = f;
    moy_sh_host_t host;
    moy_sh_board_host(&host, h, sh_out, sh_poll);
    h->s = moy_sh_new(&host, MOY_SH_ARENA, "/", "/");
    if (h->s == NULL) {
        moy_ssh_free(h);
        return NULL;
    }
    return h;
}

static int sh_run(void *sh, const char *text, size_t n) {
    return moy_sh_run(((sh_t *)sh)->s, text, n);
}

static size_t sh_prompt(void *sh, int more, char *out, size_t cap) {
    return moy_sh_prompt(((sh_t *)sh)->s, more, out, cap);
}

static int sh_status(void *sh) {
    return moy_sh_status(((sh_t *)sh)->s);
}

static void sh_cancel(void *sh) {
    moy_sh_cancel(((sh_t *)sh)->s);
}

static void sh_close(void *sh) {
    sh_t *h = sh;
    moy_sh_free(h->s);
    moy_ssh_free(h);
}

const moy_ssh_shell_t moy_ssh_moy_sh = {sh_open, sh_run, sh_prompt, sh_status, sh_cancel, sh_close};
