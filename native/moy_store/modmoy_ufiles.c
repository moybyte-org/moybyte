// moy_ufiles's MicroPython binding: the user-files layer (moy_ufiles.h) as the
// module `moy_carts` re-exports its user-files names from, on every console,
// the browser and the desktop MicroPython. It is built with native/moy_app (its
// micropython.cmake), whose files and wallpaper rows reach the same layer, so
// an image that denies moy_app (the Zero) carries neither.
//
// A verb answers what moy_files.py's and moy_file_ops.py's did: a name, a list,
// None for nothing there; ValueError for a refused argument, OSError for the
// store, MemoryError for memory. Every call runs under nlr_push: a borrowed
// volume's block device can raise, and the store's scratch is freed before the
// exception goes on. `root` is the carts folder, absolute; every verb takes it
// last, defaulting to CARTS_DIR.

#include <string.h>

#include "py/mperrno.h"
#include "py/objstr.h"
#include "py/runtime.h"

#include "moy_ufiles.h"

void moy_store_unwind(void);

#define GUARD_BEGIN { nlr_buf_t nlr_; if (nlr_push(&nlr_) == 0) {
#define GUARD_END nlr_pop(); } else { moy_store_unwind(); nlr_jump(nlr_.ret_val); } }

static const char CARTS_DIR[] = "/sd/moybyte/carts";

static MP_NORETURN void raise_rc(int rc) {
    if (rc == MOY_UF_BAD) {
        mp_raise_ValueError(MP_ERROR_TEXT("an argument the store refuses"));
    }
    if (rc == MOY_ENOMEM) {
        mp_raise_type(&mp_type_MemoryError);
    }
    mp_raise_OSError(rc);
}

static const char *s(mp_obj_t o) {
    return mp_obj_str_get_str(o);
}

static const char *root_of(size_t n_args, const mp_obj_t *args, size_t at) {
    return n_args > at ? s(args[at]) : CARTS_DIR;
}

static mp_obj_t take(moy_buf_t *b, int text) {
    mp_obj_t o;
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        o = text ? mp_obj_new_str(b->p, b->n) : mp_obj_new_bytes((const byte *)b->p, b->n);
        nlr_pop();
    } else {
        moy_buf_free(b);
        nlr_jump(nlr.ret_val);
    }
    moy_buf_free(b);
    return o;
}

static mp_obj_t json_call(qstr name, mp_obj_t arg) {
    mp_obj_t json = mp_import_name(MP_QSTR_json, mp_const_none, MP_OBJ_NEW_SMALL_INT(0));
    return mp_call_function_1(mp_load_attr(json, name), arg);
}

static mp_obj_t names(moy_buf_t *b, uint32_t count, int pairs) {
    mp_obj_t raw = take(b, 0);
    mp_buffer_info_t bi;
    mp_get_buffer_raise(raw, &bi, MP_BUFFER_READ);
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

static mp_obj_t str_tail(mp_obj_t name, size_t k) {
    size_t n;
    const char *p = mp_obj_str_get_data(name, &n);
    return k ? mp_obj_new_str(p + n - k, k) : MP_OBJ_NEW_QSTR(MP_QSTR_);
}

// -- the registry and the vault ------------------------------------------------------

static mp_obj_t mod_script_ext(mp_obj_t name) {
    return str_tail(name, moy_uf_script_ext(s(name)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_script_ext_obj, mod_script_ext);

static mp_obj_t mod_vault_ext(mp_obj_t name) {
    return str_tail(name, moy_uf_vault_ext(s(name)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_vault_ext_obj, mod_vault_ext);

// FILE_KINDS: {kind: (ext, folder_valued, auto-name base)}
static mp_obj_t mod_kinds(void) {
    mp_obj_t d = mp_obj_new_dict(MOY_UF_KINDS);
    for (int i = 0; i < MOY_UF_KINDS; i++) {
        const char *e = moy_uf_kind_ext(i), *b = moy_uf_kind_base(i), *k = moy_uf_kind(i);
        mp_obj_t t[3] = { mp_obj_new_str(e, strlen(e)), mp_obj_new_bool(moy_uf_kind_folder(i)),
                          mp_obj_new_str(b, strlen(b)) };
        mp_obj_dict_store(d, mp_obj_new_str(k, strlen(k)), mp_obj_new_tuple(3, t));
    }
    return d;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_kinds_obj, mod_kinds);

// project_kind(path_or_folder): "project:" and the last path segment.
static mp_obj_t mod_project_kind(mp_obj_t path) {
    size_t n;
    const char *p = mp_obj_str_get_data(path, &n);
    size_t cut = 0;
    for (size_t i = 0; i < n; i++) {
        if (p[i] == '/' || p[i] == '\\') {
            cut = i + 1u;
        }
    }
    vstr_t v;
    vstr_init(&v, 8u + n - cut);
    vstr_add_str(&v, "project:");
    vstr_add_strn(&v, p + cut, n - cut);
    return mp_obj_new_str_from_vstr(&v);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_project_kind_obj, mod_project_kind);

static mp_obj_t mod_project_folder(mp_obj_t kind) {
    const char *k = s(kind);
    const char *f = moy_uf_project_folder(k);
    return mp_obj_new_str(f, strlen(f));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_project_folder_obj, mod_project_folder);

// -- paths ------------------------------------------------------------------------------

static mp_obj_t path_of(int which, const char *root, const char *kind, const char *name) {
    moy_buf_t b = { NULL, 0 };
    int rc = 0;
    GUARD_BEGIN
    rc = moy_uf_path(which, root, kind, name, &b);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return take(&b, 1);
}

// f(root=CARTS_DIR)
static mp_obj_t mod_files_root(size_t n_args, const mp_obj_t *args) {
    return path_of(MOY_UF_PATH_FILES, root_of(n_args, args, 0), "", "");
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_files_root_obj, 0, 1, mod_files_root);

// f(kind, root=CARTS_DIR)
static mp_obj_t mod_file_kind_dir(size_t n_args, const mp_obj_t *args) {
    return path_of(MOY_UF_PATH_KIND, root_of(n_args, args, 1), s(args[0]), "");
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_file_kind_dir_obj, 1, 2, mod_file_kind_dir);

static mp_obj_t mod_project_dir(size_t n_args, const mp_obj_t *args) {
    return path_of(MOY_UF_PATH_PROJECT, root_of(n_args, args, 1), s(args[0]), "");
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_project_dir_obj, 1, 2, mod_project_dir);

// f(kind, name, root=CARTS_DIR)
#define PATH_VERB(fn, which) \
    static mp_obj_t mod_##fn(size_t n_args, const mp_obj_t *args) { \
        return path_of(which, root_of(n_args, args, 2), s(args[0]), s(args[1])); \
    } \
    static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_##fn##_obj, 2, 3, mod_##fn);

PATH_VERB(file_path, MOY_UF_PATH_FILE)
PATH_VERB(history_path, MOY_UF_PATH_HISTORY)
PATH_VERB(trash_path, MOY_UF_PATH_TRASH)
PATH_VERB(history_trash_path, MOY_UF_PATH_HISTORY_TRASH)
PATH_VERB(project_file_path, MOY_UF_PATH_PROJECT_FILE)

// -- the items -----------------------------------------------------------------------------

static mp_obj_t mod_list_files(size_t n_args, const mp_obj_t *args) {
    const char *root = root_of(n_args, args, 1), *kind = s(args[0]);
    moy_buf_t b = { NULL, 0 };
    uint32_t n = 0;
    int rc = 0;
    GUARD_BEGIN
    rc = moy_uf_list(root, kind, &b, &n);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return names(&b, n, 0);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_list_files_obj, 1, 2, mod_list_files);

static mp_obj_t mod_count_files(size_t n_args, const mp_obj_t *args) {
    const char *root = root_of(n_args, args, 1), *kind = s(args[0]);
    uint32_t n = 0;
    int rc = 0;
    GUARD_BEGIN
    rc = moy_uf_count(root, kind, &n);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return mp_obj_new_int_from_uint(n);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_count_files_obj, 1, 2, mod_count_files);

static mp_obj_t mod_load_file(size_t n_args, const mp_obj_t *args) {
    const char *root = root_of(n_args, args, 2), *kind = s(args[0]), *name = s(args[1]);
    moy_buf_t b = { NULL, 0 };
    int binary = 0, rc = 0;
    GUARD_BEGIN
    rc = moy_uf_load(root, kind, name, &b, &binary);
    GUARD_END
    if (rc == MOY_UF_NONE) {
        return mp_const_none;
    }
    if (rc) {
        raise_rc(rc);
    }
    return take(&b, !binary);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_load_file_obj, 2, 3, mod_load_file);

static mp_obj_t named(int rc, const char *out) {
    if (rc) {
        raise_rc(rc);
    }
    return mp_obj_new_str(out, strlen(out));
}

// save_file(kind, name, text, root=CARTS_DIR)
static mp_obj_t mod_save_file(size_t n_args, const mp_obj_t *args) {
    const char *root = root_of(n_args, args, 3), *kind = s(args[0]), *name = s(args[1]);
    mp_buffer_info_t bi;
    mp_get_buffer_raise(args[2], &bi, MP_BUFFER_READ);
    char out[MOY_UF_NAME_MAX + 1];
    int rc = 0;
    GUARD_BEGIN
    rc = moy_uf_save(root, kind, name, bi.buf, bi.len, out);
    GUARD_END
    return named(rc, out);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_save_file_obj, 3, 4, mod_save_file);

// new_file_name(kind, root=CARTS_DIR, base=None)
static mp_obj_t mod_new_file_name(size_t n_args, const mp_obj_t *args) {
    const char *root = root_of(n_args, args, 1), *kind = s(args[0]);
    const char *base = n_args > 2 && mp_obj_is_true(args[2]) ? s(args[2]) : NULL;
    char out[MOY_UF_NAME_MAX + 1];
    int rc = 0;
    GUARD_BEGIN
    rc = moy_uf_new_name(root, kind, base, out);
    GUARD_END
    return named(rc, out);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_new_file_name_obj, 1, 3, mod_new_file_name);

typedef int (*name_fn)(const char *, const char *, const char *, char *);

// f(kind, name, root=CARTS_DIR) -> a name
static mp_obj_t name_verb(name_fn fn, size_t n_args, const mp_obj_t *args) {
    const char *root = root_of(n_args, args, 2), *kind = s(args[0]), *name = s(args[1]);
    char out[MOY_UF_NAME_MAX + 1];
    int rc = 0;
    GUARD_BEGIN
    rc = fn(root, kind, name, out);
    GUARD_END
    return named(rc, out);
}

#define NAME_VERB(fn, c) \
    static mp_obj_t mod_##fn(size_t n_args, const mp_obj_t *args) { \
        return name_verb(c, n_args, args); \
    } \
    static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_##fn##_obj, 2, 3, mod_##fn);

static int free_name(const char *root, const char *kind, const char *title, char *out) {
    return moy_uf_free_name(root, kind, title, out);
}

NAME_VERB(free_file_name, free_name)
NAME_VERB(duplicate_file, moy_uf_duplicate)
NAME_VERB(delete_file, moy_uf_delete)
NAME_VERB(restore_file, moy_uf_restore)

// rename_file(kind, name, new_title, root=CARTS_DIR)
static mp_obj_t mod_rename_file(size_t n_args, const mp_obj_t *args) {
    const char *root = root_of(n_args, args, 3), *kind = s(args[0]), *name = s(args[1]);
    const char *title = s(args[2]);
    char out[MOY_UF_NAME_MAX + 1];
    int rc = 0;
    GUARD_BEGIN
    rc = moy_uf_rename(root, kind, name, title, out);
    GUARD_END
    return named(rc, out);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_rename_file_obj, 3, 4, mod_rename_file);

// -- the trash ------------------------------------------------------------------------------

static mp_obj_t mod_trash_list(size_t n_args, const mp_obj_t *args) {
    const char *root = root_of(n_args, args, 0);
    moy_buf_t b = { NULL, 0 };
    uint32_t n = 0;
    int rc = 0;
    GUARD_BEGIN
    rc = moy_uf_trash_list(root, &b, &n);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return names(&b, n, 1);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_trash_list_obj, 0, 1, mod_trash_list);

static mp_obj_t prune_trash(const char *root, uint32_t keep) {
    int rc = 0;
    GUARD_BEGIN
    rc = moy_uf_prune_trash(root, keep);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return mp_const_none;
}

// prune_trash(root=CARTS_DIR, keep=TRASH_KEEP)
static mp_obj_t mod_prune_trash(size_t n_args, const mp_obj_t *args, mp_map_t *kw) {
    static const mp_arg_t allowed[] = {
        { MP_QSTR_root, MP_ARG_OBJ, { .u_obj = mp_const_none } },
        { MP_QSTR_keep, MP_ARG_INT, { .u_int = MOY_UF_TRASH_KEEP } },
    };
    mp_arg_val_t v[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, args, kw, MP_ARRAY_SIZE(allowed), allowed, v);
    const char *root = v[0].u_obj == mp_const_none ? CARTS_DIR : s(v[0].u_obj);
    return prune_trash(root, v[1].u_int < 0 ? 0u : (uint32_t)v[1].u_int);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(mod_prune_trash_obj, 0, mod_prune_trash);

static mp_obj_t mod_empty_trash(size_t n_args, const mp_obj_t *args) {
    return prune_trash(root_of(n_args, args, 0), 0u);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_empty_trash_obj, 0, 1, mod_empty_trash);

// -- the sidecars -----------------------------------------------------------------------------

typedef int (*json_fn)(const char *, const char *, const char *, moy_buf_t *);

static mp_obj_t json_verb(json_fn fn, size_t n_args, const mp_obj_t *args) {
    const char *root = root_of(n_args, args, 2), *kind = s(args[0]), *name = s(args[1]);
    moy_buf_t b = { NULL, 0 };
    int rc = 0;
    GUARD_BEGIN
    rc = fn(root, kind, name, &b);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return json_call(MP_QSTR_loads, take(&b, 1));
}

static mp_obj_t mod_load_history(size_t n_args, const mp_obj_t *args) {
    return json_verb(moy_uf_history, n_args, args);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_load_history_obj, 2, 3, mod_load_history);

static mp_obj_t mod_history_ops(size_t n_args, const mp_obj_t *args) {
    return json_verb(moy_uf_history_ops, n_args, args);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_history_ops_obj, 2, 3, mod_history_ops);

// history_commit(kind, name, ops, keyframe=None, root=CARTS_DIR) -> a prune's
// failure as text, or None
static mp_obj_t mod_history_commit(size_t n_args, const mp_obj_t *args, mp_map_t *kw) {
    static const mp_arg_t allowed[] = {
        { MP_QSTR_kind, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_name, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_ops, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_keyframe, MP_ARG_OBJ, { .u_obj = mp_const_none } },
        { MP_QSTR_root, MP_ARG_OBJ, { .u_obj = mp_const_none } },
    };
    mp_arg_val_t v[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, args, kw, MP_ARRAY_SIZE(allowed), allowed, v);
    const char *kind = s(v[0].u_obj), *name = s(v[1].u_obj);
    const char *root = v[4].u_obj == mp_const_none ? CARTS_DIR : s(v[4].u_obj);
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
    int err = 0, rc = 0;
    GUARD_BEGIN
    rc = moy_uf_history_commit(root, kind, name, ops, on, kf, kn, &err);
    GUARD_END
    (void)ops_text;
    (void)kf_text;
    if (rc) {
        raise_rc(rc);
    }
    if (err == 0) {
        return mp_const_none;
    }
    if (err == MOY_UF_BAD) {
        return mp_obj_new_str("an argument the store refuses", 29);
    }
    mp_obj_t e = mp_obj_new_exception_arg1(&mp_type_OSError, MP_OBJ_NEW_SMALL_INT(err));
    return mp_obj_str_make_new(&mp_type_str, 1, 0, &e);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(mod_history_commit_obj, 3, mod_history_commit);

// prune_history(kind, name, root=CARTS_DIR, keep=HISTORY_KEEP) -> records dropped
static mp_obj_t mod_prune_history(size_t n_args, const mp_obj_t *args, mp_map_t *kw) {
    static const mp_arg_t allowed[] = {
        { MP_QSTR_kind, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_name, MP_ARG_REQUIRED | MP_ARG_OBJ, { .u_obj = MP_OBJ_NULL } },
        { MP_QSTR_root, MP_ARG_OBJ, { .u_obj = mp_const_none } },
        { MP_QSTR_keep, MP_ARG_INT, { .u_int = MOY_UF_HISTORY_KEEP } },
    };
    mp_arg_val_t v[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, args, kw, MP_ARRAY_SIZE(allowed), allowed, v);
    const char *root = v[2].u_obj == mp_const_none ? CARTS_DIR : s(v[2].u_obj);
    const char *kind = s(v[0].u_obj), *name = s(v[1].u_obj);
    uint32_t dropped = 0;
    int rc = 0;
    GUARD_BEGIN
    rc = moy_uf_prune_history(root, kind, name,
                              v[3].u_int < 0 ? 0u : (uint32_t)v[3].u_int, &dropped);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return mp_obj_new_int_from_uint(dropped);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(mod_prune_history_obj, 2, mod_prune_history);

static mp_obj_t mod_clear_history(size_t n_args, const mp_obj_t *args) {
    const char *root = root_of(n_args, args, 2), *kind = s(args[0]), *name = s(args[1]);
    int rc = 0;
    GUARD_BEGIN
    rc = moy_uf_clear_history(root, kind, name);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_clear_history_obj, 2, 3, mod_clear_history);

static mp_obj_t mod_history_prune_fails(void) {
    return mp_obj_new_int_from_uint(moy_uf_prune_fails());
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_history_prune_fails_obj, mod_history_prune_fails);

// -- provenance and the codecs ----------------------------------------------------------------

static mp_obj_t mod_content_sig(mp_obj_t text) {
    if (!mp_obj_is_true(text)) {
        return MP_OBJ_NEW_SMALL_INT(0);
    }
    size_t n;
    const char *p = mp_obj_str_get_data(text, &n);
    return mp_obj_new_int_from_uint(moy_uf_sig(p, n));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_content_sig_obj, mod_content_sig);

static mp_obj_t mod_stamp_provenance(size_t n_args, const mp_obj_t *args) {
    mp_obj_t blob = args[0];
    if (!mp_obj_is_str(blob)) {
        return blob;
    }
    size_t n;
    const char *p = mp_obj_str_get_data(blob, &n);
    uint32_t sig = (uint32_t)(mp_obj_get_int_truncated(args[3]) & 0xFFFFFFFF);
    moy_buf_t b = { NULL, 0 };
    int rc = 0;
    GUARD_BEGIN
    rc = moy_uf_stamp(p, n, s(args[1]), s(args[2]), sig, &b);
    GUARD_END
    if (rc == MOY_UF_NONE) {
        return blob;
    }
    if (rc) {
        raise_rc(rc);
    }
    return take(&b, 1);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_stamp_provenance_obj, 4, 4, mod_stamp_provenance);

static mp_obj_t mod_read_provenance(mp_obj_t blob) {
    mp_obj_t none2[2] = { mp_const_none, mp_const_none };
    if (!mp_obj_is_str(blob)) {
        return mp_obj_new_tuple(2, none2);
    }
    size_t n;
    const char *p = mp_obj_str_get_data(blob, &n);
    moy_buf_t b = { NULL, 0 };
    int64_t sig = 0;
    int rc = 0;
    GUARD_BEGIN
    rc = moy_uf_provenance(p, n, &b, &sig);
    GUARD_END
    if (rc == MOY_UF_NONE) {
        return mp_obj_new_tuple(2, none2);
    }
    if (rc) {
        raise_rc(rc);
    }
    mp_obj_t t[2] = { take(&b, 1), mp_obj_new_int_from_ll(sig) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_read_provenance_obj, mod_read_provenance);

static mp_obj_t mod_load_copy(size_t n_args, const mp_obj_t *args) {
    const char *root = root_of(n_args, args, 0);
    moy_buf_t b = { NULL, 0 };
    int rc = 0;
    GUARD_BEGIN
    rc = moy_uf_copy_load(root, &b);
    GUARD_END
    if (rc == MOY_UF_NONE) {
        return mp_const_none;
    }
    if (rc) {
        raise_rc(rc);
    }
    return take(&b, 1);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_load_copy_obj, 0, 1, mod_load_copy);

static mp_obj_t mod_save_copy(size_t n_args, const mp_obj_t *args) {
    const char *root = root_of(n_args, args, 1);
    mp_buffer_info_t bi;
    mp_get_buffer_raise(args[0], &bi, MP_BUFFER_READ);
    int rc = 0;
    GUARD_BEGIN
    rc = moy_uf_copy_save(root, bi.buf, bi.len);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_save_copy_obj, 1, 2, mod_save_copy);

// The codecs: what the files role's rows answer.
static mp_obj_t pixels(mp_obj_t pix, mp_buffer_info_t *bi) {
    if (!mp_get_buffer(pix, bi, MP_BUFFER_READ)) {
        pix = mp_call_function_1(MP_OBJ_FROM_PTR(&mp_type_bytes), pix);
        mp_get_buffer_raise(pix, bi, MP_BUFFER_READ);
    }
    return pix;
}

static mp_obj_t mod_encode_image(mp_obj_t w, mp_obj_t h, mp_obj_t pix) {
    mp_buffer_info_t bi;
    pix = pixels(pix, &bi);
    mp_int_t iw = mp_obj_get_int(w), ih = mp_obj_get_int(h);
    moy_buf_t b = { NULL, 0 };
    int rc = iw > 0 && ih > 0
             ? moy_uf_encode_image((uint32_t)iw, (uint32_t)ih, bi.buf, bi.len, &b) : MOY_UF_BAD;
    if (rc == MOY_UF_BAD) {
        mp_raise_ValueError(MP_ERROR_TEXT("bad artwork size"));
    }
    if (rc) {
        raise_rc(rc);
    }
    return take(&b, 1);
}
static MP_DEFINE_CONST_FUN_OBJ_3(mod_encode_image_obj, mod_encode_image);

static mp_obj_t mod_decode_image(mp_obj_t blob) {
    mp_buffer_info_t bi;
    mp_get_buffer_raise(blob, &bi, MP_BUFFER_READ);
    moy_buf_t b = { NULL, 0 };
    uint32_t w = 0, h = 0;
    int rc = moy_uf_decode_image(bi.buf, bi.len, &b, &w, &h);
    if (rc == MOY_UF_NONE) {
        return mp_const_none;
    }
    if (rc) {
        raise_rc(rc);
    }
    mp_obj_t t[3] = { mp_obj_new_int_from_uint(w), mp_obj_new_int_from_uint(h), take(&b, 0) };
    return mp_obj_new_tuple(3, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_decode_image_obj, mod_decode_image);

static mp_obj_t mod_encode_cover(mp_obj_t pix) {
    mp_buffer_info_t bi;
    pix = pixels(pix, &bi);
    moy_buf_t b = { NULL, 0 };
    int rc = moy_uf_encode_cover(bi.buf, bi.len, &b);
    if (rc) {
        raise_rc(rc);
    }
    return take(&b, 0);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_encode_cover_obj, mod_encode_cover);

static mp_obj_t mod_decode_cover(mp_obj_t data) {
    mp_buffer_info_t bi;
    mp_get_buffer_raise(data, &bi, MP_BUFFER_READ);
    moy_buf_t b = { NULL, 0 };
    int rc = moy_uf_decode_cover(bi.buf, bi.len, &b);
    if (rc == MOY_UF_NONE) {
        return mp_const_none;
    }
    if (rc) {
        raise_rc(rc);
    }
    mp_obj_t t[3] = { MP_OBJ_NEW_SMALL_INT(MOY_UF_COVER_SIDE),
                      MP_OBJ_NEW_SMALL_INT(MOY_UF_COVER_SIDE), take(&b, 0) };
    return mp_obj_new_tuple(3, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_decode_cover_obj, mod_decode_cover);

static mp_obj_t mod_palette(void) {
    return mp_obj_new_bytes(moy_uf_palette(), 192);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_palette_obj, mod_palette);

static mp_obj_t mod_clock_fix(mp_obj_t ts) {
    moy_uf_clock_fix((int64_t)mp_obj_get_int(ts));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_clock_fix_obj, mod_clock_fix);

static MP_DEFINE_STR_OBJ(carts_dir_obj, "/sd/moybyte/carts");
static MP_DEFINE_STR_OBJ(history_dir_obj, ".history");
static MP_DEFINE_STR_OBJ(history_ext_obj, ".jsonl");
static MP_DEFINE_STR_OBJ(doc_ext_obj, ".md");
static MP_DEFINE_STR_OBJ(project_kind_obj, "project:");

static const mp_rom_map_elem_t moy_ufiles_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_ufiles) },
    { MP_ROM_QSTR(MP_QSTR_CARTS_DIR), MP_ROM_PTR(&carts_dir_obj) },
    { MP_ROM_QSTR(MP_QSTR_FILES_DIR), MP_ROM_QSTR(MP_QSTR_files) },
    { MP_ROM_QSTR(MP_QSTR_TRASH_DIR), MP_ROM_QSTR(MP_QSTR_trash) },
    { MP_ROM_QSTR(MP_QSTR_TRASH_KEEP), MP_ROM_INT(MOY_UF_TRASH_KEEP) },
    { MP_ROM_QSTR(MP_QSTR_HISTORY_DIR), MP_ROM_PTR(&history_dir_obj) },
    { MP_ROM_QSTR(MP_QSTR_HISTORY_EXT), MP_ROM_PTR(&history_ext_obj) },
    { MP_ROM_QSTR(MP_QSTR_HISTORY_KEEP), MP_ROM_INT(MOY_UF_HISTORY_KEEP) },
    { MP_ROM_QSTR(MP_QSTR_DOC_EXT), MP_ROM_PTR(&doc_ext_obj) },
    { MP_ROM_QSTR(MP_QSTR_PROJECT_KIND), MP_ROM_PTR(&project_kind_obj) },
    { MP_ROM_QSTR(MP_QSTR_kinds), MP_ROM_PTR(&mod_kinds_obj) },
    { MP_ROM_QSTR(MP_QSTR_script_ext), MP_ROM_PTR(&mod_script_ext_obj) },
    { MP_ROM_QSTR(MP_QSTR_vault_ext), MP_ROM_PTR(&mod_vault_ext_obj) },
    { MP_ROM_QSTR(MP_QSTR_project_kind), MP_ROM_PTR(&mod_project_kind_obj) },
    { MP_ROM_QSTR(MP_QSTR_project_folder), MP_ROM_PTR(&mod_project_folder_obj) },
    { MP_ROM_QSTR(MP_QSTR_project_dir), MP_ROM_PTR(&mod_project_dir_obj) },
    { MP_ROM_QSTR(MP_QSTR_project_file_path), MP_ROM_PTR(&mod_project_file_path_obj) },
    { MP_ROM_QSTR(MP_QSTR_files_root), MP_ROM_PTR(&mod_files_root_obj) },
    { MP_ROM_QSTR(MP_QSTR_file_kind_dir), MP_ROM_PTR(&mod_file_kind_dir_obj) },
    { MP_ROM_QSTR(MP_QSTR_file_path), MP_ROM_PTR(&mod_file_path_obj) },
    { MP_ROM_QSTR(MP_QSTR_history_path), MP_ROM_PTR(&mod_history_path_obj) },
    { MP_ROM_QSTR(MP_QSTR_trash_path), MP_ROM_PTR(&mod_trash_path_obj) },
    { MP_ROM_QSTR(MP_QSTR_history_trash_path), MP_ROM_PTR(&mod_history_trash_path_obj) },
    { MP_ROM_QSTR(MP_QSTR_list_files), MP_ROM_PTR(&mod_list_files_obj) },
    { MP_ROM_QSTR(MP_QSTR_count_files), MP_ROM_PTR(&mod_count_files_obj) },
    { MP_ROM_QSTR(MP_QSTR_load_file), MP_ROM_PTR(&mod_load_file_obj) },
    { MP_ROM_QSTR(MP_QSTR_save_file), MP_ROM_PTR(&mod_save_file_obj) },
    { MP_ROM_QSTR(MP_QSTR_new_file_name), MP_ROM_PTR(&mod_new_file_name_obj) },
    { MP_ROM_QSTR(MP_QSTR_free_file_name), MP_ROM_PTR(&mod_free_file_name_obj) },
    { MP_ROM_QSTR(MP_QSTR_rename_file), MP_ROM_PTR(&mod_rename_file_obj) },
    { MP_ROM_QSTR(MP_QSTR_duplicate_file), MP_ROM_PTR(&mod_duplicate_file_obj) },
    { MP_ROM_QSTR(MP_QSTR_delete_file), MP_ROM_PTR(&mod_delete_file_obj) },
    { MP_ROM_QSTR(MP_QSTR_restore_file), MP_ROM_PTR(&mod_restore_file_obj) },
    { MP_ROM_QSTR(MP_QSTR_trash_list), MP_ROM_PTR(&mod_trash_list_obj) },
    { MP_ROM_QSTR(MP_QSTR_prune_trash), MP_ROM_PTR(&mod_prune_trash_obj) },
    { MP_ROM_QSTR(MP_QSTR_empty_trash), MP_ROM_PTR(&mod_empty_trash_obj) },
    { MP_ROM_QSTR(MP_QSTR_load_history), MP_ROM_PTR(&mod_load_history_obj) },
    { MP_ROM_QSTR(MP_QSTR_history_ops), MP_ROM_PTR(&mod_history_ops_obj) },
    { MP_ROM_QSTR(MP_QSTR_history_commit), MP_ROM_PTR(&mod_history_commit_obj) },
    { MP_ROM_QSTR(MP_QSTR_prune_history), MP_ROM_PTR(&mod_prune_history_obj) },
    { MP_ROM_QSTR(MP_QSTR_clear_history), MP_ROM_PTR(&mod_clear_history_obj) },
    { MP_ROM_QSTR(MP_QSTR_history_prune_fails), MP_ROM_PTR(&mod_history_prune_fails_obj) },
    { MP_ROM_QSTR(MP_QSTR_content_sig), MP_ROM_PTR(&mod_content_sig_obj) },
    { MP_ROM_QSTR(MP_QSTR_stamp_provenance), MP_ROM_PTR(&mod_stamp_provenance_obj) },
    { MP_ROM_QSTR(MP_QSTR_read_provenance), MP_ROM_PTR(&mod_read_provenance_obj) },
    { MP_ROM_QSTR(MP_QSTR_encode_image), MP_ROM_PTR(&mod_encode_image_obj) },
    { MP_ROM_QSTR(MP_QSTR_decode_image), MP_ROM_PTR(&mod_decode_image_obj) },
    { MP_ROM_QSTR(MP_QSTR_encode_cover), MP_ROM_PTR(&mod_encode_cover_obj) },
    { MP_ROM_QSTR(MP_QSTR_decode_cover), MP_ROM_PTR(&mod_decode_cover_obj) },
    { MP_ROM_QSTR(MP_QSTR_palette), MP_ROM_PTR(&mod_palette_obj) },
    { MP_ROM_QSTR(MP_QSTR_load_copy), MP_ROM_PTR(&mod_load_copy_obj) },
    { MP_ROM_QSTR(MP_QSTR_save_copy), MP_ROM_PTR(&mod_save_copy_obj) },
    { MP_ROM_QSTR(MP_QSTR_clock_fix), MP_ROM_PTR(&mod_clock_fix_obj) },
};
static MP_DEFINE_CONST_DICT(moy_ufiles_globals, moy_ufiles_globals_table);

const mp_obj_module_t moy_ufiles_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_ufiles_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_ufiles, moy_ufiles_module);
