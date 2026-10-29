;; App Nest Wasm: a nest opened past the engine's depth (moy_ui_api.h's
;; MOY_UI_API_NEST) as a compiled app, through the `ui` rows it imports from
;; module "moybyte.app". tests/fixtures/ui_nest/nest_py.moy and nest_lua.moy
;; make the same calls, and tests/test_ui_nest.py reads the answers.
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed. Into pmem: 0..3 what ui_scroll,
;; ui_text_view, ui_dialog and ui_popover answered inside two open scroll
;; regions, 4 what a scroll region opened after them answered, 5 frames.
(module
  (import "moy" "pmem" (func $pmem (param i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_page" (func $ui_page
    (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_label" (func $ui_label (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_scroll" (func $ui_scroll (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_dialog" (func $ui_dialog
    (param i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_popover" (func $ui_popover (param i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_text_view" (func $ui_text_view
    (param i32 i32 i32 i32 i32 i32 i32 i32 i32 i32) (result i32)))

  (memory (export "memory") 1 1)

  (data (i32.const 1024) "Nest")
  (data (i32.const 1032) "a")
  (data (i32.const 1036) "b")
  (data (i32.const 1040) "c")
  (data (i32.const 1044) "d")
  (data (i32.const 1048) "t")
  (data (i32.const 1052) "x\00")
  (data (i32.const 1056) "D")
  (data (i32.const 1060) "OK\00")
  (data (i32.const 1064) "p")
  (data (i32.const 1068) "after")
  ;; 2048: the page's rect (16 bytes); 2096: the text view's answers (8).

  (global $frames (mut i32) (i32.const 0))

  (func (export "_init"))

  (func (export "_update") (param $dt f32))

  (func (export "_draw")
    (drop (call $ui_page (i32.const 1024) (i32.const 4) (i32.const 0) (i32.const 0)
                         (i32.const 2048) (i32.const 16)))
    (drop (call $ui_scroll (i32.const 1032) (i32.const 1) (i32.const 0) (i32.const 0)))
    (drop (call $ui_scroll (i32.const 1036) (i32.const 1) (i32.const 0) (i32.const 0)))
    (drop (call $pmem (i32.const 0)
                (call $ui_scroll (i32.const 1040) (i32.const 1) (i32.const 0) (i32.const 0))
                (i32.const 1)))
    (drop (call $pmem (i32.const 1)
                (call $ui_text_view (i32.const 1048) (i32.const 1) (i32.const 1052)
                                    (i32.const 2) (i32.const 0) (i32.const 0) (i32.const 0)
                                    (i32.const 0) (i32.const 2096) (i32.const 8))
                (i32.const 1)))
    (drop (call $pmem (i32.const 2)
                (call $ui_dialog (i32.const 1056) (i32.const 1) (i32.const 1060) (i32.const 3)
                                 (i32.const 1) (i32.const 0) (i32.const 0))
                (i32.const 1)))
    (drop (call $pmem (i32.const 3)
                (call $ui_popover (i32.const 1064) (i32.const 1) (i32.const 100) (i32.const 0)
                                  (i32.const 0))
                (i32.const 1)))
    (drop (call $ui_scroll (i32.const 0) (i32.const 0) (i32.const 0) (i32.const 0)))
    (drop (call $ui_scroll (i32.const 0) (i32.const 0) (i32.const 0) (i32.const 0)))
    (drop (call $pmem (i32.const 4)
                (call $ui_scroll (i32.const 1044) (i32.const 1) (i32.const 0) (i32.const 0))
                (i32.const 1)))
    (drop (call $ui_label (i32.const 1068) (i32.const 5) (i32.const 0) (i32.const 0)))
    (drop (call $ui_scroll (i32.const 0) (i32.const 0) (i32.const 0) (i32.const 0)))
    (global.set $frames (i32.add (global.get $frames) (i32.const 1)))
    (drop (call $pmem (i32.const 5) (global.get $frames) (i32.const 1)))))
