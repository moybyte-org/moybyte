-- Moy Mesh's shared script: what its window (main.lua) and its service
-- (service.lua) both read. The manifest lists it in `sources` ahead of
-- main.lua, so it runs first in the window and first in the service.

ME = "moymesh"

-- Meshtastic's regions an SX1262 reaches (RadioInterface.cpp, RDEF), in the
-- order the window offers them: the band's start and end (Hz) and its power
-- limit (dBm). REGIONS is their names in that order, BANDS each one's band
-- by name. The radio's own ceiling is 22 dBm.
local rows = {
    { "EU_868", 869400000, 869650000, 27 },
    { "EU_433", 433000000, 434000000, 10 },
    { "US", 902000000, 928000000, 30 },
    { "ANZ", 915000000, 928000000, 30 },
    { "ANZ_433", 433050000, 434790000, 14 },
    { "CN", 470000000, 510000000, 19 },
    { "JP", 920500000, 923500000, 13 },
    { "RU", 868700000, 869200000, 20 },
    { "KR", 920000000, 923000000, 23 },
    { "TW", 920000000, 925000000, 27 },
    { "IN", 865000000, 867000000, 30 },
    { "NZ_865", 864000000, 868000000, 36 },
    { "TH", 920000000, 925000000, 27 },
    { "UA_433", 433000000, 434700000, 10 },
    { "UA_868", 868000000, 868600000, 14 },
    { "MY_433", 433000000, 435000000, 20 },
    { "MY_919", 919000000, 924000000, 27 },
    { "SG_923", 917000000, 925000000, 20 },
    { "PH_433", 433000000, 434700000, 10 },
    { "PH_868", 868000000, 869400000, 14 },
    { "PH_915", 915000000, 918000000, 24 },
    { "KZ_433", 433075000, 434775000, 10 },
    { "KZ_863", 863000000, 868000000, 30 },
    { "NP_865", 865000000, 868000000, 30 },
    { "BR_902", 902000000, 907500000, 30 },
}
REGIONS, BANDS = {}, {}
for i = 1, #rows do
    local r = rows[i]
    REGIONS[i] = r[1]
    BANDS[r[1]] = { r[2], r[3], r[4] }
end
REGION = REGIONS[1]
