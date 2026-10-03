;; Blit Wasm: a full-frame palette blit every frame -- the compiled tier's
;; per-board ceiling (docs/wasm_tier_plan_2026-09.md, phase 3; the measured
;; figure lives in native/moy_wasm/README.md).
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed. Each _draw rewrites all 320 x 240
;; indices in the cart's own memory -- ((x xor y) + t) mod 256 -- and hands the
;; frame over with a 256-entry palette: the cart's own software raster plus the
;; host's full-frame palette resolve, which is the whole cost of a frame on
;; this tier. The manifest declares "fps": "free", so what the board presents
;; is what it can.
;;
;; Memory, three pages: the palette at 8192, the frame from 65536.
(module
  (import "moy" "blit" (func $blit (param i32 i32)))

  (memory (export "memory") 3 3)

  (global $t (mut i32) (i32.const 0))

  (func (export "_init") (local $i i32)
    ;; the palette: entry i is (i, 255 - i, 4i mod 256)
    (block $done
      (loop $each
        (br_if $done (i32.ge_u (local.get $i) (i32.const 256)))
        (i32.store8 offset=8192 (i32.mul (local.get $i) (i32.const 3))
                    (local.get $i))
        (i32.store8 offset=8193 (i32.mul (local.get $i) (i32.const 3))
                    (i32.sub (i32.const 255) (local.get $i)))
        (i32.store8 offset=8194 (i32.mul (local.get $i) (i32.const 3))
                    (i32.and (i32.shl (local.get $i) (i32.const 2))
                             (i32.const 255)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $each))))

  (func (export "_update") (param $dt f32)
    (global.set $t (i32.add (global.get $t) (i32.const 1))))

  (func (export "_draw") (local $x i32) (local $y i32) (local $p i32)
    (local.set $p (i32.const 65536))
    (local.set $y (i32.const 0))
    (block $rows
      (loop $row
        (br_if $rows (i32.ge_u (local.get $y) (i32.const 240)))
        (local.set $x (i32.const 0))
        (block $cols
          (loop $col
            (br_if $cols (i32.ge_u (local.get $x) (i32.const 320)))
            (i32.store8 (local.get $p)
              (i32.add (i32.xor (local.get $x) (local.get $y))
                       (global.get $t)))
            (local.set $p (i32.add (local.get $p) (i32.const 1)))
            (local.set $x (i32.add (local.get $x) (i32.const 1)))
            (br $col)))
        (local.set $y (i32.add (local.get $y) (i32.const 1)))
        (br $row)))
    (call $blit (i32.const 65536) (i32.const 8192)))
)
