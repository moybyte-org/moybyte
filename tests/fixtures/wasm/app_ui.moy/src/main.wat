;; App UI Wasm: a compiled app drawing the toolkit's widgets through the `ui`
;; rows (docs/kernel_toolkit_2026-10.md section 3.5): a button and a scrolling
;; list, drawn by the same C a Python app's `ui` draws, into this run's canvas.
;; It imports every `ui` row from module "moybyte.app" and needs no permission:
;; the rows are every compiled app's.
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed. Each _update reads the pointer the
;; console delivered to the run (touch), pumps the button's hit rect
;; (ui_hits_*) and the list's drag (ui_dragtap) over state kept in this
;; memory. Each _draw sets a draw state of its own -- a palette remap, a
;; camera and a fill pattern -- draws the two widgets, which ignore it, then
;; draws a square under that state to show it was kept. Into pmem it writes:
;;   0 frames drawn         1 the list's offset     2 the pump's pressed id
;;   3 the pump's hover id  4 the kept-state probe  5 the button's pixel hash
;;   6 the list's pixel hash (both every eighth frame)  7 the list's taps
;;   8 the last row answer that failed, negated (0: none)
;; Every row it imports is served in C, so the run makes no upcall.
(module
  (import "moy" "cls" (func $cls (param i32)))
  (import "moy" "rect" (func $rect (param i32 i32 i32 i32 i32)))
  (import "moy" "pix" (func $pix (param i32 i32 i32) (result i32)))
  (import "moy" "pal" (func $pal (param i32 i32 i32)))
  (import "moy" "camera" (func $camera (param i32 i32 i32)))
  (import "moy" "fillp" (func $fillp (param i32 i32)))
  (import "moy" "touch" (func $touch (param i32) (result i32)))
  (import "moy" "pmem" (func $pmem (param i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_kind" (func $ui_kind (param i32 i32) (result i32)))
  (import "moybyte.app" "ui_glyph_id" (func $ui_glyph_id (param i32 i32) (result i32)))
  (import "moybyte.app" "ui_button" (func $ui_button
    (param i32 i32 i32 i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_label" (func $ui_label
    (param i32 i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_list" (func $ui_list
    (param i32 i32 i32 i32 i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_hits_clear" (func $ui_hits_clear (param i32 i32) (result i32)))
  (import "moybyte.app" "ui_hits_add" (func $ui_hits_add
    (param i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_hits_pump" (func $ui_hits_pump
    (param i32 i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_dragtap" (func $ui_dragtap
    (param i32 i32 i32 i32 i32 i32 i32 i32 i32 i32 i32 i32 i32 f32) (result i32)))

  (memory (export "memory") 1 1)

  (data (i32.const 1024) "button")
  (data (i32.const 1040) "row_list")
  (data (i32.const 1056) "PRESS")
  (data (i32.const 1064) "gear")
  (data (i32.const 1072) "UI")
  (data (i32.const 1088)
    "one\00two\00three\00four\00five\00six\00seven\00eight\00nine\00ten\00eleven\00twelve")

  ;; 2048: the hit buffer (2 header words and 4 rects, 88 bytes); 2144: the
  ;; pump (hover, pressed, armed, down); 2160: the drag (dragtap, scroll,
  ;; offset, tap x and y: 40 bytes); 2208: touch's answer (x, y, the press
  ;; edge, held).
  (global $kbtn (mut i32) (i32.const 0))
  (global $klist (mut i32) (i32.const 0))
  (global $gear (mut i32) (i32.const 0))
  (global $x (mut i32) (i32.const 0))
  (global $y (mut i32) (i32.const 0))
  (global $down (mut i32) (i32.const 0))
  (global $edge (mut i32) (i32.const 0))
  (global $frames (mut i32) (i32.const 0))
  (global $taps (mut i32) (i32.const 0))

  ;; A row's answer: below 0 is a failure, kept in pmem 8.
  (func $ok (param $r i32)
    (if (i32.lt_s (local.get $r) (i32.const 0))
      (then (drop (call $pmem (i32.const 8) (i32.sub (i32.const 0) (local.get $r))
                              (i32.const 1))))))

  ;; A hash of the screen's indices over a rect, read under the camera (3, 4):
  ;; screen (x, y) is (x + 3, y + 4) to pix.
  (func $hash (param $x0 i32) (param $y0 i32) (param $w i32) (param $h i32) (result i32)
    (local $i i32) (local $j i32) (local $acc i32)
    (local.set $j (i32.const 0))
    (block $done
      (loop $rows
        (br_if $done (i32.ge_s (local.get $j) (local.get $h)))
        (local.set $i (i32.const 0))
        (block $rowdone
          (loop $cols
            (br_if $rowdone (i32.ge_s (local.get $i) (local.get $w)))
            (local.set $acc
              (i32.add (i32.mul (local.get $acc) (i32.const 31))
                       (call $pix (i32.add (i32.add (local.get $x0) (local.get $i)) (i32.const 3))
                                  (i32.add (i32.add (local.get $y0) (local.get $j)) (i32.const 4))
                                  (i32.const -1))))
            (local.set $i (i32.add (local.get $i) (i32.const 1)))
            (br $cols)))
        (local.set $j (i32.add (local.get $j) (i32.const 1)))
        (br $rows)))
    (local.get $acc))

  (func (export "_init")
    (global.set $kbtn (call $ui_kind (i32.const 1024) (i32.const 6)))
    (global.set $klist (call $ui_kind (i32.const 1040) (i32.const 8)))
    (global.set $gear (call $ui_glyph_id (i32.const 1064) (i32.const 4)))
    (call $ok (call $ui_hits_clear (i32.const 2048) (i32.const 88))))

  (func (export "_update") (param $dt f32)
    (if (call $touch (i32.const 2208))
      (then
        (global.set $x (i32.load (i32.const 2208)))
        (global.set $y (i32.load (i32.const 2212)))
        (global.set $edge (i32.load (i32.const 2216)))
        (global.set $down (i32.load (i32.const 2220))))
      (else
        (global.set $down (i32.const 0))
        (global.set $edge (i32.const 0))))
    (call $ok (call $ui_hits_clear (i32.const 2048) (i32.const 88)))
    (call $ok (call $ui_hits_add (i32.const 2048) (i32.const 88)
                    (i32.const 20) (i32.const 80) (i32.const 80) (i32.const 20) (i32.const 1)))
    (call $ok (call $ui_hits_pump (i32.const 2048) (i32.const 88)
                    (global.get $x) (global.get $y) (global.get $down) (i32.const 1)
                    (i32.const 2144) (i32.const 16)))
    (if (call $ui_dragtap (i32.const 2160) (i32.const 40)
                          (i32.const 120) (i32.const 80) (i32.const 160) (i32.const 96)
                          (i32.const 0) (global.get $x) (global.get $y)
                          (global.get $edge) (global.get $down) (i32.const 4)
                          (i32.const 144)
                          (f32.mul (local.get $dt) (f32.const 1000)))
      (then (global.set $taps (i32.add (global.get $taps) (i32.const 1)))))
    (drop (call $pmem (i32.const 1) (i32.load (i32.const 2188)) (i32.const 1)))
    (drop (call $pmem (i32.const 2) (i32.load (i32.const 2148)) (i32.const 1)))
    (drop (call $pmem (i32.const 3) (i32.load (i32.const 2144)) (i32.const 1)))
    (drop (call $pmem (i32.const 7) (global.get $taps) (i32.const 1))))

  (func (export "_draw")
    (local $interact i32)
    (call $cls (i32.const 0))
    ;; the cart's own draw state, which the widgets must neither use nor lose
    (call $pal (i32.const 7) (i32.const 8) (i32.const 0))
    (call $camera (i32.const 3) (i32.const 4) (i32.const 0))
    (call $fillp (i32.const 23130) (i32.const -1))
    (local.set $interact (i32.const -1))
    (if (i32.eq (i32.load (i32.const 2144)) (i32.const 1))
      (then (local.set $interact (i32.const 4))))
    (if (i32.eq (i32.load (i32.const 2148)) (i32.const 1))
      (then (local.set $interact (i32.const 1))))
    (call $ok (call $ui_button (i32.const 20) (i32.const 80) (i32.const 80) (i32.const 20)
                    (global.get $kbtn) (i32.const 0) (local.get $interact)
                    (i32.const 1056) (i32.const 5) (global.get $gear)))
    (call $ok (call $ui_list (i32.const 120) (i32.const 80) (i32.const 160) (i32.const 96)
                    (global.get $klist) (i32.const 1088) (i32.const 62) (i32.const 12)
                    (i32.const 12) (i32.load (i32.const 2188)) (i32.const -1)))
    (call $ok (call $ui_label (i32.const 20) (i32.const 110) (i32.const 80) (i32.const 10)
                    (i32.const 1072) (i32.const 2) (i32.const 7) (i32.const 1)))
    ;; the kept state: a square at (10, 10) under the camera, index 7 shown as 8
    (call $fillp (i32.const 0) (i32.const -1))
    (call $rect (i32.const 10) (i32.const 10) (i32.const 2) (i32.const 2) (i32.const 7))
    (drop (call $pmem (i32.const 4) (call $pix (i32.const 10) (i32.const 10) (i32.const -1))
                      (i32.const 1)))
    (if (i32.eqz (i32.and (global.get $frames) (i32.const 7)))
      (then
        (drop (call $pmem (i32.const 5)
                          (call $hash (i32.const 20) (i32.const 80) (i32.const 80) (i32.const 20))
                          (i32.const 1)))
        (drop (call $pmem (i32.const 6)
                          (call $hash (i32.const 120) (i32.const 80) (i32.const 160) (i32.const 96))
                          (i32.const 1)))))
    (global.set $frames (i32.add (global.get $frames) (i32.const 1)))
    (drop (call $pmem (i32.const 0) (global.get $frames) (i32.const 1)))))
