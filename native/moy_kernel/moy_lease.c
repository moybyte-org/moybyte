// moy_lease: the lease table. moy_lease.h has the contract.

#include "moy_lease.h"

#include <stdlib.h>
#include <string.h>

#include "moy_htab.h"

// A board is known by the header it has (moy_input.h says why); a board that
// stages no moy_input (the Zero) leases pins with no edges.
#if defined(__has_include)
#if __has_include("esp_heap_caps.h")
#define LEASE_BOARD 1
#endif
#if __has_include("moy_input.h")
#define LEASE_EDGES 1
#endif
#endif

#ifdef LEASE_EDGES
#include "moy_input.h"
#endif

#ifdef LEASE_BOARD
#include "esp_heap_caps.h"
#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_rom_gpio.h"
#include "soc/gpio_sig_map.h"
#ifdef LEASE_EDGES
#include "moy_edge.h"
#endif
#endif

// PSRAM on a board: the table and the offers keep out of internal RAM.
static void *mem_alloc(size_t n) {
    #ifdef LEASE_BOARD
    void *p = heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM);
    return p != NULL ? p : calloc(1, n);
    #else
    return calloc(1, n);
    #endif
}

static void mem_release(void *p, size_t n) {
    (void)n;
    free(p);
}

static const moy_htab_mem_t s_mem = { mem_alloc, mem_release };
static moy_htab_t *s_tab;
static moy_lease_offer_t *s_offer;   // MOY_LEASE_OFFERS rows, made at the first offer
static uint32_t s_offers;
static bool s_offered;          // the board's offer made, or one given

// The board's rows, the first time anything asks.
static void offered(void) {
    if (!s_offered) {
        moy_lease_offer_board();
    }
}

#define BIT(pin) ((uint64_t)1u << (pin))

// -- the offers --------------------------------------------------------------

static bool class_of(const char *name, size_t n, uint8_t *cls, uint8_t *unit) {
    static const struct { const char *pre; uint8_t cls; } k[] = {
        { "gpio", MOY_LEASE_GPIO }, { "uart", MOY_LEASE_UART }, { "spi", MOY_LEASE_SPI },
    };
    for (size_t i = 0; i < sizeof(k) / sizeof(k[0]); i++) {
        size_t p = strlen(k[i].pre);
        if (n < p || memcmp(name, k[i].pre, p) != 0) {
            continue;
        }
        if (k[i].cls == MOY_LEASE_GPIO) {
            if (n != p) {
                return false;
            }
            *cls = MOY_LEASE_GPIO;
            *unit = 0;
            return true;
        }
        if (n != p + 1 || name[p] < '0' || name[p] > '9') {
            return false;
        }
        *cls = k[i].cls;
        *unit = (uint8_t)(name[p] - '0');
        return true;
    }
    return false;
}

static uint8_t named_pins(uint8_t cls) {
    return cls == MOY_LEASE_UART ? 2u : cls == MOY_LEASE_SPI ? 3u : 0u;
}

static int parse_row(const char *s, size_t n, moy_lease_offer_t *o) {
    const char *colon = memchr(s, ':', n);
    if (colon == NULL || colon == s || (size_t)(colon - s) > MOY_LEASE_NAME) {
        return MOY_LEASE_BAD;
    }
    size_t nn = (size_t)(colon - s);
    memset(o, 0, sizeof(*o));
    memcpy(o->name, s, nn);
    if (!class_of(s, nn, &o->cls, &o->unit)) {
        return MOY_LEASE_BAD;
    }
    const char *p = colon + 1, *end = s + n;
    uint8_t want = named_pins(o->cls), got = 0;
    while (p < end) {
        unsigned v = 0;
        const char *d = p;
        while (p < end && *p >= '0' && *p <= '9') {
            v = v * 10u + (unsigned)(*p++ - '0');
        }
        if (p == d || v >= MOY_LEASE_PIN_MAX || (o->pins & BIT(v))) {
            return MOY_LEASE_BAD;
        }
        if (p < end && *p++ != ',') {
            return MOY_LEASE_BAD;
        }
        o->pins |= BIT(v);
        if (got < want) {
            o->pin[got] = (uint8_t)v;
        }
        got++;
    }
    if (got == 0 || (want != 0 && got != want)) {
        return MOY_LEASE_BAD;
    }
    o->npins = want;
    return MOY_LEASE_OK;
}

static bool pin_rows_held(void);

int moy_lease_offer(const char *spec) {
    if (pin_rows_held()) {
        return MOY_LEASE_BUSY;
    }
    s_offered = true;
    s_offers = 0;
    if (spec == NULL) {
        return MOY_LEASE_OK;
    }
    moy_lease_offer_t got[MOY_LEASE_OFFERS];
    uint32_t k = 0;
    const char *p = spec;
    while (*p) {
        const char *bar = strchr(p, '/');
        size_t n = bar ? (size_t)(bar - p) : strlen(p);
        if (k == MOY_LEASE_OFFERS || parse_row(p, n, &got[k]) != MOY_LEASE_OK) {
            return MOY_LEASE_BAD;
        }
        for (uint32_t i = 0; i < k; i++) {
            if (strcmp(got[i].name, got[k].name) == 0) {
                return MOY_LEASE_BAD;
            }
        }
        k++;
        p += n;
        if (*p == '/') {
            p++;
        }
    }
    if (k != 0 && s_offer == NULL) {
        s_offer = mem_alloc(sizeof(moy_lease_offer_t) * MOY_LEASE_OFFERS);
        if (s_offer == NULL) {
            return MOY_LEASE_NOMEM;
        }
    }
    if (k != 0) {
        memcpy(s_offer, got, sizeof(got[0]) * k);
    }
    s_offers = k;
    return MOY_LEASE_OK;
}

int moy_lease_offer_board(void) {
    #ifdef MOY_LEASE_ROWS
    return moy_lease_offer(MOY_LEASE_ROWS);
    #else
    return moy_lease_offer(NULL);
    #endif
}

uint32_t moy_lease_offers(void) {
    offered();
    return s_offers;
}

int moy_lease_offer_at(uint32_t i, moy_lease_offer_t *out) {
    offered();
    if (i >= s_offers) {
        return MOY_LEASE_STALE;
    }
    *out = s_offer[i];
    return MOY_LEASE_OK;
}

int moy_lease_offer_find(const char *name, size_t n) {
    offered();
    for (uint32_t i = 0; i < s_offers; i++) {
        if (strlen(s_offer[i].name) == n && memcmp(s_offer[i].name, name, n) == 0) {
            return (int)i;
        }
    }
    return -1;
}

static const moy_lease_offer_t *offer_of(uint8_t cls, uint8_t unit) {
    offered();
    for (uint32_t i = 0; i < s_offers; i++) {
        if (s_offer[i].cls == cls && s_offer[i].unit == unit) {
            return &s_offer[i];
        }
    }
    return NULL;
}

// -- the pins, safe ------------------------------------------------------------

#ifdef LEASE_BOARD

static void pin_safe(int pin) {
    gpio_set_intr_type(pin, GPIO_INTR_DISABLE);
    gpio_intr_disable(pin);
    esp_rom_gpio_connect_out_signal((uint32_t)pin, SIG_GPIO_OUT_IDX, false, false);
    gpio_set_direction(pin, GPIO_MODE_INPUT);
    gpio_set_pull_mode(pin, GPIO_FLOATING);
}

uint8_t moy_lease_pin_state(int pin) {
    gpio_io_config_t c;
    if (pin < 0 || !GPIO_IS_VALID_GPIO(pin) || gpio_get_io_config(pin, &c) != ESP_OK) {
        return 0;
    }
    return (uint8_t)((c.ie ? MOY_PIN_IN : 0u) | (c.oe ? MOY_PIN_OUT : 0u)
                     | (c.pu ? MOY_PIN_PU : 0u) | (c.pd ? MOY_PIN_PD : 0u)
                     | (c.sig_out != SIG_GPIO_OUT_IDX ? MOY_PIN_PERIPH : 0u));
}

static void unit_end(uint8_t cls, uint8_t unit) {
    if (cls == MOY_LEASE_UART && unit < UART_NUM_MAX && uart_is_driver_installed(unit)) {
        uart_driver_delete(unit);
    }
}

void moy_lease_sim_pin(int pin, uint8_t state, int level) {
    (void)pin;
    (void)state;
    (void)level;
}

void moy_lease_sim_level(int pin, int level) {
    (void)pin;
    (void)level;
}

int moy_lease_sim_get(int pin) {
    return gpio_get_level(pin);
}

#else // the host's stand-in

static uint8_t s_state[MOY_LEASE_PIN_MAX];
static uint8_t s_level[MOY_LEASE_PIN_MAX];
// an open edge's ISR writer, by the pin it watches
static moy_input_isr_t s_isr[MOY_LEASE_PIN_MAX];
static uint32_t s_isr_src[MOY_LEASE_PIN_MAX];

static void pin_safe(int pin) {
    s_state[pin] = MOY_PIN_IN;
}

uint8_t moy_lease_pin_state(int pin) {
    return pin >= 0 && pin < (int)MOY_LEASE_PIN_MAX ? s_state[pin] : 0u;
}

static void unit_end(uint8_t cls, uint8_t unit) {
    (void)cls;
    (void)unit;
}

void moy_lease_sim_pin(int pin, uint8_t state, int level) {
    if (pin >= 0 && pin < (int)MOY_LEASE_PIN_MAX) {
        s_state[pin] = state;
        if (state & MOY_PIN_OUT) {
            moy_lease_sim_level(pin, level);
        }
    }
}

void moy_lease_sim_level(int pin, int level) {
    if (pin < 0 || pin >= (int)MOY_LEASE_PIN_MAX) {
        return;
    }
    uint8_t v = level ? 1u : 0u;
    if (s_level[pin] == v) {
        return;
    }
    s_level[pin] = v;
    if (s_isr_src[pin] != 0) {
        moy_input_isr_put(&s_isr[pin], MOY_EV_EDGE, (uint16_t)pin, 0, v);
    }
}

int moy_lease_sim_get(int pin) {
    return pin >= 0 && pin < (int)MOY_LEASE_PIN_MAX ? s_level[pin] : 0;
}

#endif

// -- edges -----------------------------------------------------------------------

static int edge_open(int pin, uint32_t depth, bool loop, uint32_t *src) {
    #if defined(LEASE_BOARD) && !defined(LEASE_EDGES)
    (void)pin;
    (void)depth;
    (void)loop;
    (void)src;
    return MOY_LEASE_BAD;
    #elif defined(LEASE_BOARD)
    int rc = moy_edge_open(pin, depth, loop, src);
    return rc == MOY_INPUT_OK ? MOY_LEASE_OK : rc == MOY_INPUT_FULL ? MOY_LEASE_FULL
           : rc == MOY_INPUT_NOMEM ? MOY_LEASE_NOMEM : MOY_LEASE_BAD;
    #else
    (void)loop;
    moy_input_t *t = moy_input_kernel();
    char name[MOY_INPUT_NAME + 1] = "gpio";
    name[4] = (char)('0' + pin / 10);
    name[5] = (char)('0' + pin % 10);
    name[6] = 0;
    if (t == NULL || moy_input_ring_open(t, name, MOY_EV_ISR, depth, src) != MOY_INPUT_OK) {
        return MOY_LEASE_NOMEM;
    }
    moy_input_isr_attach(t, *src, &s_isr[pin]);
    s_isr_src[pin] = *src;
    s_state[pin] |= MOY_PIN_IN;
    return MOY_LEASE_OK;
    #endif
}

static void edge_close(int pin, uint32_t src) {
    #ifdef LEASE_BOARD
    (void)pin;
    #ifdef LEASE_EDGES
    moy_edge_close(src);
    #else
    (void)src;
    #endif
    #else
    moy_input_isr_detach(&s_isr[pin]);
    s_isr_src[pin] = 0;
    moy_input_ring_close(moy_input_kernel(), src);
    #endif
}

// -- the table -------------------------------------------------------------------

static moy_lease_row_t *row_of(uint32_t h) {
    void *row;
    if (s_tab == NULL || moy_htab_get(s_tab, h, &row) != MOY_HTAB_OK) {
        return NULL;
    }
    return row;
}

static int table(void);

int moy_lease_take(uint32_t owner, uint8_t cls, uint8_t unit, uint64_t pins,
                   uint8_t flags, uint32_t *h) {
    const moy_lease_offer_t *gpio = offer_of(MOY_LEASE_GPIO, 0);
    const moy_lease_offer_t *o = offer_of(cls, unit);
    if (o == NULL || pins == 0) {
        return MOY_LEASE_BAD;
    }
    uint64_t may = o->pins | (gpio != NULL ? gpio->pins : 0u);
    if (pins & ~may) {
        return MOY_LEASE_BAD;
    }
    int trc = table();
    if (trc != MOY_LEASE_OK) {
        return trc;
    }
    for (uint32_t s = 0; s < moy_htab_slots(s_tab); s++) {
        uint32_t at = moy_htab_at(s_tab, s);
        if (at == 0) {
            continue;
        }
        const moy_lease_row_t *r = moy_htab_row(s_tab, s);
        if ((r->pins & pins) || (cls != MOY_LEASE_GPIO && r->cls == cls && r->unit == unit)) {
            return MOY_LEASE_BUSY;
        }
    }
    void *row;
    int rc = moy_htab_add(s_tab, h, &row);
    if (rc != MOY_HTAB_OK) {
        return rc == MOY_HTAB_FULL ? MOY_LEASE_FULL : MOY_LEASE_NOMEM;
    }
    moy_lease_row_t *r = row;
    r->pins = pins;
    r->owner = owner;
    r->cls = cls;
    r->unit = unit;
    r->flags = flags;
    return MOY_LEASE_OK;
}

static bool is_device(uint8_t cls) {
    return cls >= MOY_LEASE_DEVICE_FIRST && cls <= MOY_LEASE_DEVICE_LAST;
}

// The drivers' ends of a device lease dropped from outside them.
void moy_mic_lease_end(uint32_t owner) __attribute__((weak));
void moy_lora_lease_end(uint32_t owner) __attribute__((weak));
void moy_gatt_lease_end(uint32_t owner) __attribute__((weak));
void moy_media_lease_end(uint32_t owner) __attribute__((weak));

static void device_end(uint8_t cls, uint32_t owner) {
    void (*end)(uint32_t) = cls == MOY_LEASE_MIC ? moy_mic_lease_end
                          : cls == MOY_LEASE_LORA ? moy_lora_lease_end
                          : cls == MOY_LEASE_BLE ? moy_gatt_lease_end
                          : cls == MOY_LEASE_MEDIA ? moy_media_lease_end : NULL;
    if (end != NULL) {
        end(owner);
    }
}

static int table(void) {
    if (s_tab == NULL) {
        s_tab = moy_htab_new(&s_mem, MOY_KIND_LEASE, MOY_LEASE_ROWS_MAX, sizeof(moy_lease_row_t));
        if (s_tab == NULL || moy_htab_reserve(s_tab) != MOY_HTAB_OK) {
            moy_htab_free(s_tab);
            s_tab = NULL;
            return MOY_LEASE_NOMEM;
        }
    }
    return MOY_LEASE_OK;
}

// The live row of device class `cls`, or 0.
static uint32_t device_row(uint8_t cls) {
    for (uint32_t s = 0; s_tab != NULL && s < moy_htab_slots(s_tab); s++) {
        uint32_t at = moy_htab_at(s_tab, s);
        if (at != 0 && ((moy_lease_row_t *)moy_htab_row(s_tab, s))->cls == cls) {
            return at;
        }
    }
    return 0;
}

int moy_lease_hold(uint32_t owner, uint8_t cls) {
    if (!is_device(cls) || owner == 0) {
        return MOY_LEASE_BAD;
    }
    uint32_t at = device_row(cls);
    if (at != 0) {
        return row_of(at)->owner == owner ? MOY_LEASE_OK : MOY_LEASE_BUSY;
    }
    int rc = table();
    if (rc != MOY_LEASE_OK) {
        return rc;
    }
    void *row;
    uint32_t h;
    rc = moy_htab_add(s_tab, &h, &row);
    if (rc != MOY_HTAB_OK) {
        return rc == MOY_HTAB_FULL ? MOY_LEASE_FULL : MOY_LEASE_NOMEM;
    }
    moy_lease_row_t *r = row;
    memset(r, 0, sizeof(*r));
    r->owner = owner;
    r->cls = cls;
    return MOY_LEASE_OK;
}

int moy_lease_let(uint32_t owner, uint8_t cls) {
    uint32_t at = is_device(cls) ? device_row(cls) : 0u;
    if (at == 0 || row_of(at)->owner != owner) {
        return MOY_LEASE_STALE;
    }
    moy_htab_release(s_tab, at);
    return MOY_LEASE_OK;
}

uint32_t moy_lease_holder(uint8_t cls) {
    uint32_t at = is_device(cls) ? device_row(cls) : 0u;
    return at != 0 ? row_of(at)->owner : 0u;
}

const char *moy_lease_device_name(uint8_t cls) {
    static const char *const names[] = { "mic", "lora", "ble", "media" };
    return is_device(cls) ? names[cls - MOY_LEASE_DEVICE_FIRST]
           : cls == MOY_LEASE_WIFI ? "wifi" : NULL;
}

static bool pin_rows_held(void) {
    for (uint32_t s = 0; s_tab != NULL && s < moy_htab_slots(s_tab); s++) {
        if (moy_htab_at(s_tab, s) != 0
            && ((moy_lease_row_t *)moy_htab_row(s_tab, s))->cls <= MOY_LEASE_SPI) {
            return true;
        }
    }
    return false;
}

// -- the radio -------------------------------------------------------------------

static const char *const wifi_tags[MOY_LEASE_WIFI_TAGS] = {
    "web", "update", "settings", "cart", "link", "carts", "dev", "http", "media", "ssh",
    "native", "net",
};

const char *moy_lease_wifi_tag(uint32_t i) {
    return i < MOY_LEASE_WIFI_TAGS ? wifi_tags[i] : NULL;
}

uint32_t moy_lease_wifi_bit(const char *tag, size_t n) {
    for (uint32_t i = 0; tag != NULL && i < MOY_LEASE_WIFI_TAGS; i++) {
        if (strlen(wifi_tags[i]) == n && memcmp(wifi_tags[i], tag, n) == 0) {
            return 1u << i;
        }
    }
    return 0u;
}

static moy_lease_row_t *wifi_at(uint32_t s) {
    if (moy_htab_at(s_tab, s) == 0) {
        return NULL;
    }
    moy_lease_row_t *r = moy_htab_row(s_tab, s);
    return r->cls == MOY_LEASE_WIFI ? r : NULL;
}

uint32_t moy_lease_wifi_mask(void) {
    uint32_t mask = 0u;
    for (uint32_t s = 0; s_tab != NULL && s < moy_htab_slots(s_tab); s++) {
        const moy_lease_row_t *r = wifi_at(s);
        mask |= r != NULL ? r->tags : 0u;
    }
    return mask;
}

int moy_lease_wifi_hold(uint32_t owner, const char *tag, size_t n, uint32_t *mask) {
    uint32_t bit = moy_lease_wifi_bit(tag, n);
    if (bit == 0u || owner == 0u) {
        return MOY_LEASE_BAD;
    }
    moy_lease_row_t *r = NULL;
    for (uint32_t s = 0; r == NULL && s_tab != NULL && s < moy_htab_slots(s_tab); s++) {
        r = wifi_at(s);
        r = r != NULL && r->owner == owner ? r : NULL;
    }
    if (r == NULL) {
        int rc = table();
        if (rc != MOY_LEASE_OK) {
            return rc;
        }
        uint32_t at;
        void *row;
        rc = moy_htab_add(s_tab, &at, &row);
        if (rc != MOY_HTAB_OK) {
            return rc == MOY_HTAB_FULL ? MOY_LEASE_FULL : MOY_LEASE_NOMEM;
        }
        r = row;
        memset(r, 0, sizeof(*r));
        r->owner = owner;
        r->cls = MOY_LEASE_WIFI;
    }
    r->tags |= bit;
    *mask = moy_lease_wifi_mask();
    return MOY_LEASE_OK;
}

int moy_lease_wifi_release(uint32_t owner, const char *tag, size_t n, uint32_t *mask) {
    uint32_t bit = moy_lease_wifi_bit(tag, n);
    if (bit == 0u) {
        return MOY_LEASE_BAD;
    }
    for (uint32_t s = 0; s_tab != NULL && s < moy_htab_slots(s_tab); s++) {
        moy_lease_row_t *r = wifi_at(s);
        if (r != NULL && (owner == 0u || r->owner == owner)) {
            r->tags &= ~bit;
            if (r->tags == 0u) {
                moy_htab_release(s_tab, moy_htab_at(s_tab, s));
            }
        }
    }
    *mask = moy_lease_wifi_mask();
    return MOY_LEASE_OK;
}

int moy_lease_wifi_holder(uint32_t i, uint32_t *owner, uint32_t *tags) {
    for (uint32_t s = 0; s_tab != NULL && s < moy_htab_slots(s_tab); s++) {
        const moy_lease_row_t *r = wifi_at(s);
        if (r != NULL && i-- == 0u) {
            *owner = r->owner;
            *tags = r->tags;
            return MOY_LEASE_OK;
        }
    }
    return MOY_LEASE_STALE;
}

void moy_lease_wifi_reset(void) {
    for (uint32_t s = 0; s_tab != NULL && s < moy_htab_slots(s_tab); s++) {
        if (wifi_at(s) != NULL) {
            moy_htab_release(s_tab, moy_htab_at(s_tab, s));
        }
    }
}

int moy_lease_drop(uint32_t h) {
    moy_lease_row_t *r = row_of(h);
    if (r == NULL) {
        return MOY_LEASE_STALE;
    }
    if (is_device(r->cls)) {
        // The row goes first: the driver's end lets a lease it no longer has.
        uint8_t cls = r->cls;
        uint32_t owner = r->owner;
        moy_htab_release(s_tab, h);
        device_end(cls, owner);
        return MOY_LEASE_OK;
    }
    for (uint32_t i = 0; i < MOY_LEASE_EDGES; i++) {
        if (r->edge[i] != 0) {
            edge_close(r->edge_pin[i], r->edge[i]);
            r->edge[i] = 0;
        }
    }
    unit_end(r->cls, r->unit);
    for (int pin = 0; pin < (int)MOY_LEASE_PIN_MAX; pin++) {
        if (r->pins & BIT(pin)) {
            pin_safe(pin);
        }
    }
    moy_htab_release(s_tab, h);
    return MOY_LEASE_OK;
}

uint32_t moy_lease_sweep(uint32_t owner) {
    uint32_t n = 0;
    for (uint32_t s = 0; s_tab != NULL && s < moy_htab_slots(s_tab); s++) {
        uint32_t at = moy_htab_at(s_tab, s);
        if (at != 0 && ((moy_lease_row_t *)moy_htab_row(s_tab, s))->owner == owner) {
            moy_lease_drop(at);
            n++;
        }
    }
    return n;
}

uint32_t moy_lease_vm_stop(void) {
    uint32_t n = 0;
    for (uint32_t s = 0; s_tab != NULL && s < moy_htab_slots(s_tab); s++) {
        uint32_t at = moy_htab_at(s_tab, s);
        const moy_lease_row_t *r = at != 0 ? moy_htab_row(s_tab, s) : NULL;
        if (r != NULL && (is_device(r->cls) || (r->flags & MOY_LEASE_VM))) {
            moy_lease_drop(at);
            n++;
        }
    }
    return n;
}

int moy_lease_get(uint32_t h, moy_lease_row_t *out) {
    const moy_lease_row_t *r = row_of(h);
    if (r == NULL) {
        return MOY_LEASE_STALE;
    }
    *out = *r;
    return MOY_LEASE_OK;
}

bool moy_lease_live(uint32_t h) {
    return row_of(h) != NULL;
}

uint32_t moy_lease_slots(void) {
    return s_tab != NULL ? moy_htab_slots(s_tab) : 0u;
}

uint32_t moy_lease_at(uint32_t slot) {
    return s_tab != NULL ? moy_htab_at(s_tab, slot) : 0u;
}

uint32_t moy_lease_held(uint8_t flags) {
    uint32_t n = 0;
    for (uint32_t s = 0; s < moy_lease_slots(); s++) {
        if (moy_htab_at(s_tab, s) != 0
            && (((moy_lease_row_t *)moy_htab_row(s_tab, s))->flags & flags) == flags) {
            n++;
        }
    }
    return n;
}

uint32_t moy_lease_of_pin(int pin) {
    if (pin < 0 || pin >= (int)MOY_LEASE_PIN_MAX) {
        return 0;
    }
    for (uint32_t s = 0; s < moy_lease_slots(); s++) {
        uint32_t at = moy_htab_at(s_tab, s);
        if (at != 0 && (((moy_lease_row_t *)moy_htab_row(s_tab, s))->pins & BIT(pin))) {
            return at;
        }
    }
    return 0;
}

int moy_lease_edge(uint32_t h, int pin, uint32_t depth, bool loop, uint32_t *src) {
    moy_lease_row_t *r = row_of(h);
    if (r == NULL) {
        return MOY_LEASE_STALE;
    }
    if (pin < 0 || pin >= (int)MOY_LEASE_PIN_MAX || !(r->pins & BIT(pin))) {
        return MOY_LEASE_BAD;
    }
    int free_ = -1;
    for (int i = 0; i < (int)MOY_LEASE_EDGES; i++) {
        if (r->edge[i] != 0 && r->edge_pin[i] == pin) {
            return MOY_LEASE_BUSY;
        }
        if (free_ < 0 && r->edge[i] == 0) {
            free_ = i;
        }
    }
    if (free_ < 0) {
        return MOY_LEASE_FULL;
    }
    int rc = edge_open(pin, depth, loop, src);
    if (rc == MOY_LEASE_OK) {
        r->edge[free_] = *src;
        r->edge_pin[free_] = (uint8_t)pin;
    }
    return rc;
}

int moy_lease_edge_close(uint32_t h, int pin) {
    moy_lease_row_t *r = row_of(h);
    if (r == NULL) {
        return MOY_LEASE_STALE;
    }
    for (uint32_t i = 0; i < MOY_LEASE_EDGES; i++) {
        if (r->edge[i] != 0 && r->edge_pin[i] == pin) {
            edge_close(pin, r->edge[i]);
            r->edge[i] = 0;
            return MOY_LEASE_OK;
        }
    }
    return MOY_LEASE_STALE;
}

void moy_lease_reset(void) {
    for (uint32_t s = 0; s < moy_lease_slots(); s++) {
        uint32_t at = moy_htab_at(s_tab, s);
        if (at != 0) {
            moy_lease_drop(at);
        }
    }
    s_offers = 0;
    s_offered = true;
    #ifndef LEASE_BOARD
    memset(s_state, 0, sizeof(s_state));
    memset(s_level, 0, sizeof(s_level));
    #endif
}
