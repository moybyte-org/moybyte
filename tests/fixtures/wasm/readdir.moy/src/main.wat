;; Read Dir Wasm: `read` of a name that is a folder in the cart, not a file.
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed. A folder reads as a missing file
;; does -- nothing, and a size of 0 -- on every host (proposals/
;; wasm-runtime.md: `read` sees the cart's own files). The cart's own `src/`
;; is the folder. Its answers go to pmem, where a test reads them: slot 0 the
;; size query of "src", slot 1 a 16-byte read of it, slot 2 the size query of
;; "manifest.json", a file, for contrast.
(module
  (import "moy" "read" (func $read (param i32 i32 i32 i32 i32) (result i32)))
  (import "moy" "pmem" (func $pmem (param i32 i32 i32) (result i32)))
  (import "moy" "cls" (func $cls (param i32)))

  (memory (export "memory") 1 1)

  (data (i32.const 1024) "src")
  (data (i32.const 1040) "manifest.json")

  (func (export "_init")
    (drop (call $pmem (i32.const 0)
      (call $read (i32.const 1024) (i32.const 3) (i32.const 0)
                  (i32.const 0) (i32.const 0))
      (i32.const 1)))
    (drop (call $pmem (i32.const 1)
      (call $read (i32.const 1024) (i32.const 3) (i32.const 0)
                  (i32.const 2048) (i32.const 16))
      (i32.const 1)))
    (drop (call $pmem (i32.const 2)
      (call $read (i32.const 1040) (i32.const 13) (i32.const 0)
                  (i32.const 0) (i32.const 0))
      (i32.const 1))))

  (func (export "_update") (param $dt f32))

  (func (export "_draw") (call $cls (i32.const 1))))
