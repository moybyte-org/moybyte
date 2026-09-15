# `lua` is stock Lua 5.4.7, not vendored here -- see ../README.md.
idf_component_register(SRCS "main.c" INCLUDE_DIRS "." REQUIRES lua esp_timer)
