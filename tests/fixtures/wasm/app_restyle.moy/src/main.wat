;; App Restyle Wasm: the public UI API's restyle verbs (docs/look_2026-10.md
;; step 31) as a compiled app, through the `ui` rows it imports from module
;; "moybyte.app": a verb, three tools (one current, one disabled), a rule and
;; the bar's title and tools.
;; tests/fixtures/ui_parts/restyle_py.moy and restyle_lua.moy draw the same
;; page with the same calls, and tests/test_ui_parts.py holds the three to the
;; same pixels.
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed. Into pmem: 0 frames, 1 the verb's
;; activations, 2 the tools' (Select 1, Cut 10), 3 the bar's answer + 1, 5
;; the focused id's length, 6 the last row answer that failed, negated (0:
;; none).
(module
  (import "moy" "pmem" (func $pmem (param i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_page" (func $ui_page
    (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_at" (func $ui_at (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_icon" (func $ui_icon (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_button" (func $ui_button
    (param i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_rule" (func $ui_rule (result i32)))
  (import "moybyte.app" "ui_tool" (func $ui_tool
    (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_bar" (func $ui_bar (param i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_focus" (func $ui_focus (param i32 i32 i32 i32) (result i32)))

  (memory (export "memory") 1 1)

  (data (i32.const 1064) "new")
  (data (i32.const 1068) "Make")
  (data (i32.const 1100) "select")
  (data (i32.const 1108) "Select")
  (data (i32.const 1116) "copy")
  (data (i32.const 1124) "Copy\09Ctrl+C")
  (data (i32.const 1136) "cut")
  (data (i32.const 1140) "Cut")
  (data (i32.const 1144) "Title")
  (data (i32.const 1152) "undo\00redo")
  ;; the override blocks (moy_ui_api.h): current and disabled
  (data (i32.const 1200) "\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\01\00\00\00\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff")
  (data (i32.const 1240) "\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\01\00\00\00\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff")
  ;; 2048: the page's rect (16 bytes); 2096: the focused id (32).

  (global $makes (mut i32) (i32.const 0))
  (global $tools (mut i32) (i32.const 0))
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
    (local $b i32) (local $f i32)
    (drop (call $ok (call $ui_page (i32.const 0) (i32.const 0) (i32.const 0) (i32.const 0)
                                   (i32.const 2048) (i32.const 16))))
    (drop (call $ok (call $ui_at (i32.const 98) (i32.const 120) (i32.const 124)
                                 (i32.const 26))))
    (drop (call $ui_icon (i32.const 1064) (i32.const 3) (i32.const 0) (i32.const 0)))
    (if (call $ok (call $ui_button (i32.const 1068) (i32.const 4) (i32.const 1) (i32.const 0)
                                   (i32.const 0)))
      (then (global.set $makes (i32.add (global.get $makes) (i32.const 1)))))
    (drop (call $ok (call $ui_at (i32.const 0) (i32.const 196) (i32.const 320) (i32.const 1))))
    (drop (call $ok (call $ui_rule)))
    (drop (call $ok (call $ui_at (i32.const 10) (i32.const 200) (i32.const 42) (i32.const 32))))
    (if (call $ok (call $ui_tool (i32.const 1100) (i32.const 6) (i32.const 1108) (i32.const 6)
                                 (i32.const 1200) (i32.const 36)))
      (then (global.set $tools (i32.add (global.get $tools) (i32.const 1)))))
    (drop (call $ok (call $ui_at (i32.const 56) (i32.const 200) (i32.const 42) (i32.const 32))))
    (drop (call $ok (call $ui_tool (i32.const 1116) (i32.const 4) (i32.const 1124) (i32.const 11)
                                   (i32.const 1240) (i32.const 36))))
    (drop (call $ok (call $ui_at (i32.const 102) (i32.const 200) (i32.const 42) (i32.const 32))))
    (if (call $ok (call $ui_tool (i32.const 1136) (i32.const 3) (i32.const 1140) (i32.const 3)
                                 (i32.const 0) (i32.const 0)))
      (then (global.set $tools (i32.add (global.get $tools) (i32.const 10)))))
    (local.set $b (call $ui_bar (i32.const 1144) (i32.const 5) (i32.const 1152) (i32.const 9)
                                (i32.const 2)))
    (local.set $f (call $ui_focus (i32.const 0) (i32.const 0) (i32.const 2096) (i32.const 32)))
    (global.set $frames (i32.add (global.get $frames) (i32.const 1)))
    (drop (call $pmem (i32.const 0) (global.get $frames) (i32.const 1)))
    (drop (call $pmem (i32.const 1) (global.get $makes) (i32.const 1)))
    (drop (call $pmem (i32.const 2) (global.get $tools) (i32.const 1)))
    (drop (call $pmem (i32.const 3) (i32.add (local.get $b) (i32.const 1)) (i32.const 1)))
    (drop (call $pmem (i32.const 5) (local.get $f) (i32.const 1)))))
