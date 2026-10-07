# moy_glass: the glass: canvas rows, the off-heap buffer table, the layer pool,
# the surface table and present (docs/kernel_survival_2026-10.md section 3).
# Its Python twin is runtime/moy_glass.py.
#
# Every build already lists this directory and it compiles nothing yet: the
# pass that crosses the subsystem adds its sources here, and no build list
# changes when it does.

add_library(usermod_moy_glass INTERFACE)

target_link_libraries(usermod INTERFACE usermod_moy_glass)
