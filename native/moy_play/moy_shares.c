// THE SHARES' ROWS (moy_play.h): the per-board lines, the fit check every
// taker of a row asks, and the kill threshold they derive.

#include <stddef.h>
#include <string.h>

#if defined(__has_include)
#if __has_include("py/mpconfig.h") && !defined(MOY_PLAY_HOST)
#include "py/mpconfig.h"
#endif
#endif

#include "moy_play.h"

typedef struct {
    const char *board;
    uint32_t kib[MOY_SHARE_ROWS];
} share_line_t;

#define LINE(b, p, m, r, e, l) { b, { p, m, r, e, l } },
static const share_line_t LINES[] = { MOY_SHARES_LINES(LINE) };
#undef LINE
#define NLINES (sizeof(LINES) / sizeof(LINES[0]))

static const share_line_t *find(const char *board) {
    if (board == NULL) {
        return NULL;
    }
    for (size_t i = 0; i < NLINES; i++) {
        if (strcmp(LINES[i].board, board) == 0) {
            return &LINES[i];
        }
    }
    return NULL;
}

static const share_line_t *own(void) {
#ifdef MOY_SHARES_BOARD
    return find(MOY_SHARES_BOARD);
#else
    return NULL;
#endif
}

static const share_line_t *s_line;
static bool s_chosen;
static uint32_t s_held[MOY_SHARE_ROWS];

static const share_line_t *line(void) {
    return s_chosen ? s_line : own();
}

static uint32_t bytes_of(const share_line_t *l, uint8_t row) {
    if (l == NULL) {
        return MOY_SHARE_NONE;
    }
    uint32_t k = l->kib[row];
    return k == MOY_SHARE_ABSENT ? MOY_SHARE_ABSENT : k * 1024u;
}

int moy_share_use(const char *board) {
    if (board == NULL) {
        s_chosen = false;
    } else {
        const share_line_t *l = find(board);
        if (l == NULL) {
            return -1;
        }
        s_line = l;
        s_chosen = true;
    }
    memset(s_held, 0, sizeof(s_held));
    return 0;
}

uint32_t moy_share_cap(uint8_t row) {
    return row < MOY_SHARE_ROWS ? bytes_of(line(), row) : MOY_SHARE_ABSENT;
}

uint32_t moy_share_row(const char *board, uint8_t row) {
    if (row >= MOY_SHARE_ROWS) {
        return MOY_SHARE_ABSENT;
    }
    return bytes_of(board == NULL ? line() : find(board), row);
}

uint32_t moy_share_held(uint8_t row) {
    return row < MOY_SHARE_ROWS ? s_held[row] : 0;
}

int moy_share_take(uint8_t row, uint32_t n) {
    uint32_t cap = moy_share_cap(row);
    if (cap == MOY_SHARE_NONE) {
        return MOY_PLAY_OK;
    }
    if (cap == MOY_SHARE_ABSENT || n > cap - s_held[row]) {
        return MOY_PLAY_FIT;
    }
    s_held[row] += n;
    return MOY_PLAY_OK;
}

void moy_share_give(uint8_t row, uint32_t n) {
    if (row < MOY_SHARE_ROWS && line() != NULL) {
        s_held[row] -= n < s_held[row] ? n : s_held[row];
    }
}

uint32_t moy_share_kill_below(uint32_t procs_held) {
    const share_line_t *l = line();
    if (l == NULL) {
        return MOY_SHARE_NONE;
    }
    uint32_t sum = 0;
    for (uint8_t r = 0; r < MOY_SHARE_ROWS; r++) {
        uint32_t cap = bytes_of(l, r);
        if (cap == MOY_SHARE_ABSENT) {
            continue;
        }
        uint32_t held = r == MOY_SHARE_PROCS ? procs_held : s_held[r];
        sum += held < cap ? cap - held : 0;
    }
    return sum;
}

uint8_t moy_play_stop_fit(uint64_t total, uint64_t block, uint64_t free_, uint64_t largest,
                          uint64_t held, uint64_t media, bool force) {
    if (!force && total <= free_ && block <= largest) {
        return MOY_PLAY_KEEP_FITS;
    }
    if (total <= free_ + held && block <= largest + held) {
        return MOY_PLAY_STOPS;
    }
    uint64_t more = held + media;
    if (media > 0 && total <= free_ + more && block <= largest + more) {
        return MOY_PLAY_KEEP_MEDIA;
    }
    return MOY_PLAY_KEEP_BIG;
}
