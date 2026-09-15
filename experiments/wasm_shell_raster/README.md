# wasm_shell_raster — what rasterizes the SHELL in the browser? (moycore stage 4)

Measured 2026-08-12, node + the real dist wasm VM + an emcc build of the
vendored libmoy, at 1024x600. The cart canvas was already moycore's; the shell
was the unpriced half, and the stage gate said two slow options meant no
completion path.

**Verdict: moy_gfx-in-wasm.** `runtime/canvas.py` interpreted in the shipped
wasm MicroPython priced MARGINAL (37 ms desk repaint, 49 ms editor, against
6.5 / 8.4 ms under CPython); the libmoy kernels compiled to wasm priced TRIVIAL
(0.04-0.1 ms per full repaint plus 0.42 ms indexed->RGBA present), some
500-1,000x apart. `docs/history/moycore_plan_2026-08.md` stage 4 carries the
numbers in context, and what shipped is `firmware/web_runner/`.

Nothing of the bench is kept -- it was a scratch harness whose emcc output sat
here with no source and no build script beside it. The measurement is the
durable part, and it is above.
