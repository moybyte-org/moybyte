# moy_input: input: the merged input table, its sources and the pointer, and
# the drivers that write them (docs/kernel_survival_2026-10.md section 4). Its
# Python twin is runtime/moy_input.py.
#
# Every build already lists this directory and it compiles nothing yet: the
# pass that crosses the subsystem adds its sources here, and no build list
# changes when it does.

add_library(usermod_moy_input INTERFACE)

target_link_libraries(usermod INTERFACE usermod_moy_input)
