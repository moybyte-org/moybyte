// moy_ssh: the console's SSH server (docs/os_2026-11/system.md section 3),
// Dropbear inside the kernel's process (README.md).
//
// THE SERVER is one task: it listens, takes one connection at a time and runs
// Dropbear's session for it to the end. Dropbear ends a session by exiting
// its process; here that exit is a jump back to the accept loop
// (moy_ssh_exit_jump), and every allocation the session made goes with it
// (Dropbear's tracking allocator, the epoch the session ran in).
//
// THE SHELL is moy_sh's: a session's `shell` or `exec` request opens a pair
// of connected sockets, Dropbear's channel on one end and the shell's face on
// the other (moy_ssh_face.c on a board, the harness's on the host).
//
// SIGN-IN is a password against a stored hash (moy_ssh_hash): any user name
// is the console's one account.

#ifndef MOY_SSH_H
#define MOY_SSH_H

#include <stddef.h>
#include <stdint.h>

#define MOY_SSH_HASH_ROUNDS 4096
#define MOY_SSH_HASH_MAX 128

typedef struct {
    uint32_t sessions;          // connections served
    uint32_t signed_in;         // sessions that signed in
    uint32_t refused;           // password attempts refused
    size_t heap_now;            // Dropbear's allocations now, bytes
    size_t heap_peak;           // their high-water since start
    size_t session_peak;        // the high-water of the last session alone
    uint32_t handshake_ms;      // accept to the shell's start, last session
    int listening;
} moy_ssh_stats_t;

// Once: the host key (Dropbear's private-key bytes) and the stored password
// hash; 0, or -1 with the reason logged. With no key one is made and its
// bytes put in *made (moy_ssh_calloc's: the caller stores them and frees
// them with moy_ssh_free): the store's files are the VM's, so the caller
// keeps them. Again later: the hash alone is replaced.
int moy_ssh_setup(const uint8_t *key, size_t n, const char *hash, uint8_t **made,
                  size_t *made_n);
// A listening socket on `port`, or -1.
int moy_ssh_listen(int port);
// Serve one connection from `lfd` to its end; 0 when one was served, 1 when
// none came within `wait_ms`, -1 on an error of the listening socket.
int moy_ssh_serve_one(int lfd, int wait_ms);

// The stored form of `password` with `salt` (hex, up to 32 characters):
// `$moy1$SALT$HEX`, SHA-256 iterated MOY_SSH_HASH_ROUNDS times.
int moy_ssh_hash(const char *password, const char *salt, char *out, size_t cap);

void moy_ssh_stats(moy_ssh_stats_t *out);

// Two connected stream sockets (the channel's end, the face's end).
int moy_ssh_pair(int fds[2]);

void *moy_ssh_calloc(size_t n, size_t size);
void moy_ssh_free(void *p);

#endif // MOY_SSH_H
