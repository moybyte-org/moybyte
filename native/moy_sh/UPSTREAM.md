# dash — vendored, patched

`dash/` is [dash](https://git.kernel.org/pub/scm/utils/dash/dash.git), the
Debian Almquist shell, at a pinned commit, with Moybyte's changes applied from
`patches/` and dash's build-time generators run over the result. It is
BSD-3-Clause (`dash/COPYING`); BusyBox ash, the other candidate, is GPLv2 and
cannot enter the firmware (`docs/licensing_v1.md`). The owner chose dash on
2026-11-03 (`docs/os_2026-11/system.md` §1).

**Which commit, which patches and the hash of every copied file are in
[`dash_vendor.json`](dash_vendor.json)**, written by `make vendor-dash` and
checked by `tests/test_dash_vendor.py`. That file is the authority; this page
is the explanation. `dash_pin.h` carries the commit into the C.

## What is copied, and what is not

`tools/vendor_dash.py`'s `FILES` is the list: the parser, the evaluator,
expansion, redirection, variables, jobs, traps, the built-ins (`printf` and
`test` from `bltin/` among them) and the generators' inputs. Left behind: the
line editor (the face edits the line), mail checking, the debug tracer,
`times`, the man pages and autotools. `mksignames.c` is GPL and is not copied;
the signal names are `moy_sh_sys.c`'s.

The generators (`mktokens`, `mkbuiltins`, `mknodes`, `mksyntax`, `mkinit`) are
ported to Python in `tools/vendor_dash.py`, so the copy needs no host build of
dash's helpers, and the generated files (`token.h`, `builtins.c`, `nodes.c`,
`syntax.c`, `init.c` and their headers) are made from the PATCHED sources.

## Changing dash

Never edit `dash/`: the next re-vendor reverts it, and the test fails first.
A change is a change to `patches/moybyte.patch` (a `git diff` over upstream's
`src/`, its header saying what it changes and why), then:

```sh
git clone https://git.kernel.org/pub/scm/utils/dash/dash.git .build/dash
make vendor-dash                          # .build/dash at the pin
make vendor-dash DASH=/path/to/clone COMMIT=<sha>
```

Moving the pin is the same command with a new `COMMIT` and the patch rebased
onto it; `tests/test_dash_vendor.py`'s last check rebuilds the copy from any
clone it finds (`.build/dash` or `$MOYBYTE_DASH`).
