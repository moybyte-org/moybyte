"""List a .wasm's imports -- the host has to provide every one of them."""
import sys


def leb(b, i):
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7f) << s
        s += 7
        if not c & 0x80:
            return r, i


def main(path):
    b = open(path, "rb").read()
    assert b[:4] == b"\0asm"
    i = 8
    while i < len(b):
        sid = b[i]
        size, i = leb(b, i + 1)
        if sid == 2:
            n, j = leb(b, i)
            for _ in range(n):
                ml, j = leb(b, j)
                m = b[j:j + ml].decode()
                j += ml
                nl, j = leb(b, j)
                nm = b[j:j + nl].decode()
                j += nl
                kind = b[j]
                j += 1
                if kind == 0:
                    _, j = leb(b, j)
                    print("  import %s.%s" % (m, nm))
                elif kind == 1:
                    j += 1
                    _, j = leb(b, j)
                    j += 2
                elif kind == 2:
                    fl = b[j]
                    j += 1
                    _, j = leb(b, j)
                    if fl & 1:
                        _, j = leb(b, j)
                else:
                    j += 2
        i += size


if __name__ == "__main__":
    main(sys.argv[1])
