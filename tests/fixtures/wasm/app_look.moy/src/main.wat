;; App Look Wasm: the public UI API's Appearance verbs (docs/look_2026-10.md
;; step 20) as a compiled app, through the `ui` rows it imports from module
;; "moybyte.app": a tab row and a grid of cards whose pictures the app draws.
;; tests/fixtures/ui_parts/look_py.moy and look_lua.moy draw the same page
;; with the same calls, and tests/test_ui_parts.py holds the three to the
;; same pixels.
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed. Into pmem: 0 frames, 1 the tab
;; (from 0), 2 the card picked (from 0), 3 the cards activated, 5 the focused
;; id's length, 6 the last row answer that failed, negated (0: none), 7 the
;; first card's picture's left.
(module
  (import "moy" "pmem" (func $pmem (param i32 i32 i32) (result i32)))
  (import "moy" "rect" (func $rect (param i32 i32 i32 i32 i32)))
  (import "moybyte.app" "ui_page" (func $ui_page
    (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_link" (func $ui_link (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_focus" (func $ui_focus (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_tabs" (func $ui_tabs
    (param i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_grid" (func $ui_grid (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_card" (func $ui_card
    (param i32 i32 i32 i32 i32 i32 i32 i32) (result i32)))

  (memory (export "memory") 1 1)

  (data (i32.const 1024) "Look")
  (data (i32.const 1032) "Kind")
  (data (i32.const 1040) "Pictures\00Carts")
  (data (i32.const 1056) "cards")
  (data (i32.const 1064) "Red")
  (data (i32.const 1068) "Green")
  (data (i32.const 1076) "Blue")
  (data (i32.const 1084) "Grey")
  (data (i32.const 1092) "one")
  (data (i32.const 1096) "two")
  (data (i32.const 1100) "three")
  (data (i32.const 1108) "four")
  (data (i32.const 1120) "After")
  ;; The override blocks (docs/ui_api_2026-10.md §3): `current` 1, then 0;
  ;; every other key absent (-1).
  (data (i32.const 1152)
    "\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff"
    "\01\00\00\00\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff")
  (data (i32.const 1192)
    "\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff"
    "\00\00\00\00\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff")
  ;; 2048: the page's rect (16 bytes); 2064: a card's answers (20); 2096:
  ;; the focused id (32).

  (global $tab (mut i32) (i32.const 0))
  (global $picked (mut i32) (i32.const 0))
  (global $acts (mut i32) (i32.const 0))
  (global $left (mut i32) (i32.const 0))
  (global $frames (mut i32) (i32.const 0))

  ;; A row's answer: below 0 is a failure, kept in pmem 6.
  (func $ok (param $r i32)
    (if (i32.lt_s (local.get $r) (i32.const 0))
      (then (drop (call $pmem (i32.const 6) (i32.sub (i32.const 0) (local.get $r))
                              (i32.const 1))))))

  ;; Card $i, its title and sub at ($np, $nl) and ($sp, $sl), its picture
  ;; drawn in colour $c.
  (func $card (param $i i32) (param $np i32) (param $nl i32) (param $sp i32)
              (param $sl i32) (param $c i32)
    (local $blk i32) (local $x i32) (local $y i32)
    (local.set $blk (select (i32.const 1152) (i32.const 1192)
                            (i32.eq (local.get $i) (global.get $picked))))
    (call $ok (call $ui_card (local.get $np) (local.get $nl) (local.get $sp) (local.get $sl)
                             (local.get $blk) (i32.const 36) (i32.const 2064) (i32.const 20)))
    (local.set $x (i32.load (i32.const 2064)))
    (local.set $y (i32.load (i32.const 2068)))
    (if (i32.eqz (local.get $i))
      (then (global.set $left (local.get $x))))
    (if (i32.gt_s (i32.load (i32.const 2072)) (i32.const 0))
      (then
        (call $rect (local.get $x) (local.get $y) (i32.load (i32.const 2072))
                    (i32.load (i32.const 2076)) (local.get $c))
        (call $rect (i32.add (local.get $x) (i32.const 4)) (i32.add (local.get $y) (i32.const 4))
                    (i32.const 8) (i32.const 8) (i32.const 7))))
    (if (i32.and (i32.load (i32.const 2080)) (i32.const 2))
      (then
        (global.set $picked (local.get $i))
        (global.set $acts (i32.add (global.get $acts) (i32.const 1))))))

  (func (export "_init"))

  (func (export "_update") (param $dt f32))

  (func (export "_draw")
    (local $r i32) (local $f i32)
    (call $ok (call $ui_page (i32.const 1024) (i32.const 4) (i32.const 0) (i32.const 0)
                             (i32.const 2048) (i32.const 16)))
    (local.set $r (call $ui_tabs (i32.const 1032) (i32.const 4) (i32.const 1040)
                                 (i32.const 14) (global.get $tab) (i32.const 0) (i32.const 0)))
    (call $ok (local.get $r))
    (if (i32.ge_s (local.get $r) (i32.const 0))
      (then (global.set $tab (local.get $r))))
    (call $ok (call $ui_grid (i32.const 1056) (i32.const 5) (i32.const 60) (i32.const 40)
                             (i32.const 0) (i32.const 0)))
    (call $card (i32.const 0) (i32.const 1064) (i32.const 3) (i32.const 1092) (i32.const 3)
                (i32.const 8))
    (call $card (i32.const 1) (i32.const 1068) (i32.const 5) (i32.const 1096) (i32.const 3)
                (i32.const 11))
    (call $card (i32.const 2) (i32.const 1076) (i32.const 4) (i32.const 1100) (i32.const 5)
                (i32.const 12))
    (call $card (i32.const 3) (i32.const 1084) (i32.const 4) (i32.const 1108) (i32.const 4)
                (i32.const 6))
    (call $ok (call $ui_grid (i32.const 0) (i32.const 0) (i32.const 0) (i32.const 0)
                             (i32.const 0) (i32.const 0)))
    (call $ok (call $ui_link (i32.const 1120) (i32.const 5) (i32.const 0) (i32.const 0)
                             (i32.const 0) (i32.const 0)))
    (local.set $f (call $ui_focus (i32.const 0) (i32.const 0) (i32.const 2096) (i32.const 32)))
    (global.set $frames (i32.add (global.get $frames) (i32.const 1)))
    (drop (call $pmem (i32.const 0) (global.get $frames) (i32.const 1)))
    (drop (call $pmem (i32.const 1) (global.get $tab) (i32.const 1)))
    (drop (call $pmem (i32.const 2) (global.get $picked) (i32.const 1)))
    (drop (call $pmem (i32.const 3) (global.get $acts) (i32.const 1)))
    (drop (call $pmem (i32.const 5) (local.get $f) (i32.const 1)))
    (drop (call $pmem (i32.const 7) (global.get $left) (i32.const 1)))))
