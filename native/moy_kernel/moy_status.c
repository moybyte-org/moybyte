// The bar's status items (moy_status.h has the contract).

#include "moy_platform.h"

#include "moy_status.h"

#include <string.h>

#include "moy_sync.h"

static MOY_SYNC_LOCK_T s_lock = MOY_SYNC_LOCK_INIT;
static moy_status_item_t (*s_item)[MOY_STATUS_CAP];  // made at the first add (moy_sync.h's THE TABLE)
static uint32_t s_gen;
static uint32_t s_age;

static moy_status_item_t *named(uint32_t pid, int h) {
    if (h < 1 || h > (int)MOY_STATUS_CAP || s_item == NULL) {
        return NULL;
    }
    moy_status_item_t *it = &(*s_item)[h - 1];
    if (it->pid == 0u || (pid != 0u && (it->pid != pid || !it->compiled))) {
        return NULL;
    }
    return it;
}

int moy_status_add(uint32_t pid, const char *icon, size_t n, int32_t order, bool compiled) {
    if (pid == 0u || icon == NULL || n == 0u || n > MOY_STATUS_ICON
        || memchr(icon, 0, n) != NULL) {
        return -MOY_STATUS_EBAD;
    }
    if (MOY_SYNC_TABLE(s_item) == NULL) {
        return -MOY_STATUS_EFULL;
    }
    int h = -MOY_STATUS_EFULL;
    MOY_SYNC_LOCK(&s_lock);
    for (uint32_t k = 0; k < MOY_STATUS_CAP; k++) {
        moy_status_item_t *it = &(*s_item)[k];
        if (it->pid == 0u) {
            memset(it, 0, sizeof *it);
            it->pid = pid;
            memcpy(it->icon, icon, n);
            it->order = order;
            it->age = ++s_age;
            it->compiled = compiled;
            __atomic_add_fetch(&s_gen, 1u, __ATOMIC_RELEASE);
            h = (int)k + 1;
            break;
        }
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return h;
}

int moy_status_show(uint32_t pid, int h, int state) {
    int rc = 0;
    MOY_SYNC_LOCK(&s_lock);
    moy_status_item_t *it = named(pid, h);
    if (it == NULL) {
        rc = -MOY_STATUS_EABSENT;
    } else if (state < MOY_STATUS_ON || state > MOY_STATUS_ERROR) {
        rc = -MOY_STATUS_EBAD;
    } else if (!it->shown || it->state != (uint8_t)state) {
        it->shown = true;
        it->state = (uint8_t)state;
        __atomic_add_fetch(&s_gen, 1u, __ATOMIC_RELEASE);
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return rc;
}

int moy_status_remove(uint32_t pid, int h) {
    int rc = 0;
    MOY_SYNC_LOCK(&s_lock);
    moy_status_item_t *it = named(pid, h);
    if (it == NULL) {
        rc = -MOY_STATUS_EABSENT;
    } else {
        it->pid = 0u;
        it->shown = false;
        __atomic_add_fetch(&s_gen, 1u, __ATOMIC_RELEASE);
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return rc;
}

uint32_t moy_status_sweep(uint32_t pid) {
    uint32_t n = 0;
    MOY_SYNC_LOCK(&s_lock);
    for (uint32_t k = 0; s_item != NULL && pid != 0u && k < MOY_STATUS_CAP; k++) {
        if ((*s_item)[k].pid == pid) {
            (*s_item)[k].pid = 0u;
            (*s_item)[k].shown = false;
            n++;
        }
    }
    if (n) {
        __atomic_add_fetch(&s_gen, 1u, __ATOMIC_RELEASE);
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return n;
}

uint32_t moy_status_gen(void) {
    return __atomic_load_n(&s_gen, __ATOMIC_ACQUIRE);
}

bool moy_status_get(int h, moy_status_item_t *out) {
    if (h < 1 || h > (int)MOY_STATUS_CAP) {
        return false;
    }
    MOY_SYNC_LOCK(&s_lock);
    if (s_item != NULL) {
        *out = (*s_item)[h - 1];
    } else {
        memset(out, 0, sizeof *out);
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return true;
}

void moy_status_reset(void) {
    MOY_SYNC_LOCK(&s_lock);
    if (s_item != NULL) {
        memset(s_item, 0, sizeof *s_item);
    }
    __atomic_add_fetch(&s_gen, 1u, __ATOMIC_RELEASE);
    MOY_SYNC_UNLOCK(&s_lock);
}
