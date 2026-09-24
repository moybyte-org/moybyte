"""Resolve an Xtensa XIP .aot's text relocations on the host.

An XIP (--xip) module is executed where it lies, and WAMR's esp-idf loader
refuses one that still carries text relocations ("cannot apply relocation to
text section for aot file generated with --enable-indirect-mode"). On Xtensa
wamrc leaves exactly one kind behind: R_XTENSA_SLOT0_OP, the PC-relative
`l32r` reaching into the module's own literal pool. The pool and the code sit
in the same section at fixed offsets, so those are constants -- this patches
them into the l32r immediates the way aot_reloc_xtensa.c would at load time
and drops the group, leaving nothing for the loader to write.

    aot_prelink.py in.aot out.aot
"""
import struct
import sys

R_XTENSA_SLOT0_OP = 20


def align(v, n):
    return (v + n - 1) & ~(n - 1)


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def main(src, dst):
    b = bytearray(open(src, "rb").read())
    assert b[:4] == b"\0aot"
    o = 8
    literal_off = code_off = code_size = literal_size = None
    reloc = None
    e_type = None
    while o + 8 <= len(b):
        o = align(o, 4)
        stype, ssize = u32(b, o), u32(b, o + 4)
        body = o + 8
        if stype == 0:
            e_type = struct.unpack_from("<H", b, body + 4)[0]
        elif stype == 2:
            literal_size = u32(b, body)
            literal_off = body + 4
            code_off = literal_off + literal_size
            code_size = ssize - 4 - literal_size
        elif stype == 5:
            reloc = (o, ssize)
        o = body + ssize
    assert e_type == 4, "not an XIP module (e_type=%r)" % e_type
    assert reloc and literal_off is not None
    assert literal_off % 4 == 0 and literal_size % 4 == 0

    sec_off, sec_size = reloc
    p = sec_off + 8
    nsym = u32(b, p)
    p += 4
    offs = [u32(b, p + 4 * i) for i in range(nsym)]
    p += 4 * nsym
    total = u32(b, p)
    p += 4
    strs_base = p
    syms = []
    for i in range(nsym):
        q = strs_base + offs[i]
        ln = struct.unpack_from("<H", b, q)[0]
        syms.append(b[q + 2:q + 2 + ln - 1].decode())
    groups_off = align(strs_base + total, 4)
    ngroups = u32(b, groups_off)
    p = groups_off + 4
    kept = []           # (name_index, [(off, add, type, sym)])
    patched = out_of_range = 0
    for g in range(ngroups):
        p = align(p, 4)
        name_idx = u32(b, p)
        p = align(p + 4, 4)
        cnt = u32(b, p)
        p += 4
        entries = []
        for r in range(cnt):
            p = align(p, 4)
            roff, radd, rtype, rsym = struct.unpack_from("<IIII", b, p)
            p += 16
            entries.append((roff, radd, rtype, rsym))
        name = syms[name_idx]
        if name != ".rela.text":
            kept.append((name_idx, entries))
            continue
        rest = []
        for roff, radd, rtype, rsym in entries:
            if rtype != R_XTENSA_SLOT0_OP or syms[rsym] != ".literal":
                rest.append((roff, radd, rtype, rsym))
                continue
            insn = code_off + roff
            target = literal_off + radd
            rel = target - ((insn + 3) & ~3)
            if rel < -256 * 1024 or rel > -4:
                out_of_range += 1
                continue
            imm16 = (rel >> 2) & 0xffff
            b[insn + 1] = imm16 & 0xff
            b[insn + 2] = (imm16 >> 8) & 0xff
            patched += 1
        if rest:
            kept.append((name_idx, rest))
    if out_of_range:
        raise SystemExit("%d l32r relocations cannot reach the literal pool "
                         "(code %d bytes, l32r range 256KB): try wamrc --size-level=0"
                         % (out_of_range, code_size))

    # rewrite the relocation section: same symbol table, the surviving groups
    new = bytearray(b[sec_off + 8:groups_off])
    new += struct.pack("<I", len(kept))
    for name_idx, entries in kept:
        while len(new) % 4:
            new.append(0)
        new += struct.pack("<II", name_idx, len(entries))
        for e in entries:
            while len(new) % 4:
                new.append(0)
            new += struct.pack("<IIII", *e)
    out = b[:sec_off] + struct.pack("<II", 5, len(new)) + new + b[sec_off + 8 + sec_size:]
    open(dst, "wb").write(out)
    print("%s -> %s: patched %d l32r, kept %d groups, %d -> %d bytes"
          % (src, dst, patched, len(kept), len(b), len(out)))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
