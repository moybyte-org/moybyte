// moy_aes: AES-128 encryption and AES-CMAC (RFC 4493), what the LoRaWAN MAC
// needs (native/moy_net/moy_lorawan.h): a device only ever encrypts, the join
// accept included (its server "decrypts" to make it). One portable body on
// every tier, checked against the RFC's vectors and an independent
// implementation in tests/test_dev_lora.py.

#ifndef MOY_AES_H
#define MOY_AES_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint8_t rk[176];                    // the eleven round keys
} moy_aes_t;

void moy_aes_key(moy_aes_t *a, const uint8_t key[16]);
void moy_aes_encrypt(const moy_aes_t *a, const uint8_t in[16], uint8_t out[16]);
// AES-CMAC of `n` bytes under `key`.
void moy_aes_cmac(const uint8_t key[16], const uint8_t *msg, size_t n, uint8_t mac[16]);

#endif // MOY_AES_H
