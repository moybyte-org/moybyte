// The handle table's parts (moy_dev.h has the contract): moy_dev.c keeps the
// rows, the leases and the buses; moy_dev_kinds.c names the device APIs and
// hands each call to its driver module's verbs (moy_lora_dev.c, moy_mic_dev.c,
// ...), which keep their handles' state by the row's slot. Every call runs
// under the lease table's lock, which moy_dev.c holds for the whole call.

#ifndef MOY_DEV_KINDS_H
#define MOY_DEV_KINDS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// The grammar (moy_dev.h's THE GRAMMAR).
typedef struct {
    const char *p, *end;
} words_t;

typedef struct {
    const char *k, *v;
    size_t kn, vn;
} word_t;

bool moy_dev_is(const char *p, size_t n, const char *s);
// The next word into *w: 1, 0 at the end, -1 for a word that is no key=value.
int moy_dev_word(words_t *it, word_t *w);
// A value as a number (decimal or 0x.. hex, with a sign): false for none.
bool moy_dev_number(const char *v, size_t n, int64_t *out);
// A value as a number in [lo, hi]: false for none or one outside.
bool moy_dev_in_range(const word_t *w, int64_t lo, int64_t hi, int64_t *out);

// A driver's -errno as the table's answer (moy_dev.h's THE ANSWERS).
int32_t moy_dev_errno(int rc);
// `v` (n chars, a 0x.. prefix or not) as exactly `len` bytes: false for
// anything else.
bool moy_dev_hex(const char *v, size_t n, uint8_t *out, size_t len);

// A device API's per-handle table, *t: MOY_DEV_HANDLES rows of `row` bytes,
// zeroed, made at its first open and kept, from PSRAM where there is any
// (moy_sync.h's THE TABLE), so the idle desk pays no internal RAM for a kind
// no program opened. NULL where there is no memory (the open answers
// ENOMEM); a reader of every row takes a NULL table as one with none.
void *moy_dev_rows(void **t, size_t row);
#define MOY_DEV_ROWS(t) ((__typeof__(t))moy_dev_rows((void **)&(t), sizeof *(t)))

// A UART row (`lease:<row>`, `row` n chars) for a device API's port: leased
// to `pid` under `perms` and its driver up at `baud`, 8N1, with a 512-byte
// transmit buffer (a write of up to that is taken whole): 0 with its lease
// and unit, or a code (BAD for a row that is no UART the board offers,
// DENIED without its permission, BUSY). Given back: the driver down, then
// the lease.
int32_t moy_dev_uart_take(uint32_t pid, uint32_t perms, const char *row, size_t n,
                          uint32_t baud, uint32_t *lease, uint8_t *unit);
void moy_dev_uart_give(uint32_t lease, uint8_t unit);

// A device API's verbs, its driver module's (moy_<driver>_dev.c): each is
// called for row `slot` of process `pid`, under the lease table's lock; open
// answers 0 or a MOY_DEV_E* code, which leaves the row free again
// (MOY_DEV_PASS among them: the console's Python serves that cfg). A NULL
// verb is BAD.
typedef struct {
    int32_t (*open)(uint32_t slot, uint32_t pid, uint32_t perms, const char *cfg, size_t cn);
    void (*close)(uint32_t slot, uint32_t pid);
    int32_t (*read)(uint32_t slot, uint32_t pid, uint8_t *out, size_t cap);
    int32_t (*write)(uint32_t slot, uint32_t pid, const uint8_t *data, size_t n);
    int32_t (*xfer)(uint32_t slot, uint32_t pid, const uint8_t *data, size_t n, uint8_t *out,
                    size_t cap);
    int32_t (*set)(uint32_t slot, uint32_t pid, const char *cfg, size_t n);
    int32_t (*get)(uint32_t slot, uint32_t pid, const char *what, size_t n);
    // The device lease class (moy_lease.h) the row's handle took beside its
    // kind's own, 0 for none: kept through the VM's end as the kind's is.
    uint8_t (*holds)(uint32_t slot);
} moy_dev_ops_t;

// The drivers' verbs (weak: an image without the driver answers ABSENT).
extern const moy_dev_ops_t moy_dev_lora;
extern const moy_dev_ops_t moy_dev_mic;
extern const moy_dev_ops_t moy_dev_midi;
extern const moy_dev_ops_t moy_dev_http;
extern const moy_dev_ops_t moy_dev_usb;
extern const moy_dev_ops_t moy_dev_ble;

// moy_dev.c's half: the device API `name` names, its kind (> 0) or 0.
int moy_dev_kind(const char *name, size_t n);
int32_t moy_dev_kind_open(uint32_t slot, int kind, uint32_t pid, uint32_t perms,
                          const char *cfg, size_t cn);
void moy_dev_kind_close(uint32_t slot);
int32_t moy_dev_kind_read(uint32_t slot, uint8_t *out, size_t cap);
int32_t moy_dev_kind_write(uint32_t slot, const uint8_t *data, size_t n);
int32_t moy_dev_kind_xfer(uint32_t slot, const uint8_t *data, size_t n, uint8_t *out,
                          size_t cap);
int32_t moy_dev_kind_set(uint32_t slot, const char *cfg, size_t n);
int32_t moy_dev_kind_get(uint32_t slot, const char *what, size_t n);

#endif // MOY_DEV_KINDS_H
