/* LibTomCrypt, modular cryptographic library -- Tom St Denis
 *
 * LibTomCrypt is a library that provides various cryptographic
 * algorithms in a highly modular and flexible manner.
 *
 * The library is free for all purposes without any express
 * guarantee it works.
 */
#include "tomcrypt.h"

/**
  @file crypt_hash_descriptor.c
  Stores the hash descriptor table, Tom St Denis
*/

#if DROPBEAR_MOY
/* the table is PSRAM, taken at the server's setup (port/moy_ssh_port.h) */
struct ltc_hash_descriptor hash_descriptor[TAB_SIZE];
#else
struct ltc_hash_descriptor hash_descriptor[TAB_SIZE] = {
{ NULL, 0, 0, 0, { 0 }, 0, NULL, NULL, NULL, NULL, NULL }
};
#endif

LTC_MUTEX_GLOBAL(ltc_hash_mutex)


/* ref:         $Format:%D$ */
/* git commit:  $Format:%H$ */
/* commit time: $Format:%ai$ */
