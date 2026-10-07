// The sync RPC's wire envelope (moy_net.h): one body for the webhost that
// applies a batch and the browser that sends one.

#include <string.h>

#include "moy_json.h"
#include "moy_net.h"

int moy_sync_decode(const char *body, size_t n, moy_sync_env_t *e) {
    memset(e, 0, sizeof(*e));
    if (!moy_utf8_valid(body, n) || moy_json_valid(body, n) != MOY_JSON_OK) {
        return MOY_SYNC_BAD;
    }
    const char *end = body + n;
    const char *obj = moy_json_ws(body, end);
    const char *obj_end = moy_json_value(obj, end, 0);
    if (obj_end == NULL || moy_json_kind(obj, obj_end) != MOY_JSON_OBJ) {
        return MOY_SYNC_BAD;
    }
    moy_json_get(obj, obj_end, "v", &e->v, &e->v_end);
    moy_json_get(obj, obj_end, "root", &e->root, &e->root_end);
    moy_json_get(obj, obj_end, "ops", &e->ops, &e->ops_end);
    moy_json_get(obj, obj_end, "pin", &e->pin, &e->pin_end);
    return MOY_SYNC_OK;
}

typedef struct {
    char *out;
    size_t cap, n;
} sink_t;

static void put(sink_t *s, const char *p, size_t k) {
    if (s->n < s->cap) {
        size_t room = s->cap - s->n;
        memcpy(s->out + s->n, p, k < room ? k : room);
    }
    s->n += k;
}

static void field(sink_t *s, const char *name, const char *v, size_t v_n) {
    put(s, s->n > 1 ? ", \"" : "\"", s->n > 1 ? 3 : 1);
    put(s, name, strlen(name));
    put(s, "\": ", 3);
    put(s, v, v_n);
}

size_t moy_sync_encode(char *out, size_t cap, const char *v, size_t v_n,
                       const char *root, size_t root_n, const char *ops,
                       size_t ops_n, const char *pin, size_t pin_n) {
    sink_t s = {out, cap, 0};
    put(&s, "{", 1);
    field(&s, "v", v, v_n);
    if (root != NULL) {
        field(&s, "root", root, root_n);
    }
    field(&s, "ops", ops, ops_n);
    if (pin != NULL) {
        field(&s, "pin", pin, pin_n);
    }
    put(&s, "}", 1);
    return s.n;
}

// The integer the span holds when it is a JSON integer from 0 to 9, else -1.
static int small_int(const char *v, const char *v_end) {
    if (v == NULL || moy_json_kind(v, v_end) != MOY_JSON_INT) {
        return -1;
    }
    if (v_end - v == 1 && v[0] >= '0' && v[0] <= '9') {
        return v[0] - '0';
    }
    return -1;
}

int moy_sync_batch(const char *body, size_t n, moy_sync_batch_t *b) {
    memset(b, 0, sizeof(*b));
    moy_sync_env_t e;
    if (moy_sync_decode(body, n, &e) != MOY_SYNC_OK) {
        return MOY_SYNC_BAD;
    }
    int v = small_int(e.v, e.v_end);
    int named = e.root != NULL && moy_json_kind(e.root, e.root_end) != MOY_JSON_NULL;
    int str = named && moy_json_kind(e.root, e.root_end) == MOY_JSON_STR;
    if (v == 1) {
        if (named && !(str && moy_json_str_is(e.root, e.root_end, "carts", 5))) {
            return MOY_SYNC_BAD;
        }
        b->root = MOY_SYNC_CARTS;
    } else if (v == 2) {
        if (!str || !moy_json_str_is(e.root, e.root_end, "files", 5)) {
            return MOY_SYNC_BAD;
        }
        b->root = MOY_SYNC_FILES;
    } else {
        return MOY_SYNC_BAD;
    }
    if (e.ops == NULL || moy_json_kind(e.ops, e.ops_end) != MOY_JSON_ARR) {
        return MOY_SYNC_BAD;
    }
    b->ops = e.ops;
    b->ops_end = e.ops_end;
    b->pin = e.pin;
    b->pin_end = e.pin_end;
    return MOY_SYNC_OK;
}

int moy_sync_pin_ok(const moy_sync_batch_t *b, const char *pin) {
    if (pin == NULL || pin[0] == '\0') {
        return 1;
    }
    return b->pin != NULL && moy_json_kind(b->pin, b->pin_end) == MOY_JSON_STR
           && moy_json_str_is(b->pin, b->pin_end, pin, strlen(pin));
}
