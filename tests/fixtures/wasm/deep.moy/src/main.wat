;; Deep Wasm: a compiled cart whose second frame recurses without end, so the
;; engine's native stack check traps it ("native stack overflow") where a
;; real overflow would take the board down. Each level calls an import after
;; its recursive call, which keeps the call out of the tail and the recursion
;; out of any loop the compiler could turn it into.
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed.
(module
  (import "moy" "cls" (func $cls (param i32)))
  (memory (export "memory") 1 1)
  (global $n (mut i32) (i32.const 0))
  (func $down (param $d i32)
    (call $down (i32.add (local.get $d) (i32.const 1)))
    (call $cls (local.get $d)))
  (func (export "_init"))
  (func (export "_update") (param $dt f32)
    (global.set $n (i32.add (global.get $n) (i32.const 1)))
    (if (i32.gt_s (global.get $n) (i32.const 1))
      (then (call $down (i32.const 0)))))
  (func (export "_draw") (call $cls (i32.const 1))))
