// moy_fetch: the `http` device API's driver (docs/os_2026-11/hardware.md
// section 1). An app's GET is a slot the VM opens and a worker serves off the
// VM's thread through the kernel's client (moy_ota.h, its MOY_HTTPC_WORKER
// slot), so a slow server never holds a frame. Each finished request posts a
// NET record on the ring source "http" (moy_input.h): `a` the request's
// handle, `id` its state, `b` its HTTP status, or the negative errno.
//
// An `https` request verifies the server's certificate (moy_ota.h); a build
// that cannot verify one refuses it, a redirect's hop too, with
// -MOY_HTTPC_ETLS (moy_fetch_tls).
// The worker is a task on a board; the host serves a queued request when
// moy_fetch_run is called (the Python face calls it), the same code.
//
// A request belongs to its owner (a pid): moy_fetch_sweep(owner) closes every
// one it left, and a request the worker is serving is closed when it ends.
// Opening, closing and sweeping hold the lease table's lock, so the face on
// the VM's task and a compiled program's handle on its own (moy_fetch_dev.c)
// share the slots; a request is read by its one holder.

#ifndef MOY_FETCH_H
#define MOY_FETCH_H

#include <stddef.h>
#include <stdint.h>

#define MOY_FETCH_SLOTS 4
#define MOY_FETCH_URL_MAX 512
#define MOY_FETCH_CAP_MAX (256u * 1024u)
#define MOY_FETCH_AGENT "moybyte-app"

enum {
    MOY_FETCH_FREE = 0,
    MOY_FETCH_QUEUED,
    MOY_FETCH_RUNNING,
    MOY_FETCH_DONE,                     // a response: its status and body
    MOY_FETCH_FAILED,                   // no response: the errno
    MOY_FETCH_STREAM,                   // a stream's head is in: its status, its body coming
};

// -errno codes, Linux's numbers on every tier.
#define MOY_FETCH_EBADF 9
#define MOY_FETCH_ENOMEM 12
#define MOY_FETCH_EINVAL 22
#define MOY_FETCH_ENOSPC 28

// A GET of `url` for `owner`, its body kept up to `cap` bytes: 0 and the
// handle, or -errno (EINVAL for a URL that does not parse or a cap past
// MOY_FETCH_CAP_MAX, MOY_HTTPC_ETLS for https where moy_fetch_tls is 0,
// ENOSPC with every slot taken).
int moy_fetch_open(uint32_t owner, const char *url, uint32_t cap, uint32_t *h);
// A GET whose body streams through a window of `window` bytes (at most
// MOY_FETCH_CAP_MAX) however long it is: STREAM once its head is in, DONE once
// its body has all come, taken as it comes with moy_fetch_take. The worker
// fills the window and waits while it is full. As moy_fetch_open's errors.
int moy_fetch_stream(uint32_t owner, const char *url, uint32_t window, uint32_t *h);
// Up to `n` of a stream's bytes, the window's oldest, taken: their count (0:
// none in now, or, once DONE, none left), or -EBADF.
int moy_fetch_take(uint32_t h, void *buf, uint32_t n);
// 1 where an https request verifies, 0 where it is refused.
int moy_fetch_tls(void);
// The request's state, its HTTP status (0 until DONE; a stream's from STREAM)
// and body length (a stream's: the bytes in its window now), or the errno of
// a FAILED one: the state, or -EBADF for a stale handle.
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
