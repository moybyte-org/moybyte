;; Stuck Wasm: a compiled cart whose third frame never ends, for the runaway
;; watch (native/moy_play/moy_play.h). Its loop calls an import, `btn`, every
;; time round, which is where a terminated instance stops; a loop that called
;; none could only be ended by the task watchdog's reset.
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed.
(module
  (import "moy" "btn" (func $btn (param i32 i32) (result i32)))
  (import "moy" "cls" (func $cls (param i32)))
  (memory (export "memory") 1 1)
  (global $n (mut i32) (i32.const 0))
  (func (export "_init"))
  (func (export "_update") (param $dt f32)
    (global.set $n (i32.add (global.get $n) (i32.const 1)))
    (if (i32.gt_s (global.get $n) (i32.const 2))
      (then (loop $ever (drop (call $btn (i32.const 0) (i32.const 0))) (br $ever)))))
  (func (export "_draw") (call $cls (i32.const 1))))
