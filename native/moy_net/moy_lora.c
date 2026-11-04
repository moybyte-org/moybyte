// moy_lora: the `lora` device API's driver (moy_lora.h).

#include "moy_lora.h"

#include <stdlib.h>
#include <string.h>

#include "moy_lorawan.h"

#if defined(__has_include)
#if __has_include("moy_input.h")
#define LORA_EVENTS 1
#include "moy_input.h"
#endif
#endif

#if defined(ESP_PLATFORM) && defined(MOY_API_LORA) && defined(MOY_HW_LORA_RST)
#define LORA_BOARD 1
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "moy_flush.h"
#endif

typedef struct {
    uint8_t len, port, kind;
    int16_t rssi;
    int8_t snr;
    uint8_t data[MOY_LORA_PKT];
} pkt_t;

static volatile uint32_t s_owner;
static uint8_t s_mode;
static int s_listen;
static moy_lora_cfg_t s_cfg;
static pkt_t *s_q;                      // MOY_LORA_QUEUE, kernel memory
static uint32_t s_qhead, s_qn;
static uint32_t s_src;
static moy_lw_t s_lw;
static int s_lw_join;                   // the exchange in flight is a join
static uint32_t s_lw_freq;
static uint8_t s_lw_dr;
static moy_lw_win_t s_win[2];
static uint32_t s_tx_end;               // ms: the exchange's transmission ended
static volatile int s_irq;
static uint32_t s_seed = 0x6d6f79u;

// -- events ---------------------------------------------------------------------------

static void post(uint8_t ev, uint16_t a, int16_t rssi, int8_t snr) {
    #ifdef LORA_EVENTS
    moy_input_t *t = moy_input_kernel();
    if (t != NULL && s_src != 0) {
        uint32_t b = (uint32_t)(uint16_t)rssi | (uint32_t)(uint16_t)(int16_t)snr << 16;
        moy_input_post(t, s_src, MOY_EV_LORA, a, ev, b);
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
    if (s_src == 0) {
        moy_input_t *t = moy_input_kernel();
        uint32_t src;
        if (t != NULL && moy_input_ring_open(t, "lora", MOY_EV_FIFO, 16, &src) == MOY_INPUT_OK) {
            s_src = src;
        }
    }
    #endif
}

static void enqueue(uint8_t kind, uint8_t port, const uint8_t *d, size_t n, int16_t rssi,
                    int8_t snr) {
    if (s_q == NULL) {
        return;
    }
    if (s_qn == MOY_LORA_QUEUE) {
        s_qhead = (s_qhead + 1u) % MOY_LORA_QUEUE;
        s_qn--;
    }
    pkt_t *p = &s_q[(s_qhead + s_qn) % MOY_LORA_QUEUE];
    p->len = (uint8_t)n;
    p->port = port;
    p->kind = kind;
    p->rssi = rssi;
    p->snr = snr;
    memcpy(p->data, d, n);
    s_qn++;
}

static uint32_t rnd(void) {
    #ifdef LORA_BOARD
    return esp_random();
    #else
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
#define PIN_CS MOY_HW_SPI2_RADIO_CS
#define SPI_HZ 8000000

static spi_device_handle_t s_spi;
static uint8_t *s_tx, *s_rx;            // DMA-capable, 264 bytes each
static TaskHandle_t s_task;
static volatile int s_run;

static void wait_busy(void) {
    int64_t t0 = esp_timer_get_time();
    while (gpio_get_level(PIN_BUSY) && esp_timer_get_time() - t0 < 20000) {
    }
}

// One command in a band gap: `n` bytes out, the reply read into s_rx.
static int xfer(size_t n) {
    moy_flush_gate_take();
    wait_busy();
    spi_transaction_t t = { .length = n * 8, .tx_buffer = s_tx, .rx_buffer = s_rx };
    esp_err_t e = spi_device_polling_transmit(s_spi, &t);
    moy_flush_gate_give();
    return e == ESP_OK ? 0 : -1;
}

static int cmd(uint8_t op, const uint8_t *a, size_t n) {
    s_tx[0] = op;
    memcpy(s_tx + 1, a, n);
    return xfer(1 + n);
}

// A read command: its `n` argument bytes, a status byte, then `m` bytes back.
static int cmd_read(uint8_t op, const uint8_t *a, size_t n, uint8_t *out, size_t m) {
    s_tx[0] = op;
    memcpy(s_tx + 1, a, n);
    memset(s_tx + 1 + n, 0, 1 + m);
    if (xfer(2 + n + m) != 0) {
        return -1;
    }
    memcpy(out, s_rx + 2 + n, m);
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

static void IRAM_ATTR dio1_isr(void *arg) {
    (void)arg;
    s_irq = 1;
    BaseType_t woke = pdFALSE;
    if (s_task != NULL) {
        vTaskNotifyGiveFromISR(s_task, &woke);
    }
    if (woke) {
        portYIELD_FROM_ISR();
    }
}

static uint32_t now_ms(void) {
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static void task_fn(void *arg) {
    (void)arg;
    while (s_run) {
        uint32_t due = moy_lora_step(now_ms());
        TickType_t wait = portMAX_DELAY;
        if (due != 0) {
            int32_t ms = (int32_t)(due - now_ms());
            wait = ms <= 0 ? 0 : pdMS_TO_TICKS((uint32_t)ms) ? pdMS_TO_TICKS((uint32_t)ms) : 1;
        }
        ulTaskNotifyTake(pdTRUE, wait);
    }
    s_task = NULL;
    vTaskDelete(NULL);
}

static int radio_up(void) {
    s_tx = heap_caps_malloc(264, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    s_rx = heap_caps_malloc(264, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (s_tx == NULL || s_rx == NULL) {
        return -MOY_LORA_ENOMEM;
    }
    spi_device_interface_config_t dc = {
        .clock_speed_hz = SPI_HZ, .mode = 0, .spics_io_num = PIN_CS, .queue_size = 1,
    };
    if (spi_bus_add_device(SPI2_HOST, &dc, &s_spi) != ESP_OK) {
        s_spi = NULL;
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
    s_run = 1;
    if (xTaskCreatePinnedToCore(task_fn, "moy_lora", 4096, NULL, 10, &s_task, 0) != pdPASS) {
        s_run = 0;
        s_task = NULL;
        return -MOY_LORA_ENOMEM;
    }
    return 0;
}

static void radio_down(void) {
    s_run = 0;
    if (s_task != NULL) {
        xTaskNotifyGive(s_task);
        for (int i = 0; s_task != NULL && i < 50; i++) {
            vTaskDelay(pdMS_TO_TICKS(2));
        }
    }
    if (s_spi != NULL) {
        uint8_t a = 0x00;
        cmd(0x80, &a, 1);
        a = 0x04;
        cmd(0x84, &a, 1);                                        // sleep, warm start
        gpio_isr_handler_remove(PIN_IRQ);
        gpio_set_intr_type(PIN_IRQ, GPIO_INTR_DISABLE);
        spi_bus_remove_device(s_spi);
        s_spi = NULL;
    }
    heap_caps_free(s_tx);
    heap_caps_free(s_rx);
    s_tx = s_rx = NULL;
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
    s_tx[0] = 0x0e;
    s_tx[1] = 0x00;
    memcpy(s_tx + 2, d, n);
    rc |= xfer(2 + n);
    static const uint8_t clr[2] = { 0xff, 0xff };
    rc |= cmd(0x02, clr, 2);
    static const uint8_t to[3] = { 0x02, 0xee, 0x00 };          // 3 s fence
    rc |= cmd(0x83, to, 3);
    return rc;
}

static int radio_rx(uint32_t timeout_ms) {
    static const uint8_t clr[2] = { 0xff, 0xff };
    int rc = cmd(0x02, clr, 2);
    uint32_t t = timeout_ms == 0 ? 0xffffffu : timeout_ms * 64u;
    uint8_t to[3] = { (uint8_t)(t >> 16), (uint8_t)(t >> 8), (uint8_t)t };
    rc |= cmd(0x82, to, 3);
    return rc;
}

static void radio_standby(void) {
    uint8_t a = 0x00;
    cmd(0x80, &a, 1);
}

static int radio_event(uint8_t *buf, size_t *n, int16_t *rssi, int8_t *snr) {
    if (!s_irq) {
        return RADIO_NONE;
    }
    s_irq = 0;
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

static int s_sim_on;
static struct {
    uint32_t freq, bw;
    uint8_t sf, sync, iq_inv;
} s_tune;
static int s_rxing;                     // the receiver is on
static int s_txing;
static pkt_t s_heard;
static int s_heard_ok;
static struct {
    uint8_t len, sf, sync, iq;
    uint32_t freq, bw;
    uint8_t data[MOY_LORA_PKT];
} s_out[MOY_LORA_QUEUE];
static uint32_t s_out_head, s_out_n;

static int radio_up(void) {
    return 0;
}

static void radio_down(void) {
    s_rxing = s_txing = 0;
}

static int radio_tune(uint32_t freq, uint8_t sf, uint32_t bw, uint8_t cr, int8_t power,
                      uint8_t sync, int iq_inv, int crc, uint16_t preamble, uint8_t len) {
    (void)cr;
    (void)power;
    (void)crc;
    (void)preamble;
    (void)len;
    s_rxing = 0;
    s_tune.freq = freq;
    s_tune.sf = sf;
    s_tune.bw = bw;
    s_tune.sync = sync;
    s_tune.iq_inv = (uint8_t)iq_inv;
    return 0;
}

static int radio_tx(const uint8_t *d, size_t n, uint8_t sf, uint16_t preamble, int crc) {
    (void)sf;
    (void)preamble;
    (void)crc;
    if (s_out_n == MOY_LORA_QUEUE) {
        s_out_head = (s_out_head + 1u) % MOY_LORA_QUEUE;
        s_out_n--;
    }
    uint32_t i = (s_out_head + s_out_n) % MOY_LORA_QUEUE;
    s_out[i].len = (uint8_t)n;
    s_out[i].freq = s_tune.freq;
    s_out[i].sf = s_tune.sf;
    s_out[i].bw = s_tune.bw;
    s_out[i].sync = s_tune.sync;
    s_out[i].iq = s_tune.iq_inv;
    memcpy(s_out[i].data, d, n);
    s_out_n++;
    s_txing = 1;
    s_rxing = 0;
    s_irq = 1;
    return 0;
}

static int radio_rx(uint32_t timeout_ms) {
    (void)timeout_ms;
    s_rxing = 1;
    return 0;
}

static void radio_standby(void) {
    s_rxing = 0;
}

static int radio_event(uint8_t *buf, size_t *n, int16_t *rssi, int8_t *snr) {
    if (!s_irq) {
        return RADIO_NONE;
    }
    s_irq = 0;
    if (s_txing) {
        s_txing = 0;
        return RADIO_TX_DONE;
    }
    if (s_heard_ok) {
        s_heard_ok = 0;
        memcpy(buf, s_heard.data, s_heard.len);
        *n = s_heard.len;
        *rssi = s_heard.rssi;
        *snr = s_heard.snr;
        return RADIO_RX_DONE;
    }
    return RADIO_NONE;
}

void moy_lora_sim(int on) {
    s_sim_on = on;
    s_out_n = s_out_head = 0;
}

int moy_lora_sim_sent(uint8_t *out, size_t cap, uint32_t *freq, uint8_t *sf, uint32_t *bw,
                      uint8_t *sync, uint8_t *iq) {
    if (s_out_n == 0) {
        return 0;
    }
    uint32_t i = s_out_head;
    size_t n = s_out[i].len < cap ? s_out[i].len : cap;
    memcpy(out, s_out[i].data, n);
    *freq = s_out[i].freq;
    *sf = s_out[i].sf;
    *bw = s_out[i].bw;
    *sync = s_out[i].sync;
    *iq = s_out[i].iq;
    s_out_head = (s_out_head + 1u) % MOY_LORA_QUEUE;
    s_out_n--;
    return (int)n;
}

int moy_lora_sim_air(const uint8_t *data, size_t n, uint32_t freq, uint8_t sf, int16_t rssi,
                     int8_t snr) {
    if (!s_rxing || s_tune.freq != freq || s_tune.sf != sf || n > MOY_LORA_PKT) {
        return 0;
    }
    memcpy(s_heard.data, data, n);
    s_heard.len = (uint8_t)n;
    s_heard.rssi = rssi;
    s_heard.snr = snr;
    s_heard_ok = 1;
    s_irq = 1;
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
    s_irq = 1;
}

// -- the state machine ------------------------------------------------------------------

static int tune_raw(void) {
    return radio_tune(s_cfg.freq, s_cfg.sf, s_cfg.bw, s_cfg.cr, s_cfg.power, s_cfg.sync, 0, 1,
                      s_cfg.preamble, MOY_LORA_PKT);
}

// Back to what the holder asked for between transmissions.
static void rest(void) {
    if (s_listen) {
        tune_raw();
        radio_rx(0);
        s_mode = MOY_LORA_LISTEN;
    } else {
        radio_standby();
        s_mode = MOY_LORA_IDLE;
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
    lw_tune(s_win[i].freq, s_win[i].dr, 1);
    radio_rx(win_ms(s_win[i].dr));
    s_mode = i == 0 ? MOY_LORA_LW_RX1 : MOY_LORA_LW_RX2;
}

static void lw_finish(int ok) {
    if (s_lw_join) {
        post(ok ? MOY_LORA_EV_JOINED : MOY_LORA_EV_JOIN_FAILED, 0, 0, 0);
    } else {
        post(MOY_LORA_EV_UP_DONE, s_lw.acked, 0, 0);
    }
    rest();
}

// A packet one of the windows caught: 1 when it ended the exchange.
static int lw_heard(const uint8_t *buf, size_t n, int16_t rssi, int8_t snr) {
    if (s_lw_join) {
        if (moy_lw_join_accept(&s_lw, buf, n) == MOY_LW_OK) {
            lw_finish(1);
            return 1;
        }
        return 0;
    }
    uint8_t port = 0;
    uint8_t out[MOY_LORA_PKT];
    int k = moy_lw_downlink(&s_lw, buf, n, &port, out, sizeof(out));
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

uint32_t moy_lora_step(uint32_t now) {
    if (s_owner == 0) {
        return 0;
    }
    uint8_t buf[MOY_LORA_PKT];
    size_t n = 0;
    int16_t rssi = 0;
    int8_t snr = 0;
    int ev = radio_event(buf, &n, &rssi, &snr);
    switch (s_mode) {
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
            s_tx_end = now;
            radio_standby();
            s_mode = MOY_LORA_LW_WAIT1;
            /* fall through */
        case MOY_LORA_LW_WAIT1: {
            uint32_t at = s_tx_end + s_win[0].delay_ms - MOY_LORA_WIN_EARLY_MS;
            if (!due(now, at)) {
                return at;
            }
            open_window(0);
            return s_tx_end + s_win[0].delay_ms + win_ms(s_win[0].dr) - MOY_LORA_WIN_EARLY_MS;
        }
        case MOY_LORA_LW_RX1:
        case MOY_LORA_LW_RX2: {
            int i = s_mode == MOY_LORA_LW_RX1 ? 0 : 1;
            if (ev == RADIO_RX_DONE && lw_heard(buf, n, rssi, snr)) {
                return 0;
            }
            uint32_t end = s_tx_end + s_win[i].delay_ms - MOY_LORA_WIN_EARLY_MS
                           + win_ms(s_win[i].dr);
            if (ev == RADIO_TIMEOUT || ev == RADIO_RX_DONE || due(now, end)) {
                radio_standby();
                if (i == 1) {
                    lw_finish(0);
                    return 0;
                }
                s_mode = MOY_LORA_LW_WAIT2;
            } else {
                return end;
            }
        }
            /* fall through */
        case MOY_LORA_LW_WAIT2: {
            uint32_t at = s_tx_end + s_win[1].delay_ms - MOY_LORA_WIN_EARLY_MS;
            if (!due(now, at)) {
                return at;
            }
            open_window(1);
            return s_tx_end + s_win[1].delay_ms + win_ms(s_win[1].dr) - MOY_LORA_WIN_EARLY_MS;
        }
        default:
            return 0;
    }
}

static void kick(void) {
    #ifdef LORA_BOARD
    if (s_task != NULL) {
        xTaskNotifyGive(s_task);
    }
    #endif
}

// -- the holder -----------------------------------------------------------------------

int moy_lora_open(uint32_t owner) {
    if (owner == 0 || !moy_lora_present()) {
        return -MOY_LORA_ENODEV;
    }
    if (s_owner == owner) {
        return 0;
    }
    if (s_owner != 0) {
        return -MOY_LORA_EBUSY;
    }
    if (s_q == NULL) {
        #ifdef LORA_BOARD
        s_q = heap_caps_malloc(sizeof(pkt_t) * MOY_LORA_QUEUE, MALLOC_CAP_SPIRAM);
        #else
        s_q = malloc(sizeof(pkt_t) * MOY_LORA_QUEUE);
        #endif
        if (s_q == NULL) {
            return -MOY_LORA_ENOMEM;
        }
    }
    s_qhead = s_qn = 0;
    open_source();
    s_cfg = (moy_lora_cfg_t){ .freq = 868100000u, .bw = 125000u, .sf = 9, .cr = 5, .power = 14,
                              .sync = 0x12, .preamble = 8 };
    s_listen = 0;
    s_mode = MOY_LORA_IDLE;
    memset(&s_lw, 0, sizeof(s_lw));
    int rc = radio_up();
    if (rc != 0) {
        radio_down();
        return rc;
    }
    s_owner = owner;
    tune_raw();
    radio_standby();
    return 0;
}

int moy_lora_close(uint32_t owner) {
    if (owner == 0 || owner != s_owner) {
        return -MOY_LORA_EBADF;
    }
    s_owner = 0;
    radio_down();
    s_mode = MOY_LORA_IDLE;
    s_listen = 0;
    s_qn = 0;
    memset(&s_lw, 0, sizeof(s_lw));
    return 0;
}

uint32_t moy_lora_sweep(uint32_t owner) {
    return moy_lora_close(owner) == 0 ? 1u : 0u;
}

uint32_t moy_lora_owner(void) {
    return s_owner;
}

static int held(uint32_t owner) {
    return owner != 0 && owner == s_owner;
}

static int busy(void) {
    return s_mode != MOY_LORA_IDLE && s_mode != MOY_LORA_LISTEN;
}

int moy_lora_config(uint32_t owner, const moy_lora_cfg_t *c) {
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
    s_cfg = *c;
    rest();
    return 0;
}

int moy_lora_listen(uint32_t owner, int on) {
    if (!held(owner)) {
        return -MOY_LORA_EBADF;
    }
    s_listen = on;
    if (!busy()) {
        rest();
    }
    return 0;
}

int moy_lora_send(uint32_t owner, const uint8_t *data, size_t n) {
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
    s_mode = MOY_LORA_TX;
    if (radio_tx(data, n, s_cfg.sf, s_cfg.preamble, 1) != 0) {
        rest();
        return -MOY_LORA_EIO;
    }
    kick();
    return 0;
}

int moy_lora_recv(uint32_t owner, uint8_t *out, size_t cap, int16_t *rssi, int8_t *snr,
                  uint8_t *port) {
    if (!held(owner)) {
        return -MOY_LORA_EBADF;
    }
    if (s_qn == 0) {
        return 0;
    }
    pkt_t *p = &s_q[s_qhead];
    size_t n = p->len < cap ? p->len : cap;
    memcpy(out, p->data, n);
    *rssi = p->rssi;
    *snr = p->snr;
    *port = p->port;
    s_qhead = (s_qhead + 1u) % MOY_LORA_QUEUE;
    s_qn--;
    return (int)n;
}

static int lw_send(const uint8_t *frame, size_t n, uint8_t dr) {
    s_lw_dr = dr;
    s_lw_freq = moy_lw_channel(&s_lw, rnd());
    moy_lw_windows(&s_lw, s_lw_join, s_lw_freq, dr, s_win);
    lw_tune(s_lw_freq, dr, 0);
    s_mode = MOY_LORA_LW_TX;
    if (radio_tx(frame, n, moy_lw_sf(dr), 8, 1) != 0) {
        rest();
        return -MOY_LORA_EIO;
    }
    kick();
    return 0;
}

int moy_lora_join(uint32_t owner, const uint8_t dev_eui[8], const uint8_t join_eui[8],
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
    moy_lw_init(&s_lw, dev_eui, join_eui, app_key);
    uint8_t req[MOY_LW_JOIN_REQ];
    moy_lw_join_request(&s_lw, (uint16_t)rnd(), req);
    s_lw_join = 1;
    return lw_send(req, sizeof(req), dr);
}

int moy_lora_uplink(uint32_t owner, uint8_t port, const uint8_t *data, size_t n, int confirmed,
                    uint8_t dr) {
    if (!held(owner)) {
        return -MOY_LORA_EBADF;
    }
    if (!s_lw.joined) {
        return -MOY_LORA_ENOTCONN;
    }
    if (dr > 5) {
        return -MOY_LORA_EINVAL;
    }
    if (busy()) {
        return -MOY_LORA_EBUSY;
    }
    uint8_t frame[MOY_LW_MAX];
    int k = moy_lw_uplink(&s_lw, port, data, n, confirmed, frame, sizeof(frame));
    if (k < 0) {
        return -MOY_LORA_EINVAL;
    }
    s_lw_join = 0;
    return lw_send(frame, (size_t)k, dr);
}

int moy_lora_joined(uint32_t *dev_addr, uint32_t *fcnt_up) {
    *dev_addr = s_lw.dev_addr;
    *fcnt_up = s_lw.fcnt_up;
    return s_lw.joined;
}

uint8_t moy_lora_mode(void) {
    return s_mode;
}

uint32_t moy_lora_source(void) {
    return s_src;
}

void moy_lora_reset(void) {
    if (s_owner != 0) {
        moy_lora_close(s_owner);
    }
    #ifndef LORA_BOARD
    moy_lora_sim(0);
    #endif
    #ifdef LORA_EVENTS
    if (s_src != 0) {
        moy_input_ring_close(moy_input_kernel(), s_src);
    }
    #endif
    s_src = 0;
}
