// moy_media: the media service (docs/os_2026-11/hardware.md section 3), an OS
// service in C. It plays one background stream for one process: an HTTP
// stream (ICY metadata read, the station's now-playing text) or bytes the
// process's face feeds it from a file, each an entry of a list an M3U or PLS
// playlist fills. MP3 is decoded (minimp3.h, vendored) and resampled to the
// output's rate and channels (moy_aud_out_rate and _channels: 44.1 kHz stereo
// on a P4, its stream's channels kept; 22.05 kHz mono elsewhere, a stereo
// stream mixed down, the browser's page among them), and the
// mix (native/moy_audio/moy_aud.h's background stream) pulls it, ducked under
// the focused session.
//
// THE BROWSER (MOY_MEDIA_CLIENT 0: its image has no HTTP client) feeds a
// URL's bytes as it feeds a file's: the face fetches it with the page's fetch
// and feeds the current generation, a URL entry OPENING until its first bytes
// or its feed's end. feed_size gives a URL its length (the response's
// Content-Length), and one with none is a live stream, opened again as a
// board's is. It reads no ICY metadata: a page's fetch may not ask for it.
//
// THE MEMORY is the MEDIA row of THE SHARES (native/moy_play/moy_play.h):
// open() takes MOY_MEDIA_SHARE from it and refuses with -ENOSPC where the row
// is absent or full, before anything is allocated. The service's state, its
// buffers and its decoder are one PSRAM block, the task's stack is PSRAM and
// the URL's client buffers are kernel memory (moy_ota.h); close() frees all of
// it and gives the row back. Nothing is static but a pointer.
//
// WHO RUNS WHAT. The VM's thread opens, closes, commands and feeds. On a board
// a task on core 0 below the flush priority (MOY_MEDIA_PRIO) reads the
// connection and decodes; it never runs a flash operation, so its stack is
// PSRAM. The host and the browser have no task: moy_media_run() is one pass
// of its loop, the face's to call. The mix's pull runs on the audio feeder's task under the
// mix's lock. The decoded ring has one writer (the task) and one reader (the
// pull); the stream buffer one writer (the task for a URL, the VM's feed for a
// file) and one reader (the task). Commands and texts change under the
// service's mutex.
//
// A STREAM'S LIFE. go(i) makes entry i current: the task drops what it held,
// and opens it. A URL whose response is a playlist (by its type or its path)
// replaces the list with its entries and plays the first. An entry that ends
// (its connection closed, or its feed ended and drained) or fails moves to
// the next; past the last the state is ENDED (FAILED when nothing played).
// A URL with no Content-Length (a live stream) that ends after playing is
// opened again MOY_MEDIA_RETRIES times before the list moves on. stop() asks the task to let go; close() then frees what
// it held once the task says it is idle (-EAGAIN until then).
//
// Errors are -errno with Linux's numbers on every tier.

#ifndef MOY_MEDIA_H
#define MOY_MEDIA_H

#include <stddef.h>
#include <stdint.h>

#define MOY_MEDIA_IN_BYTES (32u * 1024u)    // the stream buffer, compressed bytes
#define MOY_MEDIA_WIN_BYTES (16u * 1024u)   // the decoder's window
#define MOY_MEDIA_WIN_MIN (8u * 1024u)      // a frame is decoded with this much ahead
#define MOY_MEDIA_PCM_FRAMES 16384u         // decoded, in the output's rate and channels
#define MOY_MEDIA_STACK (24u * 1024u)       // the task's stack: a frame's decode takes 16.7 KB
#define MOY_MEDIA_CONN (20u * 1024u)        // a URL's client: its head and URL buffers
#define MOY_MEDIA_ENTRIES 32
#define MOY_MEDIA_LIST_BYTES 8192u
#define MOY_MEDIA_SRC_MAX 512
#define MOY_MEDIA_TEXT_MAX 128
#define MOY_MEDIA_RETRIES 2
#define MOY_MEDIA_DUCK_Q15 8192             // the stream under a sounding focused session
#define MOY_MEDIA_VOLUME 70                 // a new stream's, 0..100
#define MOY_MEDIA_PRIO 3
#define MOY_MEDIA_WAIT_MS 50                // one wait for the connection's bytes
#define MOY_MEDIA_AGENT "moybyte-media"

enum {
    MOY_MEDIA_IDLE = 0,
    MOY_MEDIA_OPENING,                      // the entry's connection is being made
    MOY_MEDIA_PLAYING,
    MOY_MEDIA_ENDED,                        // past the list's last entry
    MOY_MEDIA_FAILED,                       // the same, with nothing played
};

enum {
    MOY_MEDIA_URL = 1,                      // an http:// stream the task reads
    MOY_MEDIA_FEED = 2,                     // bytes the VM feeds (a file)
};

// -errno codes.
#define MOY_MEDIA_EPERM 1
#define MOY_MEDIA_EBADF 9
#define MOY_MEDIA_EAGAIN 11
#define MOY_MEDIA_ENOMEM 12
#define MOY_MEDIA_EBUSY 16
#define MOY_MEDIA_ENODEV 19
#define MOY_MEDIA_EINVAL 22
#define MOY_MEDIA_ENOSPC 28

typedef struct {
    int state;
    int paused;
    uint32_t owner;                         // 0: no stream is open
    uint32_t gen;                           // the current entry's generation
    int entry, entries;
    int kind;                               // the current entry's
    int armed;                              // a FEED entry takes bytes for `gen`
    int rate, channels, kbps;               // the stream's, from its first frame
    int volume;
    uint32_t in_bytes, in_room;             // the stream buffer
    uint32_t pcm_frames;                    // decoded, not yet pulled
    uint32_t decoded, played, starved;      // since open, frames at the output's rate
    int err;                                // the last entry's failure (positive errno), 0
    uint32_t text_gen;                      // bumps when a text changes
    int idle;                               // stopped and let go: close() frees
    uint32_t at_ms;                         // the current entry heard, ms
    uint32_t len_ms;                        // a fed file's length, ms; 0 unknown (a stream)
} moy_media_status_t;

// The bytes open() takes from the MEDIA row on this image (a stereo output's
// decoded ring is twice a mono one's).
uint32_t moy_media_share(void);
// Whether this image plays a stream: its output exists (a board's speaker, or
// the host's and the browser's per-frame pull).
int moy_media_present(void);

// The service for `owner`, decoding to `rate` Hz and `channels` (1 or 2), the
// host's: a board's are its output's. 0, -EBUSY when another owner holds it,
// -ENOSPC when the MEDIA row is absent or full, -ENOMEM. The same owner again
// is 0 and changes nothing.
int moy_media_open(uint32_t owner, int rate, int channels);
// The service freed and the row given back: 0, -EAGAIN while the task still
// holds what it opened (stop() first), -EBADF for an owner that holds none.
int moy_media_close(uint32_t owner);
uint32_t moy_media_owner(void);         // the MOY_LEASE_MEDIA lease's holder, 0: no one
// Its lease dropped from outside (moy_lease.h's DEVICES): `owner`'s stream
// stops and the service closes, or is the ORPHAN's until its task lets go.
void moy_media_lease_end(uint32_t owner);
// The VM ended (the kernel's moy_kernel_vm_down, a soft reset or a stop): its
// processes' stream stopped and, once the task has let go (a bounded wait),
// closed. One the task still holds is left as the ORPHAN's, closed by the
// next open. 0, or -EAGAIN for an orphan left.
#define MOY_MEDIA_ORPHAN 0xffffffffu
int moy_media_close_vm(void);

// The list: cleared, an entry added (its index, -ENOSPC when full), or an M3U
// or PLS text's entries added, relative ones resolved against `base` (a URL or
// a directory; NULL for none): how many.
int moy_media_clear(uint32_t owner);
int moy_media_add(uint32_t owner, const char *src, const char *title);
int moy_media_playlist(uint32_t owner, const char *text, size_t n, const char *base);
// Entry `i` made current (it plays from its start), the state OPENING.
int moy_media_go(uint32_t owner, int i);
int moy_media_stop(uint32_t owner);
int moy_media_pause(uint32_t owner, int on);
int moy_media_volume(uint32_t owner, int v);

// A file's bytes for entry generation `gen`: how many the stream buffer took
// (0 when full, or when `gen` is not current and armed), or -errno. end()
// says the file has no more.
int moy_media_feed(uint32_t owner, uint32_t gen, const void *buf, size_t n);
int moy_media_feed_end(uint32_t owner, uint32_t gen);
// The same, the source having failed with positive errno `err`: the entry's
// `err` once it moves on.
int moy_media_feed_fail(uint32_t owner, uint32_t gen, int err);
// The file's size in bytes for entry generation `gen`, which its length is
// read from: the bytes its decoded frames took against the frames they made.
int moy_media_feed_size(uint32_t owner, uint32_t gen, uint32_t size);

int moy_media_status(moy_media_status_t *st);
enum { MOY_MEDIA_T_SRC = 0, MOY_MEDIA_T_TITLE, MOY_MEDIA_T_STATION, MOY_MEDIA_T_ENTRY };
// A text into `out` (UTF-8, NUL-ended): the current entry's source, the
// stream's now-playing title (ICY StreamTitle), its station (icy-name), or the
// entry's own title (a playlist's). Its length.
int moy_media_text(int which, char *out, size_t cap);
// Entry `i`'s source and title: its source's length, or -EINVAL.
int moy_media_entry(int i, char *src, size_t src_cap, char *title, size_t title_cap);

// One pass of the task's loop (the host's task): 1 when it did work.
int moy_media_run(void);

// THE PULL (moy_aud.h's background stream): `n` frames in the open's
// channels (interleaved when two) added into `out`, at the console's level
// 0..7 and ducked while `duck`.
void moy_media_mix(int16_t *out, int n, int console, int duck);

// -- the parsers, pure ---------------------------------------------------------

// ICY metadata's StreamTitle='...'; into `out` (UTF-8: Latin-1 converted):
// its length, or -1 when the block has none.
int moy_media_icy_title(const char *meta, size_t n, char *out, size_t cap);
// Whether `text` is a playlist: 1 for M3U, 2 for PLS, 0 for neither, judged
// by its content type (may be NULL), its path, then its first bytes.
int moy_media_list_kind(const char *ctype, const char *path, const char *text, size_t n);

#endif // MOY_MEDIA_H
