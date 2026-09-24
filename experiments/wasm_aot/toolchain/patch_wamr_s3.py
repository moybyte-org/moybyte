"""Patch the vendored WAMR clone for AOT on the ESP32-S3 and ESP32-P4 (#158, 2026-09-24).

On the S3 the flash and PSRAM MMU serves both buses from one table, so code
in PSRAM at data address D is fetched at D + 0x06000000 -- but the
instruction-bus alias is FETCH-ONLY: a load or store through it is
LoadProhibited / StoreProhibited. WAMR's esp-idf "dual bus mirror" was
written against that idea and gets three things wrong on IDF 5.5, and a
flash-XIP module needs the same split between "where the loader reads the
file" and "where the CPU fetches it". Every edit is marker-guarded on
"Moybyte #158" so this is idempotent.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "wamr"
MARK = "Moybyte #158"


def patch(path, old, new, must=True):
    p = ROOT / path
    s = p.read_text()
    if new in s:
        return
    if old not in s:
        if must:
            raise SystemExit("anchor not found in %s:\n%s" % (path, old))
        return
    p.write_text(s.replace(old, new, 1))
    print("patched", path)


# 1) the mirror delta: IBUS = DBUS + (IROM_LOW - DROM_LOW), not (IROM_LOW - IROM_HIGH)
patch("core/shared/platform/esp-idf/espidf_memmap.c",
      "#define MEM_DUAL_BUS_OFFSET (SOC_IROM_LOW - SOC_IROM_HIGH)",
      "/* %s: PSRAM at data address D is fetched as instructions at\n"
      " * D + (SOC_IROM_LOW - SOC_DROM_LOW) -- the S3 MMU serves both buses from\n"
      " * one table. Upstream's (SOC_IROM_LOW - SOC_IROM_HIGH) lands outside every\n"
      " * mapped range on IDF 5.5. */\n"
      "#define MEM_DUAL_BUS_OFFSET (SOC_IROM_LOW - SOC_DROM_LOW)" % MARK)

# 2) clear fresh exec memory through the data bus, never the fetch-only alias
patch("core/shared/platform/esp-idf/espidf_memmap.c",
      "#if (WASM_MEM_DUAL_BUS_MIRROR != 0)\n"
      "        memset(buf_fixed + MEM_DUAL_BUS_OFFSET, 0, size);\n"
      "        return buf_fixed + MEM_DUAL_BUS_OFFSET;\n#else",
      "#if (WASM_MEM_DUAL_BUS_MIRROR != 0)\n"
      "        /* %s: the instruction-bus mirror is fetch-only on the S3\n"
      "         * (a store through it is StoreProhibited); clear via the data bus. */\n"
      "        memset(buf_fixed, 0, size);\n"
      "        return buf_fixed + MEM_DUAL_BUS_OFFSET;\n#else" % MARK)

# 3) a flash XIP window: the loader parses the DATA mapping, the CPU fetches
#    the INST mapping of the same partition. Registered by the app.
patch("core/shared/platform/esp-idf/espidf_memmap.c",
      "#if (WASM_MEM_DUAL_BUS_MIRROR != 0)\nvoid *\nos_get_dbus_mirror(void *ibus)\n{\n"
      "    if (in_ibus_ext(ibus)) {\n        return (void *)((char *)ibus - MEM_DUAL_BUS_OFFSET);\n    }\n",
      "#if (WASM_MEM_DUAL_BUS_MIRROR != 0)\n"
      "/* %s: one flash partition mapped twice -- ESP_PARTITION_MMAP_DATA for\n"
      " * the loader's reads, ESP_PARTITION_MMAP_INST for execution. */\n"
      "static const char *s_xip_dbus, *s_xip_ibus;\nstatic size_t s_xip_size;\n\n"
      "void\nos_register_xip_window(const void *ibus, const void *dbus, size_t size)\n{\n"
      "    s_xip_ibus = ibus;\n    s_xip_dbus = dbus;\n    s_xip_size = size;\n}\n\n"
      "void *\nos_get_ibus_mirror(void *dbus)\n{\n"
      "    const char *p = dbus;\n"
      "    if (s_xip_size && p >= s_xip_dbus && p < s_xip_dbus + s_xip_size) {\n"
      "        return (void *)(s_xip_ibus + (p - s_xip_dbus));\n    }\n"
      "    if ((uint32_t)p >= SOC_EXTRAM_DATA_LOW && (uint32_t)p < SOC_EXTRAM_DATA_HIGH) {\n"
      "        return (void *)(p + MEM_DUAL_BUS_OFFSET);\n    }\n"
      "    return dbus;\n}\n\n"
      "void *\nos_get_dbus_mirror(void *ibus)\n{\n"
      "    const char *p = ibus;\n"
      "    if (s_xip_size && p >= s_xip_ibus && p < s_xip_ibus + s_xip_size) {\n"
      "        return (void *)(s_xip_dbus + (p - s_xip_ibus));\n    }\n"
      "    if (in_ibus_ext(ibus)) {\n        return (void *)((char *)ibus - MEM_DUAL_BUS_OFFSET);\n    }\n"
      % MARK)

patch("core/shared/platform/include/platform_api_vmcore.h",
      "#if (WASM_MEM_DUAL_BUS_MIRROR != 0)\nvoid *\nos_get_dbus_mirror(void *ibus);\n#endif\n",
      "#if (WASM_MEM_DUAL_BUS_MIRROR != 0)\nvoid *\nos_get_dbus_mirror(void *ibus);\n"
      "/* %s */\nvoid *\nos_get_ibus_mirror(void *dbus);\n"
      "void\nos_register_xip_window(const void *ibus, const void *dbus, size_t size);\n#endif\n" % MARK)

# 4) an XIP module executes where it lies: the loader read it through the
#    data alias, the function pointers must be the instruction alias.
patch("core/iwasm/aot/aot_loader.c",
      "    module->literal = (uint8 *)buf;\n    module->code = (void *)(buf + module->literal_size);\n",
      "    module->literal = (uint8 *)buf;\n"
      "#if (WASM_MEM_DUAL_BUS_MIRROR != 0)\n"
      "    /* %s: fetch through the instruction alias (flash XIP window or\n"
      "     * PSRAM mirror); the buffer itself stays the readable one. */\n"
      "    module->code = os_get_ibus_mirror((void *)(buf + module->literal_size));\n"
      "#else\n"
      "    module->code = (void *)(buf + module->literal_size);\n"
      "#endif\n" % MARK)

# 5) say WHICH relocation is out of bounds
patch("core/iwasm/aot/arch/aot_reloc_xtensa.c",
      '        set_error_buf(error_buf, error_buf_size,\n'
      '                      "AOT module load failed: invalid relocation offset.");\n'
      '        return false;',
      '        if (error_buf) /* %s */\n'
      '            snprintf(error_buf, error_buf_size,\n'
      '                     "AOT module load failed: invalid relocation offset "\n'
      '                     "%%u+%%u > section %%u.", (unsigned)reloc_offset,\n'
      '                     (unsigned)reloc_data_size, (unsigned)target_section_size);\n'
      '        return false;' % MARK, must=False)
patch("core/iwasm/aot/arch/aot_reloc_xtensa.c",
      '#include "aot_reloc.h"', '#include <stdio.h>\n#include "aot_reloc.h"', must=False)
# 6) ESP32-P4: PSRAM is executable by default -- IDF's cpu_region_protect.c
#    gives "External RAM" no PMP entry at all ("default all permissions"), the
#    only region that is (the flash rodata mapping is R). What stops WAMR is
#    the heap: MALLOC_CAP_EXEC names internal L2MEM only, and under the
#    default CONFIG_ESP_SYSTEM_PMP_IDRAM_SPLIT that comes back empty. So on the
#    P4 an executable mapping comes from PSRAM first, and the loader's
#    os_icache_flush() -- called once the text is copied and relocated -- does
#    the write-back and instruction-cache invalidate the unified bus needs.
patch("core/shared/platform/esp-idf/espidf_memmap.c",
      "#else\n        uint32_t mem_caps = MALLOC_CAP_EXEC;\n#endif\n",
      "#elif CONFIG_IDF_TARGET_ESP32P4\n"
      "        /* %s: PSRAM carries no PMP entry on the P4, so it is RWX; the\n"
      "         * internal exec heap is the fallback (present only with\n"
      "         * CONFIG_ESP_SYSTEM_PMP_IDRAM_SPLIT=n). */\n"
      "        uint32_t mem_caps = heap_caps_get_free_size(MALLOC_CAP_SPIRAM) > size + 64\n"
      "                                ? MALLOC_CAP_SPIRAM : MALLOC_CAP_EXEC;\n"
      "        os_printf(\"WAMR exec mmap %u bytes from %s\\n\", (unsigned)size,\n"
      "                  mem_caps == MALLOC_CAP_SPIRAM ? \"PSRAM\" : \"internal exec heap\");\n"
      "#else\n        uint32_t mem_caps = MALLOC_CAP_EXEC;\n#endif\n" % MARK)
patch("core/shared/platform/esp-idf/espidf_memmap.c",
      "void\nos_icache_flush(void *start, size_t len)\n{}\n",
      "void\nos_icache_flush(void *start, size_t len)\n{\n"
      "#if CONFIG_IDF_TARGET_ESP32P4\n"
      "    /* %s: the text was written through the data cache; push it out and\n"
      "     * drop whatever the instruction cache holds for that range. */\n"
      "    if (start && len) {\n"
      "        uintptr_t a = (uintptr_t)start & ~(uintptr_t)63;   /* 64 B lines; M2C wants aligned */\n"
      "        size_t n = (((uintptr_t)start + len + 63) & ~(uintptr_t)63) - a;\n"
      "        esp_cache_msync((void *)a, n, ESP_CACHE_MSYNC_FLAG_DIR_C2M);\n"
      "        esp_cache_msync((void *)a, n, ESP_CACHE_MSYNC_FLAG_DIR_M2C | ESP_CACHE_MSYNC_FLAG_TYPE_INST);\n"
      "        __asm__ volatile(\"fence.i\" ::: \"memory\");\n"
      "    }\n"
      "#else\n    (void)start;\n    (void)len;\n#endif\n}\n" % MARK)
patch("core/shared/platform/esp-idf/espidf_memmap.c",
      '#include "platform_api_extension.h"\n',
      '#include "platform_api_extension.h"\n#if CONFIG_IDF_TARGET_ESP32P4\n#include "esp_cache.h"   /* %s */\n#endif\n' % MARK)
patch("build-scripts/esp-idf/wamr/CMakeLists.txt",
      "                       REQUIRES pthread lwip esp_timer\n",
      "                       REQUIRES pthread lwip esp_timer esp_mm   # esp_mm: %s\n" % MARK)
print("ok")
