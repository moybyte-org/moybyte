// Lua-on-S3 spike (#6 / #63): run sakura's EXACT _update loop (120 petals of
// float physics) under vanilla Lua 5.4 on the same silicon that measured
// MicroPython at ~13.5 ms/frame (XIAO ESP32-S3, minimum cache config, 240MHz).
// Also runs the same loop in plain C for the silicon baseline. Results reprint
// every 5s so a late-attached serial reader always catches them.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"

static const char *LUA_SAKURA =
    "local SIN = {}\n"
    "for i = 0, 255 do SIN[i] = math.sin(i / 256.0 * 6.2831853) end\n"
    "local W, H = 320, 240\n"
    "local petals = {}\n"
    "for i = 0, 119 do\n"
    "  local shade = i % 3\n"
    "  petals[#petals + 1] = { (i * 37) % 320 * 1.0, (i * 53) % 240 * 1.0,\n"
    "                          30.0 * (1.0 - 0.18 * shade), 0.3 + i * 0.01,\n"
    "                          4.0 + (i % 9), shade }\n"
    "end\n"
    "local function _sin(turn)\n"
    "  return SIN[math.floor(turn * 256.0) % 256]\n"
    "end\n"
    "local t = 0.0\n"
    "function _update(dt)\n"
    "  t = t + dt\n"
    "  local breeze = 18.0\n"
    "  local cx, cy = -999.0, -999.0\n"
    "  local R = 52.0\n"
    "  for i = 1, #petals do\n"
    "    local p = petals[i]\n"
    "    p[4] = p[4] + dt * (0.32 + 0.06 * p[6])\n"
    "    local sway = _sin(p[4]) * p[5]\n"
    "    p[1] = p[1] + (breeze * (1.0 - 0.15 * p[6]) + sway) * dt\n"
    "    p[2] = p[2] + p[3] * dt\n"
    "    local dx = p[1] - cx\n"
    "    local dy = p[2] - cy\n"
    "    if -R < dx and dx < R and -R < dy and dy < R then\n"
    "      local far = dx >= 0 and dx or -dx\n"
    "      local ady = dy >= 0 and dy or -dy\n"
    "      if ady > far then far = ady end\n"
    "      local k = (R - far) / R * 130.0\n"
    "      local inv = 1.0 / (far + 4.0)\n"
    "      p[1] = p[1] + dx * inv * k * dt\n"
    "      p[2] = p[2] + dy * inv * k * dt\n"
    "    end\n"
    "    if p[2] > H + 4.0 then\n"
    "      p[2] = 0.0\n"
    "    elseif p[1] < -8.0 then\n"
    "      p[1] = p[1] + W + 16.0\n"
    "    elseif p[1] > W + 8.0 then\n"
    "      p[1] = p[1] - W - 16.0\n"
    "    end\n"
    "  end\n"
    "end\n";

// The same loop in plain C: the silicon baseline.
static float c_petals[120][6];
static float c_t = 0.0f;
static float C_SIN[256];

static void c_init(void) {
    for (int i = 0; i < 256; i++) {
        C_SIN[i] = sinf(i / 256.0f * 6.2831853f);
    }
    for (int i = 0; i < 120; i++) {
        int shade = i % 3;
        c_petals[i][0] = (i * 37) % 320;
        c_petals[i][1] = (i * 53) % 240;
        c_petals[i][2] = 30.0f * (1.0f - 0.18f * shade);
        c_petals[i][3] = 0.3f + i * 0.01f;
        c_petals[i][4] = 4.0f + (i % 9);
        c_petals[i][5] = shade;
    }
}

static void c_update(float dt) {
    c_t += dt;
    float breeze = 18.0f;
    float cx = -999.0f, cy = -999.0f;
    float R = 52.0f;
    for (int i = 0; i < 120; i++) {
        float *p = c_petals[i];
        p[3] += dt * (0.32f + 0.06f * p[5]);
        float sway = C_SIN[(int)(p[3] * 256.0f) & 255] * p[4];
        p[0] += (breeze * (1.0f - 0.15f * p[5]) + sway) * dt;
        p[1] += p[2] * dt;
        float dx = p[0] - cx, dy = p[1] - cy;
        if (-R < dx && dx < R && -R < dy && dy < R) {
            float far = dx >= 0 ? dx : -dx;
            float ady = dy >= 0 ? dy : -dy;
            if (ady > far) far = ady;
            float k = (R - far) / R * 130.0f;
            float inv = 1.0f / (far + 4.0f);
            p[0] += dx * inv * k * dt;
            p[1] += dy * inv * k * dt;
        }
        if (p[1] > 244.0f) p[1] = 0.0f;
        else if (p[0] < -8.0f) p[0] += 336.0f;
        else if (p[0] > 328.0f) p[0] -= 336.0f;
    }
}

// ---------------------------------------------------------------------------
// Bridge protocol proof (#6): can a NON-Python VM feed the console's existing
// VM-neutral draw batch protocol? Reference: firmware/lilygo_t_deck_plus_
// micropython/modules/moy_runtime.py (_batch_arr / spr_tile / begin_batch /
// flush_batch) and native/moy_gfx/modmoy_gfx.c (spr_gate_call / blit_batch).
//
// Array shape, mirrored exactly: int16 flat array, arr[0] = next free index
// (starts at 4), header arr[1]=colorkey arr[2]=scale arr[3]=token, then
// (tile,x,y,flip) int16 quads from index 4 on. Capacity 4 + 4*512, same as
// _batch_arr's `array("h", bytearray(2 * (4 + 4 * 512)))`.
//
// Deliberate deviation from production: the real array stores WORLD
// coordinates -- camera is applied later, inside native blit_batch, from
// cam_x/cam_y parameters supplied at flush time (so multiple writers -- the
// Python spr_tile path and the native spr_gate -- can share one array without
// agreeing on a camera). Here, for a single-writer spike, camera is folded
// into x/y at spr() time instead, then clamped. sakura never calls camera()
// (offsets stay 0,0) so this has zero effect on the numbers below, but it IS
// a protocol deviation worth flagging if this bridge is ever extended to a
// multi-writer scenario (e.g. console chrome drawn over a running Lua cart).
#define BRIDGE_BATCH_CAP 512
#define BRIDGE_BATCH_LEN (4 + 4 * BRIDGE_BATCH_CAP)
static int16_t g_batch[BRIDGE_BATCH_LEN];
static int32_t g_cam_x = 0;
static int32_t g_cam_y = 0;
static uint32_t g_checksum = 0;         // printed so the walk can't be dead-code-eliminated
static uint32_t g_last_sprite_count = 0;

static void bridge_batch_reset(void) {
    g_batch[0] = 4;      // next free index
    g_batch[1] = -1;     // colorkey
    g_batch[2] = 1;      // scale
    g_batch[3] = 1;      // token (arbitrary non-zero: this Lua bridge is the only writer)
}

// end_frame(): the native "blit_batch" stand-in. Walks the queued int16 quads
// from index 4 to the cursor, folds (tile ^ x ^ y) into a running checksum for
// every accepted tile (tile == -1 means "rejected", skipped -- same contract
// as the real consumer), counts them, then resets the cursor to 4. No real
// display here; the checksum is the only way to eyeball protocol integrity.
static uint32_t end_frame(void) {
    int16_t k = g_batch[0];
    uint32_t count = 0;
    for (int16_t i = 4; i + 3 < k; i += 4) {
        int16_t tile = g_batch[i];
        if (tile < 0) continue;
        int16_t x = g_batch[i + 1];
        int16_t y = g_batch[i + 2];
        g_checksum += (uint32_t)((uint16_t)tile ^ (uint16_t)x ^ (uint16_t)y);
        count++;
    }
    g_batch[0] = 4;
    g_last_sprite_count = count;
    return count;
}

// spr(tile, x, y [, flip]) -- registered as a Lua C function, called directly
// from the cart's _draw() loop with NO Lua closure allocation per call (a
// lua_CFunction is a plain C pointer registered once; this is the Lua
// equivalent of the MicroPython native spr_gate that replaced the old
// Python-closure spr() because that closure's per-call heap frame cost
// ~1.5ms on a fragmented heap, see spr_gate's own comment in moy_runtime.py).
static int l_spr(lua_State *L) {
    lua_Integer tile = luaL_checkinteger(L, 1);
    lua_Integer x = luaL_checkinteger(L, 2);
    lua_Integer y = luaL_checkinteger(L, 3);
    lua_Integer flip = luaL_optinteger(L, 4, 0);

    x -= g_cam_x;
    y -= g_cam_y;

    if (tile < 0 || tile > 32767) tile = -1;   // out-of-range tile id -> invalid, skipped
    if (x < -32768) x = -32768; else if (x > 32767) x = 32767;
    if (y < -32768) y = -32768; else if (y > 32767) y = 32767;

    int16_t k = g_batch[0];
    if (k + 4 > BRIDGE_BATCH_LEN) {
        // full queue -> forced run break, exactly like spr_gate_call's
        // begin_batch flush when the array can't take another quad.
        end_frame();
        g_batch[1] = -1;
        g_batch[2] = 1;
        g_batch[3] = 1;
        k = g_batch[0];
    }
    g_batch[k]     = (int16_t)tile;
    g_batch[k + 1] = (int16_t)x;
    g_batch[k + 2] = (int16_t)y;
    g_batch[k + 3] = (int16_t)(flip & 3);
    g_batch[0] = (int16_t)(k + 4);
    return 0;
}

static int l_cls(lua_State *L) {
    (void)L;
    // Every non-spr primitive breaks a pending batch run in the real console
    // (it calls flush_batch before doing its own immediate draw). Mirrored
    // here defensively; normally a no-op since end_frame() already drained.
    if (g_batch[0] > 4) {
        end_frame();
        g_batch[1] = -1;
        g_batch[2] = 1;
        g_batch[3] = 1;
    }
    return 0;
}

static int l_btn(lua_State *L) {
    (void)L;
    lua_pushboolean(L, 0);
    return 1;
}

static int l_camera(lua_State *L) {
    g_cam_x = (int32_t)luaL_optinteger(L, 1, 0);
    g_cam_y = (int32_t)luaL_optinteger(L, 2, 0);
    return 0;
}

static void bridge_register(lua_State *L) {
    lua_register(L, "spr", l_spr);
    lua_register(L, "cls", l_cls);
    lua_register(L, "btn", l_btn);
    lua_register(L, "camera", l_camera);
}

// Same sakura cart as LUA_SAKURA above, PLUS a _draw() shaped like a real
// cart's render pass: cls() then a loop of 120 spr() calls, one per petal,
// tile id taken from the petal's shade (p[6], 0..2). Deliberately duplicated
// (not string-concatenated onto LUA_SAKURA at runtime) so the original,
// already-measured spike above stays byte-for-byte untouched.
static const char *LUA_SAKURA_CART =
    "local SIN = {}\n"
    "for i = 0, 255 do SIN[i] = math.sin(i / 256.0 * 6.2831853) end\n"
    "local W, H = 320, 240\n"
    "local petals = {}\n"
    "for i = 0, 119 do\n"
    "  local shade = i % 3\n"
    "  petals[#petals + 1] = { (i * 37) % 320 * 1.0, (i * 53) % 240 * 1.0,\n"
    "                          30.0 * (1.0 - 0.18 * shade), 0.3 + i * 0.01,\n"
    "                          4.0 + (i % 9), shade }\n"
    "end\n"
    "local function _sin(turn)\n"
    "  return SIN[math.floor(turn * 256.0) % 256]\n"
    "end\n"
    "local t = 0.0\n"
    "function _update(dt)\n"
    "  t = t + dt\n"
    "  local breeze = 18.0\n"
    "  local cx, cy = -999.0, -999.0\n"
    "  local R = 52.0\n"
    "  for i = 1, #petals do\n"
    "    local p = petals[i]\n"
    "    p[4] = p[4] + dt * (0.32 + 0.06 * p[6])\n"
    "    local sway = _sin(p[4]) * p[5]\n"
    "    p[1] = p[1] + (breeze * (1.0 - 0.15 * p[6]) + sway) * dt\n"
    "    p[2] = p[2] + p[3] * dt\n"
    "    local dx = p[1] - cx\n"
    "    local dy = p[2] - cy\n"
    "    if -R < dx and dx < R and -R < dy and dy < R then\n"
    "      local far = dx >= 0 and dx or -dx\n"
    "      local ady = dy >= 0 and dy or -dy\n"
    "      if ady > far then far = ady end\n"
    "      local k = (R - far) / R * 130.0\n"
    "      local inv = 1.0 / (far + 4.0)\n"
    "      p[1] = p[1] + dx * inv * k * dt\n"
    "      p[2] = p[2] + dy * inv * k * dt\n"
    "    end\n"
    "    if p[2] > H + 4.0 then\n"
    "      p[2] = 0.0\n"
    "    elseif p[1] < -8.0 then\n"
    "      p[1] = p[1] + W + 16.0\n"
    "    elseif p[1] > W + 8.0 then\n"
    "      p[1] = p[1] - W - 16.0\n"
    "    end\n"
    "  end\n"
    "end\n"
    "function _draw()\n"
    "  cls(1)\n"
    "  for i = 1, #petals do\n"
    "    local p = petals[i]\n"
    "    spr(p[6], math.floor(p[1]), math.floor(p[2]))\n"
    "  end\n"
    "end\n";

// ---------------------------------------------------------------------------
// GC / memory follow-up (#6): how much RAM does the full bridged cart need,
// and what does Lua's GC cost per frame? This decides whether a Lua state can
// live alongside the MicroPython console on the T-Deck.
//
// A tracking allocator wraps realloc/free and keeps live-bytes + a high-water
// mark. That watermark is the TRUE budget number: lua_gc(LUA_GCCOUNT) only
// reports what the GC accounts for, while the allocator sees every byte Lua
// ever asked the system for (including transient parser/load-time spikes).
typedef struct {
    size_t live;
    size_t peak;
} alloc_stats_t;

static void *l_alloc_track(void *ud, void *ptr, size_t osize, size_t nsize) {
    alloc_stats_t *s = (alloc_stats_t *)ud;
    if (ptr == NULL) {
        // Per the lua_Alloc contract, when ptr is NULL `osize` holds the TYPE
        // TAG of the object being created (not a size) -- treat as 0 or the
        // live-byte accounting corrupts.
        osize = 0;
    }
    if (nsize == 0) {
        free(ptr);
        s->live -= osize;
        return NULL;
    }
    void *np = realloc(ptr, nsize);
    if (np == NULL) {
        return NULL;                    // Lua handles OOM (emergency GC + retry)
    }
    s->live = s->live - osize + nsize;
    if (s->live > s->peak) {
        s->peak = s->live;
    }
    return np;
}

static double lua_heap_kb(lua_State *L) {
    return (double)lua_gc(L, LUA_GCCOUNT)
         + (double)lua_gc(L, LUA_GCCOUNTB) / 1024.0;
}

// One full bridged cart frame: _update(dt) -> _draw() -> end_frame().
static void run_cart_frame(lua_State *L) {
    lua_getglobal(L, "_update");
    lua_pushnumber(L, 1.0 / 30.0);
    lua_call(L, 1, 0);
    lua_getglobal(L, "_draw");
    lua_call(L, 0, 0);
    end_frame();
}

typedef struct {
    double heap_warm_kb;    // GC-visible heap after load + 1 warm-up frame
    double heap_steady_kb;  // GC-visible heap after 2000 full frames
    double peak_kb;         // allocator high-water mark (default-GC run)
    double frame_gc_on_ms;  // full frame, default incremental GC
    double frame_gc_off_ms; // full frame, GC stopped (collect time excluded)
    double collect_max_ms;  // worst manual full-collect pause
    double collect_avg_ms;  // mean manual full-collect pause
    double peak_gc_off_kb;  // allocator high-water mark, GC-stopped run
    int    collects;        // number of manual collects (2000/60)
    int    ok;
} gcmem_result_t;

static alloc_stats_t g_alloc_on;
static alloc_stats_t g_alloc_off;

static void bench_gcmem(gcmem_result_t *r) {
    r->ok = 0;

    // --- Run 1: default incremental GC. Heap sizes + peak + frame time. ---
    g_alloc_on.live = 0;
    g_alloc_on.peak = 0;
    lua_State *L = lua_newstate(l_alloc_track, &g_alloc_on);
    luaL_openlibs(L);
    bridge_register(L);
    if (luaL_dostring(L, LUA_SAKURA_CART) != LUA_OK) {
        printf("GCMEM LUA ERROR (gc on): %s\n", lua_tostring(L, -1));
        lua_close(L);
        return;
    }
    bridge_batch_reset();
    run_cart_frame(L);                          // one warm-up frame
    r->heap_warm_kb = lua_heap_kb(L);
    int64_t t0 = esp_timer_get_time();
    for (int f = 0; f < 2000; f++) {
        run_cart_frame(L);
    }
    r->frame_gc_on_ms = (esp_timer_get_time() - t0) / 2000.0 / 1000.0;
    r->heap_steady_kb = lua_heap_kb(L);
    lua_close(L);
    r->peak_kb = g_alloc_on.peak / 1024.0;

    // --- Run 2: GC stopped, manual full collect every 60 frames, each
    // collect timed separately (the "worst pause" a frame-scheduled collect
    // would cost). Frame time here EXCLUDES the collects, so
    // (frame_gc_on - frame_gc_off) = the incremental GC share per frame. ---
    g_alloc_off.live = 0;
    g_alloc_off.peak = 0;
    lua_State *L2 = lua_newstate(l_alloc_track, &g_alloc_off);
    luaL_openlibs(L2);
    bridge_register(L2);
    if (luaL_dostring(L2, LUA_SAKURA_CART) != LUA_OK) {
        printf("GCMEM LUA ERROR (gc off): %s\n", lua_tostring(L2, -1));
        lua_close(L2);
        return;
    }
    bridge_batch_reset();
    run_cart_frame(L2);                         // same warm-up as run 1
    lua_gc(L2, LUA_GCSTOP);
    int64_t frame_us = 0;
    int64_t collect_us_total = 0;
    int64_t collect_us_max = 0;
    int ncollect = 0;
    for (int f = 0; f < 2000; f++) {
        int64_t fs = esp_timer_get_time();
        run_cart_frame(L2);
        frame_us += esp_timer_get_time() - fs;
        if ((f + 1) % 60 == 0) {
            int64_t cs = esp_timer_get_time();
            lua_gc(L2, LUA_GCCOLLECT);          // full cycle; works while user-stopped
            int64_t cd = esp_timer_get_time() - cs;
            collect_us_total += cd;
            if (cd > collect_us_max) collect_us_max = cd;
            ncollect++;
        }
    }
    r->frame_gc_off_ms = frame_us / 2000.0 / 1000.0;
    r->collect_max_ms = collect_us_max / 1000.0;
    r->collect_avg_ms = ncollect
        ? collect_us_total / (double)ncollect / 1000.0 : 0.0;
    r->collects = ncollect;
    lua_close(L2);
    r->peak_gc_off_kb = g_alloc_off.peak / 1024.0;
    r->ok = 1;
}

// Runs `frames` steady-state iterations calling only _update(dt), returns
// ms/frame. Used both standalone (to reproduce the ~2.7ms baseline inside a
// bridge-registered state) and as half of the full_frame measurement.
static double bench_update_only(int frames) {
    lua_State *L = luaL_newstate();
    luaL_openlibs(L);
    bridge_register(L);
    double ms = -1.0;
    if (luaL_dostring(L, LUA_SAKURA_CART) == LUA_OK) {
        int64_t t0 = esp_timer_get_time();
        for (int f = 0; f < frames; f++) {
            lua_getglobal(L, "_update");
            lua_pushnumber(L, 1.0 / 30.0);
            lua_call(L, 1, 0);
        }
        ms = (esp_timer_get_time() - t0) / (double)frames / 1000.0;
    } else {
        printf("BRIDGE LUA ERROR (update): %s\n", lua_tostring(L, -1));
    }
    lua_close(L);
    return ms;
}

// Runs `frames` steady-state iterations calling only _draw() + end_frame()
// (no _update -- petal positions never move, so the same 120 quads are queued
// and drained every frame). Isolates the draw+batch-append+drain cost from
// the physics cost.
static double bench_draw_only(int frames) {
    lua_State *L = luaL_newstate();
    luaL_openlibs(L);
    bridge_register(L);
    double ms = -1.0;
    if (luaL_dostring(L, LUA_SAKURA_CART) == LUA_OK) {
        bridge_batch_reset();
        int64_t t0 = esp_timer_get_time();
        for (int f = 0; f < frames; f++) {
            lua_getglobal(L, "_draw");
            lua_call(L, 0, 0);
            end_frame();
        }
        ms = (esp_timer_get_time() - t0) / (double)frames / 1000.0;
    } else {
        printf("BRIDGE LUA ERROR (draw): %s\n", lua_tostring(L, -1));
    }
    lua_close(L);
    return ms;
}

// Runs `frames` steady-state iterations of the FULL bridged frame:
// _update(dt) -> _draw() -> end_frame(), exactly as the real harness would
// drive a running cart. Leaves g_checksum/g_last_sprite_count from this run
// for the printed BRIDGE RESULT line (most representative: positions evolve
// frame to frame, unlike bench_draw_only's static case).
static double bench_full_frame(int frames) {
    lua_State *L = luaL_newstate();
    luaL_openlibs(L);
    bridge_register(L);
    double ms = -1.0;
    if (luaL_dostring(L, LUA_SAKURA_CART) == LUA_OK) {
        bridge_batch_reset();
        g_checksum = 0;
        int64_t t0 = esp_timer_get_time();
        for (int f = 0; f < frames; f++) {
            lua_getglobal(L, "_update");
            lua_pushnumber(L, 1.0 / 30.0);
            lua_call(L, 1, 0);
            lua_getglobal(L, "_draw");
            lua_call(L, 0, 0);
            end_frame();
        }
        ms = (esp_timer_get_time() - t0) / (double)frames / 1000.0;
    } else {
        printf("BRIDGE LUA ERROR (full_frame): %s\n", lua_tostring(L, -1));
    }
    lua_close(L);
    return ms;
}

void app_main(void) {
    printf("\nLUA SPIKE: sakura _update on ESP32-S3 @240MHz (min caches)\n");

    // --- C baseline ---
    c_init();
    int64_t t0 = esp_timer_get_time();
    for (int f = 0; f < 2000; f++) {
        c_update(1.0f / 30.0f);
    }
    double c_ms = (esp_timer_get_time() - t0) / 2000.0 / 1000.0;

    // --- Lua ---
    lua_State *L = luaL_newstate();
    luaL_openlibs(L);
    double lua_ms = -1.0;
    if (luaL_dostring(L, LUA_SAKURA) == LUA_OK) {
        t0 = esp_timer_get_time();
        for (int f = 0; f < 2000; f++) {
            lua_getglobal(L, "_update");
            lua_pushnumber(L, 1.0 / 30.0);
            lua_call(L, 1, 0);
        }
        lua_ms = (esp_timer_get_time() - t0) / 2000.0 / 1000.0;
    } else {
        printf("LUA ERROR: %s\n", lua_tostring(L, -1));
    }

    // --- Bridge protocol proof (#6): Lua feeding the console's real batch
    // array format, drained by a C end_frame() consumer standing in for
    // native blit_batch. Three independent fresh Lua states so each
    // measurement is a clean steady-state read with no cross-contamination.
    double bridge_update_ms = bench_update_only(2000);
    double bridge_draw_ms = bench_draw_only(2000);
    double bridge_full_ms = bench_full_frame(2000);
    uint32_t bridge_checksum = g_checksum;
    uint32_t bridge_sprites = g_last_sprite_count;

    // --- GC / memory budget (#6 follow-up): heap size + allocator watermark
    // + GC share of frame time, in the full bridged-cart state.
    gcmem_result_t gm = {0};
    bench_gcmem(&gm);

    while (1) {
        printf("SPIKE RESULT: c=%.4f ms/frame  lua=%.3f ms/frame  "
               "(mpy same board measured 13.5)\n", c_ms, lua_ms);
        printf("BRIDGE RESULT: update=%.3f ms draw=%.3f ms full_frame=%.3f ms "
               "sprites=%u checksum=0x%08x\n",
               bridge_update_ms, bridge_draw_ms, bridge_full_ms,
               (unsigned)bridge_sprites, (unsigned)bridge_checksum);
        if (gm.ok) {
            printf("GCMEM RESULT: heap_warm=%.1fkB heap_steady=%.1fkB "
                   "peak=%.1fkB frame_gc_on=%.3f frame_gc_off=%.3f "
                   "full_collect=%.3f ms\n",
                   gm.heap_warm_kb, gm.heap_steady_kb, gm.peak_kb,
                   gm.frame_gc_on_ms, gm.frame_gc_off_ms, gm.collect_max_ms);
            printf("GCMEM DETAIL: collect_avg=%.3f ms collect_max=%.3f ms "
                   "collects=%d peak_gc_off=%.1fkB\n",
                   gm.collect_avg_ms, gm.collect_max_ms, gm.collects,
                   gm.peak_gc_off_kb);
        }
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}
