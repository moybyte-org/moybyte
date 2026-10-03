/* Linked immediately before i_video.c so this array's BSS precedes the
 * palette: an overrun arriving from below hits it first. Its twin,
 * moy_canary_hi in dg_moy.c (linked last), sits between the palette and the
 * shadow stack. dg_moy.c fills and checks both. */
#include <stdint.h>
uint32_t moy_canary_lo[64];
