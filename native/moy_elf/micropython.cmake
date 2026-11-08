# moy_elf: the native tier (README.md) -- Espressif's elf_loader, vendored
# under elf_loader/ (tools/vendor_elf_loader.py), as its own static library,
# and the usermod sources over it: the process model (moy_os.c), the OS
# table the generator writes (moy_os_table.c), the verdict a module passes
# before it is relocated (moy_elf_check.c) and the binding (modmoy_elf.c).
# ESP32-S3 (Xtensa) and ESP32-P4 (RISC-V) only; on any other target this
# file adds nothing.
#
# The component's Kconfig is not in this build, so its switches are set here
# as its Kconfig would select them for each chip with PSRAM: text and data in
# PSRAM, no symbol tables of its own (the binding's resolver refuses every
# name: a module imports nothing) and no shared objects. The S3 fetches the
# text through its instruction-bus alias of PSRAM (the bus mirror and the
# cache offset); the P4's PSRAM is executable at its data address, because
# MicroPython's sdkconfig.base leaves the IRAM/DRAM PMP split off.

if(CONFIG_IDF_TARGET_ESP32S3)
    set(MOY_ELF_ARCH xtensa)
    set(MOY_ELF_CHIP_DEFS
        CONFIG_ELF_LOADER_BUS_ADDRESS_MIRROR=1
        CONFIG_ELF_LOADER_CACHE_OFFSET=1)
elseif(CONFIG_IDF_TARGET_ESP32P4)
    set(MOY_ELF_ARCH riscv)
    set(MOY_ELF_CHIP_DEFS)
else()
    return()
endif()

set(MOY_ELF ${CMAKE_CURRENT_LIST_DIR}/elf_loader)

set(MOY_ELF_DEFS
    CONFIG_ELF_LOADER=1
    CONFIG_ELF_LOADER_LOAD_PSRAM=1
    CONFIG_ELF_LOADER_NUMBER_SYMBOLS=4
    ${MOY_ELF_CHIP_DEFS}
    ELF_LOADER_VER_MAJOR=2
    ELF_LOADER_VER_MINOR=1
    ELF_LOADER_VER_PATCH=0
)

add_library(moy_elf_loader STATIC
    ${MOY_ELF}/src/esp_elf.c
    ${MOY_ELF}/src/esp_elf_adapter.c
    ${MOY_ELF}/src/esp_elf_symbol.c
    ${MOY_ELF}/src/esp_elf_validate.c
    ${MOY_ELF}/src/arch/esp_elf_${MOY_ELF_ARCH}.c
)

idf_build_get_property(_moy_elf_inc INCLUDE_DIRECTORIES GENERATOR_EXPRESSION)
idf_build_get_property(_moy_elf_opts COMPILE_OPTIONS GENERATOR_EXPRESSION)
idf_build_get_property(_moy_elf_copts C_COMPILE_OPTIONS GENERATOR_EXPRESSION)
idf_build_get_property(_moy_elf_defs COMPILE_DEFINITIONS GENERATOR_EXPRESSION)
idf_build_get_property(_moy_elf_cfg CONFIG_DIR)

target_include_directories(moy_elf_loader
    PUBLIC
        ${MOY_ELF}/include
    PRIVATE
        ${_moy_elf_cfg}
        "${_moy_elf_inc}"
)
target_compile_definitions(moy_elf_loader PRIVATE ${MOY_ELF_DEFS} "${_moy_elf_defs}")
target_compile_options(moy_elf_loader PRIVATE
    "${_moy_elf_opts}"
    $<$<COMPILE_LANGUAGE:C>:${_moy_elf_copts}>
    -Wno-format
    -Wno-unused-variable
    -Wno-unused-function
)
target_link_libraries(moy_elf_loader PRIVATE
    idf::freertos idf::esp_timer idf::heap idf::esp_mm idf::newlib
    idf::esp_common idf::esp_system idf::esp_hw_support idf::soc idf::hal
    idf::log idf::esp_rom idf::spi_flash)

add_library(usermod_moy_elf INTERFACE)
target_sources(usermod_moy_elf INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_elf.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_os.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_os_table.c
    ${CMAKE_CURRENT_LIST_DIR}/moy_elf_check.c
)
target_include_directories(usermod_moy_elf INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${MOY_ELF}/include
    ${CMAKE_CURRENT_LIST_DIR}/../moy_sh
    ${CMAKE_CURRENT_LIST_DIR}/../moy_kernel
    ${CMAKE_CURRENT_LIST_DIR}/../moy_input
    ${CMAKE_CURRENT_LIST_DIR}/../moy_spine
    ${CMAKE_CURRENT_LIST_DIR}/../moy_net
    ${CMAKE_CURRENT_LIST_DIR}/../moy_app
    ${CMAKE_CURRENT_LIST_DIR}/../moy_store
    ${CMAKE_CURRENT_LIST_DIR}/../moy_png
)
# The loader's struct layout follows its switches, so the binding that reads
# it is compiled with them too. They reach every usermod source, which is
# harmless: nothing else in the tree reads a CONFIG_ELF_ name.
target_compile_definitions(usermod_moy_elf INTERFACE ${MOY_ELF_DEFS})
target_link_libraries(usermod_moy_elf INTERFACE moy_elf_loader)

# The P4s run a module in RISC-V U-mode (moy_umode.h): the trap side, the
# windows, each row's argument rule, the boot PMP layout, and the link's two
# wraps -- the loader's executable allocation as a module's block.
if(CONFIG_IDF_TARGET_ESP32P4)
    target_sources(usermod_moy_elf INTERFACE
        ${CMAKE_CURRENT_LIST_DIR}/moy_umode.c
        ${CMAKE_CURRENT_LIST_DIR}/moy_umode.S
        ${CMAKE_CURRENT_LIST_DIR}/moy_os_check.c
    )
    target_compile_definitions(usermod_moy_elf INTERFACE MOY_OS_UMODE=1)
    target_link_options(usermod_moy_elf INTERFACE
        "-Wl,--wrap=esp_elf_malloc" "-Wl,--wrap=esp_elf_free")
endif()
target_link_libraries(usermod INTERFACE usermod_moy_elf)
