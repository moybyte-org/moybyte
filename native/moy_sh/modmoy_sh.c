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
//
// write and read raise OSError(EBADF) outside a door crossing.

#include <errno.h>
#include <string.h>

#include "py/obj.h"
#include "py/runtime.h"

#include "moy_sh.h"
#include "moy_sh_board.h"
#include "moy_sh_screen.h"

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
    vstr_init_len(&v, (size_t)cols * 9u + 8u);
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

static const mp_rom_map_elem_t moy_sh_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_sh) },
    { MP_ROM_QSTR(MP_QSTR_DOOR), MP_ROM_INT(MOY_SH_DOOR) },
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&sh_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&sh_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_arena), MP_ROM_PTR(&sh_arena_obj) },
    { MP_ROM_QSTR(MP_QSTR_line), MP_ROM_PTR(&sh_line_obj) },
    { MP_ROM_QSTR(MP_QSTR_interrupted), MP_ROM_PTR(&sh_interrupted_obj) },
    { MP_ROM_QSTR(MP_QSTR_Screen), MP_ROM_PTR(&screen_type) },
};
static MP_DEFINE_CONST_DICT(moy_sh_globals, moy_sh_globals_table);

const mp_obj_module_t moy_sh_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_sh_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_sh, moy_sh_module);
