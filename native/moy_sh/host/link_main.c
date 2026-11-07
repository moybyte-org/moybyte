// The host harness of a link's face (moy_sh_link.h): the screen with no
// session, its `remote` app at 48 by 8, driven as the page's transport drives
// it, and the app's grid printed after each step (tests/test_moy_sh_link.py
// holds it to tests/moy_sh_link/). Lines with no inks print bare; a line's
// SGR escapes print as \e.

#include <stdio.h>
#include <string.h>

#include "moy_sh_app.h"
#include "moy_sh_link.h"
#include "moy_sh_screen.h"

static moy_sh_screen_t *S;

static void show(const char *step) {
    printf("== %s\n", step);
    moy_term_t *t = moy_sh_app_term(moy_sh_screen_app(S));
    int cols, rows;
    moy_term_size(t, &cols, &rows);
    char line[2048];
    for (int i = 0; i < rows; i++) {
        size_t n = moy_term_line(t, i, line, sizeof line);
        line[n] = 0;
        printf("|");
        for (char *p = line; *p; p++) {
            if (*p == 0x1b) {
                printf("\\e");
            } else {
                putchar(*p);
            }
        }
        printf("\n");
    }
    int want, cols2, rows2, sized;
    want = moy_sh_link_want(&cols2, &rows2, &sized);
    char out[64];
    size_t on = moy_sh_link_out(out, sizeof out);
    printf("want=%d size=%dx%d sized=%d out=%zu\n", want, cols2, rows2, sized, on);
}

static void tick(void) {
    moy_sh_app_tick(moy_sh_screen_app(S), 0);
}

static void keys(const char *k) {
    moy_sh_app_keys(moy_sh_screen_app(S), k, strlen(k));
}

static void in(const char *p) {
    moy_sh_link_in(p, strlen(p));
    tick();
}

int main(void) {
    S = moy_sh_screen_new(48, 8, NULL);
    show("open: it asks to connect");
    moy_sh_link_state(MOY_SH_LINK_OPEN, 0);
    in("files$ ");
    keys("ls");
    show("open: the board's prompt, the keys sent");
    in("ls\r\nnotes.md  pics/\r\nfiles$ ");
    show("the board's output");
    in("top\r\n\x1b[?1049h\x1b[0m\x1b[H\x1b[2Jtop  3 processes\x1b[8;1Hq quit");
    show("a full-screen app on the alternate screen");
    in("\x1b[0m\x1b[?25h\x1b[?1049lfiles$ ");
    show("the app ended: the shell's screen back");
    in("vi\x1b[?1049h\x1b[2Jvi's");
    moy_sh_link_state(MOY_SH_LINK_LOST, 0);
    show("lost under an app: the shell's screen back");
    keys("\r");
    moy_sh_link_state(MOY_SH_LINK_OPEN, 0);
    in("files$ ");
    moy_sh_app_resize(moy_sh_screen_app(S), 40, 8);
    show("a resize goes to the board");
    moy_sh_link_state(MOY_SH_LINK_ENDED, 3);
    show("the shell exited");
    keys("x");
    show("a key while closed sends nothing");
    keys("\r");
    show("Enter asks again");
    moy_sh_link_state(MOY_SH_LINK_LOST, 0);
    moy_sh_link_state(MOY_SH_LINK_UNREACHABLE, 0);
    show("lost, then no answer");
    moy_sh_link_state(MOY_SH_LINK_PIN, 0);
    moy_sh_link_state(MOY_SH_LINK_WAIT, 2400);
    show("the pin refused, then a wait");
    moy_sh_link_state(MOY_SH_LINK_BUSY, 0);
    show("the board's face is SSH's");
    moy_sh_screen_free(S);
    int c, r, z;
    printf("== the Terminal closed: want=%d\n", moy_sh_link_want(&c, &r, &z));
    S = moy_sh_screen_new(48, 4, NULL);
    show("opened again: it asks again");
    moy_sh_link_state(MOY_SH_LINK_NONE, 0);
    moy_sh_screen_free(S);
    S = moy_sh_screen_new(48, 4, NULL);
    show("a page no board serves: no shell, nothing asked");
    moy_sh_screen_free(S);
    return 0;
}
