# moy_axs: AXS15231B QSPI panel module for the Guition JC3248W535 build.
#
# Raw spi_master, no esp_lcd -- the QSPI bridge's one-CS-per-frame protocol is
# the opposite of esp_lcd's per-call CS cycling (see modmoy_axs.c's header), so
# there is no component dependency to patch into IDF_COMPONENTS here, unlike
# the T-Deck's moy_lcd / the P4's moy_dsi.

add_library(usermod_moy_axs INTERFACE)

target_sources(usermod_moy_axs INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/modmoy_axs.c
)

target_link_libraries(usermod INTERFACE usermod_moy_axs)
