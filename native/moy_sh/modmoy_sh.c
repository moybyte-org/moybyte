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
//
// write and read raise OSError(EBADF) outside a door crossing.

#include <errno.h>

#include "py/obj.h"
#include "py/runtime.h"

#include "moy_sh.h"
#include "moy_sh_board.h"

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

static const mp_rom_map_elem_t moy_sh_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_sh) },
    { MP_ROM_QSTR(MP_QSTR_DOOR), MP_ROM_INT(MOY_SH_DOOR) },
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&sh_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&sh_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_arena), MP_ROM_PTR(&sh_arena_obj) },
    { MP_ROM_QSTR(MP_QSTR_line), MP_ROM_PTR(&sh_line_obj) },
};
static MP_DEFINE_CONST_DICT(moy_sh_globals, moy_sh_globals_table);

const mp_obj_module_t moy_sh_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_sh_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_sh, moy_sh_module);
