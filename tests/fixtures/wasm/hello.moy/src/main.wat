;; Hello Wasm: the compiled-cart tier's first Player cart
;; (docs/wasm_tier_plan_2026-09.md, phase 3).
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed. It exercises what makes the tier
;; the tier: a 256-colour palette blit of a whole frame, ordinary verbs drawn
;; over it in call order, the cart's own file read through `read`, and the
;; buttons -- all from the import table (proposals/wasm-runtime.md).
;;
;; Memory, three pages (min = max = the manifest's "memory"): the file name at
;; 1024, the greeting read into 2048, the palette at 8192, and the frame --
;; 320 x 240 bytes of palette index -- from 65536.
(module
  (import "moy" "blit" (func $blit (param i32 i32)))
  (import "moy" "rect" (func $rect (param i32 i32 i32 i32 i32)))
  (import "moy" "circ" (func $circ (param i32 i32 i32 i32)))
  (import "moy" "circb" (func $circb (param i32 i32 i32 i32)))
  (import "moy" "print" (func $print (param i32 i32 i32 i32 i32)))
  (import "moy" "btn" (func $btn (param i32 i32) (result i32)))
  (import "moy" "read" (func $read (param i32 i32 i32 i32 i32) (result i32)))

  (memory (export "memory") 3 3)

  (data (i32.const 1024) "greeting.txt")

  (global $n (mut i32) (i32.const 0))     ;; the greeting's length
  (global $x (mut i32) (i32.const 160))   ;; the ball
  (global $y (mut i32) (i32.const 120))
  (global $vx (mut i32) (i32.const 3))
  (global $vy (mut i32) (i32.const 2))
  (global $px (mut i32) (i32.const 150))  ;; the paddle, on the d-pad

  (func (export "_init") (local $i i32)
    ;; the cart's own file, at most 40 bytes of it
    (global.set $n
      (call $read (i32.const 1024) (i32.const 12) (i32.const 0)
                  (i32.const 2048) (i32.const 40)))

    ;; the palette: entry i is (i, 255 - i, 4i mod 256)
    (local.set $i (i32.const 0))
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
        (br $each)))

    ;; the frame, once: pixel (x, y) is index (x + y) mod 256
    (local.set $i (i32.const 0))
    (block $done
      (loop $each
        (br_if $done (i32.ge_u (local.get $i) (i32.const 76800)))
        (i32.store8 offset=65536 (local.get $i)
          (i32.add (i32.rem_u (local.get $i) (i32.const 320))
                   (i32.div_u (local.get $i) (i32.const 320))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $each))))

  (func (export "_update") (param $dt f32)
    ;; the ball bounces inside the frame below the greeting
    (global.set $x (i32.add (global.get $x) (global.get $vx)))
    (if (i32.or (i32.lt_s (global.get $x) (i32.const 12))
                (i32.gt_s (global.get $x) (i32.const 307)))
      (then (global.set $vx (i32.sub (i32.const 0) (global.get $vx)))))
    (global.set $y (i32.add (global.get $y) (global.get $vy)))
    (if (i32.or (i32.lt_s (global.get $y) (i32.const 40))
                (i32.gt_s (global.get $y) (i32.const 212)))
      (then (global.set $vy (i32.sub (i32.const 0) (global.get $vy)))))
    ;; left (0) and right (1) move the paddle
    (if (call $btn (i32.const 0) (i32.const 0))
      (then (global.set $px (i32.sub (global.get $px) (i32.const 4)))))
    (if (call $btn (i32.const 1) (i32.const 0))
      (then (global.set $px (i32.add (global.get $px) (i32.const 4)))))
    (if (i32.lt_s (global.get $px) (i32.const 0))
      (then (global.set $px (i32.const 0))))
    (if (i32.gt_s (global.get $px) (i32.const 296))
      (then (global.set $px (i32.const 296)))))

  (func (export "_draw")
    (call $blit (i32.const 65536) (i32.const 8192))
    ;; the greeting on a black band, drawn over the blit in call order
    (call $rect (i32.const 8) (i32.const 8)
                (i32.add (i32.shl (global.get $n) (i32.const 3)) (i32.const 8))
                (i32.const 16) (i32.const 0))
    (call $print (i32.const 2048) (global.get $n) (i32.const 12) (i32.const 12)
                 (i32.const 7))
    (call $circ (global.get $x) (global.get $y) (i32.const 10) (i32.const 8))
    (call $circb (global.get $x) (global.get $y) (i32.const 10) (i32.const 7))
    (call $rect (global.get $px) (i32.const 226) (i32.const 24) (i32.const 6)
                (i32.const 12)))
)
