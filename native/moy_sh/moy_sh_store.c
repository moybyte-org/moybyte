// moy_sh_store: the console's commands the shell serves from the store while
// no VM runs (moy_sh_board.h). Each is the console's command
// (runtime/shell/sh_roles.py) over the same C the console calls, so the two
// faces of a command cannot read a file two ways.

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if __has_include("esp_heap_caps.h")
#include "esp_heap_caps.h"
#define ON_BOARD 1
#endif

#include "moy_fs.h"
#include "moy_settings.h"
#include "moy_sh_board.h"

#define USAGE 2

// -- memory ------------------------------------------------------------------------

// Zeroed and 8-aligned, as the rows' allocator promises: PSRAM on a board.
static void *m_alloc(size_t n) {
#ifdef ON_BOARD
    void *p = heap_caps_calloc(1, n ? n : 1u, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (p == NULL && heap_caps_get_total_size(MALLOC_CAP_SPIRAM) == 0) {
        p = heap_caps_calloc(1, n ? n : 1u, MALLOC_CAP_8BIT);
    }
    return p;
#else
    return calloc(1, n ? n : 1u);
#endif
}

static void m_release(void *p, size_t n) {
    (void)n;
#ifdef ON_BOARD
    heap_caps_free(p);
#else
    free(p);
#endif
}

static const moy_htab_mem_t MEM = { m_alloc, m_release };

// -- output ---------------------------------------------------------------------------

static void say(moy_sh_t *s, int fd, const char *p, size_t n) {
    moy_sh_fd_write(s, fd, p, n);
}

static void sayf(moy_sh_t *s, int fd, const char *fmt, const char *a, const char *b) {
    char line[320];
    int n = snprintf(line, sizeof line, fmt, a, b);
    if (n > 0) {
        say(s, fd, line, (size_t)n < sizeof line ? (size_t)n : sizeof line - 1);
    }
}

// -- settings -------------------------------------------------------------------------

// The file's path under `root` (the files tree's), into `out`.
static int settings_path(const char *root, char *out, size_t cap, int dir) {
    int n = snprintf(out, cap, "%s%s/%s", root, MOY_SH_STORE,
                     dir ? "system" : "system/settings");
    return n > 0 && (size_t)n < cap;
}

static int settings_write(moy_sh_t *s, const char *root, moy_settings_t *rows) {
    char path[MOY_SH_STORE_PATH], dir[MOY_SH_STORE_PATH];
    size_t n = moy_settings_file(rows, NULL, 0);
    char *text = m_alloc(n + 1u);
    if (text == NULL) {
        say(s, 2, "settings: out of memory\n", 24);
        return 1;
    }
    moy_settings_file(rows, text, n);
    settings_path(root, dir, sizeof dir, 1);
    settings_path(root, path, sizeof path, 0);
    moy_fs_mkdir(dir);
    int rc = moy_fs_publish(path, text, n);
    m_release(text, n + 1u);
    if (rc != 0) {
        sayf(s, 2, "settings: %s: %s\n", path, strerror(rc));
        return 1;
    }
    return 0;
}

// `words` joined by spaces as a row's JSON: the words when they are one JSON
// value, else them as a JSON string (UTF-8 kept, as the board's json.dumps).
static char *value_of(int argc, char **argv, size_t *len) {
    size_t n = 0;
    for (int i = 0; i < argc; i++) {
        n += strlen(argv[i]) + 1u;
    }
    char *words = m_alloc(n + 1u), *json = m_alloc(n * 6u + 3u);
    if (words == NULL || json == NULL) {
        m_release(words, 0);
        m_release(json, 0);
        return NULL;
    }
    size_t k = 0;
    for (int i = 0; i < argc; i++) {
        size_t m = strlen(argv[i]);
        if (i) {
            words[k++] = ' ';
        }
        memcpy(words + k, argv[i], m);
        k += m;
    }
    if (moy_settings_validate(words, k) == MOY_SETTINGS_OK) {
        m_release(json, 0);
        *len = k;
        return words;
    }
    static const char hex[] = "0123456789abcdef";
    size_t j = 0;
    json[j++] = '"';
    for (size_t i = 0; i < k; i++) {
        unsigned char c = (unsigned char)words[i];
        if (c == '"' || c == '\\') {
            json[j++] = '\\';
            json[j++] = (char)c;
        } else if (c == '\n' || c == '\r' || c == '\t') {
            json[j++] = '\\';
            json[j++] = c == '\n' ? 'n' : c == '\r' ? 'r' : 't';
        } else if (c < 0x20u) {
            memcpy(json + j, "\\u00", 4);
            json[j + 4] = hex[c >> 4];
            json[j + 5] = hex[c & 15u];
            j += 6;
        } else {
            json[j++] = (char)c;
        }
    }
    json[j++] = '"';
    m_release(words, 0);
    *len = j;
    return json;
}

static int settings_cmd(moy_sh_t *s, const char *root, int argc, char **argv) {
    const char *verb = argc > 1 ? argv[1] : "list";
    int list = argc == 1 || (argc == 2 && strcmp(verb, "list") == 0);
    int get = argc == 3 && strcmp(verb, "get") == 0;
    int set = argc >= 4 && strcmp(verb, "set") == 0;
    int unset = argc == 3 && strcmp(verb, "unset") == 0;
    if (argc == 2 && strcmp(verb, "reload") == 0) {
        say(s, 2, "settings: reload: needs the console\n", 36);
        return 1;
    }
    if (!list && !get && !set && !unset) {
        say(s, 2, "usage: settings [list] | get KEY | set KEY VALUE... | unset KEY | reload\n",
            73);
        return USAGE;
    }
    char path[MOY_SH_STORE_PATH];
    if (!settings_path(root, path, sizeof path, 0)) {
        say(s, 2, "settings: the path is too long\n", 31);
        return 1;
    }
    moy_settings_t *rows = moy_settings_new(&MEM);
    if (rows == NULL) {
        say(s, 2, "settings: out of memory\n", 24);
        return 1;
    }
    int code = 0;
    moy_buf_t buf = { NULL, 0 };
    int rc = moy_fs_read(path, NULL, &buf);
    uint32_t n;
    if (rc == 0) {
        rc = moy_settings_load_file(rows, buf.p, buf.n, &n, NULL);
        moy_buf_free(&buf);
        if (rc != MOY_SETTINGS_OK) {
            say(s, 2, "settings: out of memory\n", 24);
            moy_settings_free(rows);
            return 1;
        }
    } else if (rc != ENOENT && rc != MOY_FS_NONE) {
        sayf(s, 2, "settings: %s: %s\n", path, strerror(rc));
        moy_settings_free(rows);
        return 1;
    }
    const char *key = argc > 2 ? argv[2] : "";
    size_t kn = strlen(key);
    if (list) {
        size_t len = moy_settings_file(rows, NULL, 0);
        char *text = m_alloc(len + 1u);
        if (text != NULL) {
            moy_settings_file(rows, text, len);
            say(s, 1, text, len);
            m_release(text, len + 1u);
        }
    } else if (get) {
        const char *j;
        size_t jn;
        if (moy_settings_get(rows, key, kn, &j, &jn)) {
            say(s, 1, j, jn);
            say(s, 1, "\n", 1);
        } else {
            sayf(s, 2, "settings: %s: %s\n", key, "not set");
            code = 1;
        }
    } else if (unset) {
        if (moy_settings_delete(rows, key, kn)) {
            code = settings_write(s, root, rows);
        } else {
            sayf(s, 2, "settings: %s: %s\n", key, "not set");
            code = 1;
        }
    } else {
        size_t jn;
        char *j = value_of(argc - 3, argv + 3, &jn);
        if (!moy_settings_key_ok(key, kn)) {
            sayf(s, 2, "settings: %s: %s\n", key, "not a key the file holds");
            code = 1;
        } else if (j == NULL || moy_settings_set(rows, key, kn, j, jn) != MOY_SETTINGS_OK) {
            say(s, 2, "settings: out of memory\n", 24);
            code = 1;
        } else {
            code = settings_write(s, root, rows);
        }
        m_release(j, 0);
    }
    moy_settings_free(rows);
    return code;
}

// -- the table ------------------------------------------------------------------------

int moy_sh_store_role(moy_sh_t *s, const char *root, int argc, char **argv) {
    if (argc > 0 && strcmp(argv[0], "settings") == 0) {
        return settings_cmd(s, root, argc, argv);
    }
    return MOY_SH_NOT_FOUND;
}
