// The WiFi driver's credential rules (moy_net.h).

#include <string.h>

#include "moy_net.h"

int moy_wifi_use_stored(size_t password_n, size_t stored_n) {
    return password_n == 0 && stored_n != 0;
}

int moy_wifi_remember(int ok, const char *password, size_t password_n,
                      const char *stored, size_t stored_n) {
    if (ok) {
        return 1;
    }
    if (password_n == 0) {
        return 0;
    }
    return stored == NULL || stored_n != password_n
           || memcmp(password, stored, password_n) != 0;
}
