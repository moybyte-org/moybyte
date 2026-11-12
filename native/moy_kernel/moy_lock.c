// The kernel's writer lock (moy_lock.h has the contract).

#include "moy_lock.h"

#include "moy_platform.h"

static moy_mutex_t s_mu = MOY_MUTEX_INIT;

void moy_lock_take(void) {
    moy_mutex_lock(&s_mu);
}

void moy_lock_give(void) {
    moy_mutex_unlock(&s_mu);
}
