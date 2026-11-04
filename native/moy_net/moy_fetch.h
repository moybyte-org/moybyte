// moy_fetch: the `http` device API's driver (docs/os_2026-11/hardware.md
// section 1). An app's GET is a slot the VM opens and a worker serves off the
// VM's thread through the kernel's client (moy_ota.h, its MOY_HTTPC_WORKER
// slot), so a slow server never holds a frame. Each finished request posts a
// NET record on the ring source "http" (moy_input.h): `a` the request's
// handle, `id` its state, `b` its HTTP status, or the negative errno.
//
// An app's request is MOY_FETCH_APP: `https` is refused, a redirect's hop
// too, with -MOY_HTTPC_ETLS, the rule until TLS verifies for apps (P3.4).
// The worker is a task on a board; the host serves a queued request when
// moy_fetch_run is called (the Python face calls it), the same code.
//
// A request belongs to its owner (a pid): moy_fetch_sweep(owner) closes every
// one it left, and a request the worker is serving is closed when it ends.

#ifndef MOY_FETCH_H
#define MOY_FETCH_H

#include <stddef.h>
#include <stdint.h>

#define MOY_FETCH_SLOTS 4
#define MOY_FETCH_URL_MAX 512
#define MOY_FETCH_CAP_MAX (256u * 1024u)
#define MOY_FETCH_AGENT "moybyte-app"
#define MOY_FETCH_APP 0x01u             // an app's: no https

enum {
    MOY_FETCH_FREE = 0,
    MOY_FETCH_QUEUED,
    MOY_FETCH_RUNNING,
    MOY_FETCH_DONE,                     // a response: its status and body
    MOY_FETCH_FAILED,                   // no response: the errno
};

// -errno codes, Linux's numbers on every tier.
#define MOY_FETCH_EBADF 9
#define MOY_FETCH_ENOMEM 12
#define MOY_FETCH_EINVAL 22
#define MOY_FETCH_ENOSPC 28

// A GET of `url` for `owner`, its body kept up to `cap` bytes: 0 and the
// handle, or -errno (EINVAL for a URL that does not parse or a cap past
// MOY_FETCH_CAP_MAX, MOY_HTTPC_ETLS for an app's https, ENOSPC with every
// slot taken).
int moy_fetch_open(uint32_t owner, const char *url, uint32_t cap, uint32_t flags, uint32_t *h);
// The request's state, its HTTP status (0 until DONE) and body length, or
// the errno of a FAILED one: the state, or -EBADF for a stale handle.
int moy_fetch_state(uint32_t h, int *status, uint32_t *n, int *err);
// Up to `n` body bytes from offset `off`: their count, or -EBADF.
int moy_fetch_read(uint32_t h, uint32_t off, void *buf, uint32_t n);
// The request ended: its body freed, its handle stale. One the worker is
// serving is ended when the worker returns.
int moy_fetch_close(uint32_t h);
// Every request `owner` holds closed: how many.
uint32_t moy_fetch_sweep(uint32_t owner);
// How many requests are open, `owner`'s (0: everyone's).
uint32_t moy_fetch_count(uint32_t owner);
// The "http" ring source's handle once a request opened it, else 0.
uint32_t moy_fetch_source(void);
// Serve every queued request now (the host's worker; a board's task does it).
void moy_fetch_run(void);
// Whether this image has the worker (a board with the API, or the host).
int moy_fetch_present(void);
// Every slot freed and the source forgotten (a test).
void moy_fetch_reset(void);

#endif // MOY_FETCH_H
