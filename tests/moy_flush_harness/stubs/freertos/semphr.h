#ifndef H_STUB_FREERTOS_SEMPHR_H
#define H_STUB_FREERTOS_SEMPHR_H

#include "freertos/FreeRTOS.h"

// Real FreeRTOS makes these macros over the queue API; a function is enough
// here, and it keeps the handle a distinct type the harness can peek at.
typedef struct h_sem *SemaphoreHandle_t;

SemaphoreHandle_t xSemaphoreCreateBinary(void);
// A mutex is a binary semaphore that starts given (no priority inheritance:
// the harness is cooperative).
SemaphoreHandle_t xSemaphoreCreateMutex(void);
BaseType_t xSemaphoreTake(SemaphoreHandle_t sem, TickType_t ticks);
BaseType_t xSemaphoreGive(SemaphoreHandle_t sem);
BaseType_t xSemaphoreGiveFromISR(SemaphoreHandle_t sem, BaseType_t *woken);
void vSemaphoreDelete(SemaphoreHandle_t sem);

#endif // H_STUB_FREERTOS_SEMPHR_H
