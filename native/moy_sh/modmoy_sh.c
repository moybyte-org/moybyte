// moy_sh: the C shell's MicroPython face (native/moy_sh/moy_sh_board.h).
//
//   DOOR              the ROLE door's row the shell's crossings take: the
//                     console's door handler (runtime/shell/sh_door.py) serves it
//   write(fd, data)   a spawned program's or a role command's output, through
//                     the session that called the door (fd 1 stdout, 2 stderr)
//   read(fd, n)       its input (fd 0): up to n bytes, b"" at the end
//   arena()           the serial session's arena bytes in use, None with none
//   line(text)        `text` in the serial session, answered on the dev channel
//                     as the `sh` word answers (and what links the serial
//                     face: the kernel's word reaches it only weakly)
//   interrupted()     Ctrl-C on the face of the running session (a role
//                     command's wait asks it): True, and the rest of the
//                     line is dropped; False outside a run
//   Screen(cols, rows)
//                     the screen face (moy_sh_screen.h): a session and its
//                     grid, taken here and given back by close() (or when
//                     the object is collected):
//     run(text)       what was typed, its newline included: 0 done, 1 more
//                     (show the continuation prompt), 2 the shell exited
//     prompt(more)    PS1, or PS2 after a 1
//     write(text)     into the grid (the face's echo of what was typed)
//     resize(c, r)    False when memory refuses (the grid as it was)
//     gen()           moves with every change of the grid
//     take(into)      into[0..2]: the oldest line's number, the lines the
//                     view shows, the lowest changed since the last take
//     line(i)         the view's line i: str, its inks as SGR escapes
//     cursor(into)    into[0..2]: its view line, its column, 1 shown
//     complete(p)     [names]: the built-ins, the core's and the roles'
//                     starting with p
//     cwd() status() arena()
//     interrupt()     the face's own Ctrl-C; cancel() drops a held `more`
//   Face()            the Terminal's face (moy_sh_face.h), on an image that
//                     carries the toolkit: a session made at its first need,
//                     the prompt's line, the history, completion and the key
//                     rows, drawn through the UI engine; given back by
//                     close(). Its verbs are runtime/host/moy_sh.py's Face's:
//                     close_session open key press input elsewhere draw line
//                     col set_line submit complete recall pick more prompt
//                     take in_row set_in_row cands clear_cands history size
//                     lines status interrupt
//   face_row(desk) tdeck_row()
//                     the key row the input in use gets; the T-Deck's symbols
//
// write and read raise OSError(EBADF) outside a door crossing.

#include <errno.h>
#include <string.h>

#include "py/obj.h"
#include "py/runtime.h"

#include "moy_sh.h"
#include "moy_sh_board.h"
#include "moy_sh_screen.h"

#if __has_include("moy_ui_api.h")
#define MOY_SH_FACE 1
#include "moy_sh_face.h"
// native/moy_ui's module: the context and the sink of a Python frame
moy_ui_ctx_t *moy_ui_mp_ctx(mp_obj_t ctx);
void moy_ui_mp_sink(moy_ui_sink_t *s, mp_obj_t cv, mp_obj_t look);
#endif

static moy_sh_t *running(void) {
    moy_sh_t *s = moy_sh_running();
    if (s == NULL) {
        mp_raise_OSError(EBADF);
    }
    return s;
}

static mp_obj_t sh_write(mp_obj_t fd_in, mp_obj_t data) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(data, &b, MP_BUFFER_READ);
    long r = moy_sh_fd_write(running(), mp_obj_get_int(fd_in), b.buf, b.len);
    if (r < 0) {
        mp_raise_OSError(EIO);
    }
    return MP_OBJ_NEW_SMALL_INT(r);
}
static MP_DEFINE_CONST_FUN_OBJ_2(sh_write_obj, sh_write);

static mp_obj_t sh_read(mp_obj_t fd_in, mp_obj_t n_in) {
    moy_sh_t *s = running();
    mp_int_t n = mp_obj_get_int(n_in);
    if (n < 0) {
        mp_raise_ValueError(NULL);
    }
    vstr_t v;
    vstr_init_len(&v, (size_t)n);
    long r = moy_sh_fd_read(s, mp_obj_get_int(fd_in), v.buf, (size_t)n);
    if (r < 0) {
        vstr_clear(&v);
        mp_raise_OSError(EIO);
    }
    v.len = (size_t)r;
    return mp_obj_new_bytes_from_vstr(&v);
}
static MP_DEFINE_CONST_FUN_OBJ_2(sh_read_obj, sh_read);

static mp_obj_t sh_arena(void) {
    moy_sh_t *s = moy_sh_running();
    return s != NULL ? mp_obj_new_int_from_uint(moy_sh_arena_in_use(s)) : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(sh_arena_obj, sh_arena);

static mp_obj_t sh_line(mp_obj_t text) {
    return mp_obj_new_bool(moy_sh_board_line(mp_obj_str_get_str(text)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(sh_line_obj, sh_line);

static mp_obj_t sh_interrupted(void) {
    moy_sh_t *s = moy_sh_running();
    return mp_obj_new_bool(s != NULL && moy_sh_interrupted(s));
}
static MP_DEFINE_CONST_FUN_OBJ_0(sh_interrupted_obj, sh_interrupted);

// -- the screen face ------------------------------------------------------------

typedef struct {
    mp_obj_base_t base;
    moy_sh_screen_t *f;
} screen_obj_t;

static const mp_obj_type_t screen_type;

static moy_sh_screen_t *face(mp_obj_t self) {
    screen_obj_t *o = MP_OBJ_TO_PTR(self);
    if (o->f == NULL) {
        mp_raise_OSError(EBADF);
    }
    return o->f;
}

static mp_obj_t screen_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw,
                                const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 2, 2, false);
    screen_obj_t *o = mp_obj_malloc_with_finaliser(screen_obj_t, type);
    o->f = moy_sh_screen_new(mp_obj_get_int(args[0]), mp_obj_get_int(args[1]), NULL);
    if (o->f == NULL) {
        mp_raise_OSError(ENOMEM);
    }
    return MP_OBJ_FROM_PTR(o);
}

static mp_obj_t screen_close(mp_obj_t self) {
    screen_obj_t *o = MP_OBJ_TO_PTR(self);
    moy_sh_screen_free(o->f);
    o->f = NULL;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(screen_close_obj, screen_close);

static mp_obj_t screen_run(mp_obj_t self, mp_obj_t text) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(text, &b, MP_BUFFER_READ);
    return MP_OBJ_NEW_SMALL_INT(moy_sh_screen_run(face(self), b.buf, b.len));
}
static MP_DEFINE_CONST_FUN_OBJ_2(screen_run_obj, screen_run);

static mp_obj_t screen_prompt(mp_obj_t self, mp_obj_t more) {
    char buf[256];
    size_t n = moy_sh_prompt(moy_sh_screen_session(face(self)), mp_obj_is_true(more), buf,
                             sizeof buf);
    return mp_obj_new_str(buf, n < sizeof buf ? n : sizeof buf - 1);
}
static MP_DEFINE_CONST_FUN_OBJ_2(screen_prompt_obj, screen_prompt);

static mp_obj_t screen_write(mp_obj_t self, mp_obj_t text) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(text, &b, MP_BUFFER_READ);
    moy_term_write(moy_sh_screen_term(face(self)), b.buf, b.len);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(screen_write_obj, screen_write);

static mp_obj_t screen_resize(mp_obj_t self, mp_obj_t cols, mp_obj_t rows) {
    return mp_obj_new_bool(moy_term_resize(moy_sh_screen_term(face(self)),
                                           mp_obj_get_int(cols), mp_obj_get_int(rows)) == 0);
}
static MP_DEFINE_CONST_FUN_OBJ_3(screen_resize_obj, screen_resize);

static mp_obj_t screen_gen(mp_obj_t self) {
    return MP_OBJ_NEW_SMALL_INT(moy_term_gen(moy_sh_screen_term(face(self))) & 0x3FFFFFFFu);
}
static MP_DEFINE_CONST_FUN_OBJ_1(screen_gen_obj, screen_gen);

static int32_t *words3(mp_obj_t into) {
    mp_buffer_info_t b;
    mp_get_buffer_raise(into, &b, MP_BUFFER_WRITE);
    if (b.len < 3 * sizeof(int32_t) || b.typecode != 'i') {
        mp_raise_ValueError(NULL);
    }
    return b.buf;
}

static mp_obj_t screen_take(mp_obj_t self, mp_obj_t into) {
    int32_t *w = words3(into);
    uint32_t v[3];
    moy_term_take(moy_sh_screen_term(face(self)), v);
    for (int i = 0; i < 3; i++) {
        w[i] = (int32_t)(v[i] & 0x3FFFFFFFu);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(screen_take_obj, screen_take);

static mp_obj_t screen_cursor(mp_obj_t self, mp_obj_t into) {
    int32_t *w = words3(into);
    int c[3];
    moy_term_cursor(moy_sh_screen_term(face(self)), c);
    w[0] = c[0];
    w[1] = c[1];
    w[2] = c[2];
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(screen_cursor_obj, screen_cursor);

static mp_obj_t screen_line(mp_obj_t self, mp_obj_t i) {
    moy_term_t *t = moy_sh_screen_term(face(self));
    int cols, rows;
    moy_term_size(t, &cols, &rows);
    vstr_t v;
    vstr_init_len(&v, (size_t)cols * MOY_TERM_COL_BYTES + 8u);
    v.len = moy_term_line(t, mp_obj_get_int(i), v.buf, v.alloc);
    return mp_obj_new_str_from_vstr(&v);
}
static MP_DEFINE_CONST_FUN_OBJ_2(screen_line_obj, screen_line);

static int complete_one(void *arg, const char *name, int is_dir, uint32_t size) {
    (void)is_dir;
    (void)size;
    mp_obj_list_append(MP_OBJ_FROM_PTR(arg), mp_obj_new_str(name, strlen(name)));
    return 0;
}

static mp_obj_t screen_complete(mp_obj_t self, mp_obj_t prefix) {
    mp_obj_t out = mp_obj_new_list(0, NULL);
    moy_sh_complete(moy_sh_screen_session(face(self)), mp_obj_str_get_str(prefix),
                    complete_one, MP_OBJ_TO_PTR(out));
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_2(screen_complete_obj, screen_complete);

static mp_obj_t screen_cwd(mp_obj_t self) {
    const char *c = moy_sh_cwd(moy_sh_screen_session(face(self)));
    return mp_obj_new_str(c, strlen(c));
}
static MP_DEFINE_CONST_FUN_OBJ_1(screen_cwd_obj, screen_cwd);

static mp_obj_t screen_status(mp_obj_t self) {
    return MP_OBJ_NEW_SMALL_INT(moy_sh_status(moy_sh_screen_session(face(self))));
}
static MP_DEFINE_CONST_FUN_OBJ_1(screen_status_obj, screen_status);

static mp_obj_t screen_arena(mp_obj_t self) {
    return mp_obj_new_int_from_uint(moy_sh_arena_in_use(moy_sh_screen_session(face(self))));
}
static MP_DEFINE_CONST_FUN_OBJ_1(screen_arena_obj, screen_arena);

static mp_obj_t screen_interrupt(mp_obj_t self) {
    moy_sh_screen_interrupt(face(self));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(screen_interrupt_obj, screen_interrupt);

static mp_obj_t screen_cancel(mp_obj_t self) {
    moy_sh_cancel(moy_sh_screen_session(face(self)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(screen_cancel_obj, screen_cancel);

static const mp_rom_map_elem_t screen_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_run), MP_ROM_PTR(&screen_run_obj) },
    { MP_ROM_QSTR(MP_QSTR_prompt), MP_ROM_PTR(&screen_prompt_obj) },
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&screen_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_resize), MP_ROM_PTR(&screen_resize_obj) },
    { MP_ROM_QSTR(MP_QSTR_gen), MP_ROM_PTR(&screen_gen_obj) },
    { MP_ROM_QSTR(MP_QSTR_take), MP_ROM_PTR(&screen_take_obj) },
    { MP_ROM_QSTR(MP_QSTR_line), MP_ROM_PTR(&screen_line_obj) },
    { MP_ROM_QSTR(MP_QSTR_cursor), MP_ROM_PTR(&screen_cursor_obj) },
    { MP_ROM_QSTR(MP_QSTR_complete), MP_ROM_PTR(&screen_complete_obj) },
    { MP_ROM_QSTR(MP_QSTR_cwd), MP_ROM_PTR(&screen_cwd_obj) },
    { MP_ROM_QSTR(MP_QSTR_status), MP_ROM_PTR(&screen_status_obj) },
    { MP_ROM_QSTR(MP_QSTR_arena), MP_ROM_PTR(&screen_arena_obj) },
    { MP_ROM_QSTR(MP_QSTR_interrupt), MP_ROM_PTR(&screen_interrupt_obj) },
    { MP_ROM_QSTR(MP_QSTR_cancel), MP_ROM_PTR(&screen_cancel_obj) },
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&screen_close_obj) },
    { MP_ROM_QSTR(MP_QSTR___del__), MP_ROM_PTR(&screen_close_obj) },
};
static MP_DEFINE_CONST_DICT(screen_locals, screen_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    screen_type, MP_QSTR_Screen, MP_TYPE_FLAG_NONE,
    make_new, screen_make_new,
    locals_dict, &screen_locals
    );

#if MOY_SH_FACE
// -- the Terminal's face -------------------------------------------------------------

typedef struct {
    mp_obj_base_t base;
    moy_sh_face_t *f;
} face_obj_t;

static moy_sh_face_t *tface(mp_obj_t self) {
    face_obj_t *o = MP_OBJ_TO_PTR(self);
    if (o->f == NULL) {
        mp_raise_OSError(EBADF);
    }
    return o->f;
}

static mp_obj_t face_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw,
                              const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 0, 1, false);
    face_obj_t *o = mp_obj_malloc_with_finaliser(face_obj_t, type);
    o->f = moy_sh_face_new(n_args && args[0] != mp_const_none ? mp_obj_str_get_str(args[0])
                                                              : NULL);
    if (o->f == NULL) {
        mp_raise_OSError(ENOMEM);
    }
    return MP_OBJ_FROM_PTR(o);
}

static mp_obj_t face_close(mp_obj_t self) {
    face_obj_t *o = MP_OBJ_TO_PTR(self);
    moy_sh_face_free(o->f);
    o->f = NULL;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_close_obj, face_close);

static mp_obj_t face_close_session(mp_obj_t self) {
    moy_sh_face_close_session(tface(self));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_close_session_obj, face_close_session);

static mp_obj_t face_open(mp_obj_t self) {
    moy_sh_face_open(tface(self));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_open_obj, face_open);

static mp_obj_t face_key(mp_obj_t self, mp_obj_t code) {
    return mp_obj_new_bool(moy_sh_face_key(tface(self), mp_obj_get_int(code)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(face_key_obj, face_key);

static mp_obj_t face_press(mp_obj_t self, mp_obj_t name) {
    moy_sh_face_press(tface(self), mp_obj_str_get_str(name));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(face_press_obj, face_press);

// input(desk, key, repeats, repeat_key, left, right, up, down) -> bits
static mp_obj_t face_input(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    return MP_OBJ_NEW_SMALL_INT(moy_sh_face_input(
        tface(a[0]), mp_obj_is_true(a[1]), mp_obj_get_int(a[2]), mp_obj_get_int(a[3]),
        mp_obj_get_int(a[4]), mp_obj_get_int(a[5]), mp_obj_get_int(a[6]), mp_obj_get_int(a[7]),
        mp_obj_get_int(a[8])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(face_input_obj, 9, 9, face_input);

static mp_obj_t face_elsewhere(mp_obj_t self, mp_obj_t desk) {
    return mp_obj_new_bool(moy_sh_face_elsewhere(tface(self), mp_obj_is_true(desk)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(face_elsewhere_obj, face_elsewhere);

// cancel(desk) -> whether the text app in front took the board's cancel key
static mp_obj_t face_cancel(mp_obj_t self, mp_obj_t desk) {
    return mp_obj_new_bool(moy_sh_face_cancel(tface(self), mp_obj_is_true(desk)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(face_cancel_obj, face_cancel);

// app() -> the text app in front's name, or None
static mp_obj_t face_app(mp_obj_t self) {
    const char *n = moy_sh_face_app(tface(self));
    return n != NULL ? mp_obj_new_str(n, strlen(n)) : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_app_obj, face_app);

static mp_obj_t face_full(mp_obj_t self) {
    return mp_obj_new_bool(moy_sh_face_full(tface(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_full_obj, face_full);

// draw(ctx, cv, look, cx, cy, cw, ch, fs, desk[, bar]) -> bits
static mp_obj_t face_draw(size_t n_args, const mp_obj_t *a) {
    moy_sh_face_t *f = tface(a[0]);
    moy_ui_ctx_t *c = moy_ui_mp_ctx(a[1]);
    moy_ui_sink_t s;
    moy_ui_mp_sink(&s, a[2], a[3]);
    moy_sh_face_frame_t fr = {
        mp_obj_get_int(a[4]), mp_obj_get_int(a[5]), mp_obj_get_int(a[6]), mp_obj_get_int(a[7]),
        mp_obj_get_int(a[8]), mp_obj_is_true(a[9]), NULL, 0,
    };
    if (n_args > 10 && a[10] != mp_const_none) {
        mp_buffer_info_t b;
        mp_get_buffer_raise(a[10], &b, MP_BUFFER_READ);
        fr.bar = b.buf;
        fr.bar_n = (uint32_t)b.len;
    }
    return MP_OBJ_NEW_SMALL_INT(moy_sh_face_draw(f, c, &s, &fr));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(face_draw_obj, 10, 11, face_draw);

static mp_obj_t face_line(mp_obj_t self) {
    const char *t = moy_sh_face_line(tface(self), NULL);
    return mp_obj_new_str(t, strlen(t));
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_line_obj, face_line);

static mp_obj_t face_col(mp_obj_t self) {
    int32_t col;
    moy_sh_face_line(tface(self), &col);
    return MP_OBJ_NEW_SMALL_INT(col);
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_col_obj, face_col);

static mp_obj_t face_set_line(size_t n_args, const mp_obj_t *a) {
    size_t n;
    const char *t = mp_obj_str_get_data(a[1], &n);
    moy_sh_face_set_line(tface(a[0]), t,
                         n_args > 2 && a[2] != mp_const_none ? mp_obj_get_int(a[2]) : (int32_t)n);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(face_set_line_obj, 2, 3, face_set_line);

static mp_obj_t face_submit(mp_obj_t self) {
    moy_sh_face_submit(tface(self));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_submit_obj, face_submit);

static mp_obj_t face_complete(mp_obj_t self) {
    moy_sh_face_complete(tface(self));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_complete_obj, face_complete);

static mp_obj_t face_recall(mp_obj_t self, mp_obj_t d) {
    moy_sh_face_recall(tface(self), mp_obj_get_int(d));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(face_recall_obj, face_recall);

static mp_obj_t face_pick(mp_obj_t self, mp_obj_t i) {
    moy_sh_face_pick(tface(self), mp_obj_get_int(i));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(face_pick_obj, face_pick);

static mp_obj_t face_busy(mp_obj_t self) {
    return mp_obj_new_bool(moy_sh_face_busy(tface(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_busy_obj, face_busy);

static mp_obj_t face_more(mp_obj_t self) {
    return mp_obj_new_bool(moy_sh_face_more(tface(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_more_obj, face_more);

static mp_obj_t face_prompt(mp_obj_t self) {
    char buf[256];
    size_t n = moy_sh_face_prompt(tface(self), buf, sizeof buf);
    return mp_obj_new_str(buf, n);
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_prompt_obj, face_prompt);

static mp_obj_t face_take(mp_obj_t self) {
    return mp_obj_new_bool(moy_sh_face_take(tface(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_take_obj, face_take);

static mp_obj_t face_in_row(mp_obj_t self) {
    return mp_obj_new_bool(moy_sh_face_in_row(tface(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_in_row_obj, face_in_row);

static mp_obj_t face_set_in_row(mp_obj_t self, mp_obj_t on) {
    moy_sh_face_set_in_row(tface(self), mp_obj_is_true(on));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(face_set_in_row_obj, face_set_in_row);

static mp_obj_t face_cands(mp_obj_t self) {
    moy_sh_face_t *f = tface(self);
    int n = moy_sh_face_cands(f);
    mp_obj_t out = mp_obj_new_list(0, NULL);
    for (int i = 0; i < n; i++) {
        const char *t = moy_sh_face_cand(f, i);
        mp_obj_list_append(out, mp_obj_new_str(t, strlen(t)));
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_cands_obj, face_cands);

static mp_obj_t face_clear_cands(mp_obj_t self) {
    moy_sh_face_clear_cands(tface(self));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_clear_cands_obj, face_clear_cands);

static mp_obj_t face_history(mp_obj_t self) {
    moy_sh_face_t *f = tface(self);
    int n = moy_sh_face_history(f, 0, NULL);
    mp_obj_t out = mp_obj_new_list(0, NULL);
    for (int i = 0; i < n; i++) {
        const char *t;
        moy_sh_face_history(f, i, &t);
        mp_obj_list_append(out, mp_obj_new_str(t, strlen(t)));
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_history_obj, face_history);

static mp_obj_t face_size(mp_obj_t self) {
    int32_t c, r;
    moy_sh_face_size(tface(self), &c, &r);
    mp_obj_t t[2] = {MP_OBJ_NEW_SMALL_INT(c), MP_OBJ_NEW_SMALL_INT(r)};
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_size_obj, face_size);

static mp_obj_t face_lines(mp_obj_t self) {
    moy_sh_face_t *f = tface(self);
    int32_t cols, rows;
    moy_sh_face_size(f, &cols, &rows);
    int32_t n = moy_sh_face_lines(f);
    mp_obj_t out = mp_obj_new_list(0, NULL);
    vstr_t v;
    vstr_init(&v, (size_t)cols * MOY_TERM_COL_BYTES + 8u);
    for (int32_t i = 0; i < n; i++) {
        size_t k = moy_sh_face_log_line(f, i, v.buf, v.alloc);
        mp_obj_list_append(out, mp_obj_new_str(v.buf, k));
    }
    vstr_clear(&v);
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_lines_obj, face_lines);

static mp_obj_t face_status(mp_obj_t self) {
    moy_sh_screen_t *scr = moy_sh_face_screen(tface(self));
    return MP_OBJ_NEW_SMALL_INT(scr ? moy_sh_status(moy_sh_screen_session(scr)) : 0);
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_status_obj, face_status);

static mp_obj_t face_interrupt(mp_obj_t self) {
    moy_sh_screen_t *scr = moy_sh_face_screen(tface(self));
    if (scr != NULL) {
        moy_sh_screen_interrupt(scr);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(face_interrupt_obj, face_interrupt);

static const mp_rom_map_elem_t face_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_close_session), MP_ROM_PTR(&face_close_session_obj) },
    { MP_ROM_QSTR(MP_QSTR_open), MP_ROM_PTR(&face_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_key), MP_ROM_PTR(&face_key_obj) },
    { MP_ROM_QSTR(MP_QSTR_press), MP_ROM_PTR(&face_press_obj) },
    { MP_ROM_QSTR(MP_QSTR_input), MP_ROM_PTR(&face_input_obj) },
    { MP_ROM_QSTR(MP_QSTR_elsewhere), MP_ROM_PTR(&face_elsewhere_obj) },
    { MP_ROM_QSTR(MP_QSTR_cancel), MP_ROM_PTR(&face_cancel_obj) },
    { MP_ROM_QSTR(MP_QSTR_app), MP_ROM_PTR(&face_app_obj) },
    { MP_ROM_QSTR(MP_QSTR_full), MP_ROM_PTR(&face_full_obj) },
    { MP_ROM_QSTR(MP_QSTR_draw), MP_ROM_PTR(&face_draw_obj) },
    { MP_ROM_QSTR(MP_QSTR_line), MP_ROM_PTR(&face_line_obj) },
    { MP_ROM_QSTR(MP_QSTR_col), MP_ROM_PTR(&face_col_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_line), MP_ROM_PTR(&face_set_line_obj) },
    { MP_ROM_QSTR(MP_QSTR_submit), MP_ROM_PTR(&face_submit_obj) },
    { MP_ROM_QSTR(MP_QSTR_complete), MP_ROM_PTR(&face_complete_obj) },
    { MP_ROM_QSTR(MP_QSTR_recall), MP_ROM_PTR(&face_recall_obj) },
    { MP_ROM_QSTR(MP_QSTR_pick), MP_ROM_PTR(&face_pick_obj) },
    { MP_ROM_QSTR(MP_QSTR_busy), MP_ROM_PTR(&face_busy_obj) },
    { MP_ROM_QSTR(MP_QSTR_more), MP_ROM_PTR(&face_more_obj) },
    { MP_ROM_QSTR(MP_QSTR_prompt), MP_ROM_PTR(&face_prompt_obj) },
    { MP_ROM_QSTR(MP_QSTR_take), MP_ROM_PTR(&face_take_obj) },
    { MP_ROM_QSTR(MP_QSTR_in_row), MP_ROM_PTR(&face_in_row_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_in_row), MP_ROM_PTR(&face_set_in_row_obj) },
    { MP_ROM_QSTR(MP_QSTR_cands), MP_ROM_PTR(&face_cands_obj) },
    { MP_ROM_QSTR(MP_QSTR_clear_cands), MP_ROM_PTR(&face_clear_cands_obj) },
    { MP_ROM_QSTR(MP_QSTR_history), MP_ROM_PTR(&face_history_obj) },
    { MP_ROM_QSTR(MP_QSTR_size), MP_ROM_PTR(&face_size_obj) },
    { MP_ROM_QSTR(MP_QSTR_lines), MP_ROM_PTR(&face_lines_obj) },
    { MP_ROM_QSTR(MP_QSTR_status), MP_ROM_PTR(&face_status_obj) },
    { MP_ROM_QSTR(MP_QSTR_interrupt), MP_ROM_PTR(&face_interrupt_obj) },
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&face_close_obj) },
    { MP_ROM_QSTR(MP_QSTR___del__), MP_ROM_PTR(&face_close_obj) },
};
static MP_DEFINE_CONST_DICT(face_locals, face_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    face_type, MP_QSTR_Face, MP_TYPE_FLAG_NONE,
    make_new, face_make_new,
    locals_dict, &face_locals
    );

static mp_obj_t sh_face_row(mp_obj_t desk) {
    return MP_OBJ_NEW_SMALL_INT(moy_sh_face_row(mp_obj_is_true(desk)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(sh_face_row_obj, sh_face_row);

static mp_obj_t sh_tdeck_row(void) {
    char buf[64];
    int n = moy_sh_face_tdeck_row(buf, sizeof buf);
    return mp_obj_new_str(buf, (size_t)n);
}
static MP_DEFINE_CONST_FUN_OBJ_0(sh_tdeck_row_obj, sh_tdeck_row);
#endif

static const mp_rom_map_elem_t moy_sh_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_sh) },
    { MP_ROM_QSTR(MP_QSTR_DOOR), MP_ROM_INT(MOY_SH_DOOR) },
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&sh_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&sh_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_arena), MP_ROM_PTR(&sh_arena_obj) },
    { MP_ROM_QSTR(MP_QSTR_line), MP_ROM_PTR(&sh_line_obj) },
    { MP_ROM_QSTR(MP_QSTR_interrupted), MP_ROM_PTR(&sh_interrupted_obj) },
    { MP_ROM_QSTR(MP_QSTR_Screen), MP_ROM_PTR(&screen_type) },
    #if MOY_SH_FACE
    { MP_ROM_QSTR(MP_QSTR_Face), MP_ROM_PTR(&face_type) },
    { MP_ROM_QSTR(MP_QSTR_face_row), MP_ROM_PTR(&sh_face_row_obj) },
    { MP_ROM_QSTR(MP_QSTR_tdeck_row), MP_ROM_PTR(&sh_tdeck_row_obj) },
    #endif
};
static MP_DEFINE_CONST_DICT(moy_sh_globals, moy_sh_globals_table);

const mp_obj_module_t moy_sh_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_sh_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_sh, moy_sh_module);
