# A CMake toolchain for a Moybyte native module (sdk/README.md): the chip's
# compiler and the SDK's flags and libraries, as tools/elf_cart.py builds
# them. Pick the chip's file (esp32s3.cmake, esp32p4.cmake); a module is an
# executable target, which the link makes a relocatable module:
#
#   cmake -B build -DCMAKE_TOOLCHAIN_FILE=<repo>/sdk/cmake/esp32p4.cmake
#   add_executable(nc nc.c)                # build/nc, linked
#   moy_module(nc)                         # stripped and signed: build/main.esp32p4.elf
#
# MOY_UNSIGNED=ON leaves the signature off.

if(NOT MOY_CHIP)
    message(FATAL_ERROR "sdk/cmake/moybyte.cmake: include a chip's file (esp32s3.cmake, esp32p4.cmake)")
endif()

get_filename_component(MOY_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
find_program(MOY_PYTHON NAMES python3 python REQUIRED)
execute_process(
    COMMAND ${MOY_PYTHON} ${MOY_ROOT}/tools/elf_cart.py sdk --chip ${MOY_CHIP}
    OUTPUT_VARIABLE _moy_sdk RESULT_VARIABLE _moy_rc ERROR_VARIABLE _moy_err)
if(NOT _moy_rc EQUAL 0)
    message(FATAL_ERROR "tools/elf_cart.py sdk: ${_moy_err}")
endif()
string(REGEX MATCH "CC=([^\n]*)" _ "${_moy_sdk}")
set(_moy_cc "${CMAKE_MATCH_1}")
string(REGEX MATCH "CFLAGS=([^\n]*)" _ "${_moy_sdk}")
set(_moy_cflags "${CMAKE_MATCH_1}")
string(REGEX MATCH "LDFLAGS=([^\n]*)" _ "${_moy_sdk}")
set(_moy_ldflags "${CMAKE_MATCH_1}")
string(REGEX MATCH "LIBS=([^\n]*)" _ "${_moy_sdk}")
set(_moy_libs "${CMAKE_MATCH_1}")

set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_C_COMPILER "${_moy_cc}")
set(CMAKE_C_FLAGS_INIT "${_moy_cflags}")
set(CMAKE_EXE_LINKER_FLAGS_INIT "${_moy_ldflags}")
set(CMAKE_C_STANDARD_LIBRARIES "${_moy_libs}")
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

option(MOY_UNSIGNED "leave the module's signature off" OFF)

# `target`'s module, stripped and signed, as main.<chip>.elf in the build folder.
function(moy_module target)
    set(_out "${CMAKE_BINARY_DIR}/main.${MOY_CHIP}.elf")
    set(_sign "")
    if(MOY_UNSIGNED)
        set(_sign "--unsigned")
    endif()
    add_custom_command(TARGET ${target} POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy $<TARGET_FILE:${target}> ${_out}
        COMMAND ${MOY_PYTHON} ${MOY_ROOT}/tools/elf_cart.py finish ${_out} --chip ${MOY_CHIP} ${_sign}
        VERBATIM)
endfunction()
