// moy_midi: the `midi` device API's driver (moy_midi.h).

#include "moy_midi.h"

#include <stdlib.h>
#include <string.h>

#include "moy_input.h"

#if defined(MOY_INPUT_BOARD)
#include "driver/uart.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/task.h"
#endif

typedef struct {
    uint8_t used, transport;
    uint8_t status, need, n, sysex;     // the parser: running status, data wanted and had
    uint8_t d[2];
    uint32_t unit;
    #if defined(MOY_INPUT_BOARD)
    TaskHandle_t reader;
    volatile uint8_t stop;
    #endif
} port_t;

// Everything, taken at the first open (a board's PSRAM): an image whose apps
// never open MIDI pays one pointer.
typedef struct {
    uint32_t owner;
    uint32_t src;
    port_t port[MOY_MIDI_PORTS];
} midi_t;
static midi_t *M;

static void post(int port, uint8_t status, uint8_t d1, uint8_t d2, uint8_t prov) {
    moy_input_t *t = moy_input_kernel();
    if (t == NULL || M == NULL || M->src == 0) {
        return;
    }
    (void)prov;
    moy_input_post(t, M->src, MOY_EV_MIDI, (uint16_t)(status | d1 << 8), (uint16_t)port, d2);
}

static int held(uint32_t owner) {
    return M != NULL && owner != 0 && owner == M->owner;
}

int moy_midi_open(uint32_t owner) {
    if (owner == 0) {
        return -MOY_MIDI_EBADF;
    }
    if (M == NULL) {
        #if defined(MOY_INPUT_BOARD)
        M = heap_caps_calloc(1, sizeof(*M), MALLOC_CAP_SPIRAM);
        #else
        M = calloc(1, sizeof(*M));
        #endif
        if (M == NULL) {
            return -MOY_MIDI_ENODEV;
        }
    }
    if (M->owner == owner) {
        return 0;
    }
    if (M->owner != 0) {
        return -MOY_MIDI_EBUSY;
    }
    moy_input_t *t = moy_input_kernel();
    char was[8];
    if (M->src != 0 && (t == NULL || moy_input_source_name(t, M->src, was, sizeof(was)) != MOY_INPUT_OK
                        || strcmp(was, "midi") != 0)) {
        M->src = 0;                     // the table it was opened on is gone
    }
    if (M->src == 0) {
        uint32_t src;
        if (t != NULL && moy_input_ring_open(t, "midi", MOY_EV_FIFO, 64, &src) == MOY_INPUT_OK) {
            M->src = src;
        }
    }
    M->owner = owner;
    return 0;
}

static void unport(port_t *p) {
    #if defined(MOY_INPUT_BOARD)
    if (p->reader != NULL) {
        p->stop = 1;
        for (int i = 0; i < 100 && p->reader != NULL; i++) {
            vTaskDelay(1);
        }
    }
    #endif
    memset(p, 0, sizeof(*p));
}

int moy_midi_close(uint32_t owner) {
    if (!held(owner)) {
        return -MOY_MIDI_EBADF;
    }
    for (int i = 0; i < MOY_MIDI_PORTS; i++) {
        unport(&M->port[i]);
    }
    M->owner = 0;
    return 0;
}

uint32_t moy_midi_sweep(uint32_t owner) {
    return moy_midi_close(owner) == 0 ? 1u : 0u;
}

uint32_t moy_midi_owner(void) {
    return M != NULL ? M->owner : 0;
}

uint32_t moy_midi_source(void) {
    return M != NULL ? M->src : 0;
}

int moy_midi_port(uint32_t owner, int transport, uint32_t unit) {
    if (!held(owner)) {
        return -MOY_MIDI_EBADF;
    }
    if (transport < MOY_MIDI_UART || transport > MOY_MIDI_BLE) {
        return -MOY_MIDI_EINVAL;
    }
    int free = -1;
    for (int i = 0; i < MOY_MIDI_PORTS; i++) {
        port_t *p = &M->port[i];
        if (p->used && p->transport == transport && p->unit == unit && transport != MOY_MIDI_BLE) {
            return -MOY_MIDI_EBUSY;
        }
        if (!p->used && free < 0) {
            free = i;
        }
    }
    if (free < 0) {
        return -MOY_MIDI_EMFILE;
    }
    port_t *p = &M->port[free];
    memset(p, 0, sizeof(*p));
    p->transport = (uint8_t)transport;
    p->unit = unit;
    p->used = 1;
    return free;
}

static port_t *port_of(uint32_t owner, int port) {
    if (!held(owner) || port < 0 || port >= MOY_MIDI_PORTS || !M->port[port].used) {
        return NULL;
    }
    return &M->port[port];
}

int moy_midi_unport(uint32_t owner, int port) {
    port_t *p = port_of(owner, port);
    if (p == NULL) {
        return -MOY_MIDI_EBADF;
    }
    unport(p);
    return 0;
}

int moy_midi_port_of(uint32_t owner, int port, int *transport, uint32_t *unit) {
    port_t *p = port_of(owner, port);
    if (p == NULL) {
        return -MOY_MIDI_EBADF;
    }
    *transport = p->transport;
    *unit = p->unit;
    return 0;
}

// -- the byte stream ---------------------------------------------------------------

// The data bytes a status takes: a channel message's by its kind, a system
// common message's by its number; -1 for a status that starts nothing here.
static int need_of(uint8_t s) {
    if (s < 0xf0) {
        return (s & 0xe0) == 0xc0 ? 1 : 2;      // program change, channel pressure: 1
    }
    switch (s) {
        case 0xf1: case 0xf3: return 1;
        case 0xf2: return 2;
        case 0xf6: return 0;
        default: return -1;
    }
}

static void byte(int port, port_t *p, uint8_t b, uint8_t prov) {
    if (b >= 0xf8) {
        post(port, b, 0, 0, prov);              // realtime: whole wherever it falls
        return;
    }
    if (b & 0x80) {
        p->sysex = b == 0xf0;
        p->n = 0;
        int need = b == 0xf0 || b == 0xf7 ? -1 : need_of(b);
        if (need < 0) {
            p->status = 0;
            return;
        }
        if (need == 0) {
            post(port, b, 0, 0, prov);
            p->status = 0;
            return;
        }
        p->status = b;
        p->need = (uint8_t)need;
        return;
    }
    if (p->sysex || p->status == 0) {
        return;                                 // system exclusive, or no status yet
    }
    p->d[p->n++] = b;
    if (p->n == p->need) {
        post(port, p->status, p->d[0], p->n > 1 ? p->d[1] : 0, prov);
        p->n = 0;
        if (p->status >= 0xf0) {
            p->status = 0;                      // system common keeps no running status
        }
    }
}

void moy_midi_feed(int port, const uint8_t *d, size_t n) {
    if (M == NULL || port < 0 || port >= MOY_MIDI_PORTS || !M->port[port].used) {
        return;
    }
    port_t *p = &M->port[port];
    for (size_t i = 0; i < n; i++) {
        byte(port, p, d[i], MOY_EV_FIFO);
    }
}

// -- USB-MIDI ----------------------------------------------------------------------

// The MIDI bytes an event packet carries, by its code index number; 0 for
// the system exclusive ones (read past) and the reserved.
static const uint8_t s_cin_len[16] = {0, 0, 2, 3, 0, 1, 0, 0, 3, 3, 3, 3, 2, 2, 3, 1};

void moy_midi_usb_in(uint8_t addr, const uint8_t *d, size_t n) {
    if (M == NULL || M->owner == 0) {
        return;
    }
    int port = -1;
    for (int i = 0; i < MOY_MIDI_PORTS; i++) {
        if (M->port[i].used && M->port[i].transport == MOY_MIDI_USB && M->port[i].unit == addr) {
            port = i;
        }
    }
    if (port < 0) {
        return;
    }
    for (size_t k = 0; k + 4 <= n; k += 4) {
        uint8_t len = s_cin_len[d[k] & 15];
        if (len == 0 || (d[k + 1] & 0x80) == 0) {
            continue;
        }
        post(port, d[k + 1], len > 1 ? d[k + 2] : 0, len > 2 ? d[k + 3] : 0, MOY_EV_FIFO);
    }
}

// Whole messages in `msg`: each one's status and length, or -1.
static int next_msg(const uint8_t *msg, size_t n, size_t at) {
    if (at >= n || (msg[at] & 0x80) == 0) {
        return -1;
    }
    if (msg[at] >= 0xf8) {
        return 1;
    }
    int need = need_of(msg[at]);
    if (need < 0 || at + 1 + (size_t)need > n) {
        return -1;
    }
    for (int i = 1; i <= need; i++) {
        if (msg[at + i] & 0x80) {
            return -1;
        }
    }
    return 1 + need;
}

int moy_midi_usb_pack(const uint8_t *msg, size_t n, uint8_t *out, size_t cap) {
    size_t at = 0, k = 0;
    while (at < n) {
        int len = next_msg(msg, n, at);
        if (len < 0 || k + 4 > cap) {
            return -MOY_MIDI_EINVAL;
        }
        uint8_t s = msg[at];
        uint8_t cin = s < 0xf0 ? (uint8_t)(s >> 4) : s >= 0xf8 ? 0xf : len == 1 ? 5 : (uint8_t)len;
        out[k] = cin;
        out[k + 1] = s;
        out[k + 2] = len > 1 ? msg[at + 1] : 0;
        out[k + 3] = len > 2 ? msg[at + 2] : 0;
        k += 4;
        at += (size_t)len;
    }
    return (int)k;
}

// -- BLE-MIDI ----------------------------------------------------------------------
// A packet is a header (bit 7 and the timestamp's high six bits), then
// messages, each led by a timestamp byte (bit 7 and its low seven bits); a
// message in running status leaves out its status. A byte with bit 7 is a
// status right after a timestamp, and a timestamp anywhere else.

int moy_midi_ble_in(uint32_t owner, int port, const uint8_t *d, size_t n) {
    port_t *p = port_of(owner, port);
    if (p == NULL || p->transport != MOY_MIDI_BLE) {
        return -MOY_MIDI_EBADF;
    }
    if (n < 2 || (d[0] & 0x80) == 0) {
        return -MOY_MIDI_EINVAL;
    }
    int after_ts = 0;
    for (size_t i = 1; i < n; i++) {
        uint8_t b = d[i];
        if ((b & 0x80) && !after_ts) {
            after_ts = 1;                           // a timestamp
            continue;
        }
        after_ts = 0;
        byte(port, p, b, MOY_EV_FIFO);
    }
    return 0;
}

int moy_midi_ble_pack(const uint8_t *msg, size_t n, uint32_t ms, uint8_t *out, size_t cap) {
    size_t at = 0, k = 0;
    if (cap < 1) {
        return -MOY_MIDI_EINVAL;
    }
    out[k++] = (uint8_t)(0x80 | ((ms >> 7) & 0x3f));
    while (at < n) {
        int len = next_msg(msg, n, at);
        if (len < 0 || k + 1 + (size_t)len > cap) {
            return -MOY_MIDI_EINVAL;
        }
        out[k++] = (uint8_t)(0x80 | (ms & 0x7f));
        memcpy(out + k, msg + at, (size_t)len);
        k += (size_t)len;
        at += (size_t)len;
    }
    return (int)k;
}

// -- the UART reader ---------------------------------------------------------------

#if defined(MOY_INPUT_BOARD)

// Off the UART's FIFO, a few bytes a wake: the leased UART's driver is
// MicroPython's (machine.UART), installed before the port and uninstalled
// only after the port's close has stopped this task (the face's sweep runs
// before the leases').
static void reader(void *arg) {
    int port = (int)(intptr_t)arg;
    port_t *p = &M->port[port];
    uint8_t buf[32];
    while (!p->stop) {
        int got = 0;
        if (uart_is_driver_installed((uart_port_t)p->unit)) {
            got = uart_read_bytes((uart_port_t)p->unit, buf, sizeof(buf), pdMS_TO_TICKS(10));
        } else {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
        for (int i = 0; i < got && !p->stop; i++) {
            byte(port, p, buf[i], MOY_EV_FIFO);
        }
    }
    p->reader = NULL;
    vTaskDelete(NULL);
}

int moy_midi_uart(uint32_t owner, int port) {
    port_t *p = port_of(owner, port);
    if (p == NULL || p->transport != MOY_MIDI_UART) {
        return -MOY_MIDI_EBADF;
    }
    if (!uart_is_driver_installed((uart_port_t)p->unit)) {
        return -MOY_MIDI_ENODEV;
    }
    if (p->reader != NULL) {
        return 0;
    }
    p->stop = 0;
    if (xTaskCreatePinnedToCore(reader, "midi", 2560, (void *)(intptr_t)port, 12, &p->reader, 0)
        != pdPASS) {
        p->reader = NULL;
        return -MOY_MIDI_ENODEV;
    }
    return 0;
}

#else

int moy_midi_uart(uint32_t owner, int port) {
    port_t *p = port_of(owner, port);
    return p == NULL || p->transport != MOY_MIDI_UART ? -MOY_MIDI_EBADF : 0;
}

#endif

void moy_midi_reset(void) {
    if (M != NULL) {
        for (int i = 0; i < MOY_MIDI_PORTS; i++) {
            unport(&M->port[i]);
        }
        M->owner = 0;
        if (M->src != 0) {
            moy_input_ring_close(moy_input_kernel(), M->src);
            M->src = 0;
        }
    }
}
