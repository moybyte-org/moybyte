cmake_minimum_required(VERSION 3.16)
set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../../wamr/build-scripts/esp-idf")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
# a wasm-level backtrace on trap (pairs with wamrc --enable-dump-call-stack);
# must precede project(), which is where IDF configures the components
idf_build_set_property(COMPILE_DEFINITIONS "-DWASM_ENABLE_DUMP_CALL_STACK=1" APPEND)
idf_build_set_property(COMPILE_DEFINITIONS "-DWASM_ENABLE_AOT_STACK_FRAME=1" APPEND)
project(doom_spike)
