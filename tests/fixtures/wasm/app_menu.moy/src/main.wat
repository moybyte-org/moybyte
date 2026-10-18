;; App Menu Wasm: the public UI API's menu (docs/look_2026-10.md step 25a) as
;; a compiled app, through the `ui` rows it imports from module "moybyte.app":
;; a card centred on the page (a popover with nothing to hang from) holding a
;; header row, a menu of four and its hints, then a captioned menu stacked on
;; the page. tests/fixtures/ui_parts/menu_py.moy and menu_lua.moy draw the
;; same page with the same calls, and tests/test_ui_parts.py holds the three
;; to the same pixels.
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed. Into pmem: 0 frames, 1 whether the
;; card is open, 2 the card's item activated last (from 1), 3 its
;; activations, 5 the focused id's length, 6 the last row answer that failed,
;; negated (0: none), 7 the page menu's item activated last (from 1).
(module
  (import "moy" "pmem" (func $pmem (param i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_page" (func $ui_page
    (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_info" (func $ui_info (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_hints" (func $ui_hints (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_icon" (func $ui_icon (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_focus" (func $ui_focus (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_popover" (func $ui_popover
    (param i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_menu" (func $ui_menu
    (param i32 i32 i32 i32 i32 i32 i32 i32) (result i32)))

  (memory (export "memory") 1 1)

  (data (i32.const 1024) "Menu")
  (data (i32.const 1032) "pause")
  (data (i32.const 1040) "moy")
  (data (i32.const 1044) "Star")
  (data (i32.const 1052) "Paused")
  (data (i32.const 1060) "Resume\00Restart\00Options\00Quit")
  (data (i32.const 1096) "play\00rotate\00settings\00close")
  (data (i32.const 1128) "enter\00Choose\00Esc\00Resume")
  (data (i32.const 1160) "System")
  (data (i32.const 1168) "Search\00Settings")
  (data (i32.const 1188) "search\00settings")
  ;; 2048: the page's rect (16 bytes); 2096: the focused id (32).

  (global $shown (mut i32) (i32.const 1))
  (global $last (mut i32) (i32.const 0))
  (global $picks (mut i32) (i32.const 0))
  (global $sys (mut i32) (i32.const 0))
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
    (local $i i32) (local $f i32)
    (drop (call $ok (call $ui_page (i32.const 1024) (i32.const 4) (i32.const 0) (i32.const 0)
                                   (i32.const 2048) (i32.const 16))))
    (if (global.get $shown)
      (then
        (global.set $shown (call $ok (call $ui_popover (i32.const 1032) (i32.const 5)
                                                       (i32.const 200) (i32.const 0)
                                                       (i32.const 0))))
        (drop (call $ui_icon (i32.const 1040) (i32.const 3) (i32.const 0) (i32.const 0)))
        (drop (call $ok (call $ui_info (i32.const 1044) (i32.const 4) (i32.const 1052)
                                       (i32.const 6) (i32.const 0) (i32.const 0))))
        (local.set $i (call $ui_menu (i32.const 0) (i32.const 0) (i32.const 1060) (i32.const 27)
                                     (i32.const 1096) (i32.const 26) (i32.const 0)
                                     (i32.const 0)))
        (drop (call $ok (call $ui_hints (i32.const 1128) (i32.const 23) (i32.const 0)
                                        (i32.const 0))))
        (drop (call $ok (call $ui_popover (i32.const 0) (i32.const 0) (i32.const 0)
                                          (i32.const 0) (i32.const 0))))
        (if (i32.ge_s (local.get $i) (i32.const 0))
          (then
            (global.set $last (i32.add (local.get $i) (i32.const 1)))
            (global.set $picks (i32.add (global.get $picks) (i32.const 1)))
            (if (i32.eq (local.get $i) (i32.const 3))
              (then (global.set $shown (i32.const 0))))))))
    (local.set $i (call $ui_menu (i32.const 1160) (i32.const 6) (i32.const 1168) (i32.const 15)
                                 (i32.const 1188) (i32.const 15) (i32.const 0) (i32.const 0)))
    (if (i32.ge_s (local.get $i) (i32.const 0))
      (then (global.set $sys (i32.add (local.get $i) (i32.const 1)))))
    (local.set $f (call $ui_focus (i32.const 0) (i32.const 0) (i32.const 2096) (i32.const 32)))
    (global.set $frames (i32.add (global.get $frames) (i32.const 1)))
    (drop (call $pmem (i32.const 0) (global.get $frames) (i32.const 1)))
    (drop (call $pmem (i32.const 1) (global.get $shown) (i32.const 1)))
    (drop (call $pmem (i32.const 2) (global.get $last) (i32.const 1)))
    (drop (call $pmem (i32.const 3) (global.get $picks) (i32.const 1)))
    (drop (call $pmem (i32.const 5) (local.get $f) (i32.const 1)))
    (drop (call $pmem (i32.const 7) (global.get $sys) (i32.const 1)))))
