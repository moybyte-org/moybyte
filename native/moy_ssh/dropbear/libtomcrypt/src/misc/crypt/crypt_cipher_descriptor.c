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
  @file crypt_cipher_descriptor.c
  Stores the cipher descriptor table, Tom St Denis
*/

#if DROPBEAR_MOY
/* the table is PSRAM, taken at the server's setup (port/moy_ssh_port.h) */
struct ltc_cipher_descriptor cipher_descriptor[TAB_SIZE];
#else
struct ltc_cipher_descriptor cipher_descriptor[TAB_SIZE] = {
{ NULL, 0, 0, 0, 0, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL }
 };
#endif

LTC_MUTEX_GLOBAL(ltc_cipher_mutex)


/* ref:         $Format:%D$ */
/* git commit:  $Format:%H$ */
/* commit time: $Format:%ai$ */
