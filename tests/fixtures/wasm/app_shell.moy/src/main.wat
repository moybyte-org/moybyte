;; App Shell Wasm: a compiled app that imports a row the Python console serves
;; (theme_set_variant: the look's switch, docs/kernel_appabi_2026-10.md
;; section 2.4). It never calls it; importing it is what keeps the VM up for
;; its runs, and the VM-free verdict names the import.
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed.
(module
  (import "moy" "cls" (func $cls (param i32)))
  (import "moybyte.app" "theme_gen" (func $theme_gen (result i32)))
  (import "moybyte.app" "theme_set_variant" (func $set_variant (param i32 i32) (result i32)))

  (memory (export "memory") 1 1)

  (func (export "_init"))

  (func (export "_update") (param $dt f32))

  (func (export "_draw")
    (call $cls (i32.and (call $theme_gen) (i32.const 15)))
    (if (i32.const 0) (then (drop (call $set_variant (i32.const 0) (i32.const 0)))))))
