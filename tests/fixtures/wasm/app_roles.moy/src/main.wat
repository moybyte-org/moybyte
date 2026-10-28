;; App Roles Wasm: a compiled app reaching its roles through imports
;; (docs/kernel_appabi_2026-10.md section 4). It declares the moybyte.app
;; extension, whose module its role rows come from (moy-spec SPEC.md 16.2), and
;; holds prefs by its permission (the theme's reads are every app's).
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed. At _init it reads the run count it
;; kept in prefs ("runs", one digit as JSON), keeps it one higher, and reads the
;; live theme's first token and its generation; every _draw paints a band in
;; that token's colour. Every row it imports is served in C, so the run makes
;; no upcall of any class. The same module with "prefs" left out of its
;; permissions is refused at load, the import named.
(module
  (import "moy" "cls" (func $cls (param i32)))
  (import "moy" "rect" (func $rect (param i32 i32 i32 i32 i32)))
  (import "moybyte.app" "prefs_get" (func $prefs_get (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "prefs_set" (func $prefs_set (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "theme_token" (func $theme_token (param i32) (result i32)))
  (import "moybyte.app" "theme_gen" (func $theme_gen (result i32)))

  (memory (export "memory") 1 1)

  (data (i32.const 1024) "runs")

  (global $runs (mut i32) (i32.const 0))
  (global $tok (mut i32) (i32.const 0))
  (global $gen (mut i32) (i32.const 0))

  (func (export "_init")
    ;; the count kept last time: one digit, as JSON
    (if (i32.eq (call $prefs_get (i32.const 1024) (i32.const 4)
                                 (i32.const 2048) (i32.const 8))
                (i32.const 1))
      (then (global.set $runs (i32.sub (i32.load8_u (i32.const 2048))
                                       (i32.const 48)))))
    (global.set $runs (i32.rem_u (i32.add (global.get $runs) (i32.const 1))
                                 (i32.const 10)))
    (i32.store8 (i32.const 2048) (i32.add (global.get $runs) (i32.const 48)))
    (drop (call $prefs_set (i32.const 1024) (i32.const 4) (i32.const 2048) (i32.const 1)))
    (global.set $tok (call $theme_token (i32.const 0)))
    (global.set $gen (call $theme_gen)))

  (func (export "_update") (param $dt f32))

  (func (export "_draw")
    (call $cls (i32.const 0))
    (call $rect (i32.const 0) (i32.const 0) (i32.const 320) (i32.const 40)
                (i32.and (global.get $tok) (i32.const 63)))))
