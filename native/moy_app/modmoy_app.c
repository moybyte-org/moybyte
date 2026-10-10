// moy_app's MicroPython binding: the app ABI's C rows (moy_app.h) as role
// objects over a grant, the grant policy, and the state they read.
//
//   App(settings)        a console's own state, prefs written into `settings`
//                        (a moy_spine.Settings; None: prefs answer ABSENT)
//   kernel(fresh)        a view of the kernel's (moy_app_kernel), whose rows
//                        are the kernel's settings; `fresh` ends RUN grants
//   app.grant(id, roles, kind=None, ns=None, run=False) -> handle
//   app.end(h), app.count(), app.damage_take(), app.damage_drop(), app.counts()
//   app.surface_write(canvas, w, h, fs, cs, windowed, bar_h, ox=0, oy=0)
//   app.bind_pointer(pointer), app.theme_write(name, variant, colors),
//   app.theme_write_skin(skin), app.serve(role, server),
//   app.store_bind(root, readable, writable, no_store)   the shell's writes
//   app.files_end_all(), app.copy_gen()   the store's sessions and copy
//   app.served()         {"role.verb": calls} of the rows served in Python
//   app.grant_id(h)      the id a grant was made for
//   app.role(g, row, args, cap) -> (answer, bytes)   the ROLE door
//                        (moy_app_role): table row `row` with its packed
//                        arguments, the answer and the first bytes of `cap`
//                        it wrote
//   table()              the role table's rows, "role.verb", in its order
//   door_bind(on)        the shell rows' door: the kernel's ROLE upcall
//                        (moy_loop_role), or none
//   Damage(app, g), Surface(app, g), Theme(app, g), Files(app, g),
//   Prefs(app, g), Artwork(app, g), Clipboard(app, g)    the roles with C rows
//   Carts, Nav, Notify, Wallpaper, Install (app, g)   the roles served in
//                        Python, every row the console's but the wallpaper's
//                        copy
//   tokens()             the token vocabulary, in role-id order
//   policy(perms), manifest_error(perms), id_for(id, title)   the grant policy
//
// A role object's methods are the table's rows and nothing else: no attribute
// is a property, and each call of a C row is the C function and its counter. A
// row the table serves in Python (its "shell" rows) checks that the grant holds
// the role, counts the call and calls the server the console registered for
// the role (app.serve) as `server.<verb>(grant, *args, **kw)`.
// What Python keeps as objects stays objects: surface.canvas() is the canvas
// the shell wrote into the grant's row, surface.pointer() a named tuple of the
// row's five numbers, theme.colors() a dict built from the token table at its
// generation and handed out until the generation moves.
// What a verb answers in the ABI's codes becomes what the Python roles
// answered: prefs.get the caller's default for ABSENT, clipboard.kind "text"
// or None, clipboard.put_text False for text over CLIP_MAX (the old text
// kept), a storage row `(value, err)`. A refused call raises: ValueError for
// STALE, DENIED and BAD, MemoryError for NOMEM, OSError(ENOSPC) for a full
// grant table; a storage row answers its store's failures as `err`.

#include <string.h>

#include "py/mperrno.h"
#include "py/objstr.h"
#include "py/objtuple.h"
#include "py/runtime.h"

#include "moy_app.h"

// native/moy_spine's binding: the allocator kernel state takes, the C rows a
// Settings holds, and what its saver raised.
const moy_htab_mem_t *moy_spine_mem(void);
moy_settings_t *moy_spine_settings_of(mp_obj_t o);
void moy_spine_settings_raise(void);

// -- errors -------------------------------------------------------------------------

static MP_NORETURN void raise_rc(int rc) {
    if (rc == MOY_APP_NOMEM) {
        mp_raise_type(&mp_type_MemoryError);
    }
    if (rc == MOY_APP_FULL) {
        mp_raise_OSError(MP_ENOSPC);
    }
    if (rc == MOY_APP_STALE) {
        mp_raise_ValueError(MP_ERROR_TEXT("a grant that ended"));
    }
    if (rc == MOY_APP_DENIED) {
        mp_raise_ValueError(MP_ERROR_TEXT("a role the grant does not hold"));
    }
    mp_raise_ValueError(MP_ERROR_TEXT("an argument the role refuses"));
}

static void check(int rc) {
    if (rc != MOY_APP_OK) {
        raise_rc(rc);
    }
}

static const char *str_of(mp_obj_t o, size_t *n) {
    if (!mp_obj_is_str(o)) {
        mp_raise_TypeError(MP_ERROR_TEXT("a str"));
    }
    return mp_obj_str_get_data(o, n);
}

static mp_obj_t json_call(qstr name, mp_obj_t arg) {
    mp_obj_t json = mp_import_name(MP_QSTR_json, mp_const_none, MP_OBJ_NEW_SMALL_INT(0));
    return mp_call_function_1(mp_load_attr(json, name), arg);
}

// -- App -------------------------------------------------------------------------------

typedef struct {
    mp_obj_base_t base;
    moy_appabi_t *a;
    mp_obj_t rows;          // the Settings prefs write into, kept alive
    mp_obj_t pointer;       // the Pointer the rows are bound to, kept alive
    mp_obj_t servers;       // {role: the console's server for its other rows}
    mp_obj_t served;        // {role: {verb: calls}}: those rows' counters
    mp_obj_t no_store;      // the `err` a storage row answers with no store
    mp_obj_t colors;        // the colour dict of generation colors_gen, or NULL
    uint32_t colors_gen;
    bool own;               // false: a view of the kernel's
    mp_obj_t canvases[MOY_GRANT_SLOTS];     // per grant slot: the canvas object
} app_obj_t;

static const mp_obj_type_t app_type;

static moy_appabi_t *app_of(mp_obj_t o) {
    if (!mp_obj_is_type(o, &app_type)) {
        mp_raise_TypeError(MP_ERROR_TEXT("an App"));
    }
    return ((app_obj_t *)MP_OBJ_TO_PTR(o))->a;
}

static void app_init(app_obj_t *o) {
    o->rows = mp_const_none;
    o->pointer = mp_const_none;
    o->servers = mp_obj_new_dict(0);
    o->served = mp_obj_new_dict(0);
    o->no_store = mp_const_none;
    o->colors = MP_OBJ_NULL;
    o->colors_gen = 0u;
    for (size_t i = 0; i < MOY_GRANT_SLOTS; i++) {
        o->canvases[i] = mp_const_none;
    }
}

static mp_obj_t app_make_new(const mp_obj_type_t *type, size_t n_args,
                             size_t n_kw, const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 1, 1, false);
    moy_settings_t *rows = NULL;
    if (args[0] != mp_const_none) {
        rows = moy_spine_settings_of(args[0]);
        if (rows == NULL) {
            mp_raise_TypeError(MP_ERROR_TEXT("prefs are a moy_spine.Settings"));
        }
    }
    app_obj_t *o = mp_obj_malloc_with_finaliser(app_obj_t, type);
    app_init(o);
    o->rows = args[0];
    o->own = true;
    o->a = moy_app_new(moy_spine_mem(), rows);
    if (o->a == NULL) {
        mp_raise_type(&mp_type_MemoryError);
    }
    return MP_OBJ_FROM_PTR(o);
}

static mp_obj_t app_del(mp_obj_t self_in) {
    app_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (self->own) {
        moy_app_free(self->a);
    }
    self->a = NULL;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(app_del_obj, app_del);

// The role mask an iterable of role names asks for; ValueError for a name the
// table does not have.
static uint32_t roles_mask(mp_obj_t roles) {
    uint32_t mask = 0u;
    mp_obj_t it = mp_getiter(roles, NULL), r;
    while ((r = mp_iternext(it)) != MP_OBJ_STOP_ITERATION) {
        size_t n;
        const char *s = str_of(r, &n);
        int i = 0;
        while (i < MOY_ROLE_N && !(strlen(moy_app_role_name(i)) == n
                                   && memcmp(moy_app_role_name(i), s, n) == 0)) {
            i++;
        }
        if (i == MOY_ROLE_N) {
            mp_raise_ValueError(MP_ERROR_TEXT("unknown app context role"));
        }
        mask |= 1u << i;
    }
    return mask;
}

static mp_obj_t app_grant(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_id, ARG_roles, ARG_kind, ARG_ns, ARG_run, ARG_owner };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_app_id, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_roles, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_kind, MP_ARG_OBJ, { .u_obj = mp_const_none } },
        { MP_QSTR_ns, MP_ARG_OBJ, { .u_obj = mp_const_none } },
        { MP_QSTR_run, MP_ARG_OBJ, { .u_obj = mp_const_false } },
        { MP_QSTR_owner, MP_ARG_INT, { .u_int = 0 } },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args - 1, pos_args + 1, kw_args, MP_ARRAY_SIZE(allowed),
                     allowed, args);
    moy_appabi_t *a = app_of(pos_args[0]);
    size_t n, ns_n;
    const char *id = str_of(args[ARG_id].u_obj, &n);
    const char *ns = id;
    ns_n = n;
    if (args[ARG_ns].u_obj != mp_const_none) {
        ns = str_of(args[ARG_ns].u_obj, &ns_n);
    }
    int kind = -1;
    if (args[ARG_kind].u_obj != mp_const_none) {
        size_t kn;
        const char *k = str_of(args[ARG_kind].u_obj, &kn);
        kind = moy_app_kind_of(k, kn);
        if (kind < 0) {
            mp_raise_ValueError(MP_ERROR_TEXT("unknown files kind"));
        }
    }
    uint32_t h;
    check(moy_app_grant(a, id, n,
                        mp_obj_is_true(args[ARG_run].u_obj) ? MOY_GRANT_RUN : MOY_GRANT_SHIPPED,
                        roles_mask(args[ARG_roles].u_obj), kind, ns, ns_n,
                        (uint32_t)args[ARG_owner].u_int, &h));
    return mp_obj_new_int_from_uint(h);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(app_grant_obj, 3, app_grant);

static uint32_t grant_arg(mp_obj_t h) {
    mp_int_t v = mp_obj_get_int(h);
    return v > 0 && v < ((mp_int_t)1 << 30) ? (uint32_t)v : 0u;
}

static mp_obj_t app_end(mp_obj_t self, mp_obj_t h) {
    return mp_obj_new_bool(moy_app_end(app_of(self), grant_arg(h)) == MOY_APP_OK);
}
static MP_DEFINE_CONST_FUN_OBJ_2(app_end_obj, app_end);

// grant_id(h) -> the id grant `h` was made for; ValueError for one that ended.
static mp_obj_t app_grant_id(mp_obj_t self, mp_obj_t h) {
    const moy_grant_t *g;
    check(moy_app_grant_get(app_of(self), grant_arg(h), &g));
    return mp_obj_new_str(g->id, g->id_len);
}
static MP_DEFINE_CONST_FUN_OBJ_2(app_grant_id_obj, app_grant_id);

static mp_obj_t app_count(mp_obj_t self) {
    return MP_OBJ_NEW_SMALL_INT(moy_app_grants(app_of(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(app_count_obj, app_count);

static mp_obj_t app_damage_take(mp_obj_t self) {
    return MP_OBJ_NEW_SMALL_INT(moy_app_damage_take(app_of(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(app_damage_take_obj, app_damage_take);

static mp_obj_t app_damage_drop(mp_obj_t self) {
    moy_app_damage_drop(app_of(self));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(app_damage_drop_obj, app_damage_drop);

static mp_obj_t app_counts(mp_obj_t self) {
    moy_appabi_t *a = app_of(self);
    mp_obj_t items[MOY_ROW_N];
    for (int i = 0; i < MOY_ROW_N; i++) {
        items[i] = mp_obj_new_int_from_uint(moy_app_count(a, i));
    }
    return mp_obj_new_tuple(MOY_ROW_N, items);
}
static MP_DEFINE_CONST_FUN_OBJ_1(app_counts_obj, app_counts);

// The CANVAS row a canvas object draws through (device_canvas.py's `_crow`),
// or 0 for one with none.
static uint32_t canvas_handle(mp_obj_t cv) {
    mp_obj_t dest[2];
    mp_load_method_maybe(cv, MP_QSTR__crow, dest);
    if (dest[0] == MP_OBJ_NULL || dest[1] != MP_OBJ_NULL) {
        return 0u;
    }
    mp_obj_t h[2];
    mp_load_method_maybe(dest[0], MP_QSTR_h, h);
    if (h[0] == MP_OBJ_NULL || h[1] != MP_OBJ_NULL || !mp_obj_is_int(h[0])) {
        return 0u;
    }
    return (uint32_t)mp_obj_get_int_truncated(h[0]);
}

// surface_write(canvas, w, h, fs, cs, windowed, bar_h, ox=0, oy=0): the surface
// row of every grant holding the role, and the canvas object each answers.
static mp_obj_t app_surface_write(size_t n_args, const mp_obj_t *args) {
    app_obj_t *self = MP_OBJ_TO_PTR(args[0]);
    app_of(args[0]);
    moy_app_surface_t s;
    memset(&s, 0, sizeof(s));
    s.canvas = canvas_handle(args[1]);
    s.w = (int32_t)mp_obj_get_int(args[2]);
    s.h = (int32_t)mp_obj_get_int(args[3]);
    s.font_scale = (uint8_t)mp_obj_get_int(args[4]);
    s.chrome_scale = (uint8_t)mp_obj_get_int(args[5]);
    s.windowed = mp_obj_is_true(args[6]) ? 1u : 0u;
    s.bar_h = (int32_t)mp_obj_get_int(args[7]);
    s.ox = n_args > 8 ? (int32_t)mp_obj_get_int(args[8]) : 0;
    s.oy = n_args > 9 ? (int32_t)mp_obj_get_int(args[9]) : 0;
    uint32_t mask = moy_app_surface_write(self->a, &s);
    for (size_t i = 0; i < MOY_GRANT_SLOTS; i++) {
        if (mask & (1u << i)) {
            self->canvases[i] = args[1];
        }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(app_surface_write_obj, 8, 10, app_surface_write);

// The cursor of a moy_input.Pointer, where the image has native/moy_input.
moy_input_ptr_t *moy_input_pointer_ptr(mp_obj_t o) __attribute__((weak));

static mp_obj_t app_bind_pointer(mp_obj_t self_in, mp_obj_t p) {
    app_obj_t *self = MP_OBJ_TO_PTR(self_in);
    app_of(self_in);
    const moy_input_ptr_t *c = NULL;
    if (p != mp_const_none && moy_input_pointer_ptr != NULL) {
        c = moy_input_pointer_ptr(p);
    }
    self->pointer = c != NULL ? p : mp_const_none;
    moy_app_pointer_bind(self->a, c);
    return mp_obj_new_bool(c != NULL);
}
static MP_DEFINE_CONST_FUN_OBJ_2(app_bind_pointer_obj, app_bind_pointer);

// theme_write(name, variant, colors): the live token table from the look's
// dict; a key outside the vocabulary is refused (ValueError).
static mp_obj_t app_theme_write(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    moy_appabi_t *a = app_of(args[0]);
    size_t n, vn;
    const char *name = str_of(args[1], &n);
    const char *variant = str_of(args[2], &vn);
    int32_t tokens[MOY_APP_TOKENS];
    for (size_t i = 0; i < MOY_APP_TOKENS; i++) {
        tokens[i] = MOY_TOKEN_ABSENT;
    }
    mp_map_t *map = mp_obj_dict_get_map(args[3]);
    for (size_t i = 0; i < map->alloc; i++) {
        if (!mp_map_slot_is_filled(map, i)) {
            continue;
        }
        size_t kn;
        const char *k = str_of(map->table[i].key, &kn);
        int role = moy_app_token_of(k, kn);
        if (role < 0) {
            mp_raise_ValueError(MP_ERROR_TEXT("a token outside the vocabulary"));
        }
        tokens[role] = (int32_t)mp_obj_get_int(map->table[i].value);
    }
    check(moy_app_theme_write(a, name, n, variant, vn, tokens));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(app_theme_write_obj, 4, 4, app_theme_write);

static mp_obj_t app_theme_write_skin(mp_obj_t self, mp_obj_t skin) {
    size_t n;
    const char *s = str_of(skin, &n);
    check(moy_app_theme_write_skin(app_of(self), s, n));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(app_theme_write_skin_obj, app_theme_write_skin);

static mp_obj_t app_serve(mp_obj_t self_in, mp_obj_t role, mp_obj_t server) {
    app_obj_t *self = MP_OBJ_TO_PTR(self_in);
    app_of(self_in);
    mp_obj_dict_store(self->servers, role, server);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(app_serve_obj, app_serve);

// served() -> {"role.verb": calls}: the counters of the rows served in Python.
static mp_obj_t app_served(mp_obj_t self_in) {
    app_obj_t *self = MP_OBJ_TO_PTR(self_in);
    app_of(self_in);
    mp_obj_t out = mp_obj_new_dict(0);
    mp_map_t *roles = mp_obj_dict_get_map(self->served);
    for (size_t i = 0; i < roles->alloc; i++) {
        if (!mp_map_slot_is_filled(roles, i)) {
            continue;
        }
        mp_map_t *verbs = mp_obj_dict_get_map(roles->table[i].value);
        for (size_t j = 0; j < verbs->alloc; j++) {
            if (!mp_map_slot_is_filled(verbs, j)) {
                continue;
            }
            vstr_t v;
            vstr_init(&v, 24);
            vstr_add_str(&v, qstr_str(MP_OBJ_QSTR_VALUE(roles->table[i].key)));
            vstr_add_byte(&v, '.');
            vstr_add_str(&v, qstr_str(MP_OBJ_QSTR_VALUE(verbs->table[j].key)));
            mp_obj_dict_store(out, mp_obj_new_str_from_vstr(&v), verbs->table[j].value);
        }
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_1(app_served_obj, app_served);

// store_bind(root, readable, writable, no_store): the user-files store the files
// and wallpaper rows reach (root None: none), and the `err` they answer with
// no store. The layer is this image's (moy_ufiles.h).
static mp_obj_t app_store_bind(size_t n_args, const mp_obj_t *args) {
    app_obj_t *self = MP_OBJ_TO_PTR(args[0]);
    size_t n = 0;
    const char *root = args[1] == mp_const_none ? "" : str_of(args[1], &n);
    check(moy_app_store_bind(self->a, n ? &moy_uf_ops : NULL, root, n,
                             mp_obj_is_true(args[2]), mp_obj_is_true(args[3])));
    self->no_store = args[4];
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(app_store_bind_obj, 5, 5, app_store_bind);

// files_end_all() -> the files sessions left open, ended.
static mp_obj_t app_files_end_all(mp_obj_t self) {
    return mp_obj_new_int_from_uint(moy_app_files_end_all(app_of(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(app_files_end_all_obj, app_files_end_all);

// copy_gen() -> bumped by every wallpaper.save_copy that landed.
static mp_obj_t app_copy_gen(mp_obj_t self) {
    return mp_obj_new_int_from_uint(moy_app_copy_gen(app_of(self)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(app_copy_gen_obj, app_copy_gen);

// role(g, row, args, cap) -> (answer, bytes): the ROLE door, as a compiled
// app's import reaches it.
static mp_obj_t app_role(size_t n_args, const mp_obj_t *args) {
    app_obj_t *self = MP_OBJ_TO_PTR(args[0]);
    mp_buffer_info_t in;
    mp_get_buffer_raise(args[3], &in, MP_BUFFER_READ);
    mp_int_t cap = mp_obj_get_int(args[4]);
    if (cap < 0) {
        mp_raise_ValueError(NULL);
    }
    vstr_t v;
    vstr_init_len(&v, (size_t)cap);
    int32_t r = moy_app_role(self->a, (uint32_t)mp_obj_get_int_truncated(args[1]),
                             (uint32_t)mp_obj_get_int(args[2]), in.buf, in.len,
                             (uint8_t *)v.buf, (size_t)cap);
    size_t got = r > 0 ? ((size_t)r < (size_t)cap ? (size_t)r : (size_t)cap) : 0u;
    mp_obj_t out[2] = { mp_obj_new_int(r), mp_obj_new_bytes((const byte *)v.buf, got) };
    vstr_clear(&v);
    return mp_obj_new_tuple(2, out);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(app_role_obj, 5, 5, app_role);

static const mp_rom_map_elem_t app_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_role), MP_ROM_PTR(&app_role_obj) },
    { MP_ROM_QSTR(MP_QSTR_store_bind), MP_ROM_PTR(&app_store_bind_obj) },
    { MP_ROM_QSTR(MP_QSTR_files_end_all), MP_ROM_PTR(&app_files_end_all_obj) },
    { MP_ROM_QSTR(MP_QSTR_copy_gen), MP_ROM_PTR(&app_copy_gen_obj) },
    { MP_ROM_QSTR(MP_QSTR_served), MP_ROM_PTR(&app_served_obj) },
    { MP_ROM_QSTR(MP_QSTR_surface_write), MP_ROM_PTR(&app_surface_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_bind_pointer), MP_ROM_PTR(&app_bind_pointer_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_write), MP_ROM_PTR(&app_theme_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_theme_write_skin), MP_ROM_PTR(&app_theme_write_skin_obj) },
    { MP_ROM_QSTR(MP_QSTR_serve), MP_ROM_PTR(&app_serve_obj) },
    { MP_ROM_QSTR(MP_QSTR_grant), MP_ROM_PTR(&app_grant_obj) },
    { MP_ROM_QSTR(MP_QSTR_end), MP_ROM_PTR(&app_end_obj) },
    { MP_ROM_QSTR(MP_QSTR_grant_id), MP_ROM_PTR(&app_grant_id_obj) },
    { MP_ROM_QSTR(MP_QSTR_count), MP_ROM_PTR(&app_count_obj) },
    { MP_ROM_QSTR(MP_QSTR_damage_take), MP_ROM_PTR(&app_damage_take_obj) },
    { MP_ROM_QSTR(MP_QSTR_damage_drop), MP_ROM_PTR(&app_damage_drop_obj) },
    { MP_ROM_QSTR(MP_QSTR_counts), MP_ROM_PTR(&app_counts_obj) },
    { MP_ROM_QSTR(MP_QSTR___del__), MP_ROM_PTR(&app_del_obj) },
};
static MP_DEFINE_CONST_DICT(app_locals, app_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    app_type, MP_QSTR_App, MP_TYPE_FLAG_NONE,
    make_new, app_make_new,
    locals_dict, &app_locals
    );

// kernel(fresh) -> App: a view of the kernel's state, which a VM stop leaves.
static mp_obj_t mod_kernel(mp_obj_t fresh) {
    moy_appabi_t *a = moy_app_kernel(moy_spine_mem());
    if (a == NULL) {
        mp_raise_type(&mp_type_MemoryError);
    }
    if (mp_obj_is_true(fresh)) {
        moy_app_fresh(a);
    }
    // The pointer the last VM bound lived in its heap.
    moy_app_pointer_bind(a, NULL);
    app_obj_t *o = mp_obj_malloc_with_finaliser(app_obj_t, &app_type);
    app_init(o);
    o->a = a;
    o->own = false;
    return MP_OBJ_FROM_PTR(o);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_kernel_obj, mod_kernel);

// -- the roles ---------------------------------------------------------------------------

typedef struct {
    mp_obj_base_t base;
    mp_obj_t app;
    uint32_t g;
} role_obj_t;

static mp_obj_t role_make_new(const mp_obj_type_t *type, size_t n_args,
                              size_t n_kw, const mp_obj_t *args);

// A role whose rows are all served in Python: its type, from a table of ROWs.
#define SERVED_ROLE(r, Name) \
    static MP_DEFINE_CONST_DICT(r##_locals, r##_locals_table); \
    static MP_DEFINE_CONST_OBJ_TYPE(r##_type, MP_QSTR_##Name, MP_TYPE_FLAG_NONE, \
                                    make_new, role_make_new, locals_dict, &r##_locals);

static mp_obj_t role_make_new(const mp_obj_type_t *type, size_t n_args,
                              size_t n_kw, const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 2, 2, false);
    app_of(args[0]);
    role_obj_t *o = mp_obj_malloc(role_obj_t, type);
    o->app = args[0];
    o->g = grant_arg(args[1]);
    return MP_OBJ_FROM_PTR(o);
}

static moy_appabi_t *role_app(mp_obj_t self, uint32_t *g) {
    role_obj_t *o = MP_OBJ_TO_PTR(self);
    *g = o->g;
    return ((app_obj_t *)MP_OBJ_TO_PTR(o->app))->a;
}

// damage

static mp_obj_t damage_all(mp_obj_t self) {
    uint32_t g;
    moy_appabi_t *a = role_app(self, &g);
    check(moy_app_damage_all(a, g));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(damage_all_obj, damage_all);

static mp_obj_t damage_again(mp_obj_t self) {
    uint32_t g;
    moy_appabi_t *a = role_app(self, &g);
    check(moy_app_damage_again(a, g));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(damage_again_obj, damage_again);

static const mp_rom_map_elem_t damage_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_all), MP_ROM_PTR(&damage_all_obj) },
    { MP_ROM_QSTR(MP_QSTR_again), MP_ROM_PTR(&damage_again_obj) },
};
static MP_DEFINE_CONST_DICT(damage_locals, damage_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    damage_type, MP_QSTR_Damage, MP_TYPE_FLAG_NONE,
    make_new, role_make_new,
    locals_dict, &damage_locals
    );

// A row served in Python: the grant checked for the role, the row counted,
// then the server the console registered for the role called as
// `server.<verb>(grant, *args, **kw)`. No allocation once the row has been
// called: the method is loaded unbound and the arguments sit on the stack.
static mp_obj_t serve_call(mp_obj_t self, int role_i, qstr role, qstr verb,
                           size_t n_args, const mp_obj_t *args, mp_map_t *kw) {
    role_obj_t *o = MP_OBJ_TO_PTR(self);
    app_obj_t *app = MP_OBJ_TO_PTR(o->app);
    check(moy_app_holds(app->a, o->g, role_i));
    mp_map_elem_t *e = mp_map_lookup(mp_obj_dict_get_map(app->servers),
                                     MP_OBJ_NEW_QSTR(role), MP_MAP_LOOKUP);
    if (e == NULL) {
        mp_raise_ValueError(MP_ERROR_TEXT("no server for the role"));
    }
    mp_map_elem_t *r = mp_map_lookup(mp_obj_dict_get_map(app->served),
                                     MP_OBJ_NEW_QSTR(role), MP_MAP_LOOKUP_ADD_IF_NOT_FOUND);
    if (r->value == MP_OBJ_NULL) {
        r->value = mp_obj_new_dict(0);
    }
    mp_map_elem_t *c = mp_map_lookup(mp_obj_dict_get_map(r->value),
                                     MP_OBJ_NEW_QSTR(verb), MP_MAP_LOOKUP_ADD_IF_NOT_FOUND);
    c->value = MP_OBJ_NEW_SMALL_INT(c->value == MP_OBJ_NULL ? 1
                                    : MP_OBJ_SMALL_INT_VALUE(c->value) + 1);
    size_t n_kw = kw == NULL ? 0u : kw->used;
    mp_obj_t all[14];
    if (3u + n_args + 2u * n_kw > MP_ARRAY_SIZE(all)) {
        mp_raise_TypeError(MP_ERROR_TEXT("too many arguments"));
    }
    mp_load_method(e->value, verb, all);
    all[2] = MP_OBJ_NEW_SMALL_INT(o->g);
    memcpy(all + 3, args, n_args * sizeof(mp_obj_t));
    // A builtin's keyword map is a fixed table, every entry filled.
    for (size_t i = 0; i < n_kw; i++) {
        all[3u + n_args + 2u * i] = kw->table[i].key;
        all[3u + n_args + 2u * i + 1u] = kw->table[i].value;
    }
    return mp_call_method_n_kw(n_args + 1u, n_kw, all);
}

// One row served in Python: role `r` (MOY_ROLE_<R>), verb `v`.
#define SERVED(r, R, v) \
    static mp_obj_t r##_##v(size_t n_args, const mp_obj_t *args, mp_map_t *kw) { \
        return serve_call(args[0], MOY_ROLE_##R, MP_QSTR_##r, MP_QSTR_##v, \
                          n_args - 1u, args + 1, kw); \
    } \
    static MP_DEFINE_CONST_FUN_OBJ_KW(r##_##v##_obj, 1, r##_##v);
#define ROW(r, v) { MP_ROM_QSTR(MP_QSTR_##v), MP_ROM_PTR(&r##_##v##_obj) },

// surface

static mp_obj_t surface_canvas(mp_obj_t self) {
    uint32_t g, h;
    moy_appabi_t *a = role_app(self, &g);
    check(moy_app_surface_canvas(a, g, &h));
    app_obj_t *app = MP_OBJ_TO_PTR(((role_obj_t *)MP_OBJ_TO_PTR(self))->app);
    return app->canvases[g & 0xffu];
}
static MP_DEFINE_CONST_FUN_OBJ_1(surface_canvas_obj, surface_canvas);

static mp_obj_t surface_size(mp_obj_t self) {
    uint32_t g;
    int32_t w, h;
    moy_appabi_t *a = role_app(self, &g);
    check(moy_app_surface_size(a, g, &w, &h));
    mp_obj_t items[2] = { MP_OBJ_NEW_SMALL_INT(w), MP_OBJ_NEW_SMALL_INT(h) };
    return mp_obj_new_tuple(2, items);
}
static MP_DEFINE_CONST_FUN_OBJ_1(surface_size_obj, surface_size);

static mp_obj_t scalar(int32_t v) {
    if (v < 0) {
        raise_rc(-v);
    }
    return MP_OBJ_NEW_SMALL_INT(v);
}

static mp_obj_t surface_font_scale(mp_obj_t self) {
    uint32_t g;
    moy_appabi_t *a = role_app(self, &g);
    return scalar(moy_app_surface_font_scale(a, g));
}
static MP_DEFINE_CONST_FUN_OBJ_1(surface_font_scale_obj, surface_font_scale);

static mp_obj_t surface_chrome_scale(mp_obj_t self) {
    uint32_t g;
    moy_appabi_t *a = role_app(self, &g);
    return scalar(moy_app_surface_chrome_scale(a, g));
}
static MP_DEFINE_CONST_FUN_OBJ_1(surface_chrome_scale_obj, surface_chrome_scale);

static mp_obj_t surface_windowed(mp_obj_t self) {
    uint32_t g;
    moy_appabi_t *a = role_app(self, &g);
    return mp_obj_new_bool(MP_OBJ_SMALL_INT_VALUE(scalar(moy_app_surface_windowed(a, g))));
}
static MP_DEFINE_CONST_FUN_OBJ_1(surface_windowed_obj, surface_windowed);

static mp_obj_t surface_bar_h(mp_obj_t self) {
    uint32_t g;
    moy_appabi_t *a = role_app(self, &g);
    return scalar(moy_app_surface_bar_h(a, g));
}
static MP_DEFINE_CONST_FUN_OBJ_1(surface_bar_h_obj, surface_bar_h);

static const qstr POINTER_FIELDS[5] = {
    MP_QSTR_x, MP_QSTR_y, MP_QSTR_down, MP_QSTR_click, MP_QSTR_visible,
};

static mp_obj_t surface_pointer(mp_obj_t self) {
    uint32_t g;
    int32_t v[5];
    moy_appabi_t *a = role_app(self, &g);
    int rc = moy_app_surface_pointer(a, g, v);
    if (rc == MOY_APP_ABSENT) {
        return mp_const_none;
    }
    check(rc);
    mp_obj_t items[5] = {
        MP_OBJ_NEW_SMALL_INT(v[0]), MP_OBJ_NEW_SMALL_INT(v[1]),
        mp_obj_new_bool(v[2]), mp_obj_new_bool(v[3]), mp_obj_new_bool(v[4]),
    };
    return mp_obj_new_attrtuple(POINTER_FIELDS, 5, items);
}
static MP_DEFINE_CONST_FUN_OBJ_1(surface_pointer_obj, surface_pointer);

SERVED(surface, SURFACE, glyph)

static const mp_rom_map_elem_t surface_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_canvas), MP_ROM_PTR(&surface_canvas_obj) },
    { MP_ROM_QSTR(MP_QSTR_size), MP_ROM_PTR(&surface_size_obj) },
    { MP_ROM_QSTR(MP_QSTR_font_scale), MP_ROM_PTR(&surface_font_scale_obj) },
    { MP_ROM_QSTR(MP_QSTR_chrome_scale), MP_ROM_PTR(&surface_chrome_scale_obj) },
    { MP_ROM_QSTR(MP_QSTR_windowed), MP_ROM_PTR(&surface_windowed_obj) },
    { MP_ROM_QSTR(MP_QSTR_bar_h), MP_ROM_PTR(&surface_bar_h_obj) },
    { MP_ROM_QSTR(MP_QSTR_pointer), MP_ROM_PTR(&surface_pointer_obj) },
    { MP_ROM_QSTR(MP_QSTR_glyph), MP_ROM_PTR(&surface_glyph_obj) },
};
static MP_DEFINE_CONST_DICT(surface_locals, surface_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    surface_type, MP_QSTR_Surface, MP_TYPE_FLAG_NONE,
    make_new, role_make_new,
    locals_dict, &surface_locals
    );

// theme

// The colour dict of the token table's generation `gen`, rebuilt only when it
// moves: what the look's dict holds, flags as bools.
static mp_obj_t colors_at(app_obj_t *app, uint32_t gen) {
    if (app->colors != MP_OBJ_NULL && app->colors_gen == gen) {
        return app->colors;
    }
    int32_t tok[MOY_APP_TOKENS];
    gen = moy_app_theme_read(app->a, tok);
    mp_obj_t d = mp_obj_new_dict(MOY_APP_TOKENS);
    for (int i = 0; i < (int)MOY_APP_TOKENS; i++) {
        if (tok[i] == MOY_TOKEN_ABSENT) {
            continue;
        }
        const char *k = moy_app_token_name(i);
        mp_obj_t key = MP_OBJ_NEW_QSTR(qstr_from_str(k));
        mp_obj_t v = moy_app_token_flag(i) ? mp_obj_new_bool(tok[i])
                                           : MP_OBJ_NEW_SMALL_INT(tok[i]);
        mp_obj_dict_store(d, key, v);
    }
    app->colors = d;
    app->colors_gen = gen;
    return d;
}

static mp_obj_t theme_colors(mp_obj_t self) {
    uint32_t g, gen;
    moy_appabi_t *a = role_app(self, &g);
    check(moy_app_theme_colors(a, g, &gen));
    return colors_at(MP_OBJ_TO_PTR(((role_obj_t *)MP_OBJ_TO_PTR(self))->app), gen);
}
static MP_DEFINE_CONST_FUN_OBJ_1(theme_colors_obj, theme_colors);

static mp_obj_t theme_token(mp_obj_t self, mp_obj_t role) {
    uint32_t g;
    int32_t v;
    moy_appabi_t *a = role_app(self, &g);
    int rc = moy_app_theme_token(a, g, (int)mp_obj_get_int(role), &v);
    if (rc == MOY_APP_ABSENT) {
        return mp_const_none;
    }
    check(rc);
    return MP_OBJ_NEW_SMALL_INT(v);
}
static MP_DEFINE_CONST_FUN_OBJ_2(theme_token_obj, theme_token);

static mp_obj_t theme_gen(mp_obj_t self) {
    uint32_t g;
    moy_appabi_t *a = role_app(self, &g);
    return scalar(moy_app_theme_gen(a, g));
}
static MP_DEFINE_CONST_FUN_OBJ_1(theme_gen_obj, theme_gen);

static mp_obj_t theme_light(mp_obj_t self) {
    uint32_t g;
    moy_appabi_t *a = role_app(self, &g);
    return mp_obj_new_bool(MP_OBJ_SMALL_INT_VALUE(scalar(moy_app_theme_light(a, g))));
}
static MP_DEFINE_CONST_FUN_OBJ_1(theme_light_obj, theme_light);

typedef int (*name_fn_t)(moy_appabi_t *, uint32_t, char *, size_t, size_t *);

static mp_obj_t name_read(mp_obj_t self, name_fn_t fn) {
    uint32_t g;
    char buf[MOY_APP_NAME_MAX + 1];
    size_t n = 0;
    moy_appabi_t *a = role_app(self, &g);
    check(fn(a, g, buf, sizeof(buf), &n));
    return mp_obj_new_str(buf, n);
}

static mp_obj_t theme_name(mp_obj_t self) {
    return name_read(self, moy_app_theme_name);
}
static MP_DEFINE_CONST_FUN_OBJ_1(theme_name_obj, theme_name);

static mp_obj_t theme_variant(mp_obj_t self) {
    return name_read(self, moy_app_theme_variant);
}
static MP_DEFINE_CONST_FUN_OBJ_1(theme_variant_obj, theme_variant);

static mp_obj_t theme_skin(mp_obj_t self) {
    return name_read(self, moy_app_theme_skin);
}
static MP_DEFINE_CONST_FUN_OBJ_1(theme_skin_obj, theme_skin);

SERVED(theme, THEME, set)
SERVED(theme, THEME, set_variant)
SERVED(theme, THEME, set_skin)

static const mp_rom_map_elem_t theme_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_colors), MP_ROM_PTR(&theme_colors_obj) },
    { MP_ROM_QSTR(MP_QSTR_token), MP_ROM_PTR(&theme_token_obj) },
    { MP_ROM_QSTR(MP_QSTR_gen), MP_ROM_PTR(&theme_gen_obj) },
    { MP_ROM_QSTR(MP_QSTR_light), MP_ROM_PTR(&theme_light_obj) },
    { MP_ROM_QSTR(MP_QSTR_name), MP_ROM_PTR(&theme_name_obj) },
    { MP_ROM_QSTR(MP_QSTR_variant), MP_ROM_PTR(&theme_variant_obj) },
    { MP_ROM_QSTR(MP_QSTR_skin), MP_ROM_PTR(&theme_skin_obj) },
    { MP_ROM_QSTR(MP_QSTR_set), MP_ROM_PTR(&theme_set_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_variant), MP_ROM_PTR(&theme_set_variant_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_skin), MP_ROM_PTR(&theme_set_skin_obj) },
};
static MP_DEFINE_CONST_DICT(theme_locals, theme_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    theme_type, MP_QSTR_Theme, MP_TYPE_FLAG_NONE,
    make_new, role_make_new,
    locals_dict, &theme_locals
    );

// prefs

static mp_obj_t prefs_get(size_t n_args, const mp_obj_t *args) {
    uint32_t g;
    moy_appabi_t *a = role_app(args[0], &g);
    size_t kn, len = 0;
    const char *k = str_of(args[1], &kn);
    // The size (a call with no buffer counts nothing), then the row.
    int rc = moy_app_prefs_get(a, g, k, kn, NULL, 0, &len);
    if (rc == MOY_APP_OK) {
        vstr_t v;
        vstr_init_len(&v, len);
        rc = moy_app_prefs_get(a, g, k, kn, v.buf, len, &len);
        if (rc == MOY_APP_OK) {
            return json_call(MP_QSTR_loads, mp_obj_new_str_from_vstr(&v));
        }
        vstr_clear(&v);
    }
    if (rc == MOY_APP_ABSENT) {
        return n_args > 2 ? args[2] : mp_const_none;
    }
    raise_rc(rc);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(prefs_get_obj, 2, 3, prefs_get);

static mp_obj_t prefs_set(mp_obj_t self, mp_obj_t key, mp_obj_t value) {
    uint32_t g;
    moy_appabi_t *a = role_app(self, &g);
    size_t kn, jn;
    const char *k = str_of(key, &kn);
    mp_obj_t text = json_call(MP_QSTR_dumps, value);
    const char *j = mp_obj_str_get_data(text, &jn);
    int rc = moy_app_prefs_set(a, g, k, kn, j, jn);
    moy_spine_settings_raise();
    if (rc != MOY_APP_ABSENT) {
        check(rc);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(prefs_set_obj, prefs_set);

static mp_obj_t prefs_clear(mp_obj_t self, mp_obj_t key) {
    uint32_t g;
    moy_appabi_t *a = role_app(self, &g);
    size_t kn;
    const char *k = str_of(key, &kn);
    int rc = moy_app_prefs_clear(a, g, k, kn);
    moy_spine_settings_raise();
    if (rc != MOY_APP_ABSENT) {
        check(rc);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(prefs_clear_obj, prefs_clear);

static const mp_rom_map_elem_t prefs_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_get), MP_ROM_PTR(&prefs_get_obj) },
    { MP_ROM_QSTR(MP_QSTR_set), MP_ROM_PTR(&prefs_set_obj) },
    { MP_ROM_QSTR(MP_QSTR_clear), MP_ROM_PTR(&prefs_clear_obj) },
};
static MP_DEFINE_CONST_DICT(prefs_locals, prefs_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    prefs_type, MP_QSTR_Prefs, MP_TYPE_FLAG_NONE,
    make_new, role_make_new,
    locals_dict, &prefs_locals
    );

// clipboard

static mp_obj_t clip_put_text(mp_obj_t self, mp_obj_t text) {
    uint32_t g;
    moy_appabi_t *a = role_app(self, &g);
    size_t n;
    const char *s = mp_obj_is_str(text) ? mp_obj_str_get_data(text, &n)
                    : mp_obj_str_get_data(mp_obj_str_make_new(&mp_type_str, 1, 0, &text), &n);
    int rc = moy_app_clip_put_text(a, g, s, n);
    if (rc != MOY_APP_BAD) {
        check(rc);
    }
    return mp_obj_new_bool(rc == MOY_APP_OK);
}
static MP_DEFINE_CONST_FUN_OBJ_2(clip_put_text_obj, clip_put_text);

static mp_obj_t clip_text(mp_obj_t self) {
    uint32_t g;
    moy_appabi_t *a = role_app(self, &g);
    size_t len = 0;
    check(moy_app_clip_text(a, g, NULL, 0, &len));
    vstr_t v;
    vstr_init_len(&v, len);
    check(moy_app_clip_text(a, g, v.buf, len, &len));
    return mp_obj_new_str_from_vstr(&v);
}
static MP_DEFINE_CONST_FUN_OBJ_1(clip_text_obj, clip_text);

static mp_obj_t clip_kind(mp_obj_t self) {
    uint32_t g;
    moy_appabi_t *a = role_app(self, &g);
    int32_t k = moy_app_clip_kind(a, g);
    if (k < 0) {
        raise_rc(-k);
    }
    return k == MOY_CLIP_TEXT ? MP_OBJ_NEW_QSTR(MP_QSTR_text) : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(clip_kind_obj, clip_kind);

static mp_obj_t clip_seq(mp_obj_t self) {
    uint32_t g;
    moy_appabi_t *a = role_app(self, &g);
    int32_t s = moy_app_clip_seq(a, g);
    if (s < 0) {
        raise_rc(-s);
    }
    return MP_OBJ_NEW_SMALL_INT(s);
}
static MP_DEFINE_CONST_FUN_OBJ_1(clip_seq_obj, clip_seq);

static const mp_rom_map_elem_t clip_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_put_text), MP_ROM_PTR(&clip_put_text_obj) },
    { MP_ROM_QSTR(MP_QSTR_text), MP_ROM_PTR(&clip_text_obj) },
    { MP_ROM_QSTR(MP_QSTR_kind), MP_ROM_PTR(&clip_kind_obj) },
    { MP_ROM_QSTR(MP_QSTR_seq), MP_ROM_PTR(&clip_seq_obj) },
};
static MP_DEFINE_CONST_DICT(clip_locals, clip_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    clip_type, MP_QSTR_Clipboard, MP_TYPE_FLAG_NONE,
    make_new, role_make_new,
    locals_dict, &clip_locals
    );

// -- the roles served in Python -------------------------------------------------------------
//
// Every row of these roles but the files role's and the wallpaper's copy is the
// console's (roles.json's "shell" server): the binding checks the grant, counts
// the row and calls the registered server.

// -- the storage rows ---------------------------------------------------------------------
//
// The files rows and the wallpaper's copy are C over the user-files layer the
// console binds (app.store_bind). They answer what the Python roles answered:
// `(value, err)`, `err` None, the console's NO_STORE, or the failure's text
// (an OSError's for IO), and never raise for the store. A row that raises
// inside the store (a borrowed volume's block device can) leaves the gate it
// held and frees the store's scratch before the exception goes on.

void moy_store_unwind(void);

typedef struct {
    app_obj_t *app;
    moy_appabi_t *a;
    uint32_t g;
} store_t;

static void store_of(mp_obj_t self, store_t *st) {
    role_obj_t *o = MP_OBJ_TO_PTR(self);
    st->app = MP_OBJ_TO_PTR(o->app);
    st->a = st->app->a;
    st->g = o->g;
}

#define STORE_BEGIN(st) { nlr_buf_t nlr_; if (nlr_push(&nlr_) == 0) {
#define STORE_END(st) nlr_pop(); } else { moy_app_store_unwind((st).a); \
        moy_store_unwind(); nlr_jump(nlr_.ret_val); } }

static mp_obj_t pair(mp_obj_t v, mp_obj_t err) {
    mp_obj_t t[2] = { v, err };
    return mp_obj_new_tuple(2, t);
}

// The `err` of a code; a grant problem raises.
static mp_obj_t err_of(store_t *st, int rc) {
    switch (rc) {
        case MOY_APP_NOSTORE:
            return st->app->no_store;
        case MOY_APP_IO: {
            mp_obj_t e = mp_obj_new_exception_arg1(&mp_type_OSError,
                                                   MP_OBJ_NEW_SMALL_INT(moy_app_why(st->a)));
            return mp_obj_str_make_new(&mp_type_str, 1, 0, &e);
        }
        case MOY_APP_BAD:
            return mp_obj_new_str("a refused argument", 18);
        case MOY_APP_NOMEM:
            return mp_obj_new_str("memory allocation failed", 24);
        default:
            raise_rc(rc);
    }
}

// A layer buffer as a str (`text`) or bytes, freed either way.
static mp_obj_t take(store_t *st, moy_buf_t *b, int text) {
    mp_obj_t o;
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        o = text ? mp_obj_new_str(b->p, b->n) : mp_obj_new_bytes((const byte *)b->p, b->n);
        nlr_pop();
    } else {
        moy_app_buf_free(st->a, b);
        nlr_jump(nlr.ret_val);
    }
    moy_app_buf_free(st->a, b);
    return o;
}

// A JSON answer as its value.
static mp_obj_t take_json(store_t *st, moy_buf_t *b) {
    return json_call(MP_QSTR_loads, take(st, b, 1));
}

// Names, each NUL-terminated, as a list (`pairs`: of (kind, name) tuples).
static mp_obj_t take_names(store_t *st, moy_buf_t *b, uint32_t count, int pairs) {
    mp_obj_t text = take(st, b, 0);
    mp_buffer_info_t bi;
    mp_get_buffer_raise(text, &bi, MP_BUFFER_READ);
    mp_obj_t list = mp_obj_new_list(0, NULL);
    const char *p = bi.buf, *end = p + bi.len;
    for (uint32_t i = 0; i < count && p < end; i++) {
        size_t n = strlen(p);
        mp_obj_t name = mp_obj_new_str(p, n);
        p += n + 1u;
        if (pairs) {
            size_t m = strlen(p);
            mp_obj_t t[2] = { name, mp_obj_new_str(p, m) };
            p += m + 1u;
            name = mp_obj_new_tuple(2, t);
        }
        mp_obj_list_append(list, name);
    }
    return list;
}

static const char *cstr(mp_obj_t o) {
    if (!mp_obj_is_str(o)) {
        mp_raise_TypeError(MP_ERROR_TEXT("a str"));
    }
    return mp_obj_str_get_str(o);
}

// files

static mp_obj_t files_readable(mp_obj_t self) {
    store_t st;
    store_of(self, &st);
    int32_t v = moy_app_files_readable(st.a, st.g);
    if (v < 0) {
        raise_rc((int)-v);
    }
    return mp_obj_new_bool(v);
}
static MP_DEFINE_CONST_FUN_OBJ_1(files_readable_obj, files_readable);

static mp_obj_t files_ready(mp_obj_t self) {
    store_t st;
    store_of(self, &st);
    int32_t v = moy_app_files_ready(st.a, st.g);
    if (v < 0) {
        raise_rc((int)-v);
    }
    return mp_obj_new_bool(v);
}
static MP_DEFINE_CONST_FUN_OBJ_1(files_ready_obj, files_ready);

static mp_obj_t files_begin(mp_obj_t self) {
    store_t st;
    store_of(self, &st);
    int rc = moy_app_files_begin(st.a, st.g);
    return rc == MOY_APP_OK ? pair(mp_const_true, mp_const_none)
           : pair(mp_const_none, err_of(&st, rc));
}
static MP_DEFINE_CONST_FUN_OBJ_1(files_begin_obj, files_begin);

static mp_obj_t files_end(mp_obj_t self) {
    store_t st;
    store_of(self, &st);
    check(moy_app_files_end(st.a, st.g));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(files_end_obj, files_end);

static mp_obj_t files_list(mp_obj_t self, mp_obj_t kind) {
    store_t st;
    store_of(self, &st);
    const char *k = cstr(kind);
    moy_buf_t b = { NULL, 0 };
    uint32_t n = 0;
    int rc = 0;
    STORE_BEGIN(st)
    rc = moy_app_files_list(st.a, st.g, k, &b, &n);
    STORE_END(st)
    if (rc != MOY_APP_OK) {
        return pair(mp_const_none, err_of(&st, rc));
    }
    return pair(take_names(&st, &b, n, 0), mp_const_none);
}
static MP_DEFINE_CONST_FUN_OBJ_2(files_list_obj, files_list);

static mp_obj_t files_count(mp_obj_t self, mp_obj_t kind) {
    store_t st;
    store_of(self, &st);
    const char *k = cstr(kind);
    uint32_t n = 0;
    int rc = 0;
    STORE_BEGIN(st)
    rc = moy_app_files_count(st.a, st.g, k, &n);
    STORE_END(st)
    if (rc != MOY_APP_OK) {
        return pair(mp_const_none, err_of(&st, rc));
    }
    return pair(mp_obj_new_int_from_uint(n), mp_const_none);
}
static MP_DEFINE_CONST_FUN_OBJ_2(files_count_obj, files_count);

static mp_obj_t files_load(mp_obj_t self, mp_obj_t kind, mp_obj_t name) {
    store_t st;
    store_of(self, &st);
    const char *k = cstr(kind), *nm = cstr(name);
    moy_buf_t b = { NULL, 0 };
    int binary = 0, rc = 0;
    STORE_BEGIN(st)
    rc = moy_app_files_load(st.a, st.g, k, nm, &b, &binary);
    STORE_END(st)
    if (rc == MOY_APP_ABSENT) {
        return pair(mp_const_none, mp_const_none);
    }
    if (rc != MOY_APP_OK) {
        return pair(mp_const_none, err_of(&st, rc));
    }
    return pair(take(&st, &b, !binary), mp_const_none);
}
static MP_DEFINE_CONST_FUN_OBJ_3(files_load_obj, files_load);

static mp_obj_t named_answer(store_t *st, int rc, const char *name) {
    if (rc != MOY_APP_OK) {
        return pair(mp_const_none, err_of(st, rc));
    }
    return pair(mp_obj_new_str(name, strlen(name)), mp_const_none);
}

static mp_obj_t files_save(size_t n_args, const mp_obj_t *args) {
    store_t st;
    store_of(args[0], &st);
    const char *k = cstr(args[1]), *nm = cstr(args[2]);
    mp_buffer_info_t bi;
    mp_get_buffer_raise(args[3], &bi, MP_BUFFER_READ);
    char out[MOY_UF_NAME_MAX + 1];
    int rc = 0;
    STORE_BEGIN(st)
    rc = moy_app_files_save(st.a, st.g, k, nm, bi.buf, bi.len, out);
    STORE_END(st)
    return named_answer(&st, rc, out);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(files_save_obj, 4, 4, files_save);

typedef int (*name_row_fn)(moy_appabi_t *, uint32_t, const char *, const char *, char *);

static mp_obj_t name_row(mp_obj_t self, mp_obj_t kind, mp_obj_t name, name_row_fn fn) {
    store_t st;
    store_of(self, &st);
    const char *k = cstr(kind), *nm = cstr(name);
    char out[MOY_UF_NAME_MAX + 1];
    int rc = 0;
    STORE_BEGIN(st)
    rc = fn(st.a, st.g, k, nm, out);
    STORE_END(st)
    return named_answer(&st, rc, out);
}

static mp_obj_t files_delete(mp_obj_t self, mp_obj_t kind, mp_obj_t name) {
    return name_row(self, kind, name, moy_app_files_delete);
}
static MP_DEFINE_CONST_FUN_OBJ_3(files_delete_obj, files_delete);

static mp_obj_t files_duplicate(mp_obj_t self, mp_obj_t kind, mp_obj_t name) {
    return name_row(self, kind, name, moy_app_files_duplicate);
}
static MP_DEFINE_CONST_FUN_OBJ_3(files_duplicate_obj, files_duplicate);

static mp_obj_t files_restore(mp_obj_t self, mp_obj_t kind, mp_obj_t name) {
    return name_row(self, kind, name, moy_app_files_restore);
}
static MP_DEFINE_CONST_FUN_OBJ_3(files_restore_obj, files_restore);

static mp_obj_t files_rename(size_t n_args, const mp_obj_t *args) {
    store_t st;
    store_of(args[0], &st);
    const char *k = cstr(args[1]), *nm = cstr(args[2]), *t = cstr(args[3]);
    char out[MOY_UF_NAME_MAX + 1];
    int rc = 0;
    STORE_BEGIN(st)
    rc = moy_app_files_rename(st.a, st.g, k, nm, t, out);
    STORE_END(st)
    return named_answer(&st, rc, out);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(files_rename_obj, 4, 4, files_rename);

// new_name(kind, title=None)
static mp_obj_t files_new_name(size_t n_args, const mp_obj_t *args) {
    store_t st;
    store_of(args[0], &st);
    const char *k = cstr(args[1]);
    const char *t = n_args > 2 && mp_obj_is_true(args[2]) ? cstr(args[2]) : NULL;
    char out[MOY_UF_NAME_MAX + 1];
    int rc = 0;
    STORE_BEGIN(st)
    rc = moy_app_files_new_name(st.a, st.g, k, t, out);
    STORE_END(st)
    return named_answer(&st, rc, out);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(files_new_name_obj, 2, 3, files_new_name);

static mp_obj_t files_trash_list(mp_obj_t self) {
    store_t st;
    store_of(self, &st);
    moy_buf_t b = { NULL, 0 };
    uint32_t n = 0;
    int rc = 0;
    STORE_BEGIN(st)
    rc = moy_app_files_trash_list(st.a, st.g, &b, &n);
    STORE_END(st)
    if (rc != MOY_APP_OK) {
        return pair(mp_const_none, err_of(&st, rc));
    }
    return pair(take_names(&st, &b, n, 1), mp_const_none);
}
static MP_DEFINE_CONST_FUN_OBJ_1(files_trash_list_obj, files_trash_list);

static mp_obj_t files_empty_trash(mp_obj_t self) {
    store_t st;
    store_of(self, &st);
    int rc = 0;
    STORE_BEGIN(st)
    rc = moy_app_files_empty_trash(st.a, st.g);
    STORE_END(st)
    return pair(mp_const_none, rc == MOY_APP_OK ? mp_const_none : err_of(&st, rc));
}
static MP_DEFINE_CONST_FUN_OBJ_1(files_empty_trash_obj, files_empty_trash);

typedef int (*json_row_fn)(moy_appabi_t *, uint32_t, const char *, const char *, moy_buf_t *);

static mp_obj_t json_row(mp_obj_t self, mp_obj_t kind, mp_obj_t name, json_row_fn fn) {
    store_t st;
    store_of(self, &st);
    const char *k = cstr(kind), *nm = cstr(name);
    moy_buf_t b = { NULL, 0 };
    int rc = 0;
    STORE_BEGIN(st)
    rc = fn(st.a, st.g, k, nm, &b);
    STORE_END(st)
    if (rc != MOY_APP_OK) {
        return pair(mp_const_none, err_of(&st, rc));
    }
    return pair(take_json(&st, &b), mp_const_none);
}

static mp_obj_t files_history(mp_obj_t self, mp_obj_t kind, mp_obj_t name) {
    return json_row(self, kind, name, moy_app_files_history);
}
static MP_DEFINE_CONST_FUN_OBJ_3(files_history_obj, files_history);

static mp_obj_t files_history_ops(mp_obj_t self, mp_obj_t kind, mp_obj_t name) {
    return json_row(self, kind, name, moy_app_files_history_ops);
}
static MP_DEFINE_CONST_FUN_OBJ_3(files_history_ops_obj, files_history_ops);

// history_commit(kind, name, ops, keyframe=None) -> (prune failure or None, err)
static mp_obj_t files_history_commit(size_t n_args, const mp_obj_t *args, mp_map_t *kw) {
    static const mp_arg_t allowed[] = {
        { MP_QSTR_kind, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_name, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_ops, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_keyframe, MP_ARG_OBJ, { .u_obj = mp_const_none } },
    };
    mp_arg_val_t v[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args - 1u, args + 1, kw, MP_ARRAY_SIZE(allowed), allowed, v);
    store_t st;
    store_of(args[0], &st);
    const char *k = cstr(v[0].u_obj), *nm = cstr(v[1].u_obj);
    const char *ops = NULL, *kf = NULL;
    size_t on = 0, kn = 0;
    mp_obj_t ops_text = mp_const_none, kf_text = mp_const_none;
    if (mp_obj_is_true(v[2].u_obj)) {
        mp_obj_t lst = mp_obj_new_list(0, NULL);
        mp_obj_t it = mp_getiter(v[2].u_obj, NULL), x;
        while ((x = mp_iternext(it)) != MP_OBJ_STOP_ITERATION) {
            mp_obj_list_append(lst, x);
        }
        ops_text = json_call(MP_QSTR_dumps, lst);
        ops = mp_obj_str_get_data(ops_text, &on);
    }
    if (v[3].u_obj != mp_const_none) {
        kf_text = json_call(MP_QSTR_dumps, v[3].u_obj);
        kf = mp_obj_str_get_data(kf_text, &kn);
    }
    int prune_err = 0, rc = 0;
    STORE_BEGIN(st)
    rc = moy_app_files_history_commit(st.a, st.g, k, nm, ops, on, kf, kn, &prune_err);
    STORE_END(st)
    (void)ops_text;
    (void)kf_text;
    if (rc != MOY_APP_OK) {
        return pair(mp_const_none, err_of(&st, rc));
    }
    if (prune_err == 0) {
        return pair(mp_const_none, mp_const_none);
    }
    if (prune_err == MOY_UF_BAD) {
        return pair(mp_obj_new_str("a refused argument", 18), mp_const_none);
    }
    mp_obj_t e = mp_obj_new_exception_arg1(&mp_type_OSError, MP_OBJ_NEW_SMALL_INT(prune_err));
    return pair(mp_obj_str_make_new(&mp_type_str, 1, 0, &e), mp_const_none);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(files_history_commit_obj, 4, files_history_commit);

// -- the codecs: their value, or None

static mp_obj_t files_encode_image(size_t n_args, const mp_obj_t *args) {
    store_t st;
    store_of(args[0], &st);
    mp_int_t w = mp_obj_get_int(args[1]), h = mp_obj_get_int(args[2]);
    mp_buffer_info_t bi;
    mp_obj_t pix = args[3];
    if (!mp_get_buffer(pix, &bi, MP_BUFFER_READ)) {
        pix = mp_call_function_1(MP_OBJ_FROM_PTR(&mp_type_bytes),
                                 mp_call_function_1(MP_OBJ_FROM_PTR(&mp_type_bytearray), pix));
        mp_get_buffer_raise(pix, &bi, MP_BUFFER_READ);
    }
    if (w <= 0 || h <= 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("bad artwork size"));
    }
    moy_buf_t b = { NULL, 0 };
    int rc = moy_app_files_encode_image(st.a, st.g, (uint32_t)w, (uint32_t)h, bi.buf, bi.len, &b);
    if (rc == MOY_APP_BAD) {
        mp_raise_ValueError(MP_ERROR_TEXT("bad artwork size"));
    }
    if (rc == MOY_APP_ABSENT) {
        return mp_const_none;
    }
    check(rc);
    return take(&st, &b, 1);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(files_encode_image_obj, 4, 4, files_encode_image);

static mp_obj_t files_decode_image(mp_obj_t self, mp_obj_t blob) {
    store_t st;
    store_of(self, &st);
    if (!mp_obj_is_true(blob)) {
        check(moy_app_holds(st.a, st.g, MOY_ROLE_FILES));
        return mp_const_none;
    }
    mp_buffer_info_t bi;
    mp_get_buffer_raise(blob, &bi, MP_BUFFER_READ);
    moy_buf_t b = { NULL, 0 };
    uint32_t w = 0, h = 0;
    int rc = moy_app_files_decode_image(st.a, st.g, bi.buf, bi.len, &b, &w, &h);
    if (rc == MOY_APP_ABSENT) {
        return mp_const_none;
    }
    check(rc);
    mp_obj_t t[3] = { mp_obj_new_int_from_uint(w), mp_obj_new_int_from_uint(h),
                      take(&st, &b, 0) };
    return mp_obj_new_tuple(3, t);
}
static MP_DEFINE_CONST_FUN_OBJ_2(files_decode_image_obj, files_decode_image);

static mp_obj_t files_decode_cover(mp_obj_t self, mp_obj_t blob) {
    store_t st;
    store_of(self, &st);
    if (!mp_obj_is_true(blob)) {
        check(moy_app_holds(st.a, st.g, MOY_ROLE_FILES));
        return mp_const_none;
    }
    mp_buffer_info_t bi;
    mp_get_buffer_raise(blob, &bi, MP_BUFFER_READ);
    moy_buf_t b = { NULL, 0 };
    int rc = moy_app_files_decode_cover(st.a, st.g, bi.buf, bi.len, &b);
    if (rc == MOY_APP_ABSENT) {
        return mp_const_none;
    }
    check(rc);
    mp_obj_t t[3] = { MP_OBJ_NEW_SMALL_INT(MOY_UF_COVER_SIDE),
                      MP_OBJ_NEW_SMALL_INT(MOY_UF_COVER_SIDE), take(&st, &b, 0) };
    return mp_obj_new_tuple(3, t);
}
static MP_DEFINE_CONST_FUN_OBJ_2(files_decode_cover_obj, files_decode_cover);

static mp_obj_t files_encode_cover(mp_obj_t self, mp_obj_t pix) {
    store_t st;
    store_of(self, &st);
    mp_buffer_info_t bi;
    if (!mp_get_buffer(pix, &bi, MP_BUFFER_READ)) {
        pix = mp_call_function_1(MP_OBJ_FROM_PTR(&mp_type_bytes),
                                 mp_call_function_1(MP_OBJ_FROM_PTR(&mp_type_bytearray), pix));
        mp_get_buffer_raise(pix, &bi, MP_BUFFER_READ);
    }
    moy_buf_t b = { NULL, 0 };
    int rc = moy_app_files_encode_cover(st.a, st.g, bi.buf, bi.len, &b);
    if (rc == MOY_APP_ABSENT) {
        return mp_const_none;
    }
    check(rc);
    return take(&st, &b, 0);
}
static MP_DEFINE_CONST_FUN_OBJ_2(files_encode_cover_obj, files_encode_cover);

static mp_obj_t files_sig(mp_obj_t self, mp_obj_t blob) {
    store_t st;
    store_of(self, &st);
    size_t n = 0;
    const char *p = mp_obj_is_true(blob) ? str_of(blob, &n) : "";
    uint32_t sig = 0;
    int rc = moy_app_files_sig(st.a, st.g, p, n, &sig);
    if (rc == MOY_APP_ABSENT) {
        return mp_const_none;
    }
    check(rc);
    return mp_obj_new_int_from_uint(sig);
}
static MP_DEFINE_CONST_FUN_OBJ_2(files_sig_obj, files_sig);

// stamp(blob, kind, name, sig) -> the blob stamped, or the blob as it was.
static mp_obj_t files_stamp(size_t n_args, const mp_obj_t *args) {
    store_t st;
    store_of(args[0], &st);
    mp_obj_t blob = args[1];
    if (!mp_obj_is_str(blob)) {
        check(moy_app_holds(st.a, st.g, MOY_ROLE_FILES));
        return blob;
    }
    size_t n;
    const char *p = mp_obj_str_get_data(blob, &n);
    const char *k = cstr(args[2]), *nm = cstr(args[3]);
    uint32_t sig = (uint32_t)(mp_obj_get_int_truncated(args[4]) & 0xFFFFFFFF);
    moy_buf_t b = { NULL, 0 };
    int rc = moy_app_files_stamp(st.a, st.g, p, n, k, nm, sig, &b);
    if (rc == MOY_APP_ABSENT) {
        return blob;
    }
    check(rc);
    return take(&st, &b, 1);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(files_stamp_obj, 5, 5, files_stamp);

static mp_obj_t files_encode_text(mp_obj_t self, mp_obj_t body) {
    store_t st;
    store_of(self, &st);
    int rc = moy_app_files_encode_text(st.a, st.g);
    if (rc == MOY_APP_ABSENT) {
        return mp_const_none;
    }
    check(rc);
    return mp_obj_str_make_new(&mp_type_str, 1, 0, &body);
}
static MP_DEFINE_CONST_FUN_OBJ_2(files_encode_text_obj, files_encode_text);

// decode_text(blob) -> the document's lines, [] for no text.
static mp_obj_t files_decode_text(mp_obj_t self, mp_obj_t blob) {
    store_t st;
    store_of(self, &st);
    int rc = moy_app_files_decode_text(st.a, st.g);
    if (rc != MOY_APP_ABSENT) {
        check(rc);
    }
    if (rc != MOY_APP_OK || !mp_obj_is_str(blob) || !mp_obj_is_true(blob)) {
        return mp_obj_new_list(0, NULL);
    }
    mp_obj_t a[2] = { blob, mp_obj_new_str("\n", 1) };
    return mp_obj_str_split(2, a);
}
static MP_DEFINE_CONST_FUN_OBJ_2(files_decode_text_obj, files_decode_text);

static mp_obj_t files_provenance(mp_obj_t self, mp_obj_t blob) {
    store_t st;
    store_of(self, &st);
    mp_obj_t none2 = pair(mp_const_none, mp_const_none);
    if (!mp_obj_is_str(blob) || !mp_obj_is_true(blob)) {
        check(moy_app_holds(st.a, st.g, MOY_ROLE_FILES));
        return none2;
    }
    size_t n;
    const char *p = mp_obj_str_get_data(blob, &n);
    moy_buf_t b = { NULL, 0 };
    int64_t sig = 0;
    int rc = moy_app_files_provenance(st.a, st.g, p, n, &b, &sig);
    if (rc == MOY_APP_ABSENT) {
        return none2;
    }
    check(rc);
    return pair(take(&st, &b, 1), mp_obj_new_int_from_ll(sig));
}
static MP_DEFINE_CONST_FUN_OBJ_2(files_provenance_obj, files_provenance);

static const mp_rom_map_elem_t files_locals_table[] = {
    ROW(files, readable)
    ROW(files, ready)
    ROW(files, begin)
    ROW(files, end)
    ROW(files, list)
    ROW(files, count)
    ROW(files, load)
    ROW(files, save)
    ROW(files, delete)
    ROW(files, duplicate)
    ROW(files, rename)
    ROW(files, new_name)
    ROW(files, trash_list)
    ROW(files, restore)
    ROW(files, empty_trash)
    ROW(files, history)
    ROW(files, history_ops)
    ROW(files, history_commit)
    ROW(files, encode_image)
    ROW(files, decode_image)
    ROW(files, decode_cover)
    ROW(files, encode_cover)
    ROW(files, sig)
    ROW(files, stamp)
    ROW(files, encode_text)
    ROW(files, decode_text)
    ROW(files, provenance)
};
SERVED_ROLE(files, Files)

// carts

SERVED(carts, CARTS, readable)
SERVED(carts, CARTS, ready)
SERVED(carts, CARTS, begin)
SERVED(carts, CARTS, end)
SERVED(carts, CARTS, all)
SERVED(carts, CARTS, can_journal)
SERVED(carts, CARTS, slug)
SERVED(carts, CARTS, create)
SERVED(carts, CARTS, journal)
SERVED(carts, CARTS, rescan)
SERVED(carts, CARTS, hydrate)
SERVED(carts, CARTS, load_deck)
SERVED(carts, CARTS, save_deck)
SERVED(carts, CARTS, save_code)
SERVED(carts, CARTS, images)
SERVED(carts, CARTS, save_image)
SERVED(carts, CARTS, encode_image)

static const mp_rom_map_elem_t carts_locals_table[] = {
    ROW(carts, readable)
    ROW(carts, ready)
    ROW(carts, begin)
    ROW(carts, end)
    ROW(carts, all)
    ROW(carts, can_journal)
    ROW(carts, slug)
    ROW(carts, create)
    ROW(carts, journal)
    ROW(carts, rescan)
    ROW(carts, hydrate)
    ROW(carts, load_deck)
    ROW(carts, save_deck)
    ROW(carts, save_code)
    ROW(carts, images)
    ROW(carts, save_image)
    ROW(carts, encode_image)
};
SERVED_ROLE(carts, Carts)

// nav

SERVED(nav, NAV, open_app)
SERVED(nav, NAV, is_system_app)
SERVED(nav, NAV, projects)
SERVED(nav, NAV, edit)
SERVED(nav, NAV, open_image)
SERVED(nav, NAV, open_text)
SERVED(nav, NAV, edit_file)
SERVED(nav, NAV, play)
SERVED(nav, NAV, run_script)
SERVED(nav, NAV, text_mode)

static const mp_rom_map_elem_t nav_locals_table[] = {
    ROW(nav, open_app)
    ROW(nav, is_system_app)
    ROW(nav, projects)
    ROW(nav, edit)
    ROW(nav, open_image)
    ROW(nav, open_text)
    ROW(nav, edit_file)
    ROW(nav, play)
    ROW(nav, run_script)
    ROW(nav, text_mode)
};
SERVED_ROLE(nav, Nav)

// notify

SERVED(notify, NOTIFY, achieve)

static const mp_rom_map_elem_t notify_locals_table[] = {
    ROW(notify, achieve)
};
SERVED_ROLE(notify, Notify)

// wallpaper

SERVED(wallpaper, WALLPAPER, current)
SERVED(wallpaper, WALLPAPER, carts)
SERVED(wallpaper, WALLPAPER, fills)
SERVED(wallpaper, WALLPAPER, id_for)
SERVED(wallpaper, WALLPAPER, title)
SERVED(wallpaper, WALLPAPER, select)
SERVED(wallpaper, WALLPAPER, preview)
SERVED(wallpaper, WALLPAPER, thumbnail)

// load_copy() -> (text or None, err); save_copy(text) -> (None, err): C rows.
static mp_obj_t wallpaper_load_copy(mp_obj_t self) {
    store_t st;
    store_of(self, &st);
    moy_buf_t b = { NULL, 0 };
    int rc = 0;
    STORE_BEGIN(st)
    rc = moy_app_wallpaper_load_copy(st.a, st.g, &b);
    STORE_END(st)
    if (rc == MOY_APP_ABSENT) {
        return pair(mp_const_none, mp_const_none);
    }
    if (rc != MOY_APP_OK) {
        return pair(mp_const_none, err_of(&st, rc));
    }
    return pair(take(&st, &b, 1), mp_const_none);
}
static MP_DEFINE_CONST_FUN_OBJ_1(wallpaper_load_copy_obj, wallpaper_load_copy);

static mp_obj_t wallpaper_save_copy(mp_obj_t self, mp_obj_t text) {
    store_t st;
    store_of(self, &st);
    mp_buffer_info_t bi;
    mp_get_buffer_raise(text, &bi, MP_BUFFER_READ);
    int rc = 0;
    STORE_BEGIN(st)
    rc = moy_app_wallpaper_save_copy(st.a, st.g, bi.buf, bi.len);
    STORE_END(st)
    return pair(mp_const_none, rc == MOY_APP_OK ? mp_const_none : err_of(&st, rc));
}
static MP_DEFINE_CONST_FUN_OBJ_2(wallpaper_save_copy_obj, wallpaper_save_copy);

static const mp_rom_map_elem_t wallpaper_locals_table[] = {
    ROW(wallpaper, current)
    ROW(wallpaper, carts)
    ROW(wallpaper, fills)
    ROW(wallpaper, id_for)
    ROW(wallpaper, title)
    ROW(wallpaper, select)
    ROW(wallpaper, preview)
    ROW(wallpaper, thumbnail)
    ROW(wallpaper, load_copy)
    ROW(wallpaper, save_copy)
};
SERVED_ROLE(wallpaper, Wallpaper)

// install

SERVED(install, INSTALL, hold)
SERVED(install, INSTALL, release)
SERVED(install, INSTALL, fit)
SERVED(install, INSTALL, memory)
SERVED(install, INSTALL, chip)
SERVED(install, INSTALL, runtimes)
SERVED(install, INSTALL, home)
SERVED(install, INSTALL, can_pick)
SERVED(install, INSTALL, pick)
SERVED(install, INSTALL, root)
SERVED(install, INSTALL, writable)
SERVED(install, INSTALL, op)
SERVED(install, INSTALL, rescan)
SERVED(install, INSTALL, free)
SERVED(install, INSTALL, find)
SERVED(install, INSTALL, net)
SERVED(install, INSTALL, keep)

static const mp_rom_map_elem_t install_locals_table[] = {
    ROW(install, hold)
    ROW(install, release)
    ROW(install, fit)
    ROW(install, memory)
    ROW(install, chip)
    ROW(install, runtimes)
    ROW(install, home)
    ROW(install, can_pick)
    ROW(install, pick)
    ROW(install, root)
    ROW(install, writable)
    ROW(install, op)
    ROW(install, rescan)
    ROW(install, free)
    ROW(install, find)
    ROW(install, net)
    ROW(install, keep)
};
SERVED_ROLE(install, Install)

// artwork

// current() -> (kind, name) of Paint's open picture, or None while none is named.
static mp_obj_t artwork_current(mp_obj_t self) {
    uint32_t g;
    moy_appabi_t *a = role_app(self, &g);
    char kind[MOY_APP_DOC_MAX], name[MOY_APP_DOC_MAX];
    size_t kn = 0, nn = 0;
    int rc = moy_app_artwork_current(a, g, kind, sizeof(kind), &kn, name, sizeof(name), &nn);
    if (rc == MOY_APP_ABSENT) {
        return mp_const_none;
    }
    check(rc);
    if (kn > sizeof(kind) || nn > sizeof(name)) {
        return mp_const_none;           // a row no picture of Paint's names
    }
    mp_obj_t items[2] = { mp_obj_new_str(kind, kn), mp_obj_new_str(name, nn) };
    return mp_obj_new_tuple(2, items);
}
static MP_DEFINE_CONST_FUN_OBJ_1(artwork_current_obj, artwork_current);

// follow(kind, old, new) -> True when the open picture was kind/old and is now
// kind/new.
static mp_obj_t artwork_follow(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    uint32_t g;
    moy_appabi_t *a = role_app(args[0], &g);
    size_t kn, on, nn;
    const char *k = str_of(args[1], &kn);
    const char *o = str_of(args[2], &on);
    const char *n = str_of(args[3], &nn);
    int rc = moy_app_artwork_follow(a, g, k, kn, o, on, n, nn);
    moy_spine_settings_raise();
    if (rc == MOY_APP_ABSENT) {
        return mp_const_false;
    }
    check(rc);
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(artwork_follow_obj, 4, 4, artwork_follow);

static const mp_rom_map_elem_t artwork_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_current), MP_ROM_PTR(&artwork_current_obj) },
    { MP_ROM_QSTR(MP_QSTR_follow), MP_ROM_PTR(&artwork_follow_obj) },
};
static MP_DEFINE_CONST_DICT(artwork_locals, artwork_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    artwork_type, MP_QSTR_Artwork, MP_TYPE_FLAG_NONE,
    make_new, role_make_new,
    locals_dict, &artwork_locals
    );

// -- the policy ----------------------------------------------------------------------------

static void policy_read(mp_obj_t perms, moy_app_policy_t *p) {
    moy_app_policy_init(p);
    if (perms == mp_const_none) {
        return;
    }
    mp_obj_t it = mp_getiter(perms, NULL), x;
    while ((x = mp_iternext(it)) != MP_OBJ_STOP_ITERATION) {
        if (!mp_obj_is_str(x)) {
            x = mp_obj_str_make_new(&mp_type_str, 1, 0, &x);
        }
        size_t n;
        const char *s = mp_obj_str_get_data(x, &n);
        moy_app_policy_add(p, s, n);
    }
}

// policy(perms) -> (roles, kind): the roles in the table's order, the files
// kind or None.
static mp_obj_t mod_policy(mp_obj_t perms) {
    moy_app_policy_t p;
    policy_read(perms, &p);
    int kind;
    uint32_t mask = moy_app_policy_roles(&p, &kind);
    mp_obj_t items[MOY_ROLE_N];
    size_t n = 0;
    for (int i = 0; i < MOY_ROLE_N; i++) {
        if (mask & (1u << i)) {
            const char *r = moy_app_role_name(i);
            items[n++] = mp_obj_new_str(r, strlen(r));
        }
    }
    const char *k = moy_app_kind_name(kind);
    mp_obj_t out[2] = { mp_obj_new_tuple(n, items),
                        k != NULL ? mp_obj_new_str(k, strlen(k)) : mp_const_none };
    return mp_obj_new_tuple(2, out);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_policy_obj, mod_policy);

static mp_obj_t mod_manifest_error(mp_obj_t perms) {
    moy_app_policy_t p;
    policy_read(perms, &p);
    char why[96];
    size_t n = moy_app_policy_error(&p, why, sizeof(why));
    return n ? mp_obj_new_str(why, n) : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_manifest_error_obj, mod_manifest_error);

static mp_obj_t mod_id_for(mp_obj_t id, mp_obj_t title) {
    size_t idn = 0, tn = 0;
    const char *i = NULL, *t = NULL;
    if (mp_obj_is_str(id)) {
        i = mp_obj_str_get_data(id, &idn);
    }
    if (mp_obj_is_str(title)) {
        t = mp_obj_str_get_data(title, &tn);
    }
    char out[MOY_APP_ID_MAX + 1];
    size_t n = moy_app_id_for(i, idn, t, tn, out, sizeof(out));
    return mp_obj_new_str(out, n);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_id_for_obj, mod_id_for);

// The most names a table has: roles, rows, kinds, tokens.
#define NAMES_MAX (MOY_ROW_N > MOY_APP_TOKENS ? MOY_ROW_N : MOY_APP_TOKENS)

static mp_obj_t names_tuple(const char *(*name)(int), int n) {
    mp_obj_t items[NAMES_MAX];
    for (int i = 0; i < n; i++) {
        const char *s = name(i);
        items[i] = mp_obj_new_str(s, strlen(s));
    }
    return mp_obj_new_tuple(n, items);
}

static mp_obj_t mod_roles(void) {
    return names_tuple(moy_app_role_name, MOY_ROLE_N);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_roles_obj, mod_roles);

static mp_obj_t mod_rows(void) {
    return names_tuple(moy_app_row_name, MOY_ROW_N);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_rows_obj, mod_rows);

static mp_obj_t mod_table(void) {
    mp_obj_tuple_t *t = MP_OBJ_TO_PTR(mp_obj_new_tuple(MOY_APP_TABLE_N, NULL));
    for (uint32_t i = 0; i < MOY_APP_TABLE_N; i++) {
        const char *s = moy_app_table_name(i);
        t->items[i] = mp_obj_new_str(s, strlen(s));
    }
    return MP_OBJ_FROM_PTR(t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_table_obj, mod_table);

// The kernel's ROLE upcall (native/moy_kernel), which every image carrying
// this module links.
int32_t moy_loop_role(uint32_t grant, uint32_t row, const uint8_t *arg, size_t n,
                      uint8_t *ans, size_t cap);

static mp_obj_t mod_door_bind(mp_obj_t on) {
    moy_app_door_bind(mp_obj_is_true(on) ? moy_loop_role : NULL);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_door_bind_obj, mod_door_bind);

// perms() -> ((permission, role), ...): the allowlist, in role order.
static mp_obj_t mod_perms(void) {
    mp_obj_t items[MOY_ROLE_N];
    size_t n = 0;
    for (int i = 0; i < MOY_ROLE_N; i++) {
        const char *p = moy_app_perm_of(i);
        if (*p != '\0') {
            const char *r = moy_app_role_name(i);
            mp_obj_t pair[2] = { mp_obj_new_str(p, strlen(p)), mp_obj_new_str(r, strlen(r)) };
            items[n++] = mp_obj_new_tuple(2, pair);
        }
    }
    return mp_obj_new_tuple(n, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_perms_obj, mod_perms);

static mp_obj_t mod_tokens(void) {
    return names_tuple(moy_app_token_name, MOY_APP_TOKENS);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_tokens_obj, mod_tokens);

static const char *kind_name(int k) {
    return moy_app_kind_name(k);
}

static mp_obj_t mod_kinds(void) {
    return names_tuple(kind_name, MOY_APP_KINDS);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_kinds_obj, mod_kinds);

// -- the module ----------------------------------------------------------------------------

static const mp_rom_map_elem_t moy_app_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_app) },
    { MP_ROM_QSTR(MP_QSTR_App), MP_ROM_PTR(&app_type) },
    { MP_ROM_QSTR(MP_QSTR_kernel), MP_ROM_PTR(&mod_kernel_obj) },
    { MP_ROM_QSTR(MP_QSTR_Damage), MP_ROM_PTR(&damage_type) },
    { MP_ROM_QSTR(MP_QSTR_Surface), MP_ROM_PTR(&surface_type) },
    { MP_ROM_QSTR(MP_QSTR_Theme), MP_ROM_PTR(&theme_type) },
    { MP_ROM_QSTR(MP_QSTR_Prefs), MP_ROM_PTR(&prefs_type) },
    { MP_ROM_QSTR(MP_QSTR_Clipboard), MP_ROM_PTR(&clip_type) },
    { MP_ROM_QSTR(MP_QSTR_Artwork), MP_ROM_PTR(&artwork_type) },
    { MP_ROM_QSTR(MP_QSTR_Files), MP_ROM_PTR(&files_type) },
    { MP_ROM_QSTR(MP_QSTR_Carts), MP_ROM_PTR(&carts_type) },
    { MP_ROM_QSTR(MP_QSTR_Nav), MP_ROM_PTR(&nav_type) },
    { MP_ROM_QSTR(MP_QSTR_Notify), MP_ROM_PTR(&notify_type) },
    { MP_ROM_QSTR(MP_QSTR_Wallpaper), MP_ROM_PTR(&wallpaper_type) },
    { MP_ROM_QSTR(MP_QSTR_Install), MP_ROM_PTR(&install_type) },
    { MP_ROM_QSTR(MP_QSTR_policy), MP_ROM_PTR(&mod_policy_obj) },
    { MP_ROM_QSTR(MP_QSTR_manifest_error), MP_ROM_PTR(&mod_manifest_error_obj) },
    { MP_ROM_QSTR(MP_QSTR_id_for), MP_ROM_PTR(&mod_id_for_obj) },
    { MP_ROM_QSTR(MP_QSTR_roles), MP_ROM_PTR(&mod_roles_obj) },
    { MP_ROM_QSTR(MP_QSTR_rows), MP_ROM_PTR(&mod_rows_obj) },
    { MP_ROM_QSTR(MP_QSTR_table), MP_ROM_PTR(&mod_table_obj) },
    { MP_ROM_QSTR(MP_QSTR_door_bind), MP_ROM_PTR(&mod_door_bind_obj) },
    { MP_ROM_QSTR(MP_QSTR_perms), MP_ROM_PTR(&mod_perms_obj) },
    { MP_ROM_QSTR(MP_QSTR_kinds), MP_ROM_PTR(&mod_kinds_obj) },
    { MP_ROM_QSTR(MP_QSTR_tokens), MP_ROM_PTR(&mod_tokens_obj) },
    { MP_ROM_QSTR(MP_QSTR_CLIP_MAX), MP_ROM_INT(MOY_APP_CLIP_MAX) },
    { MP_ROM_QSTR(MP_QSTR_SLOTS), MP_ROM_INT(MOY_GRANT_SLOTS) },
};
static MP_DEFINE_CONST_DICT(moy_app_globals, moy_app_globals_table);

const mp_obj_module_t moy_app_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_app_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_app, moy_app_module);
