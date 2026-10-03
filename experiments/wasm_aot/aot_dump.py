"""Dump a WAMR .aot's target info, text layout and relocation groups -- the
host-side view of what the device loader will see. Layouts transcribed from
core/iwasm/compilation/aot_emit_aot_file.c (WAMR 2.4.5).

    aot_dump.py file.aot [--relocs]
"""
import struct
import sys

SECTION = {0: "target_info", 1: "init_data", 2: "text", 3: "function", 4: "export",
           5: "relocation", 6: "signature", 100: "custom"}
E_TYPE = {1: "REL", 4: "XIP"}
XTENSA_RELOC = {1: "R_XTENSA_32", 20: "R_XTENSA_SLOT0_OP"}


def align(v, n):
    return (v + n - 1) & ~(n - 1)


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def main(path, show_relocs):
    b = open(path, "rb").read()
    assert b[:4] == b"\0aot", "not an aot file"
    print("aot version %d, %d bytes" % (u32(b, 4), len(b)))
    o = 8
    literal_size = code_size = 0
    while o + 8 <= len(b):
        o = align(o, 4)
        stype, ssize = u32(b, o), u32(b, o + 4)
        body = o + 8
        print("section %-12s size=%d" % (SECTION.get(stype, stype), ssize))
        if stype == 0:
            bin_type, abi, e_type, e_machine = struct.unpack_from("<HHHH", b, body)
            arch = b[body + 32:body + 48].split(b"\0")[0].decode()
            print("  bin_type=%d abi=%d e_type=%s e_machine=%d arch=%s"
                  % (bin_type, abi, E_TYPE.get(e_type, e_type), e_machine, arch))
        elif stype == 2:
            literal_size = u32(b, body)
            code_size = ssize - 4 - literal_size
            print("  literal_size=%d (mod 4 = %d) code_size=%d (loader: code = literal + %d)"
                  % (literal_size, literal_size % 4, code_size, literal_size))
        elif stype == 5:
            p = body
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
            p = align(strs_base + total, 4)
            ngroups = u32(b, p)
            p += 4
            for g in range(ngroups):
                p = align(p, 4)
                name_idx = u32(b, p)
                p = align(p + 4, 4)
                cnt = u32(b, p)
                p += 4
                name = syms[name_idx]
                target = {".rela.text": code_size, ".rela.literal": literal_size}.get(name)
                types = {}
                bad = 0
                for r in range(cnt):
                    p = align(p, 4)
                    roff, radd, rtype, rsym = struct.unpack_from("<IIII", b, p)
                    p += 16
                    types[rtype] = types.get(rtype, 0) + 1
                    size = 4 if rtype == 1 else 3
                    over = target is not None and roff + size > target
                    bad += over
                    if show_relocs or over:
                        print("    %s off=%d addend=%d type=%s sym=%s%s"
                              % (name, roff, radd, XTENSA_RELOC.get(rtype, rtype), syms[rsym],
                                 "   <-- PAST SECTION END (%d)" % target if over else ""))
                print("  group %-16s relocs=%d types=%s target_size=%s bad=%d"
                      % (name, cnt, {XTENSA_RELOC.get(k, k): v for k, v in types.items()},
                         target, bad))
        o = body + ssize


if __name__ == "__main__":
    main(sys.argv[1], "--relocs" in sys.argv)
