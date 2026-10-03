;; Huge Wasm: a compiled cart whose memory no console board has -- 640 pages,
;; 40 MB, past the 32 MB of PSRAM on the biggest board (the P4s).
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed. The cart is well formed and would
;; run anywhere the memory existed: every console refuses it before it loads
;; with the fit notice, and the host refuses it over its configured limit
;; (runtime/wasm_host.py). It clears the screen, so a console that ever did
;; load it would show a plain frame.
(module
  (import "moy" "cls" (func $cls (param i32)))

  (memory (export "memory") 640 640)

  (func (export "_init"))

  (func (export "_update") (param $dt f32))

  (func (export "_draw") (call $cls (i32.const 1))))
