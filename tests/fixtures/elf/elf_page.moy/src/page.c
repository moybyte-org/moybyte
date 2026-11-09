// page: a native app drawing a page with the toolkit it links
// (sdk/include/moybyte/ui.h). Each frame it shows a label, a toggle and a
// button: the toggle answers its state inline, and a press of the button
// ends the app (0, a quit). tests/fixtures/ui_page/page_mini_py.moy is the
// same page from a Python app, and tests/test_ui_linked.py holds the two to
// the same pixels.
#include <stdint.h>

#include "moybyte/ui.h"

int main(int argc, char **argv) {
    (void)argc;
    (void)argv;
    int32_t on = 1;
    moy_frame_t f;
    while (moy_ui_link_frame(&f)) {
        ui_page("Native page", 11, NULL, 0, NULL, 0);
        ui_label("Hello", 5, NULL, 0);
        on = ui_toggle("Sound", 5, on, NULL, 0);
        int32_t done = ui_button("Done", 4, 1, NULL, 0);
        if (moy_ui_link_show() != 0) {
            return 3;
        }
        if (done > 0) {
            return 0;
        }
    }
    return 0;
}
