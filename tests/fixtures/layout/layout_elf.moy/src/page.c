// The responsive fixture app (docs/app_api_v1.md section 3) as a native app
// drawing with the toolkit it links (sdk/include/moybyte/ui.h):
// layout_py.moy's page, `_layout` the toolkit's to call when the frame's
// layout words move (sdk/src/moyui_link.c).
#include <stdint.h>

#include "moybyte/ui.h"

static int32_t calls;
static char last[40] = "none";
static uint32_t last_n = 4;

// `v` in decimal at `out`: the characters written.
static uint32_t put_int(char *out, int32_t v) {
    char tmp[12];
    uint32_t n = 0, k = 0;
    uint32_t u = v < 0 ? (uint32_t)(-(int64_t)v) : (uint32_t)v;
    do {
        tmp[n++] = (char)('0' + u % 10u);
        u /= 10u;
    } while (u != 0u);
    if (v < 0) {
        out[k++] = '-';
    }
    while (n > 0u) {
        out[k++] = tmp[--n];
    }
    return k;
}

void _layout(int32_t w, int32_t h, int32_t fs) {
    calls++;
    uint32_t k = put_int(last, w);
    last[k++] = ' ';
    k += put_int(last + k, h);
    last[k++] = ' ';
    k += put_int(last + k, fs);
    last_n = k;
}

int main(int argc, char **argv) {
    (void)argc;
    (void)argv;
    char line[24] = "calls ";
    moy_frame_t f;
    while (moy_ui_link_frame(&f)) {
        uint32_t n = 6u + put_int(line + 6, calls);
        ui_page("Layout", 6, NULL, 0, NULL, 0);
        ui_label(line, n, NULL, 0);
        ui_label(last, last_n, NULL, 0);
        ui_button("Go", 2, 1, NULL, 0);
        ui_list("Rows", 4, "one\0two\0three", 13, 1, NULL, 0);
        if (moy_ui_link_show() != 0) {
            return 3;
        }
    }
    return 0;
}
