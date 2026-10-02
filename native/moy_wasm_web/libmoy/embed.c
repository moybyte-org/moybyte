/* libmoy's wasm binding in a page: the embedder's half (MOY_WASM_JS).
 *
 * A page that runs a compiled cart as a SIBLING module -- the browser's own
 * engine instantiating the cart's main.wasm beside this one -- links this
 * file beside src/moy_wasm.c, built the same way. It is all the binding asks
 * of a JavaScript embedder in C, and nothing in it is this player's: cart.c
 * is the console this player binds the cart to, and another page binds it to
 * its own.
 *
 * What C cannot do is address the cart's memory, which is a different
 * WebAssembly.Memory from this module's, or call into the cart. The three
 * functions moy_wasm.h asks of its embedder for those are here, in
 * JavaScript, and reach the page through Module.moyCart, which the page sets
 * while a cart is bound: two hand the binding copies -- a range of the cart's
 * memory copied in, or bytes copied back out -- and one runs one of par's
 * items. The page's adapters reach the import table, the hooks' brackets and
 * the trap the other way, through the five exports at the end.
 *
 * page/cart.js is this player's page.
 */

#include <stdint.h>

#include <emscripten.h>

#include "moy.h"
#include "moy_wasm.h"

#define KEEP EMSCRIPTEN_KEEPALIVE

/* The page sets Module.moyCart while a cart is bound. */
EM_JS(uint8_t *, moy_wasm_js_span, (moy_wasm *w, uint32_t offset, uint32_t n), {
    return Module.moyCart ? Module.moyCart.span(offset >>> 0, n >>> 0) : 0;
});

EM_JS(int, moy_wasm_js_store, (moy_wasm *w, uint32_t offset, const uint8_t *src, uint32_t n), {
    return Module.moyCart ? Module.moyCart.store(offset >>> 0, src, n >>> 0) : 0;
});

/* A par item, which the page runs on the cart itself: a page has one core to
 * give a cart, so the binding runs the items here, one after another. */
EM_JS(int, moy_wasm_js_item, (moy_wasm *w, int32_t i, int32_t arg, uint32_t sp), {
    return Module.moyCart ? Module.moyCart.item(i, arg, sp >>> 0) : 1;
});

/* The table and the hooks' brackets, for the page's adapters. */
KEEP const NativeSymbol *moy_web_natives(uint32_t *count) { return moy_wasm_natives(count); }
KEEP void moy_web_begin(moy_wasm *w, int hook) { moy_wasm_begin(w, hook); }
KEEP int moy_web_end(moy_wasm *w, int threw) { return moy_wasm_end(w, threw); }
KEEP const char *moy_web_trapped(const moy_wasm *w) { return moy_wasm_trapped(w); }
KEEP void moy_web_item_trap(moy_wasm *w, const char *msg) { moy_wasm_item_trap(w, msg); }
