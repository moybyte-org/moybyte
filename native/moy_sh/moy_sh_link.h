// moy_sh_link: the Terminal's face where the shell is another console's (the
// browser console a board serves: docs/os_2026-11/system.md section 3).
//
// Built instead of the session (moy_sh.c and dash) into a console that has
// no shell of its own: the screen (moy_sh_screen.h) has no session behind
// it, and its one app is `remote`, full-screen and always in front, so the
// face, its grid, its look and its key rows are the Terminal's. The far
// shell's bytes write the app's grid as any VT100 app's output does, and the
// keys the face turns its input into go back unread. The transport is the
// page's (the worker's WebSocket to the board's GET /term, through web_boot's
// term_*): between frames it reads what the app wants and the keys typed, and
// hands in the bytes that came and what became of the connection, which the
// app says under the far shell's last screen, drawn on the next frame.

#ifndef MOY_SH_LINK_H
#define MOY_SH_LINK_H

#include <stddef.h>

// What became of the connection (moy_sh_link_state).
enum {
    MOY_SH_LINK_IDLE,                   // nothing asked
    MOY_SH_LINK_CONNECTING,
    MOY_SH_LINK_OPEN,
    MOY_SH_LINK_ENDED,                  // the far shell exited (n: its status)
    MOY_SH_LINK_LOST,                   // the socket closed under a session
    MOY_SH_LINK_UNREACHABLE,            // no answer from the board
    MOY_SH_LINK_PIN,                    // the board refused the page's pin
    MOY_SH_LINK_WAIT,                   // too many wrong pins: n ms to wait
    MOY_SH_LINK_BUSY,                   // another transport holds the board's face
    MOY_SH_LINK_NONE,                   // no board serves a shell to this page
};

// What the app wants of the transport.
enum {
    MOY_SH_LINK_WANT_NONE,
    MOY_SH_LINK_WANT_OPEN,              // connect, at the grid's size
    MOY_SH_LINK_WANT_CLOSE,             // the Terminal let go of the session
};

// MOY_SH_LINK_WANT_*, once; the grid's size, and whether it changed.
int moy_sh_link_want(int *cols, int *rows, int *sized);
// The keys typed since the last call, up to `cap`.
size_t moy_sh_link_out(char *buf, size_t cap);
// The far shell's bytes: how many were taken (the rest waits for a tick).
size_t moy_sh_link_in(const char *p, size_t n);
// What became of the connection: MOY_SH_LINK_*, said in the app's grid.
void moy_sh_link_state(int state, int n);

#endif // MOY_SH_LINK_H
