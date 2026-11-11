// moy_lorawan: the OS-side LoRaWAN MAC (moy_lorawan.h).

#include "moy_lorawan.h"

#include <string.h>

#include "moy_aes.h"

static void put32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint32_t get32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static void rev(uint8_t *dst, const uint8_t *src, size_t n) {
    for (size_t i = 0; i < n; i++) {
        dst[i] = src[n - 1 - i];
    }
}

static void mic4(const uint8_t key[16], const uint8_t *msg, size_t n, uint8_t out[4]) {
    uint8_t m[16];
    moy_aes_cmac(key, msg, n, m);
    memcpy(out, m, 4);
}

void moy_lw_init(moy_lw_t *s, const uint8_t dev_eui[8], const uint8_t join_eui[8],
                 const uint8_t app_key[16]) {
    memset(s, 0, sizeof(*s));
    memcpy(s->dev_eui, dev_eui, 8);
    memcpy(s->join_eui, join_eui, 8);
    memcpy(s->app_key, app_key, 16);
    s->rx_delay_s = 1;
    s->rx2_freq = MOY_LW_RX2_FREQ;
    s->chan[0] = 868100000u;
    s->chan[1] = 868300000u;
    s->chan[2] = 868500000u;
}

int moy_lw_join_request(moy_lw_t *s, uint16_t nonce, uint8_t *out) {
    out[0] = 0x00;
    rev(out + 1, s->join_eui, 8);
    rev(out + 9, s->dev_eui, 8);
    out[17] = (uint8_t)nonce;
    out[18] = (uint8_t)(nonce >> 8);
    mic4(s->app_key, out, 19, out + 19);
    s->dev_nonce = nonce;
    return MOY_LW_JOIN_REQ;
}

static void skey(const moy_lw_t *s, uint8_t kind, const uint8_t *app_nonce_net_id,
                 uint8_t out[16]) {
    uint8_t b[16] = {0};
    b[0] = kind;
    memcpy(b + 1, app_nonce_net_id, 6);
    b[7] = (uint8_t)s->dev_nonce;
    b[8] = (uint8_t)(s->dev_nonce >> 8);
    moy_aes_t a;
    moy_aes_key(&a, s->app_key);
    moy_aes_encrypt(&a, b, out);
}

int moy_lw_join_accept(moy_lw_t *s, const uint8_t *in, size_t n) {
    if ((n != 17 && n != 33) || in[0] != 0x20) {
        return MOY_LW_BAD;
    }
    uint8_t m[33];
    m[0] = in[0];
    moy_aes_t a;
    moy_aes_key(&a, s->app_key);
    for (size_t i = 1; i < n; i += 16) {
        moy_aes_encrypt(&a, in + i, m + i);     // the server decrypted to make it
    }
    uint8_t mic[4];
    mic4(s->app_key, m, n - 4, mic);
    if (memcmp(mic, m + n - 4, 4) != 0) {
        return MOY_LW_MIC;
    }
    skey(s, 0x01, m + 1, s->nwk_skey);
    skey(s, 0x02, m + 1, s->app_skey);
    s->dev_addr = get32(m + 7);
    s->rx1_dr_offset = (uint8_t)((m[11] >> 4) & 0x07);
    s->rx2_dr = (uint8_t)(m[11] & 0x0f);
    s->rx_delay_s = (uint8_t)(m[12] & 0x0f) ? (uint8_t)(m[12] & 0x0f) : 1;
    if (n == 33 && m[28] == 0) {                // CFList type 0: five more frequencies
        for (int i = 0; i < 5; i++) {
            const uint8_t *f = m + 13 + 3 * i;
            uint32_t hz = ((uint32_t)f[0] | (uint32_t)f[1] << 8 | (uint32_t)f[2] << 16) * 100u;
            s->chan[3 + i] = hz;
        }
    }
    s->fcnt_up = 0;
    s->fcnt_down = 0;
    s->ack_due = 0;
    s->joined = 1;
    return MOY_LW_OK;
}

// The FRMPayload cipher (LoRaWAN 1.0 section 4.3.3): its A blocks under `key`.
static void crypt(const uint8_t key[16], int down, uint32_t addr, uint32_t fcnt,
                  const uint8_t *in, uint8_t *out, size_t n) {
    moy_aes_t a;
    moy_aes_key(&a, key);
    uint8_t blk[16], s[16];
    for (size_t i = 0; i < n; i += 16) {
        memset(blk, 0, 16);
        blk[0] = 0x01;
        blk[5] = (uint8_t)down;
        put32(blk + 6, addr);
        put32(blk + 10, fcnt);
        blk[15] = (uint8_t)(i / 16 + 1);
        moy_aes_encrypt(&a, blk, s);
        for (size_t j = 0; j < 16 && i + j < n; j++) {
            out[i + j] = (uint8_t)(in[i + j] ^ s[j]);
        }
    }
}

static void data_mic(const moy_lw_t *s, int down, uint32_t fcnt, const uint8_t *msg, size_t n,
                     uint8_t out[4]) {
    uint8_t b[16 + MOY_LW_MAX];
    memset(b, 0, 16);
    b[0] = 0x49;
    b[5] = (uint8_t)down;
    put32(b + 6, s->dev_addr);
    put32(b + 10, fcnt);
    b[15] = (uint8_t)n;
    memcpy(b + 16, msg, n);
    mic4(s->nwk_skey, b, 16 + n, out);
}

int moy_lw_uplink(moy_lw_t *s, uint8_t port, const uint8_t *data, size_t n, int confirmed,
                  uint8_t *out, size_t cap) {
    if (!s->joined) {
        return MOY_LW_STATE;
    }
    if (port == 0 || port > 223 || 13 + n > cap || 13 + n > MOY_LW_MAX) {
        return MOY_LW_BAD;
    }
    out[0] = confirmed ? 0x80 : 0x40;
    put32(out + 1, s->dev_addr);
    out[5] = s->ack_due ? 0x20 : 0x00;
    out[6] = (uint8_t)s->fcnt_up;
    out[7] = (uint8_t)(s->fcnt_up >> 8);
    out[8] = port;
    crypt(s->app_skey, 0, s->dev_addr, s->fcnt_up, data, out + 9, n);
    data_mic(s, 0, s->fcnt_up, out, 9 + n, out + 9 + n);
    s->fcnt_up++;
    s->ack_due = 0;
    s->acked = 0;
    return (int)(13 + n);
}

int moy_lw_downlink(moy_lw_t *s, const uint8_t *in, size_t n, uint8_t *port, uint8_t *out,
                    size_t cap) {
    if (!s->joined) {
        return MOY_LW_STATE;
    }
    if (n < 12 || (in[0] != 0x60 && in[0] != 0xa0) || get32(in + 1) != s->dev_addr) {
        return MOY_LW_BAD;
    }
    uint8_t fopts = in[5] & 0x0f;
    uint32_t f16 = (uint32_t)in[6] | (uint32_t)in[7] << 8;
    uint32_t fcnt = (s->fcnt_down & 0xffff0000u) | f16;
    if (fcnt < s->fcnt_down) {
        fcnt += 0x10000u;
    }
    uint8_t mic[4];
    data_mic(s, 1, fcnt, in, n - 4, mic);
    if (memcmp(mic, in + n - 4, 4) != 0) {
        return MOY_LW_MIC;
    }
    if (s->fcnt_down != 0 && fcnt < s->fcnt_down) {
        return MOY_LW_BAD;
    }
    s->fcnt_down = fcnt + 1u;
    s->acked = (in[5] & 0x20) != 0;
    s->ack_due = in[0] == 0xa0;
    size_t at = 8u + fopts;
    *port = 0;
    if (at + 4 > n) {
        return MOY_LW_BAD;
    }
    size_t len = n - 4 - at;
    if (len == 0) {
        return 0;
    }
    *port = in[at];
    len--;
    if (len > cap) {
        return MOY_LW_BAD;
    }
    crypt(*port == 0 ? s->nwk_skey : s->app_skey, 1, s->dev_addr, fcnt, in + at + 1, out, len);
    return (int)len;
}

void moy_lw_windows(const moy_lw_t *s, int join, uint32_t freq, uint8_t dr, moy_lw_win_t w[2]) {
    uint8_t off = join ? 0 : s->rx1_dr_offset;
    w[0].freq = freq;
    w[0].dr = dr > off ? (uint8_t)(dr - off) : 0;
    w[0].delay_ms = join ? MOY_LW_JOIN_DELAY1_MS : 1000u * s->rx_delay_s;
    w[1].freq = join ? MOY_LW_RX2_FREQ : s->rx2_freq;
    w[1].dr = join ? 0 : s->rx2_dr;
    w[1].delay_ms = join ? MOY_LW_JOIN_DELAY2_MS : 1000u * s->rx_delay_s + 1000u;
}

uint8_t moy_lw_sf(uint8_t dr) {
    return dr <= 5 ? (uint8_t)(12 - dr) : dr == 6 ? 7 : 0;
}

uint32_t moy_lw_bw(uint8_t dr) {
    return dr <= 5 ? 125000u : dr == 6 ? 250000u : 0;
}

uint32_t moy_lw_channel(const moy_lw_t *s, uint32_t r) {
    uint32_t n = 0;
    for (int i = 0; i < MOY_LW_CHANNELS; i++) {
        n += s->chan[i] != 0;
    }
    uint32_t k = r % (n ? n : 1);
    for (int i = 0; i < MOY_LW_CHANNELS; i++) {
        if (s->chan[i] != 0 && k-- == 0) {
            return s->chan[i];
        }
    }
    return s->chan[0];
}
