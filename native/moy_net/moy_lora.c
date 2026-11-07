// moy_lora: the `lora` device API's driver (moy_lora.h).

#include "moy_lora.h"

#include <stdlib.h>
#include <string.h>

#include "moy_lorawan.h"
#include "moy_lease.h"

#if defined(__has_include)
#if __has_include("moy_input.h")
#define LORA_EVENTS 1
#include "moy_input.h"
#endif
// ESP_PLATFORM is not defined for a usermod's sources on the esp32 port, so a
// board is recognised by the header it has.
#if __has_include("esp_heap_caps.h")
#define LORA_ON_BOARD 1
#endif
#endif

#if defined(LORA_ON_BOARD) && defined(MOY_API_LORA) && defined(MOY_HW_LORA_RST)
#define LORA_BOARD 1
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/task.h"
// A radio row that names its own SPI pins owns its bus (the Zero's Wio-SX1262);
// one that does not rides SPI2 beside the panel, inside the flush's band gaps
// (the T-Deck).
#ifdef MOY_HW_LORA_SCK
#define LORA_OWN_BUS 1
// portYIELD_FROM_ISR expands this IDF trace hook, whose empty default sits
// past where a usermod's include order reaches (moy_flush.h carries the same
// default for the boards that include it).
#ifndef traceISR_EXIT_TO_SCHEDULER
#define traceISR_EXIT_TO_SCHEDULER()
#endif
#else
#include "moy_flush.h"
#endif
#endif

#ifdef LORA_BOARD
#include "freertos/semphr.h"
#endif

typedef struct {
    uint8_t len, port, kind;
    int16_t rssi;
    int8_t snr;
    uint8_t data[MOY_LORA_PKT];
} pkt_t;

// What the driver keeps, taken at the first open (a board's PSRAM): a console
// whose apps never open the radio pays a pointer for it.
typedef struct {
    volatile uint32_t owner;
    uint8_t mode;
    int listen;
    moy_lora_cfg_t cfg;
    pkt_t *q;                           // MOY_LORA_QUEUE, kernel memory
    uint32_t qhead, qn;
    uint32_t src;
    moy_lw_t lw;
    int lw_join;                        // the exchange in flight is a join
    uint32_t lw_freq;
    uint8_t lw_dr;
    moy_lw_win_t win[2];
    uint32_t tx_end;                    // ms: the exchange's transmission ended
    volatile int irq;
    #ifdef LORA_BOARD
    SemaphoreHandle_t mx;               // the radio's one lock
    spi_device_handle_t spi;
    uint8_t *tx, *rx;                   // DMA-capable, 264 bytes each
    TaskHandle_t task;
    volatile int run;
    uint32_t xfers, wait_us, wait_max;
    #ifdef LORA_OWN_BUS
    uint8_t bus;                        // the radio's own bus is up
    #endif
    #endif
} lora_t;
static lora_t *R;

// The radio's one lock: a verb on the holder's task and the driver's own task
// never drive the SX1262 or its queue at once. The first verb makes the state.
static int radio_lock(void) {
    if (R == NULL) {
        #ifdef LORA_BOARD
        R = heap_caps_calloc(1, sizeof(*R), MALLOC_CAP_SPIRAM);
        #else
        R = calloc(1, sizeof(*R));
        #endif
        if (R == NULL) {
            return -1;
        }
    }
    #ifdef LORA_BOARD
    if (R->mx == NULL && (R->mx = xSemaphoreCreateMutex()) == NULL) {
        return -1;
    }
    xSemaphoreTake(R->mx, portMAX_DELAY);
    #endif
    return 0;
}

static void radio_unlock(void) {
    #ifdef LORA_BOARD
    xSemaphoreGive(R->mx);
    #endif
}


// -- events ---------------------------------------------------------------------------

static void post(uint8_t ev, uint16_t a, int16_t rssi, int8_t snr) {
    #ifdef LORA_EVENTS
    moy_input_t *t = moy_input_kernel();
    if (t != NULL && R->src != 0) {
        uint32_t b = (uint32_t)(uint16_t)rssi | (uint32_t)(uint16_t)(int16_t)snr << 16;
        moy_input_post(t, R->src, MOY_EV_LORA, a, ev, b);
    }
    #else
    (void)ev;
    (void)a;
    (void)rssi;
    (void)snr;
    #endif
}

static void open_source(void) {
    #ifdef LORA_EVENTS
    moy_input_t *t = moy_input_kernel();
    char was[8];
    if (R->src != 0 && (t == NULL || moy_input_source_name(t, R->src, was, sizeof(was)) != MOY_INPUT_OK
                       || strcmp(was, "lora") != 0)) {
        R->src = 0;                      // the table it was opened on is gone
    }
    if (R->src == 0) {
        uint32_t src;
        if (t != NULL && moy_input_ring_open(t, "lora", MOY_EV_FIFO, 16, &src) == MOY_INPUT_OK) {
            R->src = src;
        }
    }
    #endif
}

static void enqueue(uint8_t kind, uint8_t port, const uint8_t *d, size_t n, int16_t rssi,
                    int8_t snr) {
    if (R->q == NULL) {
        return;
    }
    if (R->qn == MOY_LORA_QUEUE) {
        R->qhead = (R->qhead + 1u) % MOY_LORA_QUEUE;
        R->qn--;
    }
    pkt_t *p = &R->q[(R->qhead + R->qn) % MOY_LORA_QUEUE];
    p->len = (uint8_t)n;
    p->port = port;
    p->kind = kind;
    p->rssi = rssi;
    p->snr = snr;
    memcpy(p->data, d, n);
    R->qn++;
}

static uint32_t rnd(void) {
    #ifdef LORA_BOARD
    return esp_random();
    #else
    static uint32_t s_seed = 0x6d6f79u;
    s_seed = s_seed * 1103515245u + 12345u;
    return s_seed >> 8;
    #endif
}

// -- the radio ------------------------------------------------------------------------
// Five verbs the state machine drives: up/down, tune, tx, rx (0 ms:
// continuous), standby; and what it said since the last ask.

enum { RADIO_NONE = 0, RADIO_TX_DONE = 1, RADIO_RX_DONE = 2, RADIO_TIMEOUT = 3 };

#ifdef LORA_BOARD

#define PIN_RST MOY_HW_LORA_RST
#define PIN_BUSY MOY_HW_LORA_BUSY
#define PIN_IRQ MOY_HW_LORA_IRQ
#ifdef LORA_OWN_BUS
#define PIN_CS MOY_HW_LORA_CS
#define gate_take()
#define gate_give()
#else
#define PIN_CS MOY_HW_SPI2_RADIO_CS
#define gate_take() moy_flush_gate_take()
#define gate_give() moy_flush_gate_give()
#endif
#define SPI_HZ 8000000

// An RF switch the radio's DIO2 does not drive: its receive path on while it
// listens, off otherwise.
static void rx_path(int on) {
    #ifdef MOY_HW_LORA_RXEN
    gpio_set_level(MOY_HW_LORA_RXEN, on);
    #else
    (void)on;
    #endif
}


static void wait_busy(void) {
    int64_t t0 = esp_timer_get_time();
    while (gpio_get_level(PIN_BUSY) && esp_timer_get_time() - t0 < 20000) {
    }
}

// One command in a band gap: `n` bytes out, the reply read into R->rx. BUSY is
// the radio's own line, waited for before the gate so the panel never waits
// on the radio.
static int xfer(size_t n) {
    wait_busy();
    int64_t t0 = esp_timer_get_time();
    gate_take();
    uint32_t w = (uint32_t)(esp_timer_get_time() - t0);
    spi_transaction_t t = { .length = n * 8, .tx_buffer = R->tx, .rx_buffer = R->rx };
    esp_err_t e = spi_device_polling_transmit(R->spi, &t);
    gate_give();
    R->xfers++;
    R->wait_us += w;
    if (w > R->wait_max) {
        R->wait_max = w;
    }
    return e == ESP_OK ? 0 : -1;
}

void moy_lora_xfer_stats(uint32_t *n, uint32_t *wait_us, uint32_t *wait_max_us) {
    if (R == NULL) {
        *n = *wait_us = *wait_max_us = 0;
        return;
    }
    *n = R->xfers;
    *wait_us = R->wait_us;
    *wait_max_us = R->wait_max;
    R->wait_max = 0;
}

static int cmd(uint8_t op, const uint8_t *a, size_t n) {
    R->tx[0] = op;
    memcpy(R->tx + 1, a, n);
    return xfer(1 + n);
}

// A read command: its `n` argument bytes, a status byte, then `m` bytes back.
static int cmd_read(uint8_t op, const uint8_t *a, size_t n, uint8_t *out, size_t m) {
    R->tx[0] = op;
    memcpy(R->tx + 1, a, n);
    memset(R->tx + 1 + n, 0, 1 + m);
    if (xfer(2 + n + m) != 0) {
        return -1;
    }
    memcpy(out, R->rx + 2 + n, m);
    return 0;
}

static int wreg(uint16_t r, uint8_t v) {
    uint8_t a[3] = { (uint8_t)(r >> 8), (uint8_t)r, v };
    return cmd(0x0d, a, 3);
}

static int rreg(uint16_t r, uint8_t *v) {
    uint8_t a[2] = { (uint8_t)(r >> 8), (uint8_t)r };
    return cmd_read(0x1d, a, 2, v, 1);
}

static void dio1_isr(void *arg) {
    (void)arg;
    R->irq = 1;
    BaseType_t woke = pdFALSE;
    if (R->task != NULL) {
        vTaskNotifyGiveFromISR(R->task, &woke);
    }
    if (woke) {
        portYIELD_FROM_ISR();
    }
}

static uint32_t now_ms(void) {
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static uint32_t step_(uint32_t now);

static void task_fn(void *arg) {
    (void)arg;
    while (R->run) {
        // A verb holding the radio while it closes it: look at R->run again.
        if (xSemaphoreTake(R->mx, pdMS_TO_TICKS(10)) != pdTRUE) {
            continue;
        }
        uint32_t due = R->run ? step_(now_ms()) : 0;
        xSemaphoreGive(R->mx);
        TickType_t wait = portMAX_DELAY;
        if (due != 0) {
            int32_t ms = (int32_t)(due - now_ms());
            wait = ms <= 0 ? 0 : pdMS_TO_TICKS((uint32_t)ms) ? pdMS_TO_TICKS((uint32_t)ms) : 1;
        }
        ulTaskNotifyTake(pdTRUE, wait);
    }
    R->task = NULL;
    vTaskDelete(NULL);
}

static int radio_up(void) {
    #ifdef LORA_OWN_BUS
    spi_bus_config_t bc = { .mosi_io_num = MOY_HW_LORA_MOSI, .miso_io_num = MOY_HW_LORA_MISO,
                            .sclk_io_num = MOY_HW_LORA_SCK, .quadwp_io_num = -1,
                            .quadhd_io_num = -1, .max_transfer_sz = 264 };
    if (spi_bus_initialize(SPI2_HOST, &bc, SPI_DMA_CH_AUTO) != ESP_OK) {
        return -MOY_LORA_EIO;
    }
    R->bus = 1;
    #else
    moy_flush_gate_open();
    #endif
    #ifdef MOY_HW_LORA_RXEN
    gpio_config_t rxen = { .pin_bit_mask = 1ULL << MOY_HW_LORA_RXEN, .mode = GPIO_MODE_OUTPUT };
    gpio_config(&rxen);
    rx_path(0);
    #endif
    R->tx = heap_caps_malloc(264, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    R->rx = heap_caps_malloc(264, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (R->tx == NULL || R->rx == NULL) {
        return -MOY_LORA_ENOMEM;
    }
    spi_device_interface_config_t dc = {
        .clock_speed_hz = SPI_HZ, .mode = 0, .spics_io_num = PIN_CS, .queue_size = 1,
    };
    if (spi_bus_add_device(SPI2_HOST, &dc, &R->spi) != ESP_OK) {
        R->spi = NULL;
        return -MOY_LORA_EIO;
    }
    gpio_config_t out = { .pin_bit_mask = 1ULL << PIN_RST, .mode = GPIO_MODE_OUTPUT };
    gpio_config(&out);
    gpio_config_t in = { .pin_bit_mask = 1ULL << PIN_BUSY, .mode = GPIO_MODE_INPUT };
    gpio_config(&in);
    gpio_set_level(PIN_RST, 0);
    esp_rom_delay_us(200);
    gpio_set_level(PIN_RST, 1);
    vTaskDelay(pdMS_TO_TICKS(10));
    wait_busy();
    static const uint8_t tcxo[4] = { 0x02, 0x00, 0x01, 0x40 };   // 1.8 V, 5 ms
    uint8_t st = 0;
    uint8_t a;
    a = 0x00;
    int rc = cmd(0x80, &a, 1);                                   // standby RC
    rc |= cmd(0x97, tcxo, 4);
    a = 0x7f;
    rc |= cmd(0x89, &a, 1);                                      // calibrate everything
    vTaskDelay(pdMS_TO_TICKS(10));
    wait_busy();
    a = 0x01;
    rc |= cmd(0x96, &a, 1);                                      // DC-DC
    rc |= cmd(0x9d, &a, 1);                                      // DIO2 the RF switch
    rc |= cmd(0x8a, &a, 1);                                      // LoRa
    static const uint8_t base[2] = { 0, 0 };
    rc |= cmd(0x8f, base, 2);
    static const uint8_t pa[4] = { 0x04, 0x07, 0x00, 0x01 };     // up to +22 dBm
    rc |= cmd(0x95, pa, 4);
    rc |= wreg(0x08e7, 0x38);                                    // OCP 140 mA
    static const uint8_t img[2] = { 0xd7, 0xdb };                // 863..870 MHz
    rc |= cmd(0x98, img, 2);
    // TxDone, RxDone, HeaderErr, CrcErr, Timeout on DIO1.
    static const uint8_t irq[8] = { 0x02, 0x63, 0x02, 0x63, 0, 0, 0, 0 };
    rc |= cmd(0x08, irq, 8);
    rc |= cmd_read(0xc0, NULL, 0, &st, 0);
    uint8_t ver = 0;
    rreg(0x0320, &ver);                                          // "SX1261 V2D 2D02"
    if (rc != 0 || ver != 'S') {
        return -MOY_LORA_EIO;
    }
    gpio_config_t irqp = { .pin_bit_mask = 1ULL << PIN_IRQ, .mode = GPIO_MODE_INPUT,
                           .intr_type = GPIO_INTR_POSEDGE };
    gpio_config(&irqp);
    esp_err_t svc = gpio_install_isr_service(0);
    if ((svc != ESP_OK && svc != ESP_ERR_INVALID_STATE)
        || gpio_isr_handler_add(PIN_IRQ, dio1_isr, NULL) != ESP_OK) {
        return -MOY_LORA_EIO;
    }
    R->run = 1;
    if (xTaskCreatePinnedToCore(task_fn, "moy_lora", 4096, NULL, 10, &R->task, 0) != pdPASS) {
        R->run = 0;
        R->task = NULL;
        return -MOY_LORA_ENOMEM;
    }
    return 0;
}

static void radio_down(void) {
    R->run = 0;
    if (R->task != NULL) {
        xTaskNotifyGive(R->task);
        for (int i = 0; R->task != NULL && i < 50; i++) {
            vTaskDelay(pdMS_TO_TICKS(2));
        }
    }
    if (R->spi != NULL) {
        uint8_t a = 0x00;
        cmd(0x80, &a, 1);
        a = 0x04;
        cmd(0x84, &a, 1);                                        // sleep, warm start
        gpio_isr_handler_remove(PIN_IRQ);
        gpio_set_intr_type(PIN_IRQ, GPIO_INTR_DISABLE);
        spi_bus_remove_device(R->spi);
        R->spi = NULL;
    }
    rx_path(0);
    #ifdef LORA_OWN_BUS
    if (R->bus) {
        spi_bus_free(SPI2_HOST);
        R->bus = 0;
    }
    #endif
    heap_caps_free(R->tx);
    heap_caps_free(R->rx);
    R->tx = R->rx = NULL;
}

static int radio_tune(uint32_t freq, uint8_t sf, uint32_t bw, uint8_t cr, int8_t power,
                      uint8_t sync, int iq_inv, int crc, uint16_t preamble, uint8_t len) {
    uint8_t a = 0x00;
    int rc = cmd(0x80, &a, 1);
    uint32_t f = (uint32_t)(((uint64_t)freq << 25) / 32000000u);
    uint8_t fb[4] = { (uint8_t)(f >> 24), (uint8_t)(f >> 16), (uint8_t)(f >> 8), (uint8_t)f };
    rc |= cmd(0x86, fb, 4);
    uint8_t bwc = bw == 500000 ? 0x06 : bw == 250000 ? 0x05 : bw == 62500 ? 0x03 : 0x04;
    uint8_t ldro = (sf >= 11 && bw <= 125000) ? 1 : 0;
    uint8_t mod[4] = { sf, bwc, (uint8_t)(cr - 4), ldro };
    rc |= cmd(0x8b, mod, 4);
    uint8_t pp[6] = { (uint8_t)(preamble >> 8), (uint8_t)preamble, 0x00, len,
                      (uint8_t)(crc ? 1 : 0), (uint8_t)(iq_inv ? 1 : 0) };
    rc |= cmd(0x8c, pp, 6);
    uint8_t iq = 0;
    if (rreg(0x0736, &iq) == 0) {                                // the IQ errata (15.4)
        rc |= wreg(0x0736, iq_inv ? (uint8_t)(iq & ~0x04) : (uint8_t)(iq | 0x04));
    }
    uint8_t tx[2] = { (uint8_t)power, 0x04 };
    rc |= cmd(0x8e, tx, 2);
    uint8_t sw = sync;
    rc |= wreg(0x0740, (uint8_t)((sw & 0xf0) | 0x04));
    rc |= wreg(0x0741, (uint8_t)(((sw & 0x0f) << 4) | 0x04));
    return rc;
}

static int radio_tx(const uint8_t *d, size_t n, uint8_t sf, uint16_t preamble, int crc) {
    (void)sf;
    uint8_t pp[6] = { (uint8_t)(preamble >> 8), (uint8_t)preamble, 0x00, (uint8_t)n,
                      (uint8_t)(crc ? 1 : 0), 0 };
    int rc = cmd(0x8c, pp, 6);
    R->tx[0] = 0x0e;
    R->tx[1] = 0x00;
    memcpy(R->tx + 2, d, n);
    rc |= xfer(2 + n);
    static const uint8_t clr[2] = { 0xff, 0xff };
    rc |= cmd(0x02, clr, 2);
    static const uint8_t to[3] = { 0x02, 0xee, 0x00 };          // 3 s fence
    rx_path(0);
    rc |= cmd(0x83, to, 3);
    return rc;
}

static int radio_rx(uint32_t timeout_ms) {
    static const uint8_t clr[2] = { 0xff, 0xff };
    int rc = cmd(0x02, clr, 2);
    uint32_t t = timeout_ms == 0 ? 0xffffffu : timeout_ms * 64u;
    uint8_t to[3] = { (uint8_t)(t >> 16), (uint8_t)(t >> 8), (uint8_t)t };
    rx_path(1);
    rc |= cmd(0x82, to, 3);
    return rc;
}

static void radio_standby(void) {
    uint8_t a = 0x00;
    cmd(0x80, &a, 1);
    rx_path(0);
}

static int radio_event(uint8_t *buf, size_t *n, int16_t *rssi, int8_t *snr) {
    if (!R->irq) {
        return RADIO_NONE;
    }
    R->irq = 0;
    uint8_t st[2];
    if (cmd_read(0x12, NULL, 0, st, 2) != 0) {
        return RADIO_NONE;
    }
    uint16_t f = (uint16_t)(st[0] << 8 | st[1]);
    static const uint8_t clr[2] = { 0xff, 0xff };
    cmd(0x02, clr, 2);
    if (f & 0x0001) {
        return RADIO_TX_DONE;
    }
    if (f & 0x0200) {
        return RADIO_TIMEOUT;
    }
    if ((f & 0x0002) && !(f & 0x0060)) {
        uint8_t bs[2], ps[3];
        if (cmd_read(0x13, NULL, 0, bs, 2) != 0 || cmd_read(0x14, NULL, 0, ps, 3) != 0) {
            return RADIO_NONE;
        }
        uint8_t a[1] = { bs[1] };
        if (cmd_read(0x1e, a, 1, buf, bs[0]) != 0) {
            return RADIO_NONE;
        }
        *n = bs[0];
        *rssi = (int16_t)(-(int)ps[0] / 2);
        *snr = (int8_t)ps[1];
        return RADIO_RX_DONE;
    }
    return f & 0x0060 ? RADIO_TIMEOUT : RADIO_NONE;
}

void moy_lora_sim(int on) {
    (void)on;
}

int moy_lora_sim_sent(uint8_t *out, size_t cap, uint32_t *freq, uint8_t *sf, uint32_t *bw,
                      uint8_t *sync, uint8_t *iq) {
    (void)out;
    (void)cap;
    (void)freq;
    (void)sf;
    (void)bw;
    (void)sync;
    (void)iq;
    return 0;
}

int moy_lora_sim_air(const uint8_t *data, size_t n, uint32_t freq, uint8_t sf, int16_t rssi,
                     int8_t snr) {
    (void)data;
    (void)n;
    (void)freq;
    (void)sf;
    (void)rssi;
    (void)snr;
    return 0;
}

#else   // the stand-in

void moy_lora_xfer_stats(uint32_t *n, uint32_t *wait_us, uint32_t *wait_max_us) {
    *n = *wait_us = *wait_max_us = 0;
}

// The stand-in's air, taken when a test installs it: an image that carries the
// stand-in (a board without the radio) pays a pointer.
typedef struct {
    struct {
        uint32_t freq, bw;
        uint8_t sf, sync, iq_inv;
    } tune;
    int rxing;                          // the receiver is on
    int txing;
    pkt_t heard;
    int heard_ok;
    struct {
        uint8_t len, sf, sync, iq;
        uint32_t freq, bw;
        uint8_t data[MOY_LORA_PKT];
    } out[MOY_LORA_QUEUE];
    uint32_t out_head, out_n;
} sim_t;
static sim_t *S;
static int s_sim_on;

static int radio_up(void) {
    return 0;
}

static void radio_down(void) {
    S->rxing = S->txing = 0;
}

static int radio_tune(uint32_t freq, uint8_t sf, uint32_t bw, uint8_t cr, int8_t power,
                      uint8_t sync, int iq_inv, int crc, uint16_t preamble, uint8_t len) {
    (void)cr;
    (void)power;
    (void)crc;
    (void)preamble;
    (void)len;
    S->rxing = 0;
    S->tune.freq = freq;
    S->tune.sf = sf;
    S->tune.bw = bw;
    S->tune.sync = sync;
    S->tune.iq_inv = (uint8_t)iq_inv;
    return 0;
}

static int radio_tx(const uint8_t *d, size_t n, uint8_t sf, uint16_t preamble, int crc) {
    (void)sf;
    (void)preamble;
    (void)crc;
    if (S->out_n == MOY_LORA_QUEUE) {
        S->out_head = (S->out_head + 1u) % MOY_LORA_QUEUE;
        S->out_n--;
    }
    uint32_t i = (S->out_head + S->out_n) % MOY_LORA_QUEUE;
    S->out[i].len = (uint8_t)n;
    S->out[i].freq = S->tune.freq;
    S->out[i].sf = S->tune.sf;
    S->out[i].bw = S->tune.bw;
    S->out[i].sync = S->tune.sync;
    S->out[i].iq = S->tune.iq_inv;
    memcpy(S->out[i].data, d, n);
    S->out_n++;
    S->txing = 1;
    S->rxing = 0;
    R->irq = 1;
    return 0;
}

static int radio_rx(uint32_t timeout_ms) {
    (void)timeout_ms;
    S->rxing = 1;
    return 0;
}

static void radio_standby(void) {
    S->rxing = 0;
}

static int radio_event(uint8_t *buf, size_t *n, int16_t *rssi, int8_t *snr) {
    if (!R->irq) {
        return RADIO_NONE;
    }
    R->irq = 0;
    if (S->txing) {
        S->txing = 0;
        return RADIO_TX_DONE;
    }
    if (S->heard_ok) {
        S->heard_ok = 0;
        memcpy(buf, S->heard.data, S->heard.len);
        *n = S->heard.len;
        *rssi = S->heard.rssi;
        *snr = S->heard.snr;
        return RADIO_RX_DONE;
    }
    return RADIO_NONE;
}

void moy_lora_sim(int on) {
    if (on && S == NULL) {
        S = calloc(1, sizeof(*S));
    }
    s_sim_on = on && S != NULL;
    if (S != NULL) {
        S->out_n = S->out_head = 0;
    }
}

int moy_lora_sim_sent(uint8_t *out, size_t cap, uint32_t *freq, uint8_t *sf, uint32_t *bw,
                      uint8_t *sync, uint8_t *iq) {
    if (S == NULL || S->out_n == 0) {
        return 0;
    }
    uint32_t i = S->out_head;
    size_t n = S->out[i].len < cap ? S->out[i].len : cap;
    memcpy(out, S->out[i].data, n);
    *freq = S->out[i].freq;
    *sf = S->out[i].sf;
    *bw = S->out[i].bw;
    *sync = S->out[i].sync;
    *iq = S->out[i].iq;
    S->out_head = (S->out_head + 1u) % MOY_LORA_QUEUE;
    S->out_n--;
    return (int)n;
}

int moy_lora_sim_air(const uint8_t *data, size_t n, uint32_t freq, uint8_t sf, int16_t rssi,
                     int8_t snr) {
    if (S == NULL || !S->rxing || S->tune.freq != freq || S->tune.sf != sf || n > MOY_LORA_PKT) {
        return 0;
    }
    memcpy(S->heard.data, data, n);
    S->heard.len = (uint8_t)n;
    S->heard.rssi = rssi;
    S->heard.snr = snr;
    S->heard_ok = 1;
    moy_lora_irq();
    return 1;
}

#endif

int moy_lora_present(void) {
    #ifdef LORA_BOARD
    return 1;
    #else
    return s_sim_on;
    #endif
}

void moy_lora_irq(void) {
    if (R != NULL) {
        R->irq = 1;
    }
}

// -- the state machine ------------------------------------------------------------------

static int tune_raw(void) {
    return radio_tune(R->cfg.freq, R->cfg.sf, R->cfg.bw, R->cfg.cr, R->cfg.power, R->cfg.sync, 0, 1,
                      R->cfg.preamble, MOY_LORA_PKT);
}

// Back to what the holder asked for between transmissions.
static void rest(void) {
    if (R->listen) {
        tune_raw();
        radio_rx(0);
        R->mode = MOY_LORA_LISTEN;
    } else {
        radio_standby();
        R->mode = MOY_LORA_IDLE;
    }
}

static int lw_tune(uint32_t freq, uint8_t dr, int down) {
    return radio_tune(freq, moy_lw_sf(dr), moy_lw_bw(dr), 5, 14, 0x34, down,
                      down ? 0 : 1, 8, MOY_LORA_PKT);
}

// How long a window stays open past its time: MOY_LORA_WIN_MS and 12 symbols.
static uint32_t win_ms(uint8_t dr) {
    uint32_t sym_us = (1000000u << moy_lw_sf(dr)) / (moy_lw_bw(dr) ? moy_lw_bw(dr) : 125000u);
    return MOY_LORA_WIN_EARLY_MS + MOY_LORA_WIN_MS + 12u * sym_us / 1000u;
}

static void open_window(int i) {
    lw_tune(R->win[i].freq, R->win[i].dr, 1);
    radio_rx(win_ms(R->win[i].dr));
    R->mode = i == 0 ? MOY_LORA_LW_RX1 : MOY_LORA_LW_RX2;
}

static void lw_finish(int ok) {
    if (R->lw_join) {
        post(ok ? MOY_LORA_EV_JOINED : MOY_LORA_EV_JOIN_FAILED, 0, 0, 0);
    } else {
        post(MOY_LORA_EV_UP_DONE, R->lw.acked, 0, 0);
    }
    rest();
}

// A packet one of the windows caught: 1 when it ended the exchange.
static int lw_heard(const uint8_t *buf, size_t n, int16_t rssi, int8_t snr) {
    if (R->lw_join) {
        if (moy_lw_join_accept(&R->lw, buf, n) == MOY_LW_OK) {
            lw_finish(1);
            return 1;
        }
        return 0;
    }
    uint8_t port = 0;
    uint8_t out[MOY_LORA_PKT];
    int k = moy_lw_downlink(&R->lw, buf, n, &port, out, sizeof(out));
    if (k < 0) {
        return 0;
    }
    if (port != 0) {
        enqueue(MOY_LORA_EV_DOWN, port, out, (size_t)k, rssi, snr);
        post(MOY_LORA_EV_DOWN, (uint16_t)k, rssi, snr);
    }
    lw_finish(1);
    return 1;
}

static int due(uint32_t now, uint32_t at) {
    return (int32_t)(now - at) >= 0;
}

static uint32_t step_(uint32_t now) {
    if (R->owner == 0) {
        return 0;
    }
    uint8_t buf[MOY_LORA_PKT];
    size_t n = 0;
    int16_t rssi = 0;
    int8_t snr = 0;
    int ev = radio_event(buf, &n, &rssi, &snr);
    switch (R->mode) {
        case MOY_LORA_LISTEN:
            if (ev == RADIO_RX_DONE) {
                enqueue(MOY_LORA_EV_RX, 0, buf, n, rssi, snr);
                post(MOY_LORA_EV_RX, (uint16_t)n, rssi, snr);
            }
            if (ev != RADIO_NONE) {
                radio_rx(0);
            }
            return 0;
        case MOY_LORA_TX:
            if (ev == RADIO_TX_DONE) {
                post(MOY_LORA_EV_TX_DONE, 0, 0, 0);
                rest();
            }
            return 0;
        case MOY_LORA_LW_TX:
            if (ev != RADIO_TX_DONE) {
                return 0;
            }
            R->tx_end = now;
            radio_standby();
            R->mode = MOY_LORA_LW_WAIT1;
            /* fall through */
        case MOY_LORA_LW_WAIT1: {
            uint32_t at = R->tx_end + R->win[0].delay_ms - MOY_LORA_WIN_EARLY_MS;
            if (!due(now, at)) {
                return at;
            }
            open_window(0);
            return R->tx_end + R->win[0].delay_ms + win_ms(R->win[0].dr) - MOY_LORA_WIN_EARLY_MS;
        }
        case MOY_LORA_LW_RX1:
        case MOY_LORA_LW_RX2: {
            int i = R->mode == MOY_LORA_LW_RX1 ? 0 : 1;
            if (ev == RADIO_RX_DONE && lw_heard(buf, n, rssi, snr)) {
                return 0;
            }
            uint32_t end = R->tx_end + R->win[i].delay_ms - MOY_LORA_WIN_EARLY_MS
                           + win_ms(R->win[i].dr);
            if (ev == RADIO_TIMEOUT || ev == RADIO_RX_DONE || due(now, end)) {
                radio_standby();
                if (i == 1) {
                    lw_finish(0);
                    return 0;
                }
                R->mode = MOY_LORA_LW_WAIT2;
            } else {
                return end;
            }
        }
            /* fall through */
        case MOY_LORA_LW_WAIT2: {
            uint32_t at = R->tx_end + R->win[1].delay_ms - MOY_LORA_WIN_EARLY_MS;
            if (!due(now, at)) {
                return at;
            }
            open_window(1);
            return R->tx_end + R->win[1].delay_ms + win_ms(R->win[1].dr) - MOY_LORA_WIN_EARLY_MS;
        }
        default:
            return 0;
    }
}

static void kick(void) {
    #ifdef LORA_BOARD
    if (R->task != NULL) {
        xTaskNotifyGive(R->task);
    }
    #endif
}

// -- the holder -----------------------------------------------------------------------

static int open_(uint32_t owner) {
    if (owner == 0 || !moy_lora_present()) {
        return -MOY_LORA_ENODEV;
    }
    // The holder is the lease table's (moy_lease.h, MOY_LEASE_LORA); R->owner
    // is its copy for the radio's task.
    int lr = moy_lease_hold(owner, MOY_LEASE_LORA);
    if (lr != MOY_LEASE_OK) {
        return lr == MOY_LEASE_BUSY ? -MOY_LORA_EBUSY : -MOY_LORA_ENOMEM;
    }
    if (R->owner == owner) {
        return 0;
    }
    if (R->q == NULL) {
        #ifdef LORA_BOARD
        R->q = heap_caps_malloc(sizeof(pkt_t) * MOY_LORA_QUEUE, MALLOC_CAP_SPIRAM);
        #else
        R->q = malloc(sizeof(pkt_t) * MOY_LORA_QUEUE);
        #endif
        if (R->q == NULL) {
            moy_lease_let(owner, MOY_LEASE_LORA);
            return -MOY_LORA_ENOMEM;
        }
    }
    R->qhead = R->qn = 0;
    open_source();
    R->cfg = (moy_lora_cfg_t){ .freq = 868100000u, .bw = 125000u, .sf = 9, .cr = 5, .power = 14,
                              .sync = 0x12, .preamble = 8 };
    R->listen = 0;
    R->mode = MOY_LORA_IDLE;
    memset(&R->lw, 0, sizeof(R->lw));
    int rc = radio_up();
    if (rc != 0) {
        radio_down();
        moy_lease_let(owner, MOY_LEASE_LORA);
        return rc;
    }
    R->owner = owner;
    tune_raw();
    radio_standby();
    return 0;
}

static int close_(uint32_t owner) {
    if (owner == 0 || owner != R->owner) {
        return -MOY_LORA_EBADF;
    }
    R->owner = 0;
    radio_down();
    R->mode = MOY_LORA_IDLE;
    R->listen = 0;
    R->qn = 0;
    memset(&R->lw, 0, sizeof(R->lw));
    moy_lease_let(owner, MOY_LEASE_LORA);
    return 0;
}

uint32_t moy_lora_sweep(uint32_t owner) {
    return moy_lora_close(owner) == 0 ? 1u : 0u;
}

void moy_lora_lease_end(uint32_t owner) {
    moy_lora_close(owner);
}

uint32_t moy_lora_owner(void) {
    return moy_lease_holder(MOY_LEASE_LORA);
}

static int held(uint32_t owner) {
    return owner != 0 && owner == R->owner;
}

static int busy(void) {
    return R->mode != MOY_LORA_IDLE && R->mode != MOY_LORA_LISTEN;
}

static int config_(uint32_t owner, const moy_lora_cfg_t *c) {
    if (!held(owner)) {
        return -MOY_LORA_EBADF;
    }
    if (c->freq < 150000000u || c->freq > 960000000u || c->sf < 7 || c->sf > 12 || c->cr < 5
        || c->cr > 8 || c->power < -9 || c->power > 22 || c->preamble < 6
        || (c->bw != 62500 && c->bw != 125000 && c->bw != 250000 && c->bw != 500000)) {
        return -MOY_LORA_EINVAL;
    }
    if (busy()) {
        return -MOY_LORA_EBUSY;
    }
    R->cfg = *c;
    rest();
    return 0;
}

static int listen_(uint32_t owner, int on) {
    if (!held(owner)) {
        return -MOY_LORA_EBADF;
    }
    R->listen = on;
    if (!busy()) {
        rest();
    }
    return 0;
}

static int send_(uint32_t owner, const uint8_t *data, size_t n) {
    if (!held(owner)) {
        return -MOY_LORA_EBADF;
    }
    if (n == 0 || n > MOY_LORA_PKT) {
        return -MOY_LORA_EINVAL;
    }
    if (busy()) {
        return -MOY_LORA_EBUSY;
    }
    tune_raw();
    R->mode = MOY_LORA_TX;
    if (radio_tx(data, n, R->cfg.sf, R->cfg.preamble, 1) != 0) {
        rest();
        return -MOY_LORA_EIO;
    }
    kick();
    return 0;
}

static int recv_(uint32_t owner, uint8_t *out, size_t cap, int16_t *rssi, int8_t *snr,
           uint8_t *port) {
    if (!held(owner)) {
        return -MOY_LORA_EBADF;
    }
    if (R->qn == 0) {
        return 0;
    }
    pkt_t *p = &R->q[R->qhead];
    size_t n = p->len < cap ? p->len : cap;
    memcpy(out, p->data, n);
    *rssi = p->rssi;
    *snr = p->snr;
    *port = p->port;
    R->qhead = (R->qhead + 1u) % MOY_LORA_QUEUE;
    R->qn--;
    return (int)n;
}

static int lw_send(const uint8_t *frame, size_t n, uint8_t dr) {
    R->lw_dr = dr;
    R->lw_freq = moy_lw_channel(&R->lw, rnd());
    moy_lw_windows(&R->lw, R->lw_join, R->lw_freq, dr, R->win);
    lw_tune(R->lw_freq, dr, 0);
    R->mode = MOY_LORA_LW_TX;
    if (radio_tx(frame, n, moy_lw_sf(dr), 8, 1) != 0) {
        rest();
        return -MOY_LORA_EIO;
    }
    kick();
    return 0;
}

static int join_(uint32_t owner, const uint8_t dev_eui[8], const uint8_t join_eui[8],
           const uint8_t app_key[16], uint8_t dr) {
    if (!held(owner)) {
        return -MOY_LORA_EBADF;
    }
    if (dr > 5) {
        return -MOY_LORA_EINVAL;
    }
    if (busy()) {
        return -MOY_LORA_EBUSY;
    }
    moy_lw_init(&R->lw, dev_eui, join_eui, app_key);
    uint8_t req[MOY_LW_JOIN_REQ];
    moy_lw_join_request(&R->lw, (uint16_t)rnd(), req);
    R->lw_join = 1;
    return lw_send(req, sizeof(req), dr);
}

static int uplink_(uint32_t owner, uint8_t port, const uint8_t *data, size_t n, int confirmed,
             uint8_t dr) {
    if (!held(owner)) {
        return -MOY_LORA_EBADF;
    }
    if (!R->lw.joined) {
        return -MOY_LORA_ENOTCONN;
    }
    if (dr > 5) {
        return -MOY_LORA_EINVAL;
    }
    if (busy()) {
        return -MOY_LORA_EBUSY;
    }
    uint8_t frame[MOY_LW_MAX];
    int k = moy_lw_uplink(&R->lw, port, data, n, confirmed, frame, sizeof(frame));
    if (k < 0) {
        return -MOY_LORA_EINVAL;
    }
    R->lw_join = 0;
    return lw_send(frame, (size_t)k, dr);
}

int moy_lora_joined(uint32_t *dev_addr, uint32_t *fcnt_up) {
    if (R == NULL) {
        *dev_addr = *fcnt_up = 0;
        return 0;
    }
    *dev_addr = R->lw.dev_addr;
    *fcnt_up = R->lw.fcnt_up;
    return R->lw.joined;
}

uint8_t moy_lora_mode(void) {
    return R != NULL ? R->mode : MOY_LORA_IDLE;
}

uint32_t moy_lora_source(void) {
    return R != NULL ? R->src : 0;
}

// -- the verbs, each under the radio's lock --

int moy_lora_open(uint32_t owner) {
    if (radio_lock() != 0) {
        return -MOY_LORA_ENOMEM;
    }
    int r = open_(owner);
    radio_unlock();
    return r;
}

int moy_lora_close(uint32_t owner) {
    if (radio_lock() != 0) {
        return -MOY_LORA_ENOMEM;
    }
    int r = close_(owner);
    radio_unlock();
    return r;
}

int moy_lora_config(uint32_t owner, const moy_lora_cfg_t *c) {
    if (radio_lock() != 0) {
        return -MOY_LORA_ENOMEM;
    }
    int r = config_(owner, c);
    radio_unlock();
    return r;
}

int moy_lora_listen(uint32_t owner, int on) {
    if (radio_lock() != 0) {
        return -MOY_LORA_ENOMEM;
    }
    int r = listen_(owner, on);
    radio_unlock();
    return r;
}

int moy_lora_send(uint32_t owner, const uint8_t *data, size_t n) {
    if (radio_lock() != 0) {
        return -MOY_LORA_ENOMEM;
    }
    int r = send_(owner, data, n);
    radio_unlock();
    return r;
}

int moy_lora_recv(uint32_t owner, uint8_t *out, size_t cap, int16_t *rssi, int8_t *snr,
                  uint8_t *port) {
    if (radio_lock() != 0) {
        return -MOY_LORA_ENOMEM;
    }
    int r = recv_(owner, out, cap, rssi, snr, port);
    radio_unlock();
    return r;
}

int moy_lora_join(uint32_t owner, const uint8_t dev_eui[8], const uint8_t join_eui[8],
                  const uint8_t app_key[16], uint8_t dr) {
    if (radio_lock() != 0) {
        return -MOY_LORA_ENOMEM;
    }
    int r = join_(owner, dev_eui, join_eui, app_key, dr);
    radio_unlock();
    return r;
}

int moy_lora_uplink(uint32_t owner, uint8_t port, const uint8_t *data, size_t n, int confirmed,
                    uint8_t dr) {
    if (radio_lock() != 0) {
        return -MOY_LORA_ENOMEM;
    }
    int r = uplink_(owner, port, data, n, confirmed, dr);
    radio_unlock();
    return r;
}

uint32_t moy_lora_step(uint32_t now) {
    if (radio_lock() != 0) {
        return 0;
    }
    uint32_t r = step_(now);
    radio_unlock();
    return r;
}

void moy_lora_reset(void) {
    #ifndef LORA_BOARD
    moy_lora_sim(0);
    #endif
    if (R == NULL) {
        return;
    }
    if (R->owner != 0) {
        moy_lora_close(R->owner);
    }
    #ifdef LORA_EVENTS
    if (R->src != 0) {
        moy_input_ring_close(moy_input_kernel(), R->src);
    }
    #endif
    R->src = 0;
}
