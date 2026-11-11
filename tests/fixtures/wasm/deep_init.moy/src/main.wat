;; Deep Init: Deep Wasm's recursion in _init, so the cart traps before its
;; first frame -- in its own code, after its module loaded -- and the trap is
;; the cart's error, never a reason to run it again on the interpreter. Before
;; it recurses, _init adds one to the byte in its written file "inits", so a
;; test reads how many times _init ran.
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed.
(module
  (import "moy" "read" (func $read (param i32 i32 i32 i32 i32) (result i32)))
  (import "moy" "write" (func $write (param i32 i32 i32 i32) (result i32)))
  (import "moy" "cls" (func $cls (param i32)))
  (memory (export "memory") 1 1)
  (data (i32.const 1024) "inits")
  (func $down (param $d i32)
    (call $down (i32.add (local.get $d) (i32.const 1)))
    (call $cls (local.get $d)))
  (func (export "_init")
    (drop (call $read (i32.const 1024) (i32.const 5) (i32.const 0)
                      (i32.const 1040) (i32.const 1)))
    (i32.store8 (i32.const 1040)
                (i32.add (i32.load8_u (i32.const 1040)) (i32.const 1)))
    (drop (call $write (i32.const 1024) (i32.const 5) (i32.const 1040) (i32.const 1)))
    (call $down (i32.const 0)))
  (func (export "_update") (param $dt f32))
  (func (export "_draw") (call $cls (i32.const 1))))
