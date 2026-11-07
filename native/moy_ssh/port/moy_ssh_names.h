// moy_ssh_names: Dropbear's and libtommath's global names that the rest of
// the image also defines, renamed. Force-included into every file of the
// server's library (micropython.cmake's -include), before any header, so a
// prototype and its definition agree. The linker takes the first definition
// it meets and says nothing: libtommath's mp_init resolved to MicroPython's
// and the first session wrote through a null VM state (2026-11-06), and
// Bluedroid has its own list_new/list_append/list_remove.

#ifndef MOY_SSH_NAMES_H
#define MOY_SSH_NAMES_H

#define list_new moy_db_list_new
#define list_append moy_db_list_append
#define list_remove moy_db_list_remove

#define mp_clamp moy_tm_mp_clamp
#define mp_clear moy_tm_mp_clear
#define mp_copy moy_tm_mp_copy
#define mp_count_bits moy_tm_mp_count_bits
#define mp_div_2d moy_tm_mp_div_2d
#define mp_from_ubin moy_tm_mp_from_ubin
#define mp_grow moy_tm_mp_grow
#define mp_init moy_tm_mp_init
#define mp_init_copy moy_tm_mp_init_copy
#define mp_init_size moy_tm_mp_init_size
#define mp_lshd moy_tm_mp_lshd
#define mp_mod_2d moy_tm_mp_mod_2d
#define mp_mul_2d moy_tm_mp_mul_2d
#define mp_rshd moy_tm_mp_rshd
#define mp_to_ubin moy_tm_mp_to_ubin
#define mp_ubin_size moy_tm_mp_ubin_size
#define mp_zero moy_tm_mp_zero
#define mp_cmp moy_tm_mp_cmp
#define mp_cmp_d moy_tm_mp_cmp_d

// Dropbear's sign-in delay sleeps through nanosleep, which ESP-IDF's newlib
// declares and no IDF component defines (a console image had one only by
// WAMR's port, which the Zero denies): the server's own, in moy_ssh.c.
#ifdef ESP_PLATFORM
#define nanosleep moy_ssh_nanosleep
#endif

#endif // MOY_SSH_NAMES_H
