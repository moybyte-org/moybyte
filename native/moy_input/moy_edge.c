// moy_edge: GPIO edges as a ring source. moy_edge.h has the contract.

#include "moy_input.h"

#ifdef MOY_INPUT_BOARD

#include "moy_edge.h"

#include "driver/gpio.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "hal/gpio_ll.h"
#include "soc/gpio_struct.h"

// Internal RAM: the ISR reads it.
typedef struct {
    moy_input_isr_t w;
    uint32_t h;                     // 0: the slot is free
    int pin;
    bool loop;
    uint8_t level;                  // what the drive wrote last
    volatile uint32_t left;         // level changes the drive has to make
    esp_timer_handle_t timer;
} edge_t;

// The slots, made at the first open and kept, as the GPIO ISR service is: a
// board that never opens a pin pays no internal RAM for them.
static edge_t *s_edge;

static void MOY_INPUT_ISR_TEXT edge_isr(void *arg) {
    edge_t *e = arg;
    moy_input_isr_put(&e->w, MOY_EV_EDGE, (uint16_t)e->pin, 0,
                      (uint32_t)gpio_ll_get_level(&GPIO, (uint32_t)e->pin));
}

static edge_t *edge_of(uint32_t h) {
    if (s_edge == NULL) {
        return NULL;
    }
    for (uint32_t i = 0; i < MOY_EDGE_PINS; i++) {
        if (h != 0 && s_edge[i].h == h) {
            return &s_edge[i];
        }
    }
    return NULL;
}

static void drive_tick(void *arg) {
    edge_t *e = arg;
    if (e->left == 0) {
        esp_timer_stop(e->timer);
        return;
    }
    e->level ^= 1u;
    gpio_set_level(e->pin, e->level);
    if (--e->left == 0) {
        esp_timer_stop(e->timer);
    }
}

static void pin_safe(int pin) {
    gpio_set_intr_type(pin, GPIO_INTR_DISABLE);
    gpio_set_direction(pin, GPIO_MODE_INPUT);
    gpio_set_pull_mode(pin, GPIO_FLOATING);
}

int moy_edge_open(int pin, uint32_t depth, bool loop, uint32_t *h) {
    if (pin < 0 || !(loop ? GPIO_IS_VALID_OUTPUT_GPIO(pin) : GPIO_IS_VALID_GPIO(pin))) {
        return MOY_INPUT_BAD;
    }
    if (s_edge == NULL) {
        s_edge = heap_caps_calloc(MOY_EDGE_PINS, sizeof(edge_t),
                                  MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        if (s_edge == NULL) {
            return MOY_INPUT_NOMEM;
        }
    }
    edge_t *e = NULL;
    for (uint32_t i = 0; i < MOY_EDGE_PINS; i++) {
        if (s_edge[i].h != 0 && s_edge[i].pin == pin) {
            return MOY_INPUT_BAD;
        }
        if (e == NULL && s_edge[i].h == 0) {
            e = &s_edge[i];
        }
    }
    if (e == NULL) {
        return MOY_INPUT_FULL;
    }
    esp_err_t svc = gpio_install_isr_service(0);
    if (svc != ESP_OK && svc != ESP_ERR_INVALID_STATE) {
        return MOY_INPUT_NOMEM;
    }
    moy_input_t *t = moy_input_kernel();
    char name[MOY_INPUT_NAME + 1] = "gpio";
    name[4] = (char)('0' + pin / 10);
    name[5] = (char)('0' + pin % 10);
    name[6] = 0;
    uint32_t src;
    int rc = t == NULL ? MOY_INPUT_NOMEM : moy_input_ring_open(t, name, MOY_EV_ISR, depth, &src);
    if (rc != MOY_INPUT_OK) {
        return rc;
    }
    moy_input_isr_attach(t, src, &e->w);
    e->pin = pin;
    e->loop = loop;
    e->level = 0;
    e->left = 0;
    e->timer = NULL;
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << pin,
        .mode = loop ? GPIO_MODE_INPUT_OUTPUT : GPIO_MODE_INPUT,
        .intr_type = GPIO_INTR_ANYEDGE,
    };
    if (gpio_config(&io) != ESP_OK) {
        moy_input_isr_detach(&e->w);
        moy_input_ring_close(t, src);
        return MOY_INPUT_BAD;
    }
    if (loop) {
        gpio_set_level(pin, 0);
    }
    if (gpio_isr_handler_add(pin, edge_isr, e) != ESP_OK) {
        pin_safe(pin);
        moy_input_isr_detach(&e->w);
        moy_input_ring_close(t, src);
        return MOY_INPUT_NOMEM;
    }
    e->h = src;
    *h = src;
    return MOY_INPUT_OK;
}

int moy_edge_close(uint32_t h) {
    edge_t *e = edge_of(h);
    if (e == NULL) {
        return MOY_INPUT_STALE;
    }
    if (e->timer != NULL) {
        esp_timer_stop(e->timer);
        esp_timer_delete(e->timer);
        e->timer = NULL;
    }
    e->left = 0;
    gpio_isr_handler_remove(e->pin);
    pin_safe(e->pin);
    moy_input_isr_detach(&e->w);
    e->h = 0;
    return moy_input_ring_close(moy_input_kernel(), h);
}

int moy_edge_drive(uint32_t h, uint32_t n, uint32_t period_us) {
    edge_t *e = edge_of(h);
    if (e == NULL) {
        return MOY_INPUT_STALE;
    }
    if (!e->loop || period_us < 50u) {
        return MOY_INPUT_BAD;
    }
    if (e->timer == NULL) {
        esp_timer_create_args_t a = {
            .callback = drive_tick,
            .arg = e,
            .dispatch_method = ESP_TIMER_TASK,
            .name = "moy_edge",
        };
        if (esp_timer_create(&a, &e->timer) != ESP_OK) {
            return MOY_INPUT_NOMEM;
        }
    }
    esp_timer_stop(e->timer);
    e->left = n;
    if (n != 0 && esp_timer_start_periodic(e->timer, period_us) != ESP_OK) {
        e->left = 0;
        return MOY_INPUT_NOMEM;
    }
    return MOY_INPUT_OK;
}

uint32_t moy_edge_driving(uint32_t h) {
    edge_t *e = edge_of(h);
    return e == NULL ? 0 : e->left;
}

#endif // MOY_INPUT_BOARD
