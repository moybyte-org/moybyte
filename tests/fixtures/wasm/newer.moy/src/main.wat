;; Newer Wasm: a compiled cart built against a newer console's import table.
;; It imports `later`, a name no console's table has, beside `cls`, which
;; every one has -- what a cart using a verb a later moy core adds looks like
;; to a console built before it.
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed. Every console refuses it before it
;; loads, with the notice naming `later` (runtime/player.py's NEWER_TITLE),
;; never with a load error or a trap when the cart calls it. A console that
;; ever did run it would show a plain frame and never reach the call.
(module
  (import "moy" "cls" (func $cls (param i32)))
  (import "moy" "later" (func $later (param i32) (result i32)))

  (memory (export "memory") 1 1)

  (func (export "_init"))

  (func (export "_update") (param $dt f32))

  (func (export "_draw")
    (call $cls (i32.const 1))
    (if (i32.const 0) (then (drop (call $later (i32.const 0)))))))
