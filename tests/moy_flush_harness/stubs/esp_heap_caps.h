#ifndef H_STUB_ESP_HEAP_CAPS_H
#define H_STUB_ESP_HEAP_CAPS_H

#include <stddef.h>
#include <stdint.h>

// The real IDF bit values, so a scenario asserting the bounce slots were asked
// for as DMA-capable INTERNAL memory (the whole reason the band machinery
// exists -- the panel DMA never reads PSRAM) is asserting something real.
#define MALLOC_CAP_DMA        (1 << 3)
#define MALLOC_CAP_INTERNAL   (1 << 11)
#define MALLOC_CAP_SPIRAM     (1 << 10)

void *heap_caps_malloc(size_t size, uint32_t caps);
void heap_caps_free(void *ptr);

#endif // H_STUB_ESP_HEAP_CAPS_H
