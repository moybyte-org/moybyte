#ifndef H_STUB_ESP_ASYNC_MEMCPY_H
#define H_STUB_ESP_ASYNC_MEMCPY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "harness.h"

// The snapshot DMA engine moy_fold.c copies the game canvas with. Modelled as
// the real driver's CONTRACT rather than its convenience: a submit is refused
// unless source, destination and length are all 64-byte multiples (the S3's
// PSRAM rule, measured on Guition glass 2026-09-08 -- a 32-byte offset is
// refused there); copies complete in submission order on one engine; and the
// bytes LAND AT COMPLETION, not at submit. That last clause is what gives a
// scenario teeth: a reader that did not wait sees whatever the scratch held
// before, so "the feeder waited" is a pixel assertion and not a timestamp.
typedef struct h_dma_engine *async_memcpy_handle_t;

typedef struct {
    void *data;
} async_memcpy_event_t;

typedef bool (*async_memcpy_isr_cb_t)(async_memcpy_handle_t mcp_hdl,
                                      async_memcpy_event_t *event,
                                      void *cb_args);

typedef struct {
    uint32_t backlog;
    size_t dma_burst_size;
    uint32_t flags;
} async_memcpy_config_t;

#define ASYNC_MEMCPY_DEFAULT_CONFIG() \
    { .backlog = 8, .dma_burst_size = 16, .flags = 0 }

esp_err_t esp_async_memcpy_install(const async_memcpy_config_t *config,
                                   async_memcpy_handle_t *mcp);
esp_err_t esp_async_memcpy(async_memcpy_handle_t mcp, void *dst, void *src,
                           size_t n, async_memcpy_isr_cb_t cb_isr,
                           void *cb_args);

#endif // H_STUB_ESP_ASYNC_MEMCPY_H
