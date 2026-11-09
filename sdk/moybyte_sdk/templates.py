"""The files `moy app new` writes, per tier (docs/app_api_v1.md section 1's
start-here table): each a program that runs as it is, using what its
manifest asks for and nothing more, so `moy app check` passes it clean."""

PY_APP = '''FRUIT = ["Apple", "Banana", "Cherry", "Date", "Elderberry", "Fig", "Grape"]
cur = 0


def _init():
    global cur
    cur = prefs.get("cur") or 0         # saved across runs and reboots


def _draw():
    global cur
    ui.page(%(title_str)s)
    new = ui.list("Fruit", FRUIT, cur)  # arrows, Enter, a tap or a drag
    if new != cur:
        cur = new
        prefs.set("cur", cur)
'''

LUA_APP = '''FRUIT = {"Apple", "Banana", "Cherry", "Date", "Elderberry", "Fig", "Grape"}
cur = 1

function _init()
  cur = prefs.get("cur") or 1
end

function _draw()
  ui.page(%(title_str)s)
  local new = ui.list("Fruit", FRUIT, cur)
  if new ~= cur then
    cur = new
    prefs.set("cur", cur)
  end
end
'''

# A compiled app: a wasm reactor whose exports are its loop, reaching the
# console through its moybyte.app imports (moybyte/app.h).
WASM_APP = '''#include <stdint.h>

#include "moybyte/app.h"

#define EXPORT(name) __attribute__((export_name(#name)))

static const char TITLE[] = "%(title_c)s";
static const char FRUIT[] = "Apple\\0Banana\\0Cherry\\0Date\\0Elderberry\\0Fig\\0Grape";
static int32_t cur;

EXPORT(_init) void init(void) {
    char json[12];
    int32_t n = prefs_get("cur", 3, json, sizeof json);   // saved across runs
    cur = 0;
    for (int32_t i = 0; i < n && json[i] >= '0' && json[i] <= '9'; i++) {
        cur = cur * 10 + (json[i] - '0');
    }
}

EXPORT(_update) void update(float dt) {
    (void)dt;
}

EXPORT(_draw) void draw(void) {
    int32_t page[4];
    ui_page(TITLE, sizeof TITLE - 1, 0, 0, page, sizeof page);
    int32_t now = ui_list("Fruit", 5, FRUIT, sizeof FRUIT - 1, cur, 0, 0);
    if (now >= 0 && now != cur) {
        char json[12];
        int32_t k = sizeof json;
        int32_t v = now;
        do {
            json[--k] = (char)('0' + v %% 10);
            v /= 10;
        } while (v);
        cur = now;
        prefs_set("cur", 3, json + k, sizeof json - k);
    }
}
'''

# A command: a WASI program or a native module, main and its exit code.
C_COMMAND = '''#include <stdio.h>

int main(int argc, char **argv) {
    printf("hello from %%s\\n", argc > 0 ? argv[0] : "%(title_c)s");
    for (int i = 1; i < argc; i++) {
        printf("  %%s\\n", argv[i]);
    }
    return 0;
}
'''

# A service, Python or Lua: it sleeps until a "ping" arrives and answers its
# sender with a "pong" (docs/app_api_v1.md sections 7 and 8).
PY_SERVICE = '''import moybyte


def moy_start():
    moybyte.on("ping", lambda sender, n: moybyte.send(sender, "pong", n + 1))
'''

LUA_SERVICE = '''function moy_start()
  moybyte.on("ping", function(sender, n)
    moybyte.send(sender, "pong", n + 1)
  end)
end
'''

# A compiled service, WASI or native: main waits on its event descriptor
# and answers each "ping" with a "pong"; end of file is its stop.
C_SERVICE = '''#include <poll.h>
#include <unistd.h>

#include "moybyte/os.h"

int main(void) {
    if (moy_on("ping", 1) < 0) {
        return 1;
    }
    static union {
        moy_evq_ev_t ev;
        char b[sizeof(moy_evq_ev_t) + 256];
    } m;
    for (;;) {
        struct pollfd p = { MOY_EVQ_FD, POLLIN, 0 };
        if (poll(&p, 1, -1) < 0) {
            return 1;
        }
        for (;;) {
            ssize_t n = read(MOY_EVQ_FD, &m, sizeof m);
            if (n == 0) {
                return 0;               // stopped
            }
            if (n < 0) {
                break;                  // nothing more queued
            }
            moy_send(m.ev.app, "pong", "[]");
        }
    }
}
'''
