// KidCode kc_alloc: DMA-capable memory allocator for the native canvas blitter.
//
// lcd_bus.allocate_framebuffer is capped at 2 slots (both are consumed by the
// LVGL ST7789 driver's draw buffers), and ESP-IDF SPI esp_lcd_panel_io_tx_color
// requires a DMA-capable buffer (no bounce buffer is configured on this bus).
// This module exposes malloc_dma(size) -> writable memoryview backed by
// MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL, so kc_canvas can DMA the canvas
// framebuffer straight to the panel, bypassing LVGL's software-rotated flush.

#include "py/obj.h"
#include "py/runtime.h"
#include "py/objarray.h"
#include "py/binary.h"

#ifdef ESP_IDF_VERSION
#include "esp_heap_caps.h"
#define KC_HAVE_HEAP_CAPS 1
#else
#define KC_HAVE_HEAP_CAPS 0
#ifndef MALLOC_CAP_DMA
#define MALLOC_CAP_DMA 0
#endif
#ifndef MALLOC_CAP_INTERNAL
#define MALLOC_CAP_INTERNAL 0
#endif
#endif

// malloc_dma(size, caps=DMA|INTERNAL) -> writable memoryview of `size` bytes.
// The memoryview is marked writable (typecode |= 0x80) the same way lcd_bus
// marks its framebuffers (lcd_types.c). kc_canvas never frees it (one-shot,
// device-lifetime allocation).
static mp_obj_t kc_alloc_malloc_dma(size_t n_args, const mp_obj_t *args) {
    mp_int_t size = mp_obj_get_int(args[0]);
    if (size <= 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("size must be positive"));
    }
#if KC_HAVE_HEAP_CAPS
    uint32_t caps = (n_args > 1)
        ? (uint32_t)mp_obj_get_int(args[1])
        : (MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    void *buf = heap_caps_calloc(1, (size_t)size, caps);
#else
    (void)n_args;
    void *buf = m_malloc0((size_t)size);
#endif
    if (buf == NULL) {
        mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("kc_alloc: out of memory"));
    }
    mp_obj_array_t *view = MP_OBJ_TO_PTR(
        mp_obj_new_memoryview(BYTEARRAY_TYPECODE, (size_t)size, buf));
    view->typecode |= 0x80;  // mark writable
    return MP_OBJ_FROM_PTR(view);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(kc_alloc_malloc_dma_obj, 1, 2, kc_alloc_malloc_dma);

static const mp_rom_map_elem_t kc_alloc_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),        MP_OBJ_NEW_QSTR(MP_QSTR_kc_alloc) },
    { MP_ROM_QSTR(MP_QSTR_malloc_dma),      MP_ROM_PTR(&kc_alloc_malloc_dma_obj) },
    { MP_ROM_QSTR(MP_QSTR_MEMORY_DMA),      MP_ROM_INT(MALLOC_CAP_DMA) },
    { MP_ROM_QSTR(MP_QSTR_MEMORY_INTERNAL), MP_ROM_INT(MALLOC_CAP_INTERNAL) },
};

static MP_DEFINE_CONST_DICT(kc_alloc_globals, kc_alloc_globals_table);

const mp_obj_module_t mp_module_kc_alloc = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&kc_alloc_globals,
};

MP_REGISTER_MODULE(MP_QSTR_kc_alloc, mp_module_kc_alloc);
