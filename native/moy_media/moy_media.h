// moy_media: the media service (docs/os_2026-11/hardware.md section 3), a
// system service in C. It plays one background stream, the system's: an HTTP
// stream (ICY metadata read, the station's now-playing text) or a file, each
// an entry of a list an M3U or PLS playlist fills. MP3 is decoded (minimp3.h,
// vendored) and resampled to the output's rate and channels (moy_aud_out_rate
// and _channels: 44.1 kHz stereo on a P4, its stream's channels kept; mono
// elsewhere, a stereo stream mixed down, at 22.05 kHz on the S3s and the host
// and at its AudioContext's rate in the browser's page), and the mix (native/moy_audio/moy_aud.h's background stream) pulls
// it, ducked under the focused session. play() is the whole of starting one:
// the Music app, the shell's `media` and the browser's page all start through
// it (runtime/system/dev_media.py, the face, checks the permissions first).
//
// A RUN, ON DEMAND. The service holds nothing until a client's first command
// starts it: open() spawns its own pid (moy_log_spawn, named "media"), takes
// the MEDIA lease under it, the MEDIA row and its task. It is then the
// system's, not any app's: no process's end, no VM stop and no soft reset
// stops it (nothing in moy_kernel_vm_down touches it), and the frame's job
// that reads its file and its media keys runs in the kernel's frame with the
// VM down as with it up. It ends by itself when its work is done -- stopped,
// its list over (ENDED), or nothing of it played (FAILED) -- on the frame's
// job, once the task has let go and the tail has played out: everything it
// took is given back, the radio's "media" row under its pid among it, and
// last() says how it ended. The next command starts a new run, a new pid.
//
// CLIENTS. Every command is a client's: any process the face lets in (the
// `media` permission, `media:url` for a URL) sends it, last command wins.
// take() names the client whose command sets what plays (its pid, its name
// and its own label for it), and answers the client it took over from while
// that one's list still played, which tell() tells the user of.
//
// A FILE the console keeps is the service's to read: a job of the console's
// frame (moy_loop.h's moy_loop_job, the VM's task on a board, the frame's
// tail on the host and in the browser) opens it through the store's volume
// seam (native/moy_store/moy_vol.h, the card's bus gate around every op),
// reads it into the stream buffer a few KB at a time, sizes it, and seeks it.
// Nothing of a playing file runs in Python. Its tag's title and artist
// (ID3v2) are read as its first frame is, and probe() reads a file's length
// and tag without playing it.
//
// THE BROWSER (MOY_MEDIA_CLIENT 0: its image has no HTTP client) keeps no
// song in its VFS (.claude/rules/web.md, Music): a URL's bytes, and a song the
// page holds (its OPFS store's, or a serving board's), are the FACE's to feed
// from the page's reader, which the service asks for by arming the entry for
// the face (status `armed`, `feed_at` the byte it starts at). page() says the
// page holds songs the VFS does not, so a list's entry that will not open is
// handed to the face rather than failed. feed_size gives a URL its length
// (the response's Content-Length), and one with none is a live stream, opened
// again as a board's is. A URL entry is OPENING until its first bytes or its
// feed's end. A station's ICY head, where the page could read it, reaches
// the service through feed_icy(): its metadata interval, which the feed's
// bytes are taken apart by as a board's connection's are, and its name.
//
// THE MEMORY is the MEDIA row of THE SHARES (native/moy_play/moy_play.h):
// open() takes moy_media_share() from it and refuses with -ENOSPC where the
// row is absent or full, before anything is allocated. The service's state,
// its buffers and its decoded ring are one PSRAM block, and the URL's client
// buffers are kernel memory (moy_ota.h). The task's stack, which carries
// minimp3's ~16.7 KB scratch through every frame's decode, and the decoder's
// state are internal SRAM where the heap keeps MOY_MEDIA_SRAM_FLOOR free
// beside them, else PSRAM: a decode that keeps them off the PSRAM cache and
// bus slows the other core's frame less (#66). What internal SRAM holds goes back to the row, so the row
// counts the run's PSRAM, and status() names the internal bytes (`sram`).
// The run's end frees all of it and gives the row back; nothing is held
// while no stream runs. An S3's VM stop counts what the row holds
// (moy_play_stop.c: a game that fits only without the music is the verdict
// MOY_PLAY_KEEP_MEDIA, and the console asks first). Nothing is static but two
// pointers (the service, the media keys' table), the last run's end and the
// life gate's mutex.
//
// WHO RUNS WHAT. The VM's thread opens, closes, commands and feeds; the
// frame's job reads a file on that same thread, so a store op never runs on
// the service's PSRAM stack. A compiled program's media handle (moy_media_dev.c,
// native/moy_kernel/moy_dev.h's media row) opens, plays, probes and commands
// on the program's own task, the VM up or stopped, holding the lease table's
// lock (moy_lease_lock) for the whole call: open() takes that lock too, and
// the run's end unlinks it under it, judging it over once more there, so no
// run is freed under a client's command and none a command moved on is
// ended. On a board a task on core 0 below the flush
// priority (MOY_MEDIA_PRIO) reads the connection and decodes; it never runs a
// flash operation, so its stack is PSRAM. The host and the browser have no
// task: the frame's job runs moy_media_run(), one pass of its loop. The mix's
// pull runs on the audio feeder's task under the mix's lock. The decoded ring
// has one writer (the task) and one reader (the pull); the stream buffer one
// writer (the task for a URL, the frame's job for a file, the face's feed for
// the page's bytes) and one reader (the task). Commands and texts change
// under the service's mutex. A TEARDOWN (close) and the frame's job take
// turns at the life gate, so a teardown on a task other than the job's frees
// nothing the job reads; the lease's end only stops the run, for the job to
// end; the job, finding no service or ending it, answers false and the loop
// drops it on its own task.
//
// A STREAM'S LIFE. go(i) makes entry i current: the task drops what it held,
// and opens it. A URL whose response is a playlist (by its type or its path)
// replaces the list with its entries and plays the first. An entry that ends
// (its connection closed, or its file read and drained) or fails moves to
// the next; past the last the state is ENDED (FAILED when nothing played).
// A URL with no Content-Length (a live stream) that ends after playing is
// opened again MOY_MEDIA_RETRIES times before the list moves on. seek(ms)
// starts the current file again at that place. stop() asks the task to let
// go, and the frame's job ends the run once it has; close() ends it at once
// where the task already has (-EAGAIN until then).
//
// MEDIA KEYS. A keyboard's media keys are MEDIA records on the console's
// input table (native/moy_input/moy_event.h: `a` the consumer-page usage, `b`
// 1 down and 0 up; a BLE keyboard's driver posts them, moy_hid.h). They are
// the console's, as Super is: no cart reads them. While a stream is open the
// frame's job reads them with a reader of its own and answers each usage
// going down from whatever is in front, a game the kernel runs with the VM
// stopped included:
// Play/Pause, Play and Pause the stream's pause, Scan Next and Scan Previous
// the list's next and previous entry while one plays, Volume Increment and
// Decrement the stream's level by MOY_MEDIA_KEY_STEP. A key with no run is
// answered by nothing, and never starts one.
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
#define MOY_MEDIA_STACK (20u * 1024u)       // the task's stack: a frame's decode takes 16.7 KB of it
#define MOY_MEDIA_SRAM_FLOOR (16u * 1024u)  // internal SRAM open() leaves free beside the stack and state
#define MOY_MEDIA_METER_FRAMES 256u         // the decode meter's window, frames
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
#define MOY_MEDIA_READ_MIN (4u * 1024u)    // a file is read once this much is free
#define MOY_MEDIA_LIST_FILE (16u * 1024u)  // the longest playlist file play() reads
#define MOY_MEDIA_TAG_READ 4096u            // what probe() reads of a tag, and of a first frame
#define MOY_MEDIA_KEY_STEP 10               // a volume key's step, of 0..100

// The consumer page's usages (HID Usage Tables, page 0x0C) the service answers.
enum {
    MOY_MEDIA_KEY_PLAY = 0xB0,
    MOY_MEDIA_KEY_PAUSE = 0xB1,
    MOY_MEDIA_KEY_NEXT = 0xB5,
    MOY_MEDIA_KEY_PREV = 0xB6,
    MOY_MEDIA_KEY_PLAY_PAUSE = 0xCD,
    MOY_MEDIA_KEY_VOL_UP = 0xE9,
    MOY_MEDIA_KEY_VOL_DOWN = 0xEA,
};

enum {
    MOY_MEDIA_IDLE = 0,
    MOY_MEDIA_OPENING,                      // the entry's connection is being made
    MOY_MEDIA_PLAYING,
    MOY_MEDIA_ENDED,                        // past the list's last entry
    MOY_MEDIA_FAILED,                       // the same, with nothing played
};

enum {
    MOY_MEDIA_URL = 1,                      // an http:// stream the task reads
    MOY_MEDIA_FEED = 2,                     // the page's bytes, the face feeds them
    MOY_MEDIA_FILE = 3,                     // a file the frame's job reads
};

// -errno codes.
#define MOY_MEDIA_EPERM 1
#define MOY_MEDIA_ENOENT 2
#define MOY_MEDIA_EIO 5
#define MOY_MEDIA_EBADF 9
#define MOY_MEDIA_EAGAIN 11
#define MOY_MEDIA_ENOMEM 12
#define MOY_MEDIA_EBUSY 16
#define MOY_MEDIA_ENODEV 19
#define MOY_MEDIA_EINVAL 22
#define MOY_MEDIA_ENOSPC 28
#define MOY_MEDIA_ESPIPE 29

typedef struct {
    int state;
    int paused;
    uint32_t pid;                           // the run's; 0: none
    uint32_t client;                        // take()'s last client, 0 none
    uint32_t gen;                           // the current entry's generation
    int entry, entries;
    int kind;                               // the current entry's
    int armed;                              // the face feeds entry `gen` its bytes
    int rate, channels, kbps;               // the stream's, from its first frame
    int volume;
    uint32_t in_bytes, in_room;             // the stream buffer
    uint32_t pcm_frames;                    // decoded, not yet pulled
    uint32_t decoded, played, starved;      // since open, frames at the output's rate
    int err;                                // the last entry's failure (positive errno), 0
    uint32_t text_gen;                      // bumps when a text changes
    int idle;                               // stopped and let go: close() frees
    uint32_t at_ms;                         // the current entry heard, ms
    uint32_t len_ms;                        // a file's length, ms; 0 unknown (a stream)
    uint32_t feed_at;                       // the byte the face's feed of `gen` starts at
    uint32_t sram;                          // the run's bytes in internal SRAM, 0 all PSRAM
    uint32_t dec_us;                        // a frame's mean decode over the last window, µs; 0 none
} moy_media_status_t;

// What probe() reads of a file: its length in seconds (0 unknown) and its
// tag's title and artist (UTF-8, "" for none).
typedef struct {
    uint32_t seconds;
    char title[MOY_MEDIA_TEXT_MAX];
    char artist[MOY_MEDIA_TEXT_MAX];
} moy_media_probe_t;

// The bytes the run holds of the MEDIA row, or with none running what open()
// takes from it on this image (a stereo output's decoded ring is twice a mono
// one's).
uint32_t moy_media_share(void);
// Whether this image plays a stream: its output exists (a board's speaker, or
// the host's and the browser's per-frame pull).
int moy_media_present(void);

// A run started, decoding to `rate` Hz and `channels` (1 or 2), the host's: a
// board's are its output's, and a rate of 0 is the last run's elsewhere
// (22050 Hz mono before any). 0 (a run already going is 0 and changes
// nothing), -EBUSY when the MEDIA lease is another's, -ENOSPC when the MEDIA
// row is absent or full, -ENODEV, -ENOMEM.
int moy_media_open(int rate, int channels);
// The run stopped and ended now: 0, -EAGAIN while the task still holds what
// it opened (the frame's job ends it once it lets go), -EBADF with no run.
int moy_media_close(void);
// The run's pid (the MEDIA lease's holder), 0 while none runs.
uint32_t moy_media_pid(void);
// Its lease dropped from outside (moy_lease.h's DEVICES): the run stops, and
// the frame's job ends it once its task lets go.
void moy_media_lease_end(uint32_t owner);

// How the last run ended: `runs` counts the ends since boot, `state` ENDED or
// FAILED for a list that was over (its `err` the last entry's), IDLE for a
// stop.
typedef struct {
    uint32_t runs;
    uint32_t pid, client;
    int state;
    int err;
} moy_media_end_t;
void moy_media_last(moy_media_end_t *out);

// `client` (a pid) now sets what plays: its name `who` and its `label` for
// it (UTF-8, each may be NULL) kept for any client to read (T_WHO, T_LABEL).
// The client it took over from while that one's list still played, else 0
// (none, the same client, or no run).
uint32_t moy_media_take(uint32_t client, const char *who, const char *label);

// Process `was`, which take() answered, told why its music changed: a
// notification as the system (moy_notes_system) naming take()'s client and
// what it now plays (its label's last tab-separated field, else the current
// source's file name). 1 when posted, 0 for none to tell.
int moy_media_tell(uint32_t was);

// `src` played from its start, replacing the list: a URL, a playlist file (by
// its path: its entries, relative ones beside it) or a song. `held`: the
// page holds the song though the VFS does not (the browser), so a missing
// file is the face's to feed. The entries, or -ENOENT for a file that is not
// there, -EINVAL for a playlist with none, -EIO.
int moy_media_play(const char *src, int held);
// Whether the file at `path` is there to play: 0, or a positive errno
// (EINVAL for a folder), read through the store's volume seam.
int moy_media_stat(const char *path);
// Whether an https:// URL plays here: its server's certificate verifies
// (native/moy_net's client, moy_fetch_tls), or the page's fetch does (the
// browser).
int moy_media_tls(void);
// The page holds songs the VFS does not: an entry that will not open is the
// face's to feed rather than failed (the browser's face sets it at open).
int moy_media_page(int on);
// The current file from `ms` in: 0, -ESPIPE for a stream, -EAGAIN while its
// length is not known (before its first frame), -EINVAL past the list.
int moy_media_seek(uint32_t ms);

// The list: cleared, an entry added (its index, -ENOSPC when full), or an M3U
// or PLS text's entries added, relative ones resolved against `base` (a URL or
// a directory; NULL for none): how many.
int moy_media_clear(void);
int moy_media_add(const char *src, const char *title);
int moy_media_playlist(const char *text, size_t n, const char *base);
// Entry `i` made current (it plays from its start), the state OPENING.
int moy_media_go(int i);
int moy_media_stop(void);
int moy_media_pause(int on);
int moy_media_volume(int v);

// The page's bytes for entry generation `gen` (the face's feed, status
// `armed`): how many the stream buffer took (0 when full, or when `gen` is
// not current and armed for the face), or -errno. end() says it has no more.
int moy_media_feed(uint32_t gen, const void *buf, size_t n);
int moy_media_feed_end(uint32_t gen);
// The same, the source having failed with positive errno `err`: the entry's
// `err` once it moves on.
int moy_media_feed_fail(uint32_t gen, int err);
// The fed entry's size in bytes for generation `gen`, which its length is
// read from: the bytes its decoded frames took against the frames they made.
int moy_media_feed_size(uint32_t gen, uint32_t size);
// The fed URL's ICY head for generation `gen`, before its first bytes: the
// response's `icy-metaint` (0: its bytes carry no metadata), which the feed
// then takes each metadata block out by, its StreamTitle the T_TITLE text,
// and its `icy-name` (NULL or "" for none), the T_STATION text.
int moy_media_feed_icy(uint32_t gen, uint32_t metaint, const char *station);

int moy_media_status(moy_media_status_t *st);
// One small int over what a player shows -- the state, pause, entry, volume,
// error, the texts' generation and the place and length in whole seconds --
// that moves when any of them does: a poll that finds it still reads nothing
// else. -1 with no run.
int32_t moy_media_stamp(void);
enum {
    MOY_MEDIA_T_SRC = 0, MOY_MEDIA_T_TITLE, MOY_MEDIA_T_STATION, MOY_MEDIA_T_ENTRY,
    MOY_MEDIA_T_SONG, MOY_MEDIA_T_ARTIST, MOY_MEDIA_T_WHO, MOY_MEDIA_T_LABEL,
};
// A text into `out` (UTF-8, NUL-ended): the current entry's source (the
// entry a go() made current, before the task has begun it too), the
// stream's now-playing title (ICY StreamTitle), its station (icy-name), the
// entry's own title (a playlist's), or its file's tag's title and artist
// (ID3v2 TIT2 and TPE1), or take()'s client's name and label. Its length.
int moy_media_text(int which, char *out, size_t cap);
// Entry `i`'s source and title: its source's length, or -EINVAL.
int moy_media_entry(int i, char *src, size_t src_cap, char *title, size_t title_cap);

// A media key's usage going down, answered against the open stream: 1 when
// it acted, 0 for a usage it does not answer or with no stream to act on.
int moy_media_key(uint16_t usage);
// The table whose MEDIA records the service reads (an open stream's reader
// moves to it): NULL, the default, is the kernel's (moy_input_kernel), which
// a board's console and drivers share; the host's console has a table of its
// own and binds it, keeping it alive while it is bound.
struct moy_input;
void moy_media_keys_on(struct moy_input *t);

// One pass of the task's loop (the frame's job runs it where there is no
// task): 1 when it did work.
int moy_media_run(void);
// The frame's job, run once: a file's bytes read, and on the host and in the
// browser the loop run. While the service is open the console's frame runs
// it (moy_loop_job); a harness that steps no frames calls it.
void moy_media_pump(void);

// The file at `path`'s length and tag, read on the VM's thread with no
// stream open: 0, or -errno.
int moy_media_probe(const char *path, moy_media_probe_t *out);

// THE PULL (moy_aud.h's background stream): `n` frames in the open's
// channels (interleaved when two) added into `out`, at the console's level
// 0..7 and ducked while `duck`.
void moy_media_mix(int16_t *out, int n, int console, int duck);

// -- the parsers, pure ---------------------------------------------------------

// ICY metadata's StreamTitle='...'; into `out` (UTF-8: Latin-1 converted):
// its length, or -1 when the block has none.
int moy_media_icy_title(const char *meta, size_t n, char *out, size_t cap);
// An ID3v2 tag's title and artist (TIT2 and TPE1, or v2.2's TT2 and TP1) from
// its first `n` bytes, header included, into `title` and `artist` (UTF-8):
// the tag's whole length (header and footer), or 0 when `p` is no tag.
uint32_t moy_media_id3(const uint8_t *p, size_t n, char *title, char *artist, size_t cap);
// Whether `text` is a playlist: 1 for M3U, 2 for PLS, 0 for neither, judged
// by its content type (may be NULL), its path, then its first bytes.
int moy_media_list_kind(const char *ctype, const char *path, const char *text, size_t n);

#endif // MOY_MEDIA_H
