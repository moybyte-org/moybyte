// A page's frame on a run (moy_uitree.h's uit_run_frame): the engine's walk
// over the RUN face's context and sink (native/moy_ui/moy_ui_run.h), so a Lua
// or compiled app's page draws into the run's canvas as a Python app's page
// draws into its layer.

#include "moy_uitree.h"
#include "moy_ui_run.h"

typedef struct {
    uit_t *e;
    int flags;
} run_frame_t;

static int frame_on_run(void *ud, moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t surf, int32_t face)
{
    const run_frame_t *a = (const run_frame_t *)ud;
    int flags = a->flags & ~UIT_F_DESK;
    if (face & MOYCORE_UI_FACE_DESK)
        flags |= UIT_F_DESK;
    int input = moy_ui_input();
    if (input < 0 || input >= MOY_UI_INPUTS)
        input = UIT_IN_KEYS;
    int fs = s->fs > 1 ? s->fs : 1;
    return uit_frame(a->e, c, s, surf.x, surf.y, surf.w, surf.h, flags, fs, input);
}

int uit_run_frame(uit_t *e, void *target, int flags)
{
    run_frame_t a = {e, flags};
    return moy_ui_run_with(target, frame_on_run, &a);
}
