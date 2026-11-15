// moy_recovery_floor: the kernel's recovery floor (moy_kernel.c's header has
// when it draws; moy_recovery.h the view it draws): the serial words, the
// floor's screen over the Terminal's face, the choices and the restart into
// them, and the restart into the floor when the console's boot ends unproven.

#include "moy_kernel_int.h"

#define K_POLL_MS        20
#define K_REPORT_MS      5000
#define K_INJECT         160      // the bytes serial's `type` queues for the screen
#define K_START_BIT      14       // `start` in MOY_INPUT_NAMES: the T-Deck's Mic, a text app's cancel

// The floor's choices, as its screen numbers them (moy_sh_floor.h), and the
// serial words that pick them.
enum { K_RETRY = 0, K_SAFE, K_REBOOT, K_REPL };
static const char *const K_WORDS[] = {"retry", "safe", "reboot", "repl"};

static int k_word(const char *w) {
    for (int i = 0; i < (int)(sizeof(K_WORDS) / sizeof(K_WORDS[0])); i++) {
        if (strcmp(w, K_WORDS[i]) == 0) {
            return i;
        }
    }
    return -1;
}

// The console's RX ring, which nothing else reads while the VM is down. The
// ISR takes a packet only into free ring space, so the FIFO is polled again
// whenever the ring has room.
static int k_getc(void) {
    int c = ringbuf_get(&stdin_ringbuf);
    #if MICROPY_HW_ESP_USB_SERIAL_JTAG
    if (c < 0) {
        usb_serial_jtag_poll_rx();
        c = ringbuf_get(&stdin_ringbuf);
    }
    #endif
    return c;
}

// The floor's screen (native/moy_sh/moy_sh_floor.h): the Terminal's face
// under the reason and the choices, drawn through the toolkit's engine and
// fed by the board's C input drivers (native/moy_input/moy_drivers.h). An
// image without the panel (the headless Zero) has a serial floor only.
#if defined(MOY_KERNEL_PANEL) && __has_include("moy_sh_floor.h")
#define K_FLOOR 1
#include "moy_drivers.h"
#include "moy_sh_floor.h"
#include "moy_ui.h"
_Static_assert((int)K_RETRY == (int)MOY_SH_FLOOR_RETRY && (int)K_SAFE == (int)MOY_SH_FLOOR_SAFE
               && (int)K_REBOOT == (int)MOY_SH_FLOOR_REBOOT
               && (int)K_REPL == (int)MOY_SH_FLOOR_REPL,
               "the floor's choices are the screen's");
#else
#define K_FLOOR 0
#endif

typedef struct {
    moy_rgeom_t g;
    moy_rview_t v;
    uint16_t *fb;
    int why;
    int sel;                            // the choice the floor takes on its own
    uint32_t crc;
    bool crc_owed;                      // drawn since the crc was taken
    uint32_t frame_ms;                  // the last drawn frame's cost
    int card;                           // the card's bring-up: 0 mounted, -1 not tried
    const char *card_what;
    bool idle;                          // no input came up: SAFE after the idle time
    char hint[MOY_RV_COLS];
    #if K_FLOOR
    moy_sh_floor_t *fl;
    moy_input_t *t;
    uint32_t touch_src, devch_src;
    moy_touchdev_t *touch;
    moy_kbd_t *kbd;
    moy_ball_t *ball;
    bool click, down;
    int32_t ax, ay;
    char inject[K_INJECT];
    uint16_t in_head, in_n;
    int tap;                            // an injected tap's frames left
    int32_t tap_x, tap_y;
    uint32_t tick;
    #endif
} k_floor_t;

#if K_FLOOR
// The floor's logical pixels onto the panel's framebuffer, turned as
// moy_recovery.h says (the PPA's counter-clockwise convention).
static void k_blit(uint16_t *fb, const uint16_t *px, const moy_rgeom_t *g) {
    int w = g->w, h = g->h, fw = g->fb_w;
    if (g->rot == 0) {
        memcpy(fb, px, (size_t)w * h * 2);
        return;
    }
    for (int y = 0; y < h; y++) {
        const uint16_t *row = px + (size_t)y * w;
        for (int x = 0; x < w; x++) {
            int fx, fy;
            if (g->rot == 90) {
                fx = y;
                fy = w - 1 - x;
            } else if (g->rot == 270) {
                fx = h - 1 - y;
                fy = x;
            } else {
                fx = w - 1 - x;
                fy = h - 1 - y;
            }
            fb[(size_t)fy * fw + fx] = row[x];
        }
    }
}
#endif

static void k_draw(k_floor_t *k) {
    #if K_FLOOR
    if (k->fb == NULL || k->fl == NULL) {
        return;
    }
    k->crc_owed = true;
    k_blit(k->fb, moy_sh_floor_pixels(k->fl), &k->g);
    int e = MOY_KERNEL_PANEL(present)();
    if (e != 0) {
        moy_k_printf("KERNEL recovery present err=0x%x\r\n", e);
    }
    #else
    (void)k;
    #endif
}

static void k_report(k_floor_t *k) {
    #if K_FLOOR
    if (k->crc_owed && k->fl != NULL) {
        k->crc_owed = false;
        k->crc = moy_crash_crc32(0, moy_sh_floor_pixels(k->fl), (size_t)k->g.w * k->g.h * 2);
    }
    #endif
    moy_k_printf("KERNEL recovery reason=%s sel=%s crc=%08x fw=%s\r\n",
             moy_crash_why_name(k->why), moy_recovery_choice_name(k->sel),
             (unsigned)(k->fb ? k->crc : 0), MOY_FW_LABEL);
    for (int i = 0; i < k->v.nlines; i++) {
        moy_k_printf("KERNEL line %d %s\r\n", i, k->v.line[i]);
    }
    #if K_FLOOR
    if (k->fl != NULL) {
        moy_k_printf("KERNEL input %s touch=%s kbd=%s ball=%s frame=%ums\r\n",
                 moy_ui_input() == MOY_UI_INPUT_TDECK ? "tdeck" : "touch",
                 k->touch == NULL ? "none" : k->touch->available ? "up" : "down",
                 k->kbd == NULL ? "none" : k->kbd->available ? "up" : "down",
                 k->ball == NULL ? "none" : "up", (unsigned)k->frame_ms);
        int32_t b[4];
        for (int i = 0; moy_sh_floor_row(k->fl, i, b); i++) {
            moy_k_printf("KERNEL row %d %s %d %d %d %d\r\n", i, moy_sh_floor_word(i),
                     (int)b[0], (int)b[1], (int)b[2], (int)b[3]);
        }
    }
    #endif
    moy_k_printf("KERNEL hint %s\r\n", k->hint);
}

static void k_state(const k_floor_t *k) {
    const moy_crash_rec_t *r = moy_kernel_last_crash();
    moy_k_printf("STATE {\"screen\": \"recovery\", \"recovery\": \"%s\", \"sel\": \"%s\", "
             "\"crash\": ", moy_crash_why_name(k->why), moy_recovery_choice_name(k->sel));
    if (r == NULL) {
        moy_k_out("null}\r\n");
    } else {
        moy_k_printf("{\"kind\": \"%s\", \"task\": \"%s\", \"id\": \"%s\", \"role\": \"%s\", "
                 "\"pc\": %u, \"reset\": \"%s\"}}\r\n", moy_crash_kind_name(r->kind), r->task,
                 r->id, moy_crash_role_name(r->role), (unsigned)r->pc,
                 moy_kernel_reset_name(r->reset));
    }
}

#if defined(MOY_NET_WIFI) && MOY_NET_WIFI
// The floor's `update` word (docs/history/kernel_survival_2026-10.md section 6.3): the
// kernel's updater with no VM. `update` checks this build's channel for this
// board and says what it offers; `update install` installs it and restarts
// into it. A URL after either names another manifest -- typed at the serial,
// the owner's choice as a card's ota.json is, so a signature is checked when
// present and required only of the baked channel. The network is the one the
// WiFi driver keeps (moy_wifi_connect_kept).
#define K_UPDATE_WAIT_MS 20000
#define K_RELEASES "https://github.com/moybyte-org/moybyte/releases/download/"

static void k_update(const char *args) {
    while (*args == ' ') {
        args++;
    }
    int install = strncmp(args, "install", 7) == 0 && (args[7] == '\0' || args[7] == ' ');
    if (install) {
        args += 7;
        while (*args == ' ') {
            args++;
        }
    }
    char url[256];
    int baked = *args == '\0';
    if (baked) {
        snprintf(url, sizeof(url), K_RELEASES "%s/latest-%s.json",
                 strcmp(MOY_FW_CHANNEL, "unstable") == 0 ? "firmware-beta" : "firmware-latest",
                 MOY_FW_BOARD);
    } else {
        snprintf(url, sizeof(url), "%s", args);
    }
    moy_wifi_state_t st;
    moy_wifi_state(&st);
    if (!st.connected) {
        int e = moy_wifi_connect_kept();
        if (e != 0) {
            moy_k_printf("KERNEL update wifi err=0x%x (no kept network)\r\n", e);
            return;
        }
        int64_t t0 = moy_now_us();
        do {
            vTaskDelay(pdMS_TO_TICKS(250));
            moy_wifi_state(&st);
        } while (!st.connected && moy_now_us() - t0 < K_UPDATE_WAIT_MS * 1000LL);
        if (!st.connected) {
            moy_k_printf("KERNEL update wifi offline reason=%u\r\n", (unsigned)st.reason);
            return;
        }
    }
    moy_k_printf("KERNEL update check %s\r\n", url);
    char *text = NULL;
    size_t n = 0;
    int rc = moy_ota_check(url, MOY_FW_BOARD, baked, &text, &n);
    if (rc == MOY_OTA_ABSENT) {
        moy_k_out("KERNEL update none published\r\n");
        return;
    }
    if (rc != MOY_OTA_OK) {
        moy_k_printf("KERNEL update refused: %s\r\n", moy_ota_error());
        return;
    }
    const char *o = moy_json_ws(text, text + n), *oe = moy_json_value(o, text + n, 0);
    const char *v, *ve;
    char murl[512] = "", sha[65] = "", chan[16] = "";
    int64_t ver = 0, size = 0;
    if (moy_json_get(o, oe, "url", &v, &ve) && moy_json_kind(v, ve) == MOY_JSON_STR
        && moy_json_strlen(v, ve) < sizeof(murl)) {
        murl[moy_json_str(v, ve, murl)] = '\0';
    }
    if (moy_json_get(o, oe, "sha256", &v, &ve) && moy_json_kind(v, ve) == MOY_JSON_STR
        && moy_json_strlen(v, ve) < sizeof(sha)) {
        sha[moy_json_str(v, ve, sha)] = '\0';
    }
    if (moy_json_get(o, oe, "channel", &v, &ve) && moy_json_kind(v, ve) == MOY_JSON_STR
        && moy_json_strlen(v, ve) < sizeof(chan)) {
        chan[moy_json_str(v, ve, chan)] = '\0';
    }
    if (moy_json_get(o, oe, "version", &v, &ve)) {
        moy_json_int(v, ve, &ver);
    }
    if (moy_json_get(o, oe, "size", &v, &ve)) {
        moy_json_int(v, ve, &size);
    }
    moy_net_free(text);
    moy_k_printf("KERNEL update offers version=%ld channel=%s size=%ld running=%s\r\n",
             (long)ver, chan, (long)size, MOY_FW_LABEL);
    if (!install) {
        return;
    }
    if (moy_ota_dl_begin(murl, (uint32_t)size, sha, MOY_OTA_SINK_SLOT) != 0) {
        moy_k_printf("KERNEL update refused: %s\r\n", moy_ota_error());
        return;
    }
    uint32_t shown = 0;
    int more;
    while ((more = moy_ota_dl_step(0)) > 0) {
        moy_ota_state_t os;
        moy_ota_state(&os);
        if (os.dl_done - shown >= 262144u) {
            shown = os.dl_done;
            moy_k_printf("KERNEL update %u/%u\r\n", (unsigned)os.dl_done, (unsigned)os.dl_total);
        }
    }
    char label[16];
    if (more < 0 || moy_ota_dl_finish() != 0 || moy_ota_activate(label, sizeof(label)) != 0) {
        moy_k_printf("KERNEL update failed: %s\r\n", moy_ota_error());
        return;
    }
    moy_k_printf("KERNEL update installed into %s; restarting\r\n", label);
    vTaskDelay(pdMS_TO_TICKS(100));
    esp_restart();
}
#endif

static void k_choose(int choice) {
    moy_k_kst.next = (uint8_t)(MOY_BOOT_START + choice);
    moy_k_printf("KERNEL recovery choice=%s\r\n", moy_recovery_choice_name(choice));
    vTaskDelay(pdMS_TO_TICKS(50));
    esp_restart();
}

// Reboot: a restart with the floor's counts started over and nothing
// chosen, so the next boot decides as a power-on would and starts the
// console. The shell's own `reboot` keeps the counts, which would bring a
// floor boot straight back here.
static void k_reboot(void) {
    moy_k_kst.next = MOY_BOOT_NONE;
    moy_k_kst.crashes = 0;
    moy_k_kst.unproven = 0;
    moy_k_out("KERNEL recovery choice=REBOOT\r\n");
    vTaskDelay(pdMS_TO_TICKS(50));
    esp_restart();
}

static void k_act(int choice) {
    switch (choice) {
        case K_RETRY: k_choose(0); break;
        case K_SAFE: k_choose(1); break;
        case K_REPL: k_choose(2); break;
        case K_REBOOT: k_reboot(); break;
        default: break;
    }
}

// The card, brought up from C (moy_store_card_up, native/moy_sd) for the
// shell's commands on screen and over serial: system/ and the carts on it are
// theirs on a boot no VM has run in. On a board whose card rides the panel's
// bus (the T-Deck's SPI2) only once the panel came up. Weak, so an image
// without the shell or the card links.
#if __has_include("moy_sh_board.h")
#include "moy_sh_board.h"
#else
bool moy_sh_board_line(const char *text);
#endif
#pragma weak moy_sh_board_line

static void k_card(k_floor_t *k) {
    #if defined(MOY_HW_SPI2_SD_CS)
    bool bus = k->fb != NULL;
    #else
    bool bus = true;
    #endif
    k->card_what = bus ? "no card" : "the panel";
    k->card = -1;
    if (bus && moy_store_card_up != NULL) {
        k->card = moy_store_card_up(&k->card_what);
    }
}

// `sh LINE` on the floor: LINE in the C shell's serial session, answered as
// the dev channel's `sh` word answers it (moy_devch.c), with the shell's
// commands that need no VM. The first says what became of the card.
static void k_sh(const k_floor_t *k, const char *line, bool *ready) {
    if (moy_sh_board_line == NULL) {
        moy_k_out("SH error no shell\r\n");
        return;
    }
    if (!*ready) {
        *ready = true;
        moy_loop_board_down();          // the shell's lines out through moy_kernel_say
        if (k->card == 0) {
            moy_k_out("KERNEL card mounted\r\n");
        } else {
            moy_k_printf("KERNEL card err=%d at %s\r\n", k->card, k->card_what);
        }
    }
    moy_sh_board_line(line);
}

#if K_FLOOR
#define K_BALL_STEP 2                   // trackball pulses a step (console_input.py's BALL_STEP)
#define K_TAP_FRAMES 3                  // an injected tap: down two frames, then up

// The screen: the face, the board's input on the kernel's table, the hint
// that names it. Where no input comes up, the idle countdown to SAFE stands.
static void k_floor_up(k_floor_t *k) {
    moy_input_t *t = moy_input_kernel();
    k->t = t;
    if (t == NULL) {
        return;
    }
    moy_input_set_text_mode(t, true);
    moy_input_source(t, "devch", 0, &k->devch_src);
    bool any = false;
    int input = MOY_UI_INPUT_TOUCH;
    #if defined(MOY_INPUT_KBD_TDECK)
    vTaskDelay(pdMS_TO_TICKS(400));     // the keyboard's C3 boots on the panel's rail
    input = MOY_UI_INPUT_TDECK;
    #endif
    if (moy_input_board_kbd(&k->kbd)) {
        moy_kbd_game_mode(k->kbd, false);
        any |= k->kbd->available;
    }
    bool click;
    if (moy_input_board_ball(&k->ball, &click)) {
        k->click = click;
        any = true;
    }
    if (moy_input_board_touch(&k->touch, MOY_KERNEL_PANEL_W, MOY_KERNEL_PANEL_H)) {
        moy_input_source(t, "touch", 0, &k->touch_src);
        any |= k->touch->available;
    }
    #if defined(MOY_KERNEL_IDLE_SAFE_MS)
    k->idle = !any;
    #else
    (void)any;
    #endif
    if (k->idle) {
        k->sel = 1;
    } else if (input == MOY_UI_INPUT_TDECK) {
        moy_str_copy(k->hint, sizeof(k->hint), MOY_S_RECOVERY_HINT_TYPE);
    } else {
        moy_str_copy(k->hint, sizeof(k->hint), MOY_S_RECOVERY_HINT_TOUCH);
    }
    int desk = MOY_KERNEL_PANEL_W >= 800;
    moy_sh_floor_geom_t fg = {MOY_KERNEL_PANEL_W, MOY_KERNEL_PANEL_H, desk, desk ? 2 : 1,
                              MOY_KERNEL_PANEL_SWAP, input};
    k->fl = moy_sh_floor_new(&fg, NULL);
    if (k->fl == NULL) {
        moy_k_out("KERNEL recovery screen no memory\r\n");
        return;
    }
    const char *lines[MOY_RV_LINES];
    for (int i = 0; i < k->v.nlines; i++) {
        lines[i] = k->v.line[i];
    }
    moy_sh_floor_say(k->fl, lines, k->v.nlines, k->hint);
}

// A frame's input from the kernel's table: the drivers' pass, the touch
// polled into its source, what serial injected into the dev channel's, the
// merge, and the trackball's pulses as steps and its click as Enter.
static bool k_floor_input(k_floor_t *k, uint32_t now, moy_sh_floor_in_t *in) {
    memset(in, 0, sizeof(*in));
    moy_input_t *t = k->t;
    moy_input_board_kick();
    if (k->touch != NULL && k->touch->available) {
        moy_touch_pt_t pt;
        moy_touchdev_poll(k->touch, now, &pt);
        moy_input_point(t, k->touch_src, pt.x, pt.y, pt.down, pt.edge, k->touch->held.fresh);
    }
    k->tick++;
    if (k->in_n > 0) {
        moy_input_key(t, k->devch_src, (uint8_t)k->inject[k->in_head]);
        k->in_head = (uint16_t)((k->in_head + 1) % K_INJECT);
        k->in_n--;
    }
    if (k->tap > 0) {
        moy_input_point(t, k->devch_src, k->tap_x, k->tap_y, k->tap > 1, k->tap == K_TAP_FRAMES,
                        true);
        k->tap--;
    }
    moy_input_begin_frame(t, now);
    in->key = moy_input_typed(moy_input_key_event(t));
    int32_t ev = 0;
    in->repeats = (int32_t)moy_input_key_repeats(t, &ev);
    in->repeat_key = in->repeats ? moy_input_typed(ev) : 0;
    uint32_t held, pressed;
    moy_input_masks(t, MOY_INPUT_UNION, &held, &pressed);
    in->cancel = (int32_t)((pressed >> K_START_BIT) & 1u);
    int32_t *dir[4] = {&in->left, &in->right, &in->up, &in->down};
    for (int b = 0; b < 4; b++) {
        *dir[b] = (int32_t)(((pressed >> b) & 1u) + moy_input_repeats(t, (uint8_t)b));
    }
    if (k->ball != NULL) {
        uint32_t c[4];
        bool click;
        moy_input_board_ball(&k->ball, &click);
        moy_ball_take(k->ball, c);
        if (click && !k->click && in->key == 0) {
            in->key = '\r';
        }
        k->click = click;
        k->ax += (int32_t)c[3] - (int32_t)c[2];
        k->ay += (int32_t)c[1] - (int32_t)c[0];
        int32_t ax = k->ax < 0 ? -k->ax : k->ax, ay = k->ay < 0 ? -k->ay : k->ay;
        if (ax >= ay && ax >= K_BALL_STEP) {
            *(k->ax > 0 ? &in->right : &in->left) += ax / K_BALL_STEP;
            k->ax = k->ax % K_BALL_STEP;
            k->ay = 0;
        } else if (ay >= K_BALL_STEP) {
            *(k->ay > 0 ? &in->down : &in->up) += ay / K_BALL_STEP;
            k->ay = k->ay % K_BALL_STEP;
            k->ax = 0;
        }
    }
    moy_input_sample_t sp;
    moy_input_sample(t, &sp);
    if (sp.down) {
        in->ptr = 3 | (sp.edge ? 4 : 0);
    } else if (k->down) {
        in->ptr = 1;
    }
    k->down = sp.down;
    in->x = sp.x;
    in->y = sp.y;
    return in->key || in->repeats || in->left || in->right || in->up || in->down || in->ptr;
}

// Serial's input for the screen: `type TEXT` its bytes, `key N` one key by
// its code (13 Enter, 9 Tab, 3 Ctrl-C), `tap X Y` a press and release at a
// logical point, each through the dev channel's source on the kernel's table.
static void k_inject(k_floor_t *k, const char *s, size_t n) {
    for (size_t i = 0; i < n && k->in_n < K_INJECT; i++) {
        k->inject[(k->in_head + k->in_n) % K_INJECT] = s[i];
        k->in_n++;
    }
}

// `screen`: what the face shows, a line each, and the prompt's line.
static void k_screen(k_floor_t *k) {
    if (k->fl == NULL) {
        return;
    }
    moy_sh_face_t *f = moy_sh_floor_face(k->fl);
    char b[160];
    int32_t n = moy_sh_face_lines(f);
    for (int32_t i = 0; i < n; i++) {
        moy_sh_face_log_line(f, i, b, sizeof(b));
        moy_k_printf("KERNEL screen %d %s\r\n", (int)i, b);
    }
    int32_t col;
    moy_k_printf("KERNEL screen line %s\r\n", moy_sh_face_line(f, &col));
}
#endif

// The floor: never returns. Runs on the VM task's stack, with no VM.
void moy_kernel_recovery(int why) {
    // PSRAM, which no VM has taken on a floor's boot: the view and the
    // screen's state are kilobytes that internal SRAM would hold from boot on
    // every console.
    k_floor_t *k = moy_kalloc(sizeof(*k), MOY_KMEM_PSRAM | MOY_KMEM_INTERNAL_OK | MOY_KMEM_ZERO);
    if (k == NULL) {
        moy_k_out("KERNEL recovery no memory\r\n");
        vTaskDelay(pdMS_TO_TICKS(1000));
        esp_restart();
    }
    k->why = why;
    k->card = -1;
    moy_str_copy(k->hint, sizeof(k->hint), MOY_S_RECOVERY_HINT_SERIAL);
    moy_recovery_view(&k->v, why, moy_kernel_last_crash(), MOY_FW_LABEL, k->hint, 0);
    #if defined(MOY_KERNEL_IDLE_SAFE_MS)
    k->idle = true;
    k->sel = 1;
    #endif
    #ifdef MOY_KERNEL_PANEL
    moy_k_geom(&k->g);
    int e = MOY_KERNEL_PANEL(init)();
    if (e == 0) {
        k->fb = MOY_KERNEL_PANEL(fb)();
    } else {
        moy_k_printf("KERNEL recovery panel err=0x%x\r\n", e);
    }
    #endif
    k_card(k);
    #if K_FLOOR
    k_floor_up(k);
    #endif
    #ifdef MOY_KERNEL_PANEL
    MOY_KERNEL_PANEL(backlight)(255);
    #endif

    char line[208];
    size_t n = 0;
    bool sh_ready = false, report = true;
    int64_t t0 = moy_now_us(), last_report = t0, last_input = t0, last_frame = t0;
    int shown_left = -1;
    for (;;) {
        int64_t now = moy_now_us();
        int c;
        while ((c = k_getc()) >= 0) {
            last_input = now;
            if (c == '\r' || c == '\n') {
                line[n] = '\0';
                int choice = n > 0 ? k_word(line) : -1;
                if (choice >= 0) {
                    k_act(choice);
                } else if (strcmp(line, "state") == 0) {
                    k_state(k);
                } else if (strncmp(line, "sh", 2) == 0 && (line[2] == '\0' || line[2] == ' ')) {
                    k_sh(k, line[2] ? line + 3 : line + 2, &sh_ready);
                    now = last_input = moy_now_us();
                #if K_FLOOR
                } else if (strncmp(line, "type ", 5) == 0) {
                    k_inject(k, line + 5, n - 5);
                } else if (strncmp(line, "key ", 4) == 0) {
                    char b = (char)atoi(line + 4);
                    k_inject(k, &b, 1);
                } else if (strncmp(line, "tap ", 4) == 0) {
                    char *end;
                    k->tap_x = (int32_t)strtol(line + 4, &end, 10);
                    k->tap_y = (int32_t)strtol(end, NULL, 10);
                    k->tap = K_TAP_FRAMES;
                } else if (strcmp(line, "screen") == 0) {
                    k_screen(k);
                #endif
                #if defined(MOY_NET_WIFI) && MOY_NET_WIFI
                } else if (strncmp(line, "update", 6) == 0
                           && (line[6] == '\0' || line[6] == ' ')) {
                    k_update(line + 6);
                    last_input = moy_now_us();
                #endif
                } else if (n > 0) {
                    report = true;
                }
                n = 0;
            } else if (c >= 0x20 && c < 0x7F && n + 1 < sizeof(line)) {
                line[n++] = (char)c;
            }
        }
        #if K_FLOOR
        if (k->fl != NULL) {
            moy_sh_floor_in_t in;
            if (k_floor_input(k, (uint32_t)(now / 1000), &in)) {
                last_input = now;
            }
            in.dt = (int32_t)((now - last_frame) / 1000);
            last_frame = now;
            int choice;
            if (moy_sh_floor_frame(k->fl, &in, &choice)) {
                k_draw(k);
                k->frame_ms = (uint32_t)((moy_now_us() - now) / 1000);
                if (report) {
                    report = false;
                    last_report = now;
                    k_report(k);
                }
            }
            if (choice != MOY_SH_FLOOR_NONE) {
                k_act(choice);
            }
            now = moy_now_us();
        }
        #endif
        #if defined(MOY_KERNEL_IDLE_SAFE_MS)
        if (k->idle) {
            int left = (int)((MOY_KERNEL_IDLE_SAFE_MS * 1000LL - (now - last_input) + 999999)
                             / 1000000);
            if (left <= 0) {
                moy_k_printf("KERNEL recovery idle %ds\r\n", MOY_KERNEL_IDLE_SAFE_MS / 1000);
                k_choose(1);
            }
            if (left != shown_left) {
                shown_left = left;
                moy_str_fmt(k->hint, sizeof(k->hint), MOY_S_RECOVERY_SAFE_IN, left);
                #if K_FLOOR
                if (k->fl != NULL) {
                    const char *lines[MOY_RV_LINES];
                    for (int i = 0; i < k->v.nlines; i++) {
                        lines[i] = k->v.line[i];
                    }
                    moy_sh_floor_say(k->fl, lines, k->v.nlines, k->hint);
                }
                #endif
            }
        }
        #endif
        (void)shown_left;
        if (report || now - last_report >= K_REPORT_MS * 1000LL) {
            report = false;
            last_report = now;
            k_report(k);
        }
        vTaskDelay(pdMS_TO_TICKS(K_POLL_MS));
    }
}

// The console's boot ended before it proved itself: record it and restart into
// the floor. Never returns.
void moy_kernel_vm_failed(void) {
    moy_kernel_record_vm(moy_k_fail_what[0] ? moy_k_fail_what : "the console's boot ended");
    moy_k_kst.next = MOY_BOOT_RECOVERY;
    moy_k_kst.reason = MOY_WHY_VM_START;
    printf("KERNEL vm_start failed: %s\n", moy_k_krec.what);
    fflush(stdout);
    vTaskDelay(pdMS_TO_TICKS(50));
    esp_restart();
}
