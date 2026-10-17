;; App Pop Wasm: the public UI API's popover (docs/look_2026-10.md step 24)
;; as a compiled app, through the `ui` rows it imports from module
;; "moybyte.app": a button opens a popover hung under it, holding a seg and a
;; button that closes it. tests/fixtures/ui_parts/pop_py.moy and pop_lua.moy
;; draw the same page with the same calls, and tests/test_ui_parts.py holds
;; the three to the same pixels.
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed. Into pmem: 0 frames, 1 whether the
;; popover is open, 2 the size (from 0), 3 the closes, 5 the focused id's
;; length, 6 the last row answer that failed, negated (0: none), 7 the link
;; under it activated.
(module
  (import "moy" "pmem" (func $pmem (param i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_page" (func $ui_page
    (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_button" (func $ui_button
    (param i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_link" (func $ui_link (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_seg" (func $ui_seg
    (param i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_focus" (func $ui_focus (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_popover" (func $ui_popover
    (param i32 i32 i32 i32 i32) (result i32)))

  (memory (export "memory") 1 1)

  (data (i32.const 1024) "Pop")
  (data (i32.const 1028) "Pick")
  (data (i32.const 1036) "Under")
  (data (i32.const 1044) "pick")
  (data (i32.const 1052) "Size")
  (data (i32.const 1060) "1x1\002x2\003x3")
  (data (i32.const 1072) "Done")
  ;; 2048: the page's rect (16 bytes); 2096: the focused id (32).

  (global $shown (mut i32) (i32.const 0))
  (global $size (mut i32) (i32.const 0))
  (global $closes (mut i32) (i32.const 0))
  (global $under (mut i32) (i32.const 0))
  (global $frames (mut i32) (i32.const 0))

  ;; A row's answer: below 0 is a failure, kept in pmem 6.
  (func $ok (param $r i32) (result i32)
    (if (i32.lt_s (local.get $r) (i32.const 0))
      (then (drop (call $pmem (i32.const 6) (i32.sub (i32.const 0) (local.get $r))
                              (i32.const 1)))))
    (local.get $r))

  (func (export "_init"))

  (func (export "_update") (param $dt f32))

  (func (export "_draw")
    (local $r i32) (local $f i32)
    (drop (call $ok (call $ui_page (i32.const 1024) (i32.const 3) (i32.const 0) (i32.const 0)
                                   (i32.const 2048) (i32.const 16))))
    (if (i32.gt_s (call $ok (call $ui_button (i32.const 1028) (i32.const 4) (i32.const 0)
                                             (i32.const 0) (i32.const 0))) (i32.const 0))
      (then (global.set $shown (i32.const 1))))
    (if (i32.gt_s (call $ok (call $ui_link (i32.const 1036) (i32.const 5) (i32.const 0)
                                           (i32.const 0) (i32.const 0) (i32.const 0)))
                  (i32.const 0))
      (then (global.set $under (i32.add (global.get $under) (i32.const 1)))))
    (if (global.get $shown)
      (then
        (global.set $shown (call $ok (call $ui_popover (i32.const 1044) (i32.const 4)
                                                       (i32.const 200) (i32.const 0)
                                                       (i32.const 0))))
        (local.set $r (call $ok (call $ui_seg (i32.const 1052) (i32.const 4) (i32.const 1060)
                                              (i32.const 11) (global.get $size) (i32.const 0)
                                              (i32.const 0))))
        (if (i32.ge_s (local.get $r) (i32.const 0))
          (then (global.set $size (local.get $r))))
        (if (i32.gt_s (call $ok (call $ui_button (i32.const 1072) (i32.const 4) (i32.const 0)
                                                 (i32.const 0) (i32.const 0))) (i32.const 0))
          (then
            (global.set $shown (i32.const 0))
            (global.set $closes (i32.add (global.get $closes) (i32.const 1)))))
        (drop (call $ok (call $ui_popover (i32.const 0) (i32.const 0) (i32.const 0)
                                          (i32.const 0) (i32.const 0))))))
    (local.set $f (call $ui_focus (i32.const 0) (i32.const 0) (i32.const 2096) (i32.const 32)))
    (global.set $frames (i32.add (global.get $frames) (i32.const 1)))
    (drop (call $pmem (i32.const 0) (global.get $frames) (i32.const 1)))
    (drop (call $pmem (i32.const 1) (global.get $shown) (i32.const 1)))
    (drop (call $pmem (i32.const 2) (global.get $size) (i32.const 1)))
    (drop (call $pmem (i32.const 3) (global.get $closes) (i32.const 1)))
    (drop (call $pmem (i32.const 5) (local.get $f) (i32.const 1)))
    (drop (call $pmem (i32.const 7) (global.get $under) (i32.const 1)))))
