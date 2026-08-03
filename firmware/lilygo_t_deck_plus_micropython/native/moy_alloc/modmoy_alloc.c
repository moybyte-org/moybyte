// Moybyte moy_alloc: DMA-capable memory allocator for the native canvas blitter.
//
// lcd_bus.allocate_framebuffer is capped at 2 slots (both are consumed by the
// LVGL ST7789 driver's draw buffers), and ESP-IDF SPI esp_lcd_panel_io_tx_color
// requires a DMA-capable buffer (no bounce buffer is configured on this bus).
// This module exposes malloc_dma(size) -> writable memoryview backed by
// MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL, so moy_canvas can DMA the canvas
// framebuffer straight to the panel, bypassing LVGL's software-rotated flush.

#include "py/obj.h"
#include "py/runtime.h"
#include "py/objarray.h"
#include "py/binary.h"

#ifdef ESP_IDF_VERSION
#include "esp_heap_caps.h"
#define MOY_HAVE_HEAP_CAPS 1
#else
#define MOY_HAVE_HEAP_CAPS 0
#ifndef MALLOC_CAP_DMA
#define MALLOC_CAP_DMA 0
#endif
#ifndef MALLOC_CAP_INTERNAL
#define MALLOC_CAP_INTERNAL 0
#endif
#ifndef MALLOC_CAP_SPIRAM
#define MALLOC_CAP_SPIRAM 0
#endif
#endif

// malloc_dma(size, caps=DMA|INTERNAL) -> writable memoryview of `size` bytes.
// The memoryview is marked writable (typecode |= 0x80) the same way lcd_bus
// marks its framebuffers (lcd_types.c). moy_canvas never frees it (one-shot,
// device-lifetime allocation).
static mp_obj_t moy_alloc_malloc_dma(size_t n_args, const mp_obj_t *args) {
    mp_int_t size = mp_obj_get_int(args[0]);
    if (size <= 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("size must be positive"));
    }
#if MOY_HAVE_HEAP_CAPS
    uint32_t caps = (n_args > 1)
        ? (uint32_t)mp_obj_get_int(args[1])
        : (MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    /* Moybyte #43: 64-byte (cache-line) aligned. PSRAM framebuffers are now DMA'd
     * directly by the SPI master (no internal bounce), and GDMA-from-PSRAM +
     * esp_cache_msync want a cache-line-aligned base; an unaligned base glitched the
     * frame tail (the Beeper bottom artifact). 64 covers the S3 32/64B line either way. */
    void *buf = heap_caps_aligned_calloc(64, 1, (size_t)size, caps);
#else
    (void)n_args;
    void *buf = m_malloc0((size_t)size);
#endif
    if (buf == NULL) {
        mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("moy_alloc: out of memory"));
    }
    mp_obj_array_t *view = MP_OBJ_TO_PTR(
        mp_obj_new_memoryview(BYTEARRAY_TYPECODE, (size_t)size, buf));
    view->typecode |= 0x80;  // mark writable
    return MP_OBJ_FROM_PTR(view);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_alloc_malloc_dma_obj, 1, 2, moy_alloc_malloc_dma);

// -- moy_buf (#186): GC-invisible byte storage with an explicit free ---------
//
// MicroPython's GC mark phase conservatively scans every word of every live
// gc-heap block, so warm byte caches (cover runs/bitmaps, RGB565 bakes) tax
// EVERY collect: measured on the T-Deck, live 638KB -> ~114ms a collect,
// live 1427KB -> ~243ms. alloc() places such payloads outside the gc heap
// (PSRAM by default); free() returns them. A registry of live allocations
// makes free() refuse any pointer this module did not hand out -- a wrong
// free raises ValueError instead of corrupting the heap -- and a freed view
// is neutered (len 0, items NULL) so a stale read raises instead of reading
// freed memory. Python-side discipline lives in runtime/moybuf.py.

#if MOY_HAVE_HEAP_CAPS
typedef struct _moy_buf_node_t {
    void *ptr;
    size_t size;
    struct _moy_buf_node_t *next;
} moy_buf_node_t;

static moy_buf_node_t *moy_buf_live = NULL;
static size_t moy_buf_count = 0;
static size_t moy_buf_bytes = 0;
#endif

// alloc(size, caps=SPIRAM) -> zeroed writable memoryview outside the gc heap.
static mp_obj_t moy_alloc_alloc(size_t n_args, const mp_obj_t *args) {
    mp_int_t size = mp_obj_get_int(args[0]);
    if (size <= 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("size must be positive"));
    }
#if MOY_HAVE_HEAP_CAPS
    uint32_t caps = (n_args > 1)
        ? (uint32_t)mp_obj_get_int(args[1])
        : MALLOC_CAP_SPIRAM;
    void *buf = heap_caps_calloc(1, (size_t)size, caps);
    if (buf == NULL) {
        mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("moy_alloc: out of memory"));
    }
    moy_buf_node_t *node = heap_caps_malloc(sizeof(moy_buf_node_t),
                                            MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (node == NULL) {
        heap_caps_free(buf);
        mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("moy_alloc: out of memory"));
    }
    node->ptr = buf;
    node->size = (size_t)size;
    node->next = moy_buf_live;
    moy_buf_live = node;
    moy_buf_count += 1;
    moy_buf_bytes += (size_t)size;
#else
    (void)n_args;
    void *buf = m_malloc0((size_t)size);   // non-IDF build: gc heap, free() no-ops
#endif
    mp_obj_array_t *view = MP_OBJ_TO_PTR(
        mp_obj_new_memoryview(BYTEARRAY_TYPECODE, (size_t)size, buf));
    view->typecode |= 0x80;  // mark writable
    return MP_OBJ_FROM_PTR(view);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_alloc_alloc_obj, 1, 2, moy_alloc_alloc);

// free(view): release an alloc() buffer, matched by its base pointer. (A
// SLICE of the view carries the same base -- mp memoryview slicing offsets a
// separate field -- so a slice would free the whole buffer too; the Python
// owner discipline only ever passes the original view.)
static mp_obj_t moy_alloc_free(mp_obj_t view_in) {
#if MOY_HAVE_HEAP_CAPS
    if (!mp_obj_is_type(view_in, &mp_type_memoryview)) {
        mp_raise_TypeError(MP_ERROR_TEXT("moy_alloc: free() wants a memoryview"));
    }
    mp_obj_array_t *view = MP_OBJ_TO_PTR(view_in);
    void *ptr = view->items;
    moy_buf_node_t **link = &moy_buf_live;
    while (*link != NULL) {
        moy_buf_node_t *node = *link;
        if (node->ptr == ptr) {
            *link = node->next;
            moy_buf_count -= 1;
            moy_buf_bytes -= node->size;
            heap_caps_free(node->ptr);
            heap_caps_free(node);
            view->len = 0;      // neuter: a stale read raises, never reads freed RAM
            view->items = NULL;
            return mp_const_none;
        }
        link = &node->next;
    }
    mp_raise_ValueError(MP_ERROR_TEXT("moy_alloc: not a live alloc() buffer"));
#else
    (void)view_in;             // gc-heap fallback storage: the collector owns it
    return mp_const_none;
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_alloc_free_obj, moy_alloc_free);

// stats() -> (live_buffers, live_bytes) for the MEMX diag line.
static mp_obj_t moy_alloc_stats(void) {
#if MOY_HAVE_HEAP_CAPS
    mp_obj_t items[2] = {
        mp_obj_new_int((mp_int_t)moy_buf_count),
        mp_obj_new_int((mp_int_t)moy_buf_bytes),
    };
#else
    mp_obj_t items[2] = { MP_OBJ_NEW_SMALL_INT(0), MP_OBJ_NEW_SMALL_INT(0) };
#endif
    return mp_obj_new_tuple(2, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_alloc_stats_obj, moy_alloc_stats);

static const mp_rom_map_elem_t moy_alloc_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),        MP_OBJ_NEW_QSTR(MP_QSTR_moy_alloc) },
    { MP_ROM_QSTR(MP_QSTR_malloc_dma),      MP_ROM_PTR(&moy_alloc_malloc_dma_obj) },
    { MP_ROM_QSTR(MP_QSTR_alloc),           MP_ROM_PTR(&moy_alloc_alloc_obj) },
    { MP_ROM_QSTR(MP_QSTR_free),            MP_ROM_PTR(&moy_alloc_free_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats),           MP_ROM_PTR(&moy_alloc_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_MEMORY_DMA),      MP_ROM_INT(MALLOC_CAP_DMA) },
    { MP_ROM_QSTR(MP_QSTR_MEMORY_INTERNAL), MP_ROM_INT(MALLOC_CAP_INTERNAL) },
    // MEMORY_SPIRAM mirrors lcd_bus's constant so a mainline build (P4 #58, no
    // lcd_bus module) can request PSRAM layer buffers through moy_alloc alone.
    { MP_ROM_QSTR(MP_QSTR_MEMORY_SPIRAM),   MP_ROM_INT(MALLOC_CAP_SPIRAM) },
};

static MP_DEFINE_CONST_DICT(moy_alloc_globals, moy_alloc_globals_table);

const mp_obj_module_t mp_module_moy_alloc = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_alloc_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_alloc, mp_module_moy_alloc);
