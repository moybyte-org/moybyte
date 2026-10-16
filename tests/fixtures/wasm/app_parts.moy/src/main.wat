;; App Parts Wasm: the public UI API's parts page (docs/look_2026-10.md step
;; 12) as a compiled app, through the `ui` rows it imports from module
;; "moybyte.app". tests/fixtures/ui_parts/parts_py.moy and parts_lua.moy draw
;; the same page with the same calls, and tests/test_ui_parts.py holds the
;; three to the same pixels.
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed. Into pmem: 0 frames, 1 the link's
;; activations, 2 the size (from 0), 3 the mode, 4 the accent, 5 the focused
;; id's length, 6 the last row answer that failed, negated (0: none), 7 the
;; content's top.
(module
  (import "moy" "pmem" (func $pmem (param i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_page" (func $ui_page
    (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_focus" (func $ui_focus (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_group" (func $ui_group (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_info" (func $ui_info (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_link" (func $ui_link (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_cycle" (func $ui_cycle
    (param i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_seg" (func $ui_seg
    (param i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_swatches" (func $ui_swatches
    (param i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_hints" (func $ui_hints (param i32 i32 i32 i32) (result i32)))

  (memory (export "memory") 1 1)

  (data (i32.const 1024) "Parts")
  (data (i32.const 1040) "enter\00Open\00left right\00Change")
  (data (i32.const 1072) "General")
  (data (i32.const 1088) "Wi-Fi")
  (data (i32.const 1096) "Home")
  (data (i32.const 1104) "Size")
  (data (i32.const 1112) "S\00M\00L")
  (data (i32.const 1120) "Board")
  (data (i32.const 1128) "T-Deck")
  (data (i32.const 1136) "Look")
  (data (i32.const 1144) "Mode")
  (data (i32.const 1152) "Day\00Night")
  (data (i32.const 1168) "Accent")
  (data (i32.const 1176) "\00\00\ff\00\00\c0\00\00\ff\00\00\00")

  ;; 2048: the page's rect (16 bytes); 2096: the focused id (32).
  (global $opens (mut i32) (i32.const 0))
  (global $size (mut i32) (i32.const 1))
  (global $mode (mut i32) (i32.const 0))
  (global $accent (mut i32) (i32.const 0))
  (global $frames (mut i32) (i32.const 0))

  ;; A row's answer: below 0 is a failure, kept in pmem 6.
  (func $ok (param $r i32)
    (if (i32.lt_s (local.get $r) (i32.const 0))
      (then (drop (call $pmem (i32.const 6) (i32.sub (i32.const 0) (local.get $r))
                              (i32.const 1))))))

  (func (export "_init"))

  (func (export "_update") (param $dt f32))

  (func (export "_draw")
    (local $f i32)
    (call $ok (call $ui_page (i32.const 1024) (i32.const 5) (i32.const 0) (i32.const 0)
                             (i32.const 2048) (i32.const 16)))
    (call $ok (call $ui_hints (i32.const 1040) (i32.const 28) (i32.const 0) (i32.const 0)))
    (call $ok (call $ui_group (i32.const 1072) (i32.const 7) (i32.const 0) (i32.const 0)))
    (if (call $ui_link (i32.const 1088) (i32.const 5) (i32.const 1096) (i32.const 4)
                       (i32.const 0) (i32.const 0))
      (then (global.set $opens (i32.add (global.get $opens) (i32.const 1)))))
    (global.set $size (call $ui_cycle (i32.const 1104) (i32.const 4) (i32.const 1112)
                                      (i32.const 5) (global.get $size) (i32.const 0)
                                      (i32.const 0)))
    (call $ok (call $ui_info (i32.const 1120) (i32.const 5) (i32.const 1128) (i32.const 6)
                             (i32.const 0) (i32.const 0)))
    (call $ok (call $ui_group (i32.const 1136) (i32.const 4) (i32.const 0) (i32.const 0)))
    (global.set $mode (call $ui_seg (i32.const 1144) (i32.const 4) (i32.const 1152)
                                    (i32.const 9) (global.get $mode) (i32.const 0)
                                    (i32.const 0)))
    (global.set $accent (call $ui_swatches (i32.const 1168) (i32.const 6) (i32.const 1176)
                                           (i32.const 12) (global.get $accent) (i32.const 0)
                                           (i32.const 0)))
    (call $ok (call $ui_group (i32.const 0) (i32.const 0) (i32.const 0) (i32.const 0)))
    (local.set $f (call $ui_focus (i32.const 0) (i32.const 0) (i32.const 2096) (i32.const 32)))
    (global.set $frames (i32.add (global.get $frames) (i32.const 1)))
    (drop (call $pmem (i32.const 0) (global.get $frames) (i32.const 1)))
    (drop (call $pmem (i32.const 1) (global.get $opens) (i32.const 1)))
    (drop (call $pmem (i32.const 2) (global.get $size) (i32.const 1)))
    (drop (call $pmem (i32.const 3) (global.get $mode) (i32.const 1)))
    (drop (call $pmem (i32.const 4) (global.get $accent) (i32.const 1)))
    (drop (call $pmem (i32.const 5) (local.get $f) (i32.const 1)))
    (drop (call $pmem (i32.const 7) (i32.load (i32.const 2052)) (i32.const 1)))))
