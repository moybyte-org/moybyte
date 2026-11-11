// The theme loader on untrusted packages, under the sanitizers.
//
// tools/moy_index_spike.py builds it (`--component theme sanitize`), and so
// does tests/test_theme_loader.py. With clang's libFuzzer
// (-fsanitize=fuzzer,address,undefined) it is a LLVMFuzzerTestOneInput; with
// -DMOY_THEME_FUZZ_MAIN it is a seeded driver any compiler builds,
// `fuzz_theme SEED RUNS`, which mutates the seed packages
// (moy_ui_tables.c) the way the corpus of tests/test_theme_loader.py does.
// It links moy_theme.c over moy_json.c's scanner and the generated tables;
// the four toolkit calls the loader makes are defined here.
//
// An input is a theme.json. It is parsed, probed and installed; a package
// that installs is resolved in both variants for every answer a console
// reads (the row, the marks, the look, the shape, the fonts, the sheets, the
// layout, each surface's backdrop), and each answer must be one the header
// allows. Its agreement with the Python reading of a package is the
// suite's.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_theme.h"
#include "moy_ui.h"

#define CHECK(c) do { \
        if (!(c)) { \
            fprintf(stderr, "fuzz_theme: %s:%d: %s\n", __FILE__, __LINE__, #c); \
            abort(); \
        } \
} while (0)

// -- the toolkit calls the loader makes --------------------------------------------

void moy_ui_shape_clear(moy_ui_shape_t *sh) {
    memset(sh, 0, sizeof(*sh));
    for (int p = 0; p < MOY_THEME_PARTS; p++) {
        for (int st = 0; st < MOY_THEME_STATES; st++) {
            sh->effect[p][st][1] = 0xff;
        }
    }
}

const char *moy_ui_icon_name(int slot) {
    static const char *const names[] = {"home", "settings", "battery", "wifi"};
    return slot >= 0 && slot < 4 ? names[slot] : NULL;
}

int moy_ui_icon_slot(const uint8_t *name, size_t n) {
    for (int i = 0; i < 4; i++) {
        const char *s = moy_ui_icon_name(i);
        if (strlen(s) == n && memcmp(s, name, n) == 0) {
            return i;
        }
    }
    return -1;
}

void moy_ui_theme_write(int begin) {
    (void)begin;
}

// -- one package ---------------------------------------------------------------------

static unsigned long installed;     // the packages that installed and were resolved

static void run(const uint8_t *src, size_t n) {
    moy_theme_pkg_t *pkg = malloc(sizeof *pkg);
    CHECK(pkg != NULL);
    int rc = moy_theme_parse(src, n, pkg);
    CHECK(rc >= MOY_THEME_OK && rc <= MOY_THEME_NOMEM);
    uint8_t notes = 0;
    int probed = moy_theme_probe(src, n, pkg, &notes);
    CHECK(probed >= MOY_THEME_OK && probed <= MOY_THEME_NOMEM);
    if (probed == MOY_THEME_OK) {
        CHECK(moy_theme_probe_name() != NULL);
        moy_theme_probe_end();
    }
    free(pkg);
    rc = moy_theme_install(src, n, &notes);
    CHECK(rc >= MOY_THEME_OK && rc <= MOY_THEME_NOMEM);
    if (rc != MOY_THEME_OK) {
        return;
    }
    installed++;
    const char *name = moy_theme_installed();
    CHECK(name != NULL);
    size_t len = strlen(name);
    CHECK(len >= 1 && len <= MOY_THEME_NAME_MAX);
    moy_theme_look_t *look = malloc(moy_theme_look_bytes);
    moy_ui_shape_t *shape = malloc(sizeof *shape);
    moy_theme_layout_t *layout = malloc(sizeof *layout);
    uint8_t *bd = malloc(MOY_THEME_BD_BYTES);
    CHECK(look != NULL && shape != NULL && layout != NULL && bd != NULL);
    for (int variant = 0; variant < 2; variant++) {
        int32_t row[MOY_THEME_ROLES];
        uint32_t unreadable = 0;
        CHECK(moy_theme_resolve(name, len, variant, row, &unreadable) == MOY_THEME_OK);
        moy_ui_mark_t marks[MOY_UI_ITEMS * MOY_UI_MSTATES];
        int warn = 0;
        moy_theme_marks(name, len, variant, row, marks, &warn);
        CHECK(moy_theme_look(name, len, variant, 1, look) == MOY_THEME_OK);
        for (int s = 0; s < MOY_THEME_BD_SURFACES; s++) {
            int got = moy_theme_backdrop(name, len, variant, s, bd, MOY_THEME_BD_BYTES);
            CHECK(got >= 0 && got <= (int)MOY_THEME_BD_BYTES);
        }
    }
    CHECK(moy_theme_shape(name, len, shape) == MOY_THEME_OK);
    char files[MOY_THEME_FONT_ROLES > MOY_THEME_SHEETS ? MOY_THEME_FONT_ROLES
               : MOY_THEME_SHEETS][MOY_THEME_NAME_MAX + 1];
    CHECK(moy_theme_fonts(name, len, files) == MOY_THEME_OK);
    CHECK(moy_theme_sheets(name, len, files) == MOY_THEME_OK);
    CHECK(moy_theme_layout(name, len, layout) == MOY_THEME_OK);
    moy_theme_modes(name, len);
    moy_theme_look_frames(name, len);
    free(look);
    free(shape);
    free(layout);
    free(bd);
    moy_theme_uninstall();
}

#ifdef MOY_THEME_FUZZ_MAIN
static uint32_t rng;

static uint32_t xorshift(void) {
    rng ^= rng << 13;
    rng ^= rng >> 17;
    rng ^= rng << 5;
    return rng;
}

int main(int argc, char **argv) {
    uint32_t seed = argc > 1 ? (uint32_t)strtoul(argv[1], NULL, 0) : 1u;
    unsigned long runs = argc > 2 ? strtoul(argv[2], NULL, 0) : 1000ul;
    static const char alphabet[] = "{}[]\":,0123456789-.eE truefalsnl\\/ NIa";
    static uint8_t buf[MOY_THEME_BYTES + 64];
    rng = seed ? seed : 1u;
    for (unsigned long r = 0; r < runs; r++) {
        int s = (int)(xorshift() % MOY_THEME_SEEDS);
        size_t n = moy_theme_seed_len[s];
        memcpy(buf, moy_theme_seed_src[s], n);
        // A seed's own name is refused on install: rename it.
        char *at = strstr((char *)buf, "\"name\"");
        if (at != NULL && n < sizeof buf) {
            char *q = strchr(at + 6, '"');
            if (q != NULL && (size_t)((uint8_t *)q - buf) + 2 < n) {
                q[1] = 'z';
            }
        }
        for (uint32_t m = 1 + xorshift() % 4; m > 0; m--) {
            size_t i = n ? xorshift() % (n + 1) : 0;
            switch (xorshift() % 4) {
                case 0:
                    if (n > 0) {
                        i = i < n ? i : n - 1;
                        memmove(buf + i, buf + i + 1, n - i - 1);
                        n--;
                    }
                    break;
                case 1:
                    if (n < sizeof buf) {
                        memmove(buf + i + 1, buf + i, n - i);
                        buf[i] = (uint8_t)alphabet[xorshift() % (sizeof alphabet - 1)];
                        n++;
                    }
                    break;
                case 2:
                    if (n > 0) {
                        buf[i < n ? i : n - 1] = (uint8_t)xorshift();
                    }
                    break;
                default:
                    n = i;
                    break;
            }
        }
        // The exact bytes on the heap, so a read past them is ASan's.
        uint8_t *src = malloc(n ? n : 1);
        CHECK(src != NULL);
        memcpy(src, buf, n);
        run(src, n);
        free(src);
    }
    printf("fuzz_theme: %lu packages, %lu installed, seed %u, ok\n", runs, installed, seed);
    return 0;
}
#else
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    run(data, size);
    return 0;
}
#endif
