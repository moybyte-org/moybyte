// The host build's shim for runtime/net_binding.py: what moy_net.h keeps
// inline, exported for ctypes.

#include "moy_net.h"

int moy_net_host_use_stored(size_t password_n, size_t stored_n) {
    return moy_wifi_use_stored(password_n, stored_n);
}

int moy_net_host_remember(int ok, const char *password, size_t password_n,
                          const char *stored, size_t stored_n) {
    return moy_wifi_remember(ok, password, password_n, stored, stored_n);
}
