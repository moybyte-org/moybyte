// The browser console's page: the add-on cart's pack (THE PACK, below), which
// the webhost serves.
//
// THE ADD-ON. The browser console is not in the firmware. It is a cart, one
// per page: `web_console_<12 hex>.moy` ("runtime": "web", "moybyte":
// {"type": "web", "fits": <the page's digest>}), whose payload is
// `moybyte_web.pack`, installed like any cart from the signed index the
// release publishes beside the firmware (runtime/carts/cart_index.py's
// load_indexes; tools/web_pack.py writes both). The image names its page
// (native/moy_web/moy_web_blob.h's moy_web_want), so the cart that fits it has a name this module can work
// out under the carts root the webhost gives it (moy_page_at):
//
//   NONE    this image names no page (the browser's own build, the host)
//   ABSENT  no add-on cart: MOY_PAGE_ABSENT_WHY
//   STALE   an add-on cart that fits another image, or a pack whose head does
//           not parse: MOY_PAGE_STALE_WHY, until the one that fits installs
//           (runtime/carts/web_addon.py's AddonStep does it after an update,
//           a cable flash on a board with no OTA: tools/board_flash.py)
//   READY   the head names this image's digest: its assets are served
//
// The head is read when the page's root is asked for and kept for the assets
// that follow. The body is trusted as the head describes it: an install checks
// every file's size and sha256 against the signed index before the cart's
// folder moves into place, and a cable push's tool checks the bytes it sent.
// Every store op is bracketed by moy_vol_gate_enter, which on the T-Deck
// drains the panel's flush first.
//
// THE PACK (tools/gen_web_blob.py writes it): a 4 KiB head, then the assets,
// each at the offset its entry names (from the pack's start, 4-byte aligned).
// An entry's `name` is the SERVED name, `.gz` suffix and all: the webhost looks
// up "<asset>.gz" first. The head's digest is the sha256 of every byte after
// the head.

#ifndef MOY_PAGE_H
#define MOY_PAGE_H

#include <stddef.h>
#include <stdint.h>

#define MOY_WEB_PACK_MAGIC "MOYWEB01"
#define MOY_WEB_PACK_HEAD 4096u
#define MOY_WEB_PACK_MAX 12
#define MOY_WEB_PACK_NAME 40

typedef struct {
    char name[MOY_WEB_PACK_NAME];   /* NUL-terminated, the served name */
    uint32_t off;
    uint32_t len;
} moy_web_pack_entry_t;

typedef struct {
    char magic[8];
    uint32_t count;
    uint32_t bytes;                 /* the whole pack, head included */
    unsigned char digest[32];       /* sha256 of bytes [MOY_WEB_PACK_HEAD, bytes) */
    moy_web_pack_entry_t entry[MOY_WEB_PACK_MAX];
} moy_web_pack_head_t;

// The add-on's folder in a carts root is this, the page's 12 hex and ".moy",
// and its pack is the file below (tools/web_pack.py's CART_ID, PACK_FILE).
#define MOY_PAGE_CART "web_console_"
#define MOY_PAGE_FILE "moybyte_web.pack"

#define MOY_PAGE_NONE 0
#define MOY_PAGE_ABSENT 1
#define MOY_PAGE_STALE 2
#define MOY_PAGE_READY 3

#define MOY_PAGE_ABSENT_WHY \
    "The browser console isn't installed on this console. Install it from Get Carts."
#define MOY_PAGE_STALE_WHY "this console needs its page updated"

// The carts root the add-on is looked for in, or NULL / "" for none.
void moy_page_at(const char *root);
// The pack that fits this image, once a question has worked it out.
const char *moy_page_path(void);
// The 32 bytes of the pack body this image names: 1, or 0 where it names none.
int moy_page_want(uint8_t out[32]);
// The host's tests name a digest the way an image does; NULL forgets it.
void moy_page_name(const uint8_t *digest);
int moy_page_state(void);
// NULL when READY, else the sentence a browser gets.
const char *moy_page_why(void);
// Asset `name` of a READY pack: 0 with its offset and length, else -ENOENT.
int moy_page_find(const char *name, uint32_t *off, uint32_t *len);
// The i-th asset's name of a READY pack, or NULL past the last.
const char *moy_page_asset(unsigned i, uint32_t *len);

// Read the head again at the next question (the file changed).
void moy_page_forget(void);

#endif
