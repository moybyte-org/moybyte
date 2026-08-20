/* The web console bundle baked into the firmware image.
 *
 * The TABLE is generated (moy_web_blob.gen.c, by tools/gen_web_blob.py, which
 * .incbin's firmware/web_runner/dist's pre-gzipped assets); this header is the
 * contract between that generated file and modmoy_web.c, and is the only part
 * of the pair a human edits.
 *
 * `name` is the SERVED name, `.gz` suffix and all -- moy_webhost looks up
 * "<asset>.gz" first exactly as it does on storage, so one rule covers both
 * sources and a future raw-bundle build needs no code change here.
 */
#ifndef MOY_WEB_BLOB_H
#define MOY_WEB_BLOB_H

typedef struct _moy_web_asset_t {
    const char *name;
    const unsigned char *data;   /* flash rodata: memory-mapped, never copied */
    unsigned int len;
} moy_web_asset_t;

extern const moy_web_asset_t moy_web_assets[];
extern const unsigned int moy_web_asset_count;
/* "<count> <total bytes> <12 hex of the bundle digest>", or "0 0 none". */
extern const char moy_web_stamp[];

#endif /* MOY_WEB_BLOB_H */
