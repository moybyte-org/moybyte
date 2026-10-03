#pragma once
#include <stdint.h>
void input_init(void);
void input_poll(void);
uint32_t input_pop_key(void);   /* 0, or (pressed << 8) | doom key */
