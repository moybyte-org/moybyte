;; Tier Wasm: one compiled cart whose frame is the same on every tier -- a
;; board, the host and the browser -- because nothing in it depends on time.
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed. Every _draw hands par four items,
;; one band of 60 rows each, which paint a 565 frame from (x, y, band); the
;; frame goes to the screen with blit565, and verbs draw over it in call
;; order: the cart's own file read through `read`, a config value through
;; `cfg` (two spans, one written back), a circle. Every _update tops its
;; sample stream up with a 441 Hz square wave through `snd`, filling exactly
;; the room the host reports, and button A (index 4) traps on purpose.
;;
;; Memory, four pages: the file name at 1024, the config key at 1040, the
;; greeting read into 2048 and the config value into 2112; par's stacks from
;; 4096, four of 4 KiB; the stream's samples at 24576; the frame from 65536.
(module
  (import "moy" "read" (func $read (param i32 i32 i32 i32 i32) (result i32)))
  (import "moy" "cfg" (func $cfg (param i32 i32 i32 i32) (result i32)))
  (import "moy" "btnp" (func $btnp (param i32 i32) (result i32)))
  (import "moy" "snd" (func $snd (param i32 i32) (result i32)))
  (import "moy" "par" (func $par (param i32 i32 i32 i32)))
  (import "moy" "blit565" (func $blit565 (param i32)))
  (import "moy" "rect" (func $rect (param i32 i32 i32 i32 i32)))
  (import "moy" "circ" (func $circ (param i32 i32 i32 i32)))
  (import "moy" "print" (func $print (param i32 i32 i32 i32 i32)))

  (memory (export "memory") 4 4)
  (global $sp (export "__stack_pointer") (mut i32) (i32.const 4096))

  (data (i32.const 1024) "greeting.txt")
  (data (i32.const 1040) "label")

  (global $n (mut i32) (i32.const 0))       ;; the greeting's length
  (global $m (mut i32) (i32.const 0))       ;; the config value's length
  (global $phase (mut i32) (i32.const 0))   ;; the square wave's

  ;; Item i paints rows 60i .. 60i + 59 of the frame at `frame`.
  (func (export "_par") (param $i i32) (param $frame i32)
        (local $x i32) (local $y i32) (local $end i32)
    (local.set $y (i32.mul (local.get $i) (i32.const 60)))
    (local.set $end (i32.add (local.get $y) (i32.const 60)))
    (block $rows
      (loop $row
        (br_if $rows (i32.ge_u (local.get $y) (local.get $end)))
        (local.set $x (i32.const 0))
        (block $cols
          (loop $col
            (br_if $cols (i32.ge_u (local.get $x) (i32.const 320)))
            (i32.store16
              (i32.add (local.get $frame)
                       (i32.shl (i32.add (i32.mul (local.get $y) (i32.const 320))
                                         (local.get $x))
                                (i32.const 1)))
              (i32.or
                (i32.or (i32.shl (i32.div_u (local.get $x) (i32.const 10)) (i32.const 11))
                        (i32.shl (i32.shr_u (local.get $y) (i32.const 2)) (i32.const 5)))
                (i32.add (i32.shl (local.get $i) (i32.const 3))
                         (i32.and (i32.xor (local.get $x) (local.get $y)) (i32.const 7)))))
            (local.set $x (i32.add (local.get $x) (i32.const 1)))
            (br $col)))
        (local.set $y (i32.add (local.get $y) (i32.const 1)))
        (br $row))))

  (func (export "_init")
    (global.set $n
      (call $read (i32.const 1024) (i32.const 12) (i32.const 0)
                  (i32.const 2048) (i32.const 40)))
    (global.set $m
      (call $cfg (i32.const 1040) (i32.const 5) (i32.const 2112) (i32.const 40))))

  (func (export "_update") (param $dt f32) (local $room i32) (local $k i32)
    (if (call $btnp (i32.const 4) (i32.const 0))
      (then unreachable))
    (local.set $room (call $snd (i32.const 0) (i32.const 0)))
    (if (i32.gt_u (local.get $room) (i32.const 2048))
      (then (local.set $room (i32.const 2048))))
    (local.set $k (i32.const 0))
    (block $done
      (loop $each
        (br_if $done (i32.ge_u (local.get $k) (local.get $room)))
        (i32.store16 offset=24576 (i32.shl (local.get $k) (i32.const 1))
          (select (i32.const 4000) (i32.const -4000)
                  (i32.lt_u (i32.rem_u (global.get $phase) (i32.const 50)) (i32.const 25))))
        (global.set $phase (i32.add (global.get $phase) (i32.const 1)))
        (local.set $k (i32.add (local.get $k) (i32.const 1)))
        (br $each)))
    (drop (call $snd (i32.const 24576) (local.get $room))))

  (func (export "_draw")
    (call $par (i32.const 4) (i32.const 65536) (i32.const 4096) (i32.const 4096))
    (call $blit565 (i32.const 65536))
    (call $rect (i32.const 8) (i32.const 8)
                (i32.add (i32.shl (global.get $n) (i32.const 3)) (i32.const 8))
                (i32.const 16) (i32.const 0))
    (call $print (i32.const 2048) (global.get $n) (i32.const 12) (i32.const 12)
                 (i32.const 7))
    (call $rect (i32.const 8) (i32.const 216)
                (i32.add (i32.shl (global.get $m) (i32.const 3)) (i32.const 8))
                (i32.const 16) (i32.const 0))
    (call $print (i32.const 2112) (global.get $m) (i32.const 12) (i32.const 220)
                 (i32.const 11))
    (call $circ (i32.const 240) (i32.const 120) (i32.const 30) (i32.const 8)))
)
