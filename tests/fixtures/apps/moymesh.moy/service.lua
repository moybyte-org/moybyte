-- Moy Mesh's service: text messages on the LoRa radio, in the background.
--
-- It holds the radio by handle (moybyte.dev_open("lora", ...)) on
-- Meshtastic's default channel, the LongFast preset in the region the app's
-- prefs name (`region`, EU_868 until one is chosen), sleeps until the radio
-- posts it "lora", and keeps the last KEEP text messages it heard or sent in
-- the app's prefs (`log`), telling the app `heard` after each and at its
-- start, when its node's name (`node`, "!" and eight hex digits) is there to
-- read. The app sends `send(text)`, which goes out on the default channel,
-- and `retune()` after it changes the region. It answers no one and relays
-- nothing: a Meshtastic CLIENT_MUTE that hears the channel and talks on it.
-- A Lua service runs on a task of its own, so it keeps listening through a
-- game that stops the VM.
-- The app's id and its regions are mesh.lua's, which runs ahead of this
-- script here as it does ahead of main.lua in the window.
--
-- THE PROTOCOL, from the Meshtastic firmware (src/mesh/RadioInterface.*,
-- Channels.cpp, CryptoEngine.cpp) and its protobufs (mesh.proto,
-- portnums.proto):
--
--   the air      LongFast: SF11, 250 kHz, 4/5, sync word 0x2b, preamble 16,
--                explicit header, CRC on. The frequency is the region's slot
--                for the channel's name: the djb2 hash of "LongFast" modulo
--                the slots the region holds (its span over the bandwidth),
--                the slot's centre (EU_868: 869.525 MHz; US: 906.875 MHz).
--   a packet     a 16-byte header, little-endian: to, from, id (u32 each),
--                flags (hop limit in bits 0-2, want-ack 0x08, via MQTT 0x10,
--                hop start in bits 5-7), the channel hash, the next hop's and
--                the relay's last byte; then the payload.
--   a channel    its key the PSK, a one-byte PSK an index into the well-known
--                key ("AQ==", 1, is the key itself; n bumps its last byte by
--                n - 1); its hash the XOR of the name's bytes and the key's.
--   encryption   AES-CTR under the channel's key (AES-128 or -256), the
--                counter block the packet id as a little-endian u64, the
--                sender as a u32, then a zero 32-bit counter.
--   a direct message to a node's key (PKI, channel hash 0): AES-CCM under
--                SHA-256 of the X25519 secret, a 13-byte nonce (the packet
--                id, the 4-byte extra nonce the payload ends with, the
--                sender), the ciphertext, an 8-byte tag, the extra nonce.
--   the payload  the Data protobuf: portnum (field 1, varint; 1 is
--                TEXT_MESSAGE_APP) and payload (field 2, bytes), its other
--                fields read past.
--
-- A Lua integer is 32 bits on the consoles (LUA_32BITS): a u32 above
-- 0x7fffffff is negative here, packs and prints as the same 32 bits, and
-- compares unsigned through math.ult.

BROADCAST = 0xFFFFFFFF
TEXT = 1                                -- portnums.proto: TEXT_MESSAGE_APP
KEEP = 30                               -- messages the log keeps
TEXT_MAX = 200                          -- bytes a message carries
HOPS = 3

-- LongFast: bandwidth (Hz), spreading factor, coding rate (4/n).
PRESET = "LongFast"
BW, SF, CR = 250000, 11, 5
SYNC, PREAMBLE = 0x2B, 16
HEADER = 16

-- The well-known key one-byte PSKs index (Channels.h, defaultpsk).
DEFAULT_KEY = "\xd4\xf1\xbb\x3a\x20\x29\x07\x59\xf0\xbc\xff\xab\xcf\x4e\x69\x01"

-- The radio's own power ceiling (dBm); a region's own limit is its band's
-- third field (BANDS, mesh.lua).
POWER_MAX = 22

local pack, unpack, byte, char, fmt = string.pack, string.unpack, string.byte, string.char,
    string.format

-- -- the codec ----------------------------------------------------------------------

-- `h` read as an unsigned 32-bit number, modulo `m` (a small positive number).
local function umod(h, m)
    return ((h >> 1) % m * 2 + (h & 1)) % m
end

function djb2(text)
    local h = 5381
    for i = 1, #text do
        h = (h * 33 + byte(text, i)) & 0xFFFFFFFF
    end
    return h
end

-- The centre (Hz) of the slot `name` hashes to in `region`.
function frequency(region, name, bw)
    name, bw = name or PRESET, bw or BW
    local r = BANDS[region]
    local slots = (r[2] - r[1]) // bw
    return r[1] + bw // 2 + umod(djb2(name), slots) * bw
end

function power(region)
    return math.min(BANDS[region][3], POWER_MAX)
end

-- A channel's AES key from its PSK: a one-byte PSK indexes the well-known
-- key (0: none), a short one is padded to 16 bytes.
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

function channel_hash(name, key)
    return xor(name) ~ xor(key)
end

function header(to, frm, pid, flags, channel, next_hop, relay)
    return pack("<I4I4I4BBBB", to, frm, pid, flags, channel, next_hop or 0, relay or 0)
end

-- to, from, id, flags, channel, next_hop, relay; nil for a packet shorter
-- than the header.
function parse_header(packet)
    if #packet < HEADER then
        return nil
    end
    local to, frm, pid, flags, ch, nh, relay = unpack("<I4I4I4BBBB", packet)
    return to, frm, pid, flags, ch, nh, relay
end

function flags(hop_limit, hop_start, want_ack)
    hop_limit, hop_start = hop_limit or HOPS, hop_start or HOPS
    return (hop_limit & 7) | (want_ack and 0x08 or 0) | ((hop_start & 7) << 5)
end

function counter_block(pid, frm)
    return pack("<I4I4I4I4", pid, 0, frm, 0)
end

-- The channel's AES-CTR, both ways; the bytes as they are for no key.
function crypt(key, pid, frm, data)
    if key == "" then
        return data
    end
    return moybyte.aes_ctr(key, counter_block(pid, frm) .. data)
end

local function varint(n)
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
local function read_varint(b, at)
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

function data_encode(portnum, payload)
    return "\x08" .. varint(portnum) .. "\x12" .. varint(#payload) .. payload
end

-- portnum, payload of a Data protobuf; nil where it does not parse.
function data_decode(b)
    local portnum, payload, at = nil, "", 0
    while at < #b do
        local tag
        tag, at = read_varint(b, at)
        if tag == nil then
            return nil
        end
        local field, kind = tag >> 3, tag & 7
        if kind == 0 then
            local v
            v, at = read_varint(b, at)
            if v == nil then
                return nil
            end
            if field == 1 then
                portnum = v
            end
        elseif kind == 2 then
            local n
            n, at = read_varint(b, at)
            if n == nil or n < 0 or at + n > #b then
                return nil
            end
            if field == 2 then
                payload = b:sub(at + 1, at + n)
            end
            at = at + n
        elseif kind == 5 then
            at = at + 4
        elseif kind == 1 then
            at = at + 8
        else
            return nil
        end
    end
    if portnum == nil or at ~= #b then
        return nil
    end
    return portnum, payload
end

-- `text` cut to TEXT_MAX bytes, whole characters only.
local function clip(text)
    if #text <= TEXT_MAX then
        return text
    end
    local cut = TEXT_MAX
    while cut > 0 and byte(text, cut + 1) & 0xC0 == 0x80 do
        cut = cut - 1
    end
    return text:sub(1, cut)
end

-- A text message on a channel, as the radio sends it.
function encode_text(text, frm, pid, key, chash, to)
    local body = data_encode(TEXT, clip(text))
    return header(to or BROADCAST, frm, pid, flags(), chash) .. crypt(key, pid, frm, body)
end

-- A channel packet's header (a table of its seven fields), portnum and
-- payload; nil where it is another channel's, too short, or does not decode.
function decode(packet, key, chash)
    local h = { parse_header(packet) }
    if h[1] == nil or h[5] ~= chash then
        return nil
    end
    local portnum, payload = data_decode(crypt(key, h[3], h[2], packet:sub(HEADER + 1)))
    if portnum == nil then
        return nil
    end
    return h, portnum, payload
end

-- A direct message's Data protobuf to the node of X25519 `secret` from the
-- node of public key `peer`; nil where it does not open.
function pki_open(secret, peer, packet)
    local to, frm, pid = parse_header(packet)
    local body = packet:sub(HEADER + 1)
    if to == nil or #body < 12 then
        return nil
    end
    local key = moybyte.sha256(moybyte.x25519(secret, peer))
    local nonce = pack("<I4", pid) .. body:sub(-4) .. pack("<I4", frm) .. "\0"
    return moybyte.aes_ccm_open(key, nonce .. body:sub(1, -5), 8)
end

-- Whether `s` is UTF-8 a text may hold (no overlong form, no surrogate).
function utf8_ok(s)
    local i, n = 1, #s
    while i <= n do
        local c = byte(s, i)
        local k, lo, hi = 0, 0x80, 0xBF
        if c < 0x80 then
            k = 0
        elseif c >= 0xC2 and c <= 0xDF then
            k = 1
        elseif c >= 0xE0 and c <= 0xEF then
            k = 2
            lo = c == 0xE0 and 0xA0 or 0x80
            hi = c == 0xED and 0x9F or 0xBF
        elseif c >= 0xF0 and c <= 0xF4 then
            k = 3
            lo = c == 0xF0 and 0x90 or 0x80
            hi = c == 0xF4 and 0x8F or 0xBF
        else
            return false
        end
        if i + k > n then
            return false
        end
        for j = i + 1, i + k do
            local d = byte(s, j)
            if d < lo or d > hi then
                return false
            end
            lo, hi = 0x80, 0xBF
        end
        i = i + k + 1
    end
    return true
end

function node_name(n)
    return fmt("!%08x", n)
end

-- Whether `n` is a node number a node may take (4 to 0xfffffffe).
function node_ok(n)
    return math.ult(3, n) and n ~= BROADCAST
end

-- -- the service --------------------------------------------------------------------

local S = { node = 0, key = "", hash = 0, seen = {}, h = nil }
local SEEN = 32

local function draw_node()
    local n = prefs.get("node")
    if type(n) == "string" and n:match("^!%x%x%x%x%x%x%x%x$") then
        n = tonumber(n:sub(2), 16)
        if node_ok(n) then
            return n
        end
    end
    repeat
        n = unpack("<I4", moybyte.random(4))
    until node_ok(n)
    prefs.set("node", node_name(n))
    return n
end

local function region()
    local r = prefs.get("region")
    return BANDS[r] and r or REGION
end

local function tune()
    local r = region()
    local cfg = fmt("freq=%d sf=%d bw=%d cr=%d power=%d sync=%d preamble=%d listen=1",
                    frequency(r), SF, BW, CR, power(r), SYNC, PREAMBLE)
    local ok, why
    if S.h then
        ok, why = moybyte.dev_set(S.h, cfg)
    else
        ok, why = moybyte.dev_open("lora", cfg)
        S.h = ok
    end
    if not ok then
        moybyte.log(moybyte.WARNING, "the radio: " .. tostring(why))
    end
end

local function log(rec)
    local l = prefs.get("log") or {}
    l[#l + 1] = rec
    while #l > KEEP do
        table.remove(l, 1)
    end
    prefs.set("log", l)
    moybyte.send(ME, "heard")
end

local function fresh(frm, pid)
    local k = fmt("%d:%d", frm, pid)
    local seen = S.seen
    for i = 1, #seen do
        if seen[i] == k then
            return false
        end
    end
    seen[#seen + 1] = k
    if #seen > SEEN then
        table.remove(seen, 1)
    end
    return true
end

local function heard(packet, rssi, snr)
    local h, portnum, payload = decode(packet, S.key, S.hash)
    if h == nil or h[2] == S.node or not fresh(h[2], h[3]) or portnum ~= TEXT
        or not utf8_ok(payload) then
        return
    end
    log({ from = node_name(h[2]), text = payload, rssi = rssi, snr = snr })
end

local function radio(_, event)
    if event ~= 1 or not S.h then            -- moy_lora.h's MOY_LORA_EV_RX
        return
    end
    while true do
        local p = moybyte.dev_read(S.h, 256)
        if not p or #p == 0 then
            return
        end
        heard(p, moybyte.dev_get(S.h, "rssi"), moybyte.dev_get(S.h, "snr") / 4)
    end
end

local function send(_, text)
    text = clip(tostring(text or ""))
    if text == "" or not utf8_ok(text) then
        return
    end
    local pid = 0
    while pid == 0 do
        pid = unpack("<I4", moybyte.random(4))
    end
    local packet = encode_text(text, S.node, pid, S.key, S.hash)
    fresh(S.node, pid)
    if not S.h or not moybyte.dev_write(S.h, packet) then
        log({ from = "", text = text, failed = true })
        return
    end
    log({ from = "", text = text })
end

function moy_start()
    S.node = draw_node()
    S.key = channel_key("\1")
    S.hash = channel_hash(PRESET, S.key)
    moybyte.on("lora", radio)
    tune()
    moybyte.on("send", send)
    moybyte.on("retune", function() tune() end)
    moybyte.send(ME, "heard")
end

function moy_stop()
    if S.h then
        moybyte.dev_close(S.h)
        S.h = nil
    end
end
