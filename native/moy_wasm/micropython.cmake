# moy_wasm: the WebAssembly cart tier's engine -- the vendored WAMR runtime
# (wamr/, AOT only, tools/vendor_wamr.py) and the MicroPython binding that
# runs a module on its own thread (modmoy_wasm.c), including the cart session
# moycore drives (moy_wasm_session.h). Device-only: there is no .mk twin,
# because the unix and wasm ports have no board to run AOT code on.
#
# The runtime builds as its OWN static library, not as usermod sources, for
# three reasons that are each enough: its -D switches (BH_MALLOC, the build
# target, the feature set) must not reach every other usermod file, which is
# what an INTERFACE definition on `usermod` does; its include tree carries
# generic names (config.h, platform_internal.h) that must not join the
# MicroPython component's include path; and usermod sources are run through
# the qstr preprocessor, which knows none of WAMR's switches. Only
# modmoy_wasm.c is a usermod source, and it sees nothing but wasm_export.h.
#
# A usermod runs inside the main component's CMakeLists, before that component
# registers, so the library takes the IDF build's flags explicitly -- the same
# generator expressions idf_component_register applies to a component.

set(MOY_WAMR ${CMAKE_CURRENT_LIST_DIR}/wamr)
set(MOY_WAMR_CORE ${MOY_WAMR}/core)

set(MOY_WAMR_SRCS
    ${MOY_WAMR_CORE}/iwasm/aot/aot_intrinsic.c
    ${MOY_WAMR_CORE}/iwasm/aot/aot_loader.c
    ${MOY_WAMR_CORE}/iwasm/aot/aot_runtime.c
    ${MOY_WAMR_CORE}/iwasm/common/wasm_blocking_op.c
    ${MOY_WAMR_CORE}/iwasm/common/wasm_c_api.c
    ${MOY_WAMR_CORE}/iwasm/common/wasm_exec_env.c
    ${MOY_WAMR_CORE}/iwasm/common/wasm_loader_common.c
    ${MOY_WAMR_CORE}/iwasm/common/wasm_memory.c
    ${MOY_WAMR_CORE}/iwasm/common/wasm_native.c
    ${MOY_WAMR_CORE}/iwasm/common/wasm_runtime_common.c
    ${MOY_WAMR_CORE}/iwasm/common/wasm_shared_memory.c
    ${MOY_WAMR_CORE}/shared/mem-alloc/ems/ems_alloc.c
    ${MOY_WAMR_CORE}/shared/mem-alloc/ems/ems_gc.c
    ${MOY_WAMR_CORE}/shared/mem-alloc/ems/ems_hmu.c
    ${MOY_WAMR_CORE}/shared/mem-alloc/ems/ems_kfc.c
    ${MOY_WAMR_CORE}/shared/mem-alloc/mem_alloc.c
    ${MOY_WAMR_CORE}/shared/platform/common/libc-util/libc_errno.c
    ${MOY_WAMR_CORE}/shared/platform/esp-idf/espidf_clock.c
    ${MOY_WAMR_CORE}/shared/platform/esp-idf/espidf_file.c
    ${MOY_WAMR_CORE}/shared/platform/esp-idf/espidf_malloc.c
    ${MOY_WAMR_CORE}/shared/platform/esp-idf/espidf_memmap.c
    ${MOY_WAMR_CORE}/shared/platform/esp-idf/espidf_platform.c
    ${MOY_WAMR_CORE}/shared/platform/esp-idf/espidf_socket.c
    ${MOY_WAMR_CORE}/shared/platform/esp-idf/espidf_thread.c
    ${MOY_WAMR_CORE}/shared/utils/bh_assert.c
    ${MOY_WAMR_CORE}/shared/utils/bh_bitmap.c
    ${MOY_WAMR_CORE}/shared/utils/bh_common.c
    ${MOY_WAMR_CORE}/shared/utils/bh_hashmap.c
    ${MOY_WAMR_CORE}/shared/utils/bh_leb128.c
    ${MOY_WAMR_CORE}/shared/utils/bh_list.c
    ${MOY_WAMR_CORE}/shared/utils/bh_log.c
    ${MOY_WAMR_CORE}/shared/utils/bh_queue.c
    ${MOY_WAMR_CORE}/shared/utils/bh_vector.c
    ${MOY_WAMR_CORE}/shared/utils/runtime_timer.c
)

# The runtime's feature set: AOT only (no interpreter, no JIT), no WASI, no
# builtin libc, no multi-module, no threads; bulk memory and reference types
# because clang emits both by default; custom sections kept, because the
# provenance key is one. The quick AOT entry stays off: it only speeds a call
# from the host into wasm, which a cart takes a few times a frame, and its
# table is a writable 768 bytes of internal RAM.
set(MOY_WAMR_DEFS
    BH_PLATFORM_ESP_IDF
    BH_MALLOC=wasm_runtime_malloc
    BH_FREE=wasm_runtime_free
    WASM_ENABLE_AOT=1
    WASM_ENABLE_INTERP=0
    WASM_ENABLE_FAST_INTERP=0
    WASM_ENABLE_JIT=0
    WASM_ENABLE_LIBC_BUILTIN=0
    WASM_ENABLE_LIBC_WASI=0
    WASM_ENABLE_MULTI_MODULE=0
    WASM_ENABLE_THREAD_MGR=0
    WASM_ENABLE_SHARED_MEMORY=0
    WASM_ENABLE_BULK_MEMORY=1
    WASM_ENABLE_REF_TYPES=1
    WASM_ENABLE_MINI_LOADER=0
    WASM_ENABLE_LOAD_CUSTOM_SECTION=1
    WASM_ENABLE_AOT_INTRINSICS=1
    WASM_ENABLE_QUICK_AOT_ENTRY=0
    WASM_ENABLE_SHRUNK_MEMORY=1
    WASM_ENABLE_EXTENDED_CONST_EXPR=0
    WASM_DISABLE_HW_BOUND_CHECK=0
    WASM_DISABLE_STACK_HW_BOUND_CHECK=0
    WASM_DISABLE_WAKEUP_BLOCKING_OP=0
)
if(CONFIG_IDF_TARGET_ARCH_XTENSA)
    list(APPEND MOY_WAMR_SRCS
        ${MOY_WAMR_CORE}/iwasm/aot/arch/aot_reloc_xtensa.c
        ${MOY_WAMR_CORE}/iwasm/common/arch/invokeNative_xtensa.s)
    list(APPEND MOY_WAMR_DEFS BUILD_TARGET_XTENSA)
    # AOT text in PSRAM, fetched through the S3's instruction-bus alias.
    if(CONFIG_SPIRAM)
        list(APPEND MOY_WAMR_DEFS WASM_MEM_DUAL_BUS_MIRROR=1)
    endif()
elseif(CONFIG_IDF_TARGET_ESP32P4)
    list(APPEND MOY_WAMR_SRCS
        ${MOY_WAMR_CORE}/iwasm/aot/arch/aot_reloc_riscv.c
        ${MOY_WAMR_CORE}/iwasm/common/arch/invokeNative_riscv.S)
    list(APPEND MOY_WAMR_DEFS BUILD_TARGET_RISCV32_ILP32F)
else()
    message(FATAL_ERROR "moy_wasm: no WAMR build target for ${IDF_TARGET}")
endif()

# moy_wasm_thread.c rides here too: it needs the pthread component's
# esp_pthread.h, which the MicroPython component cannot see.
add_library(moy_wamr STATIC ${MOY_WAMR_SRCS}
    ${CMAKE_CURRENT_LIST_DIR}/moy_wasm_thread.c)

idf_build_get_property(_moy_wamr_inc INCLUDE_DIRECTORIES GENERATOR_EXPRESSION)
idf_build_get_property(_moy_wamr_opts COMPILE_OPTIONS GENERATOR_EXPRESSION)
idf_build_get_property(_moy_wamr_copts C_COMPILE_OPTIONS GENERATOR_EXPRESSION)
idf_build_get_property(_moy_wamr_aopts ASM_COMPILE_OPTIONS GENERATOR_EXPRESSION)
idf_build_get_property(_moy_wamr_defs COMPILE_DEFINITIONS GENERATOR_EXPRESSION)
idf_build_get_property(_moy_wamr_cfg CONFIG_DIR)

target_include_directories(moy_wamr
    PUBLIC
        ${MOY_WAMR_CORE}/iwasm/include
    PRIVATE
        ${CMAKE_CURRENT_LIST_DIR}
        ${MOY_WAMR_CORE}/iwasm/common
        ${MOY_WAMR_CORE}/iwasm/aot
        ${MOY_WAMR_CORE}/iwasm/interpreter
        ${MOY_WAMR_CORE}/shared/platform/esp-idf
        ${MOY_WAMR_CORE}/shared/platform/include
        ${MOY_WAMR_CORE}/shared/platform/common/libc-util
        ${MOY_WAMR_CORE}/shared/mem-alloc
        ${MOY_WAMR_CORE}/shared/utils
        ${_moy_wamr_cfg}
        "${_moy_wamr_inc}"
)
target_compile_definitions(moy_wamr PRIVATE ${MOY_WAMR_DEFS} "${_moy_wamr_defs}")
target_compile_options(moy_wamr PRIVATE
    "${_moy_wamr_opts}"
    $<$<COMPILE_LANGUAGE:C>:${_moy_wamr_copts}>
    $<$<COMPILE_LANGUAGE:ASM>:${_moy_wamr_aopts}>
    -Wno-format
    -Wno-unused-variable
)
# Include paths only: the symbols resolve at the final link like any other
# component's.
target_link_libraries(moy_wamr PRIVATE
    idf::freertos idf::esp_timer idf::heap idf::esp_mm idf::pthread idf::lwip
    idf::newlib idf::vfs idf::esp_common idf::esp_system idf::esp_hw_support
    idf::soc idf::hal idf::log idf::esp_rom)

add_library(usermod_moy_wasm INTERFACE)

target_sources(usermod_moy_wasm INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_wasm.c
)

target_include_directories(usermod_moy_wasm INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${MOY_WAMR_CORE}/iwasm/include
)

# MOY_WASM is what compiles moycore's half of the tier -- the vendored
# libmoy/moy_wasm.c import table and its host callbacks -- so a build carrying
# this engine gets the Player path and one without it (the unix and web
# builds, the Zero) compiles none of it. It reaches every usermod source,
# which is harmless: nothing else in the tree reads it.
target_compile_definitions(usermod_moy_wasm INTERFACE MOY_WASM=1)

target_link_libraries(usermod_moy_wasm INTERFACE moy_wamr)
target_link_libraries(usermod INTERFACE usermod_moy_wasm)
