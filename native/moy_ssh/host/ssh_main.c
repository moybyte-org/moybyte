// The host harness: the server over a TCP port, its face stepped by a
// thread as a board's frame tail steps it, and a stand-in shell that answers
// each line with what it ran. Usage: ssh_main PORT KEYFILE PASSWORD

#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "moy_ssh.h"
#include "moy_ssh_face.h"

typedef struct {
    moy_ssh_face_t *f;
    int status;
} echo_t;

static void *e_open(moy_ssh_face_t *f) {
    echo_t *e = calloc(1, sizeof *e);
    e->f = f;
    return e;
}

static int e_run(void *sh, const char *text, size_t n) {
    echo_t *e = sh;
    if (n >= 4 && strncmp(text, "exit", 4) == 0) {
        return 2;
    }
    if (n >= 4 && strncmp(text, "true", 4) == 0) {
        e->status = 0;                  // a command with no output
        return 0;
    }
    char out[1200];
    int k = snprintf(out, sizeof out, "ran: %.*s", (int)n, text);
    moy_ssh_face_out(e->f, out, (size_t)k);
    e->status = 0;
    return 0;
}

static size_t e_prompt(void *sh, int more, char *out, size_t cap) {
    (void)sh;
    return (size_t)snprintf(out, cap, "%s", more ? "> " : "$ ");
}

static int e_status(void *sh) {
    return ((echo_t *)sh)->status;
}

static void e_cancel(void *sh) {
    (void)sh;
}

static void e_close(void *sh) {
    free(sh);
}

static const moy_ssh_shell_t ECHO = {e_open, e_run, e_prompt, e_status, e_cancel, e_close};

static void *tail(void *arg) {
    (void)arg;
    for (;;) {
        moy_ssh_face_tail();
        usleep(16000);
    }
    return NULL;
}

int main(int argc, char **argv) {
    if (argc != 4) {
        fprintf(stderr, "usage: %s PORT KEYFILE PASSWORD\n", argv[0]);
        return 2;
    }
    char hash[MOY_SSH_HASH_MAX];
    uint8_t key[512], *made = NULL;
    size_t n = 0, made_n = 0;
    FILE *kf = fopen(argv[2], "rb");
    if (kf != NULL) {
        n = fread(key, 1, sizeof key, kf);
        fclose(kf);
    }
    if (moy_ssh_hash(argv[3], "00112233445566778899aabbccddeeff", hash, sizeof hash) != 0
            || moy_ssh_setup(n ? key : NULL, n, hash, &made, &made_n) != 0) {
        return 1;
    }
    if (made != NULL) {
        kf = fopen(argv[2], "wb");
        fwrite(made, 1, made_n, kf);
        fclose(kf);
        moy_ssh_free(made);
    }
    moy_ssh_face_set_shell(&ECHO);
    int lfd = moy_ssh_listen(atoi(argv[1]));
    if (lfd < 0) {
        perror("listen");
        return 1;
    }
    pthread_t t;
    pthread_create(&t, NULL, tail, NULL);
    printf("listening on %s\n", argv[1]);
    fflush(stdout);
    for (;;) {
        if (moy_ssh_serve_one(lfd, 1000) == 0) {
            moy_ssh_stats_t s;
            moy_ssh_stats(&s);
            printf("STATS sessions=%u signed_in=%u refused=%u heap_now=%zu heap_peak=%zu "
                   "session_peak=%zu to_shell_ms=%u\n", s.sessions, s.signed_in, s.refused,
                   s.heap_now, s.heap_peak, s.session_peak, s.handshake_ms);
            fflush(stdout);
        }
    }
}
