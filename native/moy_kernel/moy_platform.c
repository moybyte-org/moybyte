// moy_platform: a board's mutex (moy_platform.h has the rule).

#include "moy_platform.h"

#ifdef MOY_ON_BOARD
static portMUX_TYPE s_made = portMUX_INITIALIZER_UNLOCKED;

static SemaphoreHandle_t made(moy_mutex_t *m) {
    if (m->h == NULL) {
        portENTER_CRITICAL(&s_made);
        if (m->h == NULL) {
            m->h = m->recursive ? xSemaphoreCreateRecursiveMutexStatic(&m->buf)
                : xSemaphoreCreateMutexStatic(&m->buf);
        }
        portEXIT_CRITICAL(&s_made);
    }
    return m->h;
}

void moy_mutex_init(moy_mutex_t *m, bool recursive) {
    m->h = NULL;
    m->recursive = recursive;
}

void moy_mutex_lock(moy_mutex_t *m) {
    if (m->recursive) {
        xSemaphoreTakeRecursive(made(m), portMAX_DELAY);
    } else {
        xSemaphoreTake(made(m), portMAX_DELAY);
    }
}

bool moy_mutex_trylock(moy_mutex_t *m) {
    return (m->recursive ? xSemaphoreTakeRecursive(made(m), 0)
            : xSemaphoreTake(made(m), 0)) == pdTRUE;
}

void moy_mutex_unlock(moy_mutex_t *m) {
    if (m->recursive) {
        xSemaphoreGiveRecursive(m->h);
    } else {
        xSemaphoreGive(m->h);
    }
}

void moy_mutex_destroy(moy_mutex_t *m) {
    if (m->h != NULL) {
        vSemaphoreDelete(m->h);
        m->h = NULL;
    }
}
#endif
