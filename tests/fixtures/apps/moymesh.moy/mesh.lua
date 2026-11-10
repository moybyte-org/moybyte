-- Moy Mesh's shared script: what its window (main.lua) and its service
-- (service.lua) both read. The manifest lists it in `sources` ahead of
-- main.lua, so it runs first in the window and first in the service.
--
-- It holds the radio's setup as Meshtastic's firmware (2.7) and its
-- protobufs (config.proto, channel.proto, apponly.proto) define it: the
-- region, the modem preset, the frequency slot, the hop limit and the
-- channels, kept in the app's prefs, and the channel URL a setup is shared
-- by (https://meshtastic.org/e/#<the ChannelSet, base64url>).
--
-- THE SETUP, as setup() reads it from the prefs and setup_save(t) writes it:
--   region    a name of REGIONS ("EU_868" until one is chosen)
--   preset    a name of PRESETS ("LONG_FAST")
--   slot      the frequency slot, Meshtastic's channel_num: 0 hashes the
--             primary channel's name onto the region's slots, n >= 1 is
--             slot n (1-based, modulo the slots the region holds)
--   hops      the hop limit a packet leaves with, 0 to 7 (3)
--   channels  1 to CHANNELS_MAX channels, the first the primary, each
--             { name = text of up to 11 bytes ("" is the preset's own
--             name, "LongFast"), psk = the key in base64: one byte is the
--             well-known key's shorthand ("AQ==" the key itself, 2 to 10
--             it with its last byte raised by 1 to 9, "AA==" no
--             encryption), 16 or 32 bytes an AES-128 or AES-256 key, ""
--             on a secondary the primary's key }
--
-- THE CHANNEL URL: url_parse(url) answers a preview, nil and why where the
-- URL is none: { channels = { {name, psk}, ... }, region = a name or nil
-- (the link's region unset), preset, slot, hops, add = true for an
-- "?add=true" link (its channels join this setup's), warnings = { text,
-- ... } for what the link holds that Moy Mesh does not take }.
-- setup_merge(preview) is the setup the preview makes of this one, and
-- setup_apply(preview) saves it (the window then sends "retune").
-- url_make(setup) is the setup's own URL.

ME = "moymesh"

-- Meshtastic's regions an SX1262 reaches (RadioInterface.cpp, RDEF), in the
-- order the window offers them: the band's start and end (Hz), its power
-- limit (dBm) and its RegionCode (config.proto). REGIONS is their names in
-- that order, BANDS each one's row by name. Every region's channel spacing
-- is 0, so its slots are its span over the bandwidth. The radio's own
-- ceiling is 22 dBm.
local rows = {
    { "EU_868", 869400000, 869650000, 27, 3 },
    { "EU_433", 433000000, 434000000, 10, 2 },
    { "US", 902000000, 928000000, 30, 1 },
    { "ANZ", 915000000, 928000000, 30, 6 },
    { "ANZ_433", 433050000, 434790000, 14, 22 },
    { "CN", 470000000, 510000000, 19, 4 },
    { "JP", 920500000, 923500000, 13, 5 },
    { "RU", 868700000, 869200000, 20, 9 },
    { "KR", 920000000, 923000000, 23, 7 },
    { "TW", 920000000, 925000000, 27, 8 },
    { "IN", 865000000, 867000000, 30, 10 },
    { "NZ_865", 864000000, 868000000, 36, 11 },
    { "TH", 920000000, 925000000, 27, 12 },
    { "UA_433", 433000000, 434700000, 10, 14 },
    { "UA_868", 868000000, 868600000, 14, 15 },
    { "MY_433", 433000000, 435000000, 20, 16 },
    { "MY_919", 919000000, 924000000, 27, 17 },
    { "SG_923", 917000000, 925000000, 20, 18 },
    { "PH_433", 433000000, 434700000, 10, 19 },
    { "PH_868", 868000000, 869400000, 14, 20 },
    { "PH_915", 915000000, 918000000, 24, 21 },
    { "KZ_433", 433075000, 434775000, 10, 23 },
    { "KZ_863", 863000000, 868000000, 30, 24 },
    { "NP_865", 865000000, 868000000, 30, 25 },
    { "BR_902", 902000000, 907500000, 30, 26 },
}
REGIONS, BANDS, REGION_OF = {}, {}, {}
for i = 1, #rows do
    local r = rows[i]
    REGIONS[i] = r[1]
    BANDS[r[1]] = { r[2], r[3], r[4], r[5] }
    REGION_OF[r[5]] = r[1]
end
REGION = REGIONS[1]

-- Meshtastic's modem presets (config.proto's ModemPreset, MeshRadio.h's
-- modemPresetToParams for a region that is not wide-band, DisplayFormatters'
-- names), in the order the window offers them: the code, the name the
-- primary channel takes when it has none, the spreading factor, the
-- bandwidth (Hz) and the coding rate (4/n). VERY_LONG_SLOW and the codes
-- past LONG_TURBO the firmware runs as LONG_FAST, so they are not offered.
local presets = {
    { "LONG_FAST", 0, "LongFast", 11, 250000, 5 },
    { "LONG_MODERATE", 7, "LongMod", 11, 125000, 8 },
    { "LONG_SLOW", 1, "LongSlow", 12, 125000, 8 },
    { "LONG_TURBO", 9, "LongTurbo", 11, 500000, 8 },
    { "MEDIUM_SLOW", 3, "MediumSlow", 10, 250000, 5 },
    { "MEDIUM_FAST", 4, "MediumFast", 9, 250000, 5 },
    { "SHORT_SLOW", 5, "ShortSlow", 8, 250000, 5 },
    { "SHORT_FAST", 6, "ShortFast", 7, 250000, 5 },
    { "SHORT_TURBO", 8, "ShortTurbo", 7, 500000, 5 },
}
PRESETS, MODEMS, PRESET_OF = {}, {}, {}
for i = 1, #presets do
    local p = presets[i]
    PRESETS[i] = p[1]
    MODEMS[p[1]] = { code = p[2], title = p[3], sf = p[4], bw = p[5], cr = p[6] }
    PRESET_OF[p[2]] = p[1]
end
PRESET = "LONG_FAST"

HOPS = 3                                -- the hop limit until one is chosen
CHANNELS_MAX = 8                        -- a primary and seven secondaries
CHANNEL_NAME_MAX = 11                   -- channel.options: name max_size 12
DEFAULT_PSK = "AQ=="

-- The well-known key one-byte PSKs index (Channels.h, defaultpsk).
DEFAULT_KEY = "\xd4\xf1\xbb\x3a\x20\x29\x07\x59\xf0\xbc\xff\xab\xcf\x4e\x69\x01"

local byte, char, pack, unpack = string.byte, string.char, string.pack, string.unpack

-- -- the radio's numbers --------------------------------------------------------------

-- `h` read as an unsigned 32-bit number, modulo `m` (a small positive number).
local function umod(h, m)
    return ((h >> 1) % m * 2 + (h & 1)) % m
end

-- RadioInterface.cpp's hash: djb2 over the bytes, 32 bits.
function djb2(text)
    local h = 5381
    for i = 1, #text do
        h = (h * 33 + byte(text, i)) & 0xFFFFFFFF
    end
    return h
end

-- The modem `preset` runs in `region`: its own, or LONG_FAST where its
-- bandwidth is wider than the region's band (RadioInterface::applyModemConfig).
function modem(region, preset)
    local m, r = MODEMS[preset] or MODEMS[PRESET], BANDS[region] or BANDS[REGION]
    if r[2] - r[1] < m.bw then
        return MODEMS[PRESET], PRESET
    end
    return m, MODEMS[preset] and preset or PRESET
end

-- How many slots of `bw` the region holds.
function slots(region, bw)
    local r = BANDS[region]
    return (r[2] - r[1]) // (bw or 250000)
end

-- The centre (Hz) of the slot a channel takes in `region` at bandwidth
-- `bw`: slot n >= 1 is slot n, 0 (or none) the one `name` (the primary
-- channel's) hashes to.
function frequency(region, name, bw, slot)
    name, bw, slot = name or "LongFast", bw or 250000, slot or 0
    local r = BANDS[region]
    local n = slots(region, bw)
    local i = slot > 0 and (slot - 1) % n or umod(djb2(name), n)
    return r[1] + bw // 2 + i * bw
end

function power(region)
    return math.min(BANDS[region][3], 22)
end

-- -- keys and hashes --------------------------------------------------------------------

-- A channel's AES key from its PSK (Channels::getKey): a one-byte PSK indexes
-- the well-known key (0: none), a short one is padded with zeros to 16 or 32
-- bytes.
function channel_key(psk)
    local n = #psk
    if n == 1 then
        local i = byte(psk)
        if i == 0 then
            return ""
        end
        return DEFAULT_KEY:sub(1, 15) .. char((byte(DEFAULT_KEY, 16) + i - 1) & 0xFF)
    end
    if n > 0 and n < 16 then
        return psk .. string.rep("\0", 16 - n)
    end
    if n > 16 and n < 32 then
        return psk .. string.rep("\0", 32 - n)
    end
    return psk
end

local function xor(data)
    local h = 0
    for i = 1, #data do
        h = h ~ byte(data, i)
    end
    return h
end

-- Channels::generateHash: the XOR of the name's bytes and the key's.
function channel_hash(name, key)
    return xor(name) ~ xor(key)
end

-- -- base64 ----------------------------------------------------------------------------

local B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
local B64_OF = {}
for i = 1, 64 do
    B64_OF[byte(B64, i)] = i - 1
end
B64_OF[byte("-")], B64_OF[byte("_")] = 62, 63

-- `s` in base64, padded; base64url without padding where `url`.
function b64(s, url)
    local out = {}
    for i = 1, #s, 3 do
        local a, b, c = byte(s, i, i + 2)
        local n = (a << 16) | ((b or 0) << 8) | (c or 0)
        local k = c and 4 or b and 3 or 2
        for j = 1, 4 do
            if j <= k then
                local v = (n >> (6 * (4 - j))) & 63
                out[#out + 1] = B64:sub(v + 1, v + 1)
            elseif not url then
                out[#out + 1] = "="
            end
        end
    end
    local t = table.concat(out)
    if url then
        t = t:gsub("%+", "-"):gsub("/", "_")
    end
    return t
end

-- The bytes base64 or base64url `t` holds, padded or not; nil for text
-- that is neither.
function b64_bytes(t)
    if type(t) ~= "string" then
        return nil
    end
    t = t:gsub("=+$", "")
    if #t % 4 == 1 then
        return nil
    end
    local out, n, bits = {}, 0, 0
    for i = 1, #t do
        local v = B64_OF[byte(t, i)]
        if v == nil then
            return nil
        end
        n, bits = (n << 6) | v, bits + 6
        if bits >= 8 then
            bits = bits - 8
            out[#out + 1] = char((n >> bits) & 0xFF)
            n = n & ((1 << bits) - 1)
        end
    end
    return table.concat(out)
end

-- -- the protobuf wire format ----------------------------------------------------------

function varint(n)
    local out = {}
    repeat
        local b = n & 0x7F
        n = n >> 7
        out[#out + 1] = char(n ~= 0 and (b | 0x80) or b)
    until n == 0
    return table.concat(out)
end

-- The varint at offset `at` (0-based): its value (-1 for one past 31 bits)
-- and the offset past it; nil for one that runs off the end or past 64 bits.
function read_varint(b, at)
    local n, shift, big = 0, 0, false
    while at < #b do
        local c = byte(b, at + 1)
        at = at + 1
        local v = c & 0x7F
        if shift < 31 then
            n = n | (v << shift)
            big = big or (shift > 24 and (v >> (31 - shift)) ~= 0)
        elseif v ~= 0 then
            big = true
        end
        if c & 0x80 == 0 then
            return big and -1 or n, at
        end
        shift = shift + 7
        if shift > 63 then
            break
        end
    end
    return nil, at
end

-- A message's fields: { [field] = { value, ... } }, a varint or fixed32 a
-- number, a fixed64 its 8 bytes, a length-delimited field its bytes; nil
-- where `b` does not parse.
function pb_fields(b)
    local f, at = {}, 0
    while at < #b do
        local tag, v
        tag, at = read_varint(b, at)
        if tag == nil or tag < 8 then
            return nil
        end
        local field, kind = tag >> 3, tag & 7
        if kind == 0 then
            v, at = read_varint(b, at)
            if v == nil then
                return nil
            end
        elseif kind == 2 then
            local n
            n, at = read_varint(b, at)
            if n == nil or n < 0 or at + n > #b then
                return nil
            end
            v, at = b:sub(at + 1, at + n), at + n
        elseif kind == 5 then
            if at + 4 > #b then
                return nil
            end
            v, at = unpack("<I4", b, at + 1), at + 4
        elseif kind == 1 then
            if at + 8 > #b then
                return nil
            end
            v, at = b:sub(at + 1, at + 8), at + 8
        else
            return nil
        end
        local l = f[field]
        if l == nil then
            l = {}
            f[field] = l
        end
        l[#l + 1] = v
    end
    return f
end

-- The last value of field `n` (proto3: the last one wins), else `d`.
function pb_get(f, n, d)
    local l = f[n]
    if l == nil then
        return d
    end
    return l[#l]
end

function pb_varint(n, v)
    if v == nil or v == 0 or v == false then
        return ""
    end
    return varint(n << 3) .. varint(v == true and 1 or v)
end

function pb_bytes(n, s)
    if s == nil or s == "" then
        return ""
    end
    return varint((n << 3) | 2) .. varint(#s) .. s
end

function pb_fixed32(n, v)
    if v == nil or v == 0 then
        return ""
    end
    return varint((n << 3) | 5) .. pack("<I4", v)
end

-- -- text ------------------------------------------------------------------------------

-- `text` cut to `max` bytes, whole UTF-8 characters only.
function clip(text, max)
    if #text <= max then
        return text
    end
    local cut = max
    while cut > 0 and byte(text, cut + 1) & 0xC0 == 0x80 do
        cut = cut - 1
    end
    return text:sub(1, cut)
end

-- -- the setup -------------------------------------------------------------------------

-- A channel as the setup keeps it, or nil for one that is none: its name
-- clipped, its psk base64 of 0, 1, 16 or 32 bytes (a short key padded).
local function channel_ok(c, primary)
    if type(c) ~= "table" then
        return nil
    end
    local name = type(c.name) == "string" and clip(c.name, CHANNEL_NAME_MAX) or ""
    local psk = b64_bytes(c.psk == nil and (primary and DEFAULT_PSK or "") or c.psk)
    if psk == nil or #psk > 32 then
        return nil
    end
    if #psk > 1 then
        psk = channel_key(psk)
    end
    return { name = name, psk = b64(psk) }
end

local function int_in(v, lo, hi, d)
    if math.type(v) == "float" and v == math.floor(v) then
        v = math.tointeger(v)
    end
    if math.type(v) ~= "integer" or v < lo or v > hi then
        return d
    end
    return v
end

-- The setup `t` names, every field checked and the missing ones the
-- defaults: a table setup_save() keeps as it is.
function setup_check(t)
    t = type(t) == "table" and t or {}
    local s = {
        region = BANDS[t.region] and t.region or REGION,
        preset = MODEMS[t.preset] and t.preset or PRESET,
        slot = int_in(t.slot, 0, 0xFFFF, 0),
        hops = int_in(t.hops, 0, 7, HOPS),
        channels = {},
    }
    local cs = type(t.channels) == "table" and t.channels or {}
    for i = 1, #cs do
        local c = channel_ok(cs[i], #s.channels == 0)
        if c and #s.channels < CHANNELS_MAX then
            s.channels[#s.channels + 1] = c
        end
    end
    if #s.channels == 0 then
        s.channels[1] = { name = "", psk = DEFAULT_PSK }
    end
    return s
end

function setup()
    return setup_check({
        region = prefs.get("region"), preset = prefs.get("preset"),
        slot = prefs.get("slot"), hops = prefs.get("hops"),
        channels = prefs.get("channels"),
    })
end

function setup_save(t)
    local s = setup_check(t)
    prefs.set("region", s.region)
    prefs.set("preset", s.preset)
    prefs.set("slot", s.slot)
    prefs.set("hops", s.hops)
    prefs.set("channels", s.channels)
    return s
end

-- The name channel `i` (1-based) of setup `s` goes by: its own, or the
-- preset's (Channels::getName).
function channel_name(s, i)
    local c = s.channels[i]
    if c.name ~= "" then
        return c.name
    end
    return MODEMS[s.preset].title
end

-- Each channel of setup `s` as the radio uses it: { name, key (the AES key,
-- "" for none), hash }, a secondary with no key on the primary's.
function channel_keys(s)
    local out = {}
    for i = 1, #s.channels do
        local psk = b64_bytes(s.channels[i].psk) or ""
        local key
        if psk == "" and i > 1 then
            key = out[1].key
        else
            key = channel_key(psk)
        end
        local name = channel_name(s, i)
        out[i] = { name = name, key = key, hash = channel_hash(name, key) }
    end
    return out
end

-- The radio's numbers for setup `s`: frequency (Hz), sf, bw, cr, power, and
-- the preset it runs.
function tuning(s)
    local m, preset = modem(s.region, s.preset)
    local name = s.channels[1].name ~= "" and s.channels[1].name or m.title
    return frequency(s.region, name, m.bw, s.slot), m.sf, m.bw, m.cr, power(s.region), preset
end

-- -- the channel URL ---------------------------------------------------------------------

local function channel_set(f, warn)
    local out = {}
    for _, cb in ipairs(f[1] or {}) do
        local c = pb_fields(cb)
        if c == nil then
            return nil
        end
        local name = pb_get(c, 3, "")
        if pb_get(c, 8, 0) ~= 0 then
            warn[#warn + 1] = (name ~= "" and name or "the primary") ..
                ": authenticated channel encryption, which Moy Mesh does not speak"
        end
        out[#out + 1] = { name = clip(name, CHANNEL_NAME_MAX), psk = b64(pb_get(c, 2, "")) }
    end
    return out
end

-- The preview of channel URL `url` (above), or nil and why.
function url_parse(url)
    if type(url) ~= "string" then
        return nil, "no link"
    end
    url = url:match("^%s*(.-)%s*$")
    local path, frag = url:match("^[%a]*:?/*[Ww]*%.?meshtastic%.org(/[^#]*)#(.*)$")
    if path == nil or not (path:match("^/[ed]/") or path:match("^/[ed]$")) then
        return nil, "not a Meshtastic channel link"
    end
    local b = b64_bytes(frag)
    local f = b and pb_fields(b)
    if f == nil then
        return nil, "the link does not decode"
    end
    local warn = {}
    local cs = channel_set(f, warn)
    if cs == nil or #cs == 0 then
        return nil, "the link holds no channel"
    end
    if #cs > CHANNELS_MAX then
        warn[#warn + 1] = "only the first " .. CHANNELS_MAX .. " channels"
    end
    local lora = pb_fields(pb_get(f, 2, "")) or {}
    local preset = PRESET_OF[pb_get(lora, 2, 0)]
    if preset == nil then
        warn[#warn + 1] = "a modem preset Moy Mesh does not know: LongFast instead"
        preset = PRESET
    end
    if pb_get(lora, 1, 0) == 0 and (pb_get(lora, 3, 0) ~= 0 or pb_get(lora, 4, 0) ~= 0) then
        warn[#warn + 1] = "a custom modem: the preset instead"
    end
    local code = pb_get(lora, 7, 0)
    local region = REGION_OF[code]
    if region == nil and code ~= 0 then
        warn[#warn + 1] = "a region Moy Mesh does not offer"
    end
    if pb_get(lora, 14, nil) ~= nil then
        warn[#warn + 1] = "a fixed frequency: the slot instead"
    end
    return {
        channels = cs, region = region, preset = preset,
        slot = pb_get(lora, 11, 0), hops = int_in(pb_get(lora, 8, 0), 1, 7, HOPS),
        add = url:find("?add=true", 1, true) ~= nil, warnings = warn,
    }
end

-- The setup preview `p` makes of setup `cur` (this one where nil): a link's
-- channels and radio replace this setup's, an "add" link's channels join
-- it where their names are new.
function setup_merge(p, cur)
    cur = cur or setup()
    if p.add then
        local s = setup_check(cur)
        local have = {}
        for i = 1, #s.channels do
            have[channel_name(s, i)] = true
        end
        for _, c in ipairs(p.channels) do
            if c.name ~= "" and not have[c.name] and #s.channels < CHANNELS_MAX then
                s.channels[#s.channels + 1] = c
                have[c.name] = true
            end
        end
        return setup_check(s)
    end
    return setup_check({
        region = p.region or cur.region, preset = p.preset, slot = p.slot,
        hops = p.hops, channels = p.channels,
    })
end

function setup_apply(p)
    return setup_save(setup_merge(p))
end

-- Setup `s`'s channel URL: its channels and its LoRa config (the preset,
-- the region, the hop limit, transmitting on, the slot).
function url_make(s)
    s = setup_check(s)
    local cs = {}
    for i = 1, #s.channels do
        local c = s.channels[i]
        local body = pb_bytes(2, b64_bytes(c.psk)) .. pb_bytes(3, c.name)
        cs[#cs + 1] = varint(10) .. varint(#body) .. body
    end
    local lora = pb_varint(1, true) .. pb_varint(2, MODEMS[s.preset].code)
        .. pb_varint(7, BANDS[s.region][4]) .. pb_varint(8, s.hops) .. pb_varint(9, true)
        .. pb_varint(11, s.slot)
    return "https://meshtastic.org/e/#" .. b64(table.concat(cs) .. pb_bytes(2, lora), true)
end
