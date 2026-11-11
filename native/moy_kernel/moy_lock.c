// The kernel's writer lock (moy_lock.h has the contract).

#include "moy_lock.h"

#if defined(__has_include) && __has_include("esp_heap_caps.h")
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static StaticSemaphore_t s_buf;
static SemaphoreHandle_t volatile s_mu;
static portMUX_TYPE s_once = portMUX_INITIALIZER_UNLOCKED;

void moy_lock_take(void) {
    if (s_mu == NULL) {
        portENTER_CRITICAL(&s_once);
        if (s_mu == NULL) {
            s_mu = xSemaphoreCreateMutexStatic(&s_buf);
        }
        portEXIT_CRITICAL(&s_once);
    }
    xSemaphoreTake(s_mu, portMAX_DELAY);
}

void moy_lock_give(void) {
    xSemaphoreGive(s_mu);
}
#elif !defined(__EMSCRIPTEN__) && defined(__has_include) && __has_include(<pthread.h>)
#include <pthread.h>

static pthread_mutex_t s_mu = PTHREAD_MUTEX_INITIALIZER;

void moy_lock_take(void) {
    pthread_mutex_lock(&s_mu);
}

void moy_lock_give(void) {
    pthread_mutex_unlock(&s_mu);
}
#else
void moy_lock_take(void) {
}

void moy_lock_give(void) {
}
#endif
