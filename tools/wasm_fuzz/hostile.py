"""Small hostile wasm modules for the fuzz seeds."""
import os, sys

def leb(n):
    out = bytearray()
    while True:
        b = n & 0x7f; n >>= 7
        if n: out.append(b | 0x80)
        else: out.append(b); return bytes(out)

def sleb(n):
    out = bytearray()
    while True:
        b = n & 0x7f; n >>= 7
        if (n == 0 and not b & 0x40) or (n == -1 and b & 0x40):
            out.append(b); return bytes(out)
        out.append(b | 0x80)

def sec(i, body):
    return bytes([i]) + leb(len(body)) + body

def vec(items):
    return leb(len(items)) + b"".join(items)

HDR = b"\0asm\1\0\0\0"
T_void = b"\x60\x00\x00"
T_i32_i32 = b"\x60\x01\x7f\x01\x7f"

def func_module(body, locals_=b"\x00", types=(T_void,), mem=None, exports=True):
    m = HDR + sec(1, vec(list(types))) + sec(3, vec([leb(0)]))
    if mem is not None:
        m += sec(5, vec([mem]))
    if exports:
        m += sec(7, vec([leb(len(n)) + n + b"\x00" + leb(0) for n in (b"_update",)]))
    code = locals_ + body + b"\x0b"
    m += sec(10, vec([leb(len(code)) + code]))
    return m

out = {}
# huge section counts: a type vector claiming 2^32-1 entries
out["huge_types"] = HDR + sec(1, leb(0xffffffff) + T_void)
out["huge_funcs"] = HDR + sec(1, vec([T_void])) + sec(3, leb(0x7fffffff))
out["huge_locals"] = func_module(b"", locals_=vec([leb(0xffffffff) + b"\x7f"]))
out["many_locals"] = func_module(b"", locals_=vec([leb(50000) + b"\x7f", leb(50000) + b"\x7e"]))
# overlapping data segments, and one ending exactly at the end of memory
mem1 = b"\x00" + leb(1)
data = vec([b"\x00" + b"\x41" + sleb(0) + b"\x0b" + vec([b"A"] * 100),
            b"\x00" + b"\x41" + sleb(50) + b"\x0b" + vec([b"B"] * 100),
            b"\x00" + b"\x41" + sleb(65535) + b"\x0b" + vec([b"C"]),
            b"\x00" + b"\x41" + sleb(65536) + b"\x0b" + vec([])])
out["overlap_data"] = HDR + sec(1, vec([T_void])) + sec(3, vec([leb(0)])) + sec(5, vec([mem1])) + sec(7, vec([leb(7) + b"_update\x00\x00"])) + sec(10, vec([b"\x02\x00\x0b"])) + sec(11, data)
out["data_past_end"] = HDR + sec(5, vec([mem1])) + sec(11, vec([b"\x00\x41" + sleb(65535) + b"\x0b" + vec([b"x", b"y"])]))
out["data_neg_offset"] = HDR + sec(5, vec([mem1])) + sec(11, vec([b"\x00\x41" + sleb(-1) + b"\x0b" + vec([b"x"])]))
# deep blocks
depth = 20000
out["deep_blocks"] = func_module(b"\x02\x40" * depth + b"\x0b" * depth)
out["deep_loops_br"] = func_module(b"\x03\x40" * 2000 + b"\x0c" + leb(1999) + b"\x0b" * 2000)
out["deep_if"] = func_module(b"\x41\x01\x04\x40" * 5000 + b"\x0b" * 5000)
# giant memory and table limits
out["giant_memory"] = func_module(b"", mem=b"\x00" + leb(65536))
out["giant_memory_max"] = func_module(b"", mem=b"\x01" + leb(0) + leb(65536))
out["over_memory"] = func_module(b"", mem=b"\x01" + leb(65537) + leb(0xffffffff))
out["grow_memory"] = func_module(b"\x41" + sleb(65535) + b"\x40\x00\x1a\x41\x00\x28\x02\x00\x1a", mem=b"\x00" + leb(1))
out["giant_table"] = HDR + sec(4, vec([b"\x70\x00" + leb(0xffffffff)]))
out["giant_table_max"] = HDR + sec(4, vec([b"\x70\x01" + leb(10000000) + leb(0xffffffff)]))
# bad LEB128: overlong, unterminated, too many bytes
out["leb_overlong"] = HDR + bytes([1]) + b"\x80\x80\x80\x80\x80\x00" + T_void
out["leb_unterminated"] = HDR + bytes([1]) + b"\xff\xff\xff\xff"
out["leb_sec_len_past_end"] = HDR + bytes([1]) + leb(0x7ffffff0) + b"\x01"
out["leb_i32_const_overlong"] = func_module(b"\x41\xff\xff\xff\xff\x7f\x1a")
out["leb_i32_const_unterm"] = func_module(b"\x41\xff\xff\xff\xff\xff")
# memory access at the edges
out["load_end"] = func_module(b"\x41" + sleb(65532) + b"\x28\x02\x00\x1a\x41" + sleb(65533) + b"\x28\x02\x00\x1a", mem=b"\x00" + leb(1))
out["load_wrap"] = func_module(b"\x41" + sleb(-4) + b"\x28\x02" + leb(8) + b"\x1a", mem=b"\x00" + leb(1))
# bulk memory at the edges
out["fill_edge"] = func_module(b"\x41" + sleb(65535) + b"\x41\x00\x41\x02\xfc\x0b\x00", mem=b"\x00" + leb(1))
out["copy_edge"] = func_module(b"\x41\x00\x41" + sleb(65535) + b"\x41\x02\xfc\x0a\x00\x00", mem=b"\x00" + leb(1))
# recursion with no base case
out["recurse"] = HDR + sec(1, vec([T_void])) + sec(3, vec([leb(0)])) + sec(7, vec([leb(7) + b"_update\x00\x00"])) + sec(10, vec([b"\x04\x00\x10\x00\x0b"]))
# custom sections: many, huge-named, truncated
out["custom_many"] = HDR + b"".join(sec(0, leb(3) + b"abc" + b"x" * i) for i in range(200))
out["custom_name_past"] = HDR + sec(0, leb(1000) + b"abc")
out["custom_key"] = HDR + sec(0, leb(11) + b"moybyte.key" + b"moybyte-aot 1\nwasm " + b"0" * 64 + b"\n")
# imports with hostile signatures
imp = vec([leb(3) + b"moy" + leb(3) + b"cls" + b"\x00" + leb(0)])
out["import_sig"] = HDR + sec(1, vec([b"\x60" + vec([b"\x7f"] * 1000) + vec([b"\x7f"] * 1000)])) + sec(2, imp)
out["start_unreachable"] = HDR + sec(1, vec([T_void])) + sec(3, vec([leb(0)])) + sec(8, leb(0)) + sec(10, vec([b"\x03\x00\x00\x0b"]))
out["br_table_huge"] = func_module(b"\x02\x40\x41\x00\x0e" + leb(0xffffff) + b"\x00\x0b")

d = sys.argv[1]
os.makedirs(d, exist_ok=True)
for k, v in out.items():
    with open(os.path.join(d, "hostile-" + k + ".wasm"), "wb") as f:
        f.write(v)
print(len(out))
