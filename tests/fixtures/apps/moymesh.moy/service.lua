-- Moy Mesh's service: a node of a Meshtastic mesh on the LoRa radio, in the
-- background.
--
-- It holds the radio by handle (moybyte.dev_open("lora", ...)) on the setup
-- the app's prefs hold (mesh.lua: the region, the modem preset, the
-- frequency slot, the hop limit and up to eight channels; LongFast in EU_868
-- until another is chosen), sleeps until the radio posts it "lora", and keeps
-- what it heard and sent in the app's prefs. It is a node of its own: a node
-- number, an X25519 key pair (the console's, KEY below) and a name it
-- announces (NodeInfo) at its start,
-- every three hours and to any node that asks, a table of the nodes it heard,
-- and direct messages to and from them under their keys (PKI). It answers
-- the acknowledgements a node asks for and relays nothing: a Meshtastic
-- CLIENT_MUTE. A Lua service runs on a task of its own, so it keeps
-- listening through a game that stops the VM.
-- The app's id, its regions and presets and the setup are mesh.lua's, which
-- runs ahead of this script here as it does ahead of main.lua in the window.
--
-- THE WINDOW'S SIDE (main.lua reads this).
--
--   The window sends the service:
--     send(text, to)   a text: `to` nil or a channel's index (0 the primary,
--                      1 to 7 a secondary) for that channel, "!xxxxxxxx" for
--                      a direct message to that node under its key. A node
--                      whose User came with no key (older firmware) is sent
--                      a legacy one under the channel it was last heard on
--                      (pki false). A node whose User this one has not heard
--                      is not sent to: the message is logged failed, why "no
--                      key", and the node is sent this one's NodeInfo asking
--                      for its own; nor is a node whose key changed until
--                      the change is accepted (why "key changed").
--     act(verb, ...)   what the window asks besides a text:
--       act("ask", node)      this node's NodeInfo sent to "!xxxxxxxx",
--                             asking for its own (its record's `asked` the
--                             time)
--       act("accept", node)   the key "!xxxxxxxx" announced in place of the
--                             one held (its record's `key_new`) is its key
--                             from now
--       act("delete", i, text)  this node's own message at log index i (from
--                             1) dropped, where its text is `text`
--       act("resend", i, text)  ... dropped and sent again, to the same
--                             channel or node
--       act("key", ci, bytes) channel `ci` (0 the primary) given a random key
--                             of 16 or 32 bytes; the setup saved and retuned
--       act("retire")         every channel message kept so far marked
--                             `old` = its channel's name, before a link
--                             replaces the channels (its thread read-only)
--     retune()         after it changed any of the prefs below: the radio
--                      retuned, the channels and names read again, and,
--                      where any changed, this node's NodeInfo announced.
--   The service sends the window:
--     heard()          the log changed (a message heard or sent, a direct
--                      message's state); also at its start
--     nodes()          the nodes table changed
--
--   The prefs it writes:
--     node        this node's number, "!" and eight hex digits
--     log         the messages, oldest first, at most KEEP of all threads
--                 (the fullest thread gives up its oldest first): each
--                 { from = "!xxxxxxxx" the sender, "" this node;
--                   text; t = when it was kept (mesh.lua's wall(), Unix
--                   seconds on every tier);
--                   ch = the channel's index, for a channel's message
--                   (a record with neither ch nor dm is the primary's);
--                   old = the name of the channel it was on, for one a
--                   link's channels replaced (act "retire");
--                   dm = "!xxxxxxxx" the other node, for a direct message
--                   either way; pki = true where it went under the nodes'
--                   keys, false for a legacy one under a channel's key;
--                   rssi (dBm), snr (dB), for a heard one;
--                   id = the packet's id, state = "sending", "sent",
--                   "delivered" (a direct message its node acknowledged) or
--                   "failed", why = the failure ("no key", "no ack", a
--                   Routing error's name), for this node's own;
--                   failed = true where it never left the radio }
--     nodes       the nodes heard, { ["!xxxxxxxx"] = { long, short (their
--                 names), hw (HardwareModel), role (Config.DeviceConfig.
--                 Role), key (their X25519 public key, 64 hex digits; a
--                 node with none takes only a legacy direct message),
--                 key_new (a key it announced since, which is not taken
--                 until the window accepts it), asked (when this node
--                 last asked it for its User), heard (wall() of the
--                 last packet), rssi, snr, hops (how
--                 many hops it came over, where the packet says), ch (the
--                 channel it was last heard on) } }, at most NODES_MAX, the
--                 longest unheard dropped first
--   The prefs it reads: mesh.lua's setup (region, preset, slot, hops,
--   channels), and
--     long_name   the name this node announces, up to 24 bytes; none or ""
--                 for the console's name (moybyte.name())
--     short_name  up to 4 bytes; none or "" for the long name's first four,
--                 or, where the long name is a board's (moybyte-...), the
--                 node number's last four hex digits
--
-- THE PROTOCOL, from the Meshtastic firmware 2.7.26 (src/mesh/
-- RadioInterface.cpp, Channels.cpp, CryptoEngine.cpp, Router.cpp,
-- ReliableRouter.cpp, MeshModule.cpp, modules/NodeInfoModule.cpp,
-- RoutingModule.cpp, NodeDB.cpp) and its protobufs (mesh.proto,
-- portnums.proto, config.proto, channel.proto, apponly.proto):
--
--   the air      the preset's SF, bandwidth and coding rate, sync word 0x2b,
--                preamble 16, explicit header, CRC on, on the slot the
--                setup names (mesh.lua's tuning).
--   a packet     a 16-byte header, little-endian: to, from, id (u32 each),
--                flags (hop limit in bits 0-2, want-ack 0x08, via MQTT 0x10,
--                hop start in bits 5-7), the channel hash, the next hop's and
--                the relay's last byte; then the payload.
--   a channel    its key and hash (mesh.lua); a packet is the channel's
--                whose hash it carries and under whose key it decodes.
--   encryption   AES-CTR under the channel's key (AES-128 or -256), the
--                counter block the packet id as a little-endian u64, the
--                sender as a u32, then a zero 32-bit counter.
--   a direct message to a node's key (PKI, channel hash 0): AES-CCM under
--                SHA-256 of the X25519 shared secret (CryptoEngine::
--                setDHPublicKey, then hash), a 13-byte nonce (the packet
--                id, the 4-byte extra nonce the payload ends with, the
--                sender, a zero), the ciphertext, an 8-byte tag, the extra
--                nonce. Text only: NodeInfo and routing go on the channel.
--   the payload  the Data protobuf: portnum (1), payload (2), want_response
--                (3), request_id (6, fixed32), reply_id (7, fixed32),
--                bitfield (9; bit 1 want_response), its other fields read
--                past. TEXT_MESSAGE_APP 1, NODEINFO_APP 4 (a User:
--                id, long_name, short_name, hw_model, is_licensed, role,
--                public_key), ROUTING_APP 5 (a Routing; error_reason 3).
--   an ack       a want-ack packet to this node is answered with a Routing
--                of error NONE whose request_id is its id, on the channel it
--                came on, flooded back over the hops it took plus two; a
--                direct message's ack itself wants one. A want-ack packet
--                to this node that does not open is answered
--                PKI_UNKNOWN_PUBKEY (a direct message from a node whose key
--                this one lacks) or NO_CHANNEL, on the primary.
--   reliability  this node's direct messages go with want-ack, again after
--                RETRY_TICKS without an answer, TRIES times in all, the same
--                id each time; the node's ack is "delivered", a NAK or the
--                last try's silence "failed".
--   NodeInfo     this node's User, broadcast at its start and on a retune
--                that changed it (asking for replies), every three hours
--                (default_node_info_broadcast_secs) without; sent to a node
--                that asks (want_response), to a node heard with no User yet
--                (asking for its own), to a node the window asks of, and to
--                a node whose NAK says it lacks this one's key. A node's
--                first key is the one kept: a NodeInfo with another is
--                dropped, as NodeDB::updateUser drops it, its key kept as
--                key_new for the window to accept or not. A direct message
--                under key_new still opens.
--
-- THE KEY PAIR is the console's (moybyte.key_make, key_public, key_shared):
-- the console keeps its secret where no app reads it and the settings carry
-- nothing of it, and answers this app the public key and, for a node's
-- public key, the X25519 shared secret. A console without those calls runs
-- no Moy Mesh service: the service stops at its start, saying why.
--
-- A Lua integer is 32 bits on the consoles (LUA_32BITS): a u32 above
-- 0x7fffffff is negative here, packs and prints as the same 32 bits, and
-- compares unsigned through math.ult.

BROADCAST = 0xFFFFFFFF
TEXT, NODEINFO, ROUTING = 1, 4, 5       -- portnums.proto
KEEP = 30                               -- messages the log keeps
TEXT_MAX = 200                          -- bytes a message carries
LONG_MAX = 24                           -- mesh.proto: the long name newer devices keep
LONG_READ, SHORT_MAX = 39, 4            -- mesh.options: User names' max_size less 1
KEY = "node"                            -- this node's key pair, by the console's name
NODES_MAX = 40                          -- nodes the table keeps
HW_MODEL = 255                          -- mesh.proto HardwareModel PRIVATE_HW
ROLE = 1                                -- config.proto Role CLIENT_MUTE
SYNC, PREAMBLE = 0x2B, 16
HEADER = 16
PKI_OVERHEAD = 12                       -- the tag and the extra nonce

-- The service's clock: a tick every TICK_MS.
TICK_MS = 5000
RETRY_TICKS = 6                         -- a direct message's wait for its ack
TRIES = 3                               -- ... and its transmissions
OUT_TICKS = 6                           -- a packet's wait for a radio that is busy
QUIET_TICKS = 120                       -- a node is sent this one's NodeInfo once in this
SAVE_TICKS = 12                         -- the nodes' signal is saved once in this
ANNOUNCE_TICKS = 3 * 3600 * 1000 // TICK_MS

-- mesh.proto Routing.Error, by number.
ERRORS = {
    [1] = "NO_ROUTE", [2] = "GOT_NAK", [3] = "TIMEOUT", [4] = "NO_INTERFACE",
    [5] = "MAX_RETRANSMIT", [6] = "NO_CHANNEL", [7] = "TOO_LARGE", [8] = "NO_RESPONSE",
    [9] = "DUTY_CYCLE_LIMIT", [32] = "BAD_REQUEST", [33] = "NOT_AUTHORIZED",
    [34] = "PKI_FAILED", [35] = "PKI_UNKNOWN_PUBKEY", [36] = "ADMIN_BAD_SESSION_KEY",
    [37] = "ADMIN_PUBLIC_KEY_UNAUTHORIZED", [38] = "RATE_LIMIT_EXCEEDED",
    [39] = "PKI_SEND_FAIL_PUBLIC_KEY",
}
NO_CHANNEL, PKI_UNKNOWN_PUBKEY = 6, 35

local pack, unpack, byte, char, fmt = string.pack, string.unpack, string.byte, string.char,
    string.format

-- -- the codec ----------------------------------------------------------------------

function hex(s)
    return (s:gsub(".", function(c) return fmt("%02x", byte(c)) end))
end

-- The bytes 2n hex digits hold, nil for anything else.
function unhex(h)
    if type(h) ~= "string" or #h % 2 ~= 0 or h:find("[^%x]") then
        return nil
    end
    return (h:gsub("..", function(c) return char(tonumber(c, 16)) end))
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
    hop_limit, hop_start = hop_limit or HOPS, hop_start or hop_limit or HOPS
    return (hop_limit & 7) | (want_ack and 0x08 or 0) | ((hop_start & 7) << 5)
end

-- How many hops a packet with `flags` came over, nil where it does not say.
function hops_away(f)
    local limit, start = f & 7, f >> 5
    if start == 0 or limit > start then
        return nil
    end
    return start - limit
end

-- The hop limit an answer to a packet with `flags` leaves with, this node's
-- own being `hops` (RoutingModule::getHopLimitForResponse).
function answer_hops(f, hops)
    local used = hops_away(f)
    if used ~= nil then
        if used > hops then
            return used
        elseif used + 2 < hops then
            return used + 2
        end
    end
    return hops
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

-- A Data protobuf: `o` its want_response, request_id and reply_id.
function data_encode(portnum, payload, o)
    o = o or {}
    return pb_varint(1, portnum) .. "\x12" .. varint(#payload) .. payload
        .. pb_varint(3, o.want_response) .. pb_fixed32(6, o.request_id)
        .. pb_fixed32(7, o.reply_id)
end

-- portnum, payload and { want = whether it wants a response, request,
-- reply } of a Data protobuf; nil where it does not parse.
function data_decode(b)
    local f = pb_fields(b)
    if f == nil then
        return nil
    end
    local portnum, payload = pb_get(f, 1), pb_get(f, 2, "")
    if math.type(portnum) ~= "integer" or type(payload) ~= "string" then
        return nil
    end
    local bits, want = pb_get(f, 9, 0), pb_get(f, 3, 0)
    local function n(v)
        return math.type(v) == "integer" and v or 0
    end
    return portnum, payload, {
        want = n(want) ~= 0 or n(bits) & 2 ~= 0,
        request = n(pb_get(f, 6, 0)), reply = n(pb_get(f, 7, 0)),
    }
end

-- A text message on a channel, as the radio sends it.
function encode_text(text, frm, pid, key, chash, to, hops, relay)
    local body = data_encode(TEXT, clip(text, TEXT_MAX))
    return header(to or BROADCAST, frm, pid, flags(hops, hops), chash, 0, relay)
        .. crypt(key, pid, frm, body)
end

-- A channel packet's header (a table of its seven fields), portnum, payload
-- and Data fields (data_decode); nil where it is another channel's, too
-- short, or does not decode.
function decode(packet, key, chash)
    local h = { parse_header(packet) }
    if h[1] == nil or h[5] ~= chash then
        return nil
    end
    local portnum, payload, x = data_decode(crypt(key, h[3], h[2], packet:sub(HEADER + 1)))
    if portnum == nil or portnum == 0 then
        return nil
    end
    return h, portnum, payload, x
end

-- The AES-CCM key of two nodes' X25519 shared secret `shared`.
function pki_key(shared)
    return moybyte.sha256(shared)
end

local function pki_nonce(pid, extra, frm)
    return pack("<I4", pid) .. extra .. pack("<I4", frm) .. "\0"
end

-- A direct message's Data protobuf under the AES-CCM key `key` of the two
-- nodes (pki_key); nil where it does not open.
function pki_open(key, packet)
    local to, frm, pid = parse_header(packet)
    local body = packet:sub(HEADER + 1)
    if to == nil or #body <= PKI_OVERHEAD then
        return nil
    end
    return moybyte.aes_ccm_open(key, pki_nonce(pid, body:sub(-4), frm) .. body:sub(1, -5), 8)
end

-- A direct message: Data protobuf `plain` from node `frm` to node `to` under
-- the AES-CCM key `key` of the two (pki_key), its 4-byte extra nonce `extra`.
function pki_packet(key, to, frm, pid, f, plain, extra, relay)
    local sealed = moybyte.aes_ccm_seal(key, pki_nonce(pid, extra, frm) .. plain, 8)
    return header(to, frm, pid, f, 0, 0, relay) .. sealed .. extra
end

-- A User protobuf from { id, long, short, hw, role, key (32 bytes) }.
function user_encode(u)
    return pb_bytes(1, u.id) .. pb_bytes(2, u.long) .. pb_bytes(3, u.short)
        .. pb_varint(5, u.hw) .. pb_varint(7, u.role) .. pb_bytes(8, u.key)
end

-- { id, long, short, hw, role, key (32 bytes or nil), licensed } of a User
-- protobuf; nil where it does not parse.
function user_decode(b)
    local f = pb_fields(b)
    if f == nil then
        return nil
    end
    local function text(n, max)
        local s = pb_get(f, n, "")
        if type(s) ~= "string" or not utf8_ok(s) then
            return ""
        end
        return clip(s, max)
    end
    local function int(n)
        local v = pb_get(f, n, 0)
        return math.type(v) == "integer" and v or 0
    end
    local key = pb_get(f, 8, nil)
    return {
        id = text(1, 15), long = text(2, LONG_READ), short = text(3, SHORT_MAX),
        hw = int(5), role = int(7), licensed = int(6) ~= 0,
        key = type(key) == "string" and #key == 32 and key or nil,
    }
end

-- A Routing protobuf of error `err` (its error_reason, 0 NONE).
function routing_encode(err)
    return "\x18" .. varint(err)
end

-- A Routing protobuf's error_reason, 0 for none; nil where it does not parse.
function routing_decode(b)
    local f = pb_fields(b)
    if f == nil then
        return nil
    end
    local e = pb_get(f, 3, 0)
    return math.type(e) == "integer" and e or 0
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

-- The node number "!xxxxxxxx" names, nil for anything else.
function node_number(s)
    if type(s) ~= "string" or not s:match("^!%x%x%x%x%x%x%x%x$") then
        return nil
    end
    return tonumber(s:sub(2, 5), 16) << 16 | tonumber(s:sub(6), 16)
end

-- Whether `n` is a node number a node may take (4 to 0xfffffffe).
function node_ok(n)
    return math.ult(3, n) and n ~= BROADCAST
end

-- The short name a node of long name `long` and number `node` goes by when
-- none is set.
function short_default(long, node)
    if long == "" or long:sub(1, 7):lower() == "moybyte" then
        return fmt("%04x", node & 0xFFFF)
    end
    return clip(long, SHORT_MAX)
end

-- -- the service --------------------------------------------------------------------

local S = {
    node = 0, public = "", long = "", short = "", sig = "",
    setup = nil, chans = {}, hops = HOPS, h = nil,
    seen = {}, nodes = {}, dirty = false, saved = 0,
    out = {}, pending = {}, replied = {}, asked = {}, tick = 0, announced = 0,
}
local SEEN = 64

local function draw_node()
    local n = node_number(prefs.get("node"))
    if n and node_ok(n) then
        return n
    end
    repeat
        n = unpack("<I4", moybyte.random(4))
    until node_ok(n)
    prefs.set("node", node_name(n))
    return n
end

-- This node's X25519 public key: the console's pair KEY, made at the first
-- start.
local function public_key()
    if moybyte.key_make == nil then
        error("this console keeps no keys for apps (moybyte.key_make): update it", 0)
    end
    local public = unhex(moybyte.key_make(KEY))
    if public == nil or #public ~= 32 then
        error("the console made no key", 0)
    end
    return public
end

-- The AES-CCM key between this node and the node of public key `peer` (64
-- hex digits); nil for a key that is not one or of small order.
local function dm_key(peer)
    local shared = type(peer) == "string" and #peer == 64 and moybyte.key_shared(KEY, peer)
    shared = shared and unhex(shared)
    return shared and #shared == 32 and pki_key(shared) or nil
end

local function console_name()
    local ok, n = pcall(function() return moybyte.name() end)
    if ok and type(n) == "string" and n ~= "" and utf8_ok(n) then
        return n
    end
    return "Moy Mesh " .. fmt("%04x", S.node & 0xFFFF)
end

local function names()
    local long = prefs.get("long_name")
    if type(long) ~= "string" or long == "" or not utf8_ok(long) then
        long = console_name()
    end
    long = clip(long, LONG_MAX)
    local short = prefs.get("short_name")
    if type(short) ~= "string" or short == "" or not utf8_ok(short) then
        short = short_default(long, S.node)
    end
    return long, clip(short, SHORT_MAX)
end

local function me()
    return {
        id = node_name(S.node), long = S.long, short = S.short, hw = HW_MODEL, role = ROLE,
        key = S.public,
    }
end

local function new_id()
    local pid = 0
    while pid == 0 do
        pid = unpack("<I4", moybyte.random(4))
    end
    return pid
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

-- -- the log ------------------------------------------------------------------------

local function thread(rec)
    return rec.dm or ("ch" .. tostring(rec.ch or 0))
end

local function log(rec)
    rec.t = wall()
    local l = prefs.get("log") or {}
    l[#l + 1] = rec
    while #l > KEEP do
        local count, most, at = {}, 0, 1
        for i = 1, #l do
            local k = thread(l[i])
            count[k] = (count[k] or 0) + 1
        end
        for i = 1, #l do
            local c = count[thread(l[i])]
            if c > most then
                most, at = c, i
            end
        end
        table.remove(l, at)
    end
    prefs.set("log", l)
    moybyte.send(ME, "heard")
end

-- This node's own message `pid` given the fields of `upd`.
local function mark(pid, upd)
    local l = prefs.get("log") or {}
    for i = #l, 1, -1 do
        local r = l[i]
        if r.from == "" and r.id == pid then
            for k, v in pairs(upd) do
                r[k] = v
            end
            prefs.set("log", l)
            moybyte.send(ME, "heard")
            return
        end
    end
end

-- -- the nodes ----------------------------------------------------------------------

local function save_nodes()
    S.dirty, S.saved = false, S.tick
    prefs.set("nodes", S.nodes)
    moybyte.send(ME, "nodes")
end

-- Node `frm`'s record, and whether it is new.
local function node_of(frm)
    local id = node_name(frm)
    local n = S.nodes[id]
    local new = n == nil
    if new then
        n = {}
        S.nodes[id] = n
        local count, oldest, at = 0, nil, nil
        for k, v in pairs(S.nodes) do
            count = count + 1
            if k ~= id and (oldest == nil or (v.heard or 0) < oldest) then
                oldest, at = v.heard or 0, k
            end
        end
        if count > NODES_MAX and at then
            S.nodes[at] = nil
        end
    end
    return n, new
end

-- Node `frm` heard on channel `ci` with its signal: a new node saved at
-- once, a known one's signal once in SAVE_TICKS.
local function touch(frm, ci, rssi, snr, f)
    local n, new = node_of(frm)
    n.heard, n.rssi, n.snr, n.hops, n.ch = wall(), rssi, snr, hops_away(f), ci
    S.dirty = true
    if new then
        save_nodes()
    end
end

-- Node `frm`'s User: kept where its key is the one it gave first.
local function learn(frm, u)
    if u.key and u.key == S.public then
        moybyte.log(moybyte.WARNING, node_name(frm) .. " announces this node's own key")
        return false
    end
    local n = node_of(frm)
    if n.key and (u.key == nil or hex(u.key) ~= n.key) then
        moybyte.log(moybyte.WARNING, node_name(frm) .. " announces a key it did not have")
        if u.key and n.key_new ~= hex(u.key) then
            n.key_new = hex(u.key)
            save_nodes()
        end
        return false
    end
    n.long, n.short, n.hw, n.role = u.long, u.short, u.hw, u.role
    n.key = n.key or (u.key and hex(u.key))
    save_nodes()
    return true
end

-- -- the radio ----------------------------------------------------------------------

local function tune()
    local freq, sf, bw, cr, pw = tuning(S.setup)
    local cfg = fmt("freq=%d sf=%d bw=%d cr=%d power=%d sync=%d preamble=%d listen=1",
                    freq, sf, bw, cr, pw, SYNC, PREAMBLE)
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
    return cfg
end

-- The packets waiting for the radio, oldest first, each written once the
-- last one's transmission is done; `done(ok)` told whether it left.
local function flush()
    while S.out[1] do
        local o = S.out[1]
        local ok = S.h and moybyte.dev_write(S.h, o.p)
        if not ok and S.h and S.tick - o.tick < OUT_TICKS then
            return
        end
        table.remove(S.out, 1)
        if o.done then
            o.done(ok and true or false)
        end
        if ok then
            return
        end
    end
end

local function transmit(packet, done)
    S.out[#S.out + 1] = { p = packet, done = done, tick = S.tick }
    if #S.out == 1 then
        flush()
    end
end

-- Data `port`/`payload` to node `to` on channel index `ci` (0 the primary):
-- its packet id. `o`: want_ack, want_response, request_id, hops, done.
local function send_data(to, port, payload, ci, o)
    o = o or {}
    local c = S.chans[(ci or 0) + 1] or S.chans[1]
    local pid, hops = new_id(), o.hops or S.hops
    local body = data_encode(port, payload, o)
    local packet = header(to, S.node, pid, flags(hops, hops, o.want_ack), c.hash, 0,
                          S.node & 0xFF) .. crypt(c.key, pid, S.node, body)
    fresh(S.node, pid)
    transmit(packet, o.done)
    return pid
end

-- This node's NodeInfo to `to` on channel `ci`.
local function announce(to, want_response, ci, request_id, hops)
    send_data(to, NODEINFO, user_encode(me()), ci, {
        want_response = want_response, request_id = request_id, hops = hops,
    })
    if to == BROADCAST then
        S.announced = S.tick
    end
end

-- This node's NodeInfo to node `frm` once in QUIET_TICKS (`book` the record
-- of when it was last sent).
local function announce_to(book, frm, want_response, ci, request_id, hops)
    local last = book[frm]
    if last and S.tick - last < QUIET_TICKS then
        return
    end
    book[frm] = S.tick
    announce(frm, want_response, ci, request_id, hops)
end

local function ack(frm, pid, ci, f, err, want_ack)
    send_data(frm, ROUTING, routing_encode(err), ci, {
        request_id = pid, hops = answer_hops(f, S.hops), want_ack = want_ack,
    })
end

-- An answer to this node's own packet `request` from node `frm`: Routing
-- error `err` (0 an ack).
local function answered(frm, request, err)
    local p = S.pending[request]
    if p == nil then
        return
    end
    if err == 0 then
        if frm == p.to then
            S.pending[request] = nil
            mark(request, { state = "delivered" })
        end
        return
    end
    S.pending[request] = nil
    local why = ERRORS[err] or ("error " .. err)
    if err == PKI_UNKNOWN_PUBKEY then
        why = "the node lacks this one's key: NodeInfo sent, send again"
        announce(frm, false, 0)
    end
    mark(request, { state = "failed", why = why })
end

-- A direct message from node `frm` (pid, flags) under the nodes' keys or a
-- legacy one under channel `ci`'s, or a channel's text.
local function text(frm, payload, ci, rssi, snr, dm, pki)
    if not utf8_ok(payload) then
        return
    end
    if dm then
        log({ from = node_name(frm), dm = node_name(frm), text = payload, pki = pki,
              rssi = rssi, snr = snr })
    else
        log({ from = node_name(frm), ch = ci, text = payload, rssi = rssi, snr = snr })
    end
end

-- Packet `packet` decoded: the channel index it came on (0 for a direct
-- message under the nodes' keys), portnum, payload, Data fields and whether
-- it was under the nodes' keys; nil where it opens under none.
local function open(packet, h)
    if h[5] == 0 and h[1] == S.node then
        local n = S.nodes[node_name(h[2])]
        for _, k in ipairs({ n and n.key or false, n and n.key_new or false }) do
            local key = k and dm_key(k)
            local plain = key and pki_open(key, packet)
            local port, payload, x
            if plain then
                port, payload, x = data_decode(plain)
            end
            if port and port ~= 0 then
                return 0, port, payload, x, true
            end
        end
    end
    local body = packet:sub(HEADER + 1)
    for i, c in ipairs(S.chans) do
        if c.hash == h[5] then
            local port, payload, x = data_decode(crypt(c.key, h[3], h[2], body))
            if port and port ~= 0 then
                return i - 1, port, payload, x, false
            end
        end
    end
    return nil
end

local function heard(packet, rssi, snr)
    local h = { parse_header(packet) }
    if h[1] == nil or h[2] == S.node or not node_ok(h[2]) then
        return
    end
    local to, frm, pid, f = h[1], h[2], h[3], h[4]
    local mine, wants_ack = to == S.node, f & 0x08 ~= 0
    local ci, port, payload, x, pki = open(packet, h)
    if ci == nil then
        if mine and wants_ack and fresh(frm, pid) then
            local n = S.nodes[node_name(frm)]
            if h[5] == 0 and (n == nil or n.key == nil) then
                ack(frm, pid, 0, f, PKI_UNKNOWN_PUBKEY)
            else
                ack(frm, pid, 0, f, NO_CHANNEL)
            end
        end
        return
    end
    -- ReliableRouter::sniffReceived: every copy of a want-ack packet to this
    -- node is acked, so a sender that missed the ack stops trying.
    if mine and wants_ack then
        if port == TEXT then
            ack(frm, pid, ci, f, 0, true)
        elseif x.request == 0 and x.reply == 0 then
            ack(frm, pid, ci, f, 0)
        elseif hops_away(f) == 0 then
            ack(frm, pid, ci, f, 0, false)
        end
    end
    if not fresh(frm, pid) then
        return
    end
    touch(frm, ci, rssi, snr, f)
    local id = node_name(frm)
    if port == NODEINFO then
        local u = user_decode(payload)
        if u and learn(frm, u) and x.want and (mine or to == BROADCAST) then
            announce_to(S.replied, frm, false, ci, pid, answer_hops(f, S.hops))
        end
        return
    end
    if S.nodes[id].long == nil and (mine or to == BROADCAST) then
        announce_to(S.asked, frm, true, ci)
    end
    if port == TEXT and (mine or to == BROADCAST) then
        text(frm, payload, ci, rssi, snr, mine, pki)
    elseif port == ROUTING and mine and x.request ~= 0 then
        local err = routing_decode(payload)
        if err then
            answered(frm, x.request, err)
        end
    end
end

local function radio(_, event)
    if not S.h then
        return
    end
    if event == 2 then                      -- moy_lora.h's MOY_LORA_EV_TX_DONE
        flush()
        return
    end
    if event ~= 1 then                      -- ... MOY_LORA_EV_RX
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

-- -- what the window sends --------------------------------------------------------

local function dm(text, peer)
    local to = node_number(peer)
    local n = S.nodes[peer]
    if not (n and (n.key or n.long)) then
        log({ from = "", dm = peer, text = text, state = "failed", why = "no key" })
        announce_to(S.asked, to, true, n and n.ch or 0)
        return
    end
    if n.key_new then
        log({ from = "", dm = peer, text = text, state = "failed", why = "key changed" })
        return
    end
    local pid, packet = new_id(), nil
    if n.key then
        local key = dm_key(n.key)
        if key == nil then
            log({ from = "", dm = peer, text = text, state = "failed",
                  why = "its key is not a key" })
            return
        end
        packet = pki_packet(key, to, S.node, pid, flags(S.hops, S.hops, true),
                            data_encode(TEXT, text), moybyte.random(4), S.node & 0xFF)
    else
        local c = S.chans[(n.ch or 0) + 1] or S.chans[1]
        packet = header(to, S.node, pid, flags(S.hops, S.hops, true), c.hash, 0, S.node & 0xFF)
            .. crypt(c.key, pid, S.node, data_encode(TEXT, text))
    end
    fresh(S.node, pid)
    log({ from = "", dm = peer, text = text, id = pid, pki = n.key ~= nil, state = "sending" })
    local p = { packet = packet, to = to, tries = 1, due = nil }
    S.pending[pid] = p
    local function sent(ok)
        if S.pending[pid] ~= p then
            return
        end
        if not ok then
            S.pending[pid] = nil
            mark(pid, { state = "failed", why = "the radio", failed = true })
            return
        end
        p.due = S.tick + RETRY_TICKS
        if p.tries == 1 then
            mark(pid, { state = "sent" })
        end
    end
    p.sent = sent
    transmit(packet, sent)
end

local function send(_, text, to)
    text = clip(tostring(text or ""), TEXT_MAX)
    if text == "" or not utf8_ok(text) then
        return
    end
    if node_number(to) then
        if node_ok(node_number(to)) and node_number(to) ~= S.node then
            dm(text, to)
        end
        return
    end
    local ci = math.tointeger(to) or 0
    if ci < 0 or ci >= #S.chans then
        return
    end
    local c, pid = S.chans[ci + 1], new_id()
    fresh(S.node, pid)
    log({ from = "", ch = ci, text = text, id = pid })
    transmit(encode_text(text, S.node, pid, c.key, c.hash, BROADCAST, S.hops, S.node & 0xFF),
             function(ok)
                 if not ok then
                     mark(pid, { failed = true })
                 end
             end)
end

-- This node's own message at log index `i` whose text is `text`, taken out
-- of the log; nil where there is none.
local function unlog(i, text)
    local l = prefs.get("log") or {}
    local r = l[math.tointeger(i) or 0]
    if r == nil or r.from ~= "" or r.text ~= text then
        return nil
    end
    table.remove(l, i)
    prefs.set("log", l)
    moybyte.send(ME, "heard")
    return r
end

local retune

-- What the window asks besides a text (the header's act).
local function act(_, verb, a, b)
    local n, to = S.nodes[a], node_number(a)
    if verb == "ask" and to and node_ok(to) and to ~= S.node then
        announce(to, true, n and n.ch or 0)
        n = n or node_of(to)
        n.asked = wall()
        save_nodes()
    elseif verb == "accept" and n and n.key_new then
        n.key, n.key_new = n.key_new, nil
        save_nodes()
    elseif verb == "delete" then
        unlog(a, b)
    elseif verb == "resend" then
        local r = unlog(a, b)
        if r and r.old == nil then
            send(nil, r.text, r.dm or r.ch or 0)
        end
    elseif verb == "key" then
        local s, ci = setup(), (math.tointeger(a) or -1) + 1
        if s.channels[ci] and (b == 16 or b == 32) then
            s.channels[ci].psk = b64(moybyte.random(b))
            setup_save(s)
            if retune() then
                announce(BROADCAST, true, 0)
            end
        end
    elseif verb == "retire" then
        local l = prefs.get("log") or {}
        for _, r in ipairs(l) do
            if r.dm == nil and r.old == nil then
                local c = S.chans[(r.ch or 0) + 1]
                r.old = c and c.name or "?"
            end
        end
        prefs.set("log", l)
        moybyte.send(ME, "heard")
    end
end

-- The prefs read again: the radio retuned where its setup moved, and this
-- node announced where anything it announces or hears on moved.
function retune()
    S.setup = setup()
    S.chans = channel_keys(S.setup)
    S.hops = S.setup.hops
    S.long, S.short = names()
    local sig = { tune(), S.long, S.short }
    for _, c in ipairs(S.chans) do
        sig[#sig + 1] = c.name .. "/" .. c.hash .. "/" .. hex(c.key)
    end
    sig = table.concat(sig, "\n")
    local moved = sig ~= S.sig
    S.sig = sig
    return moved
end

local function tick()
    S.tick = S.tick + 1
    flush()
    local due = {}
    for pid, p in pairs(S.pending) do
        if p.due and p.due <= S.tick then
            due[#due + 1] = pid
        end
    end
    for _, pid in ipairs(due) do
        local p = S.pending[pid]
        if p.tries >= TRIES then
            S.pending[pid] = nil
            mark(pid, { state = "failed", why = "no ack" })
        else
            p.tries, p.due = p.tries + 1, nil
            transmit(p.packet, p.sent)
        end
    end
    if S.dirty and S.tick - S.saved >= SAVE_TICKS then
        save_nodes()
    end
    if S.tick - S.announced >= ANNOUNCE_TICKS then
        announce(BROADCAST, false, 0)
    end
end

function moy_start()
    S.node = draw_node()
    S.public = public_key()
    local nodes = prefs.get("nodes")
    S.nodes = type(nodes) == "table" and nodes or {}
    moybyte.on("lora", radio)
    retune()
    moybyte.on("send", send)
    moybyte.on("act", act)
    moybyte.on("retune", function()
        if retune() then
            announce(BROADCAST, true, 0)
        end
    end)
    moybyte.every(TICK_MS, tick)
    announce(BROADCAST, true, 0)
    moybyte.send(ME, "heard")
end

function moy_stop()
    if S.dirty then
        prefs.set("nodes", S.nodes)
    end
    if S.h then
        moybyte.dev_close(S.h)
        S.h = nil
    end
end
