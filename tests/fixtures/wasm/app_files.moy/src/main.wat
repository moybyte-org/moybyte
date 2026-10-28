;; App Files Wasm: the public UI API's Files verbs (docs/look_2026-10.md step
;; 21) as a compiled app, through the `ui` rows it imports from module
;; "moybyte.app": a list in columns, a scroll region, a field, an icon on a
;; button and a dialog. tests/fixtures/ui_parts/files_py.moy and
;; files_lua.moy draw the same page with the same calls, and
;; tests/test_ui_parts.py holds the three to the same pixels.
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed. Into pmem: 0 frames, 1 the current
;; row (from 0), 2 the rows opened, 3 the name's length, 4 the times Enter was
;; pressed in it, 5 the focused id's length, 6 the last row answer that
;; failed, negated (0: none), 7 the dialog's answers (each the button + 1).
(module
  (import "moy" "pmem" (func $pmem (param i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_page" (func $ui_page
    (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_label" (func $ui_label (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_button" (func $ui_button
    (param i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_link" (func $ui_link (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_focus" (func $ui_focus (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_at" (func $ui_at (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_icon" (func $ui_icon (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_table" (func $ui_table
    (param i32 i32 i32 i32 i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_scroll" (func $ui_scroll (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_field" (func $ui_field
    (param i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_dialog" (func $ui_dialog
    (param i32 i32 i32 i32 i32 i32 i32) (result i32)))

  (memory (export "memory") 1 1)

  (data (i32.const 1024) "Files")
  (data (i32.const 1032) "Art")
  (data (i32.const 1040) "\00Name\00Kind\00")
  (data (i32.const 1056)
    "paint\00castle\00Drawing\00music\00theme\00Song\00notes\00story\00Note\00"
    "game\00pong\00Game\00paint\00dragon\00Drawing\00")
  (data (i32.const 1152) "more")
  (data (i32.const 1160) "One")
  (data (i32.const 1164) "1")
  (data (i32.const 1168) "Two")
  (data (i32.const 1172) "2")
  (data (i32.const 1176) "Three")
  (data (i32.const 1184) "3")
  (data (i32.const 1192) "Name")
  (data (i32.const 1200) "lock")
  (data (i32.const 1208) "Delete")
  (data (i32.const 1216) "Delete?")
  (data (i32.const 1224) "Cancel\00Delete")
  (data (i32.const 1240) "For good.")
  ;; 2048: the page's rect (16 bytes); 2064: the table's answers (8); 2080:
  ;; the name (8); 2096: the focused id (32).
  (data (i32.const 2080) "pic")

  (global $cur (mut i32) (i32.const 0))
  (global $opened (mut i32) (i32.const 0))
  (global $len (mut i32) (i32.const 3))
  (global $dones (mut i32) (i32.const 0))
  (global $asking (mut i32) (i32.const 0))
  (global $picks (mut i32) (i32.const 0))
  (global $frames (mut i32) (i32.const 0))

  ;; A row's answer: below 0 is a failure, kept in pmem 6.
  (func $ok (param $r i32)
    (if (i32.lt_s (local.get $r) (i32.const 0))
      (then (drop (call $pmem (i32.const 6) (i32.sub (i32.const 0) (local.get $r))
                              (i32.const 1))))))

  (func (export "_init"))

  (func (export "_update") (param $dt f32))

  (func (export "_draw")
    (local $x i32) (local $y i32) (local $w i32) (local $r i32) (local $p i32)
    (local $f i32)
    (call $ok (call $ui_page (i32.const 1024) (i32.const 5) (i32.const 0) (i32.const 0)
                             (i32.const 2048) (i32.const 16)))
    (local.set $x (i32.load (i32.const 2048)))
    (local.set $y (i32.load (i32.const 2052)))
    (local.set $w (i32.load (i32.const 2056)))
    (call $ok (call $ui_at (local.get $x) (local.get $y) (local.get $w) (i32.const 97)))
    (call $ok (call $ui_table (i32.const 1032) (i32.const 3) (i32.const 1040) (i32.const 11)
                              (i32.const 1056) (i32.const 91) (global.get $cur)
                              (i32.const 0) (i32.const 0) (i32.const 2064) (i32.const 8)))
    (global.set $cur (i32.load (i32.const 2064)))
    (if (i32.load (i32.const 2068))
      (then (global.set $opened (i32.add (global.get $opened) (i32.const 1)))))
    (call $ok (call $ui_at (local.get $x) (i32.add (local.get $y) (i32.const 97))
                           (local.get $w) (i32.const 40)))
    (call $ok (call $ui_scroll (i32.const 1152) (i32.const 4) (i32.const 0) (i32.const 0)))
    (call $ok (call $ui_link (i32.const 1160) (i32.const 3) (i32.const 1164) (i32.const 1)
                             (i32.const 0) (i32.const 0)))
    (call $ok (call $ui_link (i32.const 1168) (i32.const 3) (i32.const 1172) (i32.const 1)
                             (i32.const 0) (i32.const 0)))
    (call $ok (call $ui_link (i32.const 1176) (i32.const 5) (i32.const 1184) (i32.const 1)
                             (i32.const 0) (i32.const 0)))
    (call $ok (call $ui_scroll (i32.const 0) (i32.const 0) (i32.const 0) (i32.const 0)))
    (call $ok (call $ui_at (local.get $x) (i32.add (local.get $y) (i32.const 141))
                           (local.get $w) (i32.const 20)))
    (local.set $r (call $ui_field (i32.const 1192) (i32.const 4) (i32.const 2080) (i32.const 8)
                                  (global.get $len) (i32.const 0) (i32.const 0)))
    (call $ok (local.get $r))
    (global.set $len (i32.and (local.get $r) (i32.const 0xffff)))
    (if (i32.and (local.get $r) (i32.const 0x10000))
      (then (global.set $dones (i32.add (global.get $dones) (i32.const 1)))))
    (call $ok (call $ui_at (i32.add (local.get $x) (i32.const 8))
                           (i32.add (local.get $y) (i32.const 165)) (i32.const 96)
                           (i32.const 20)))
    (call $ok (call $ui_icon (i32.const 1200) (i32.const 4) (i32.const 0) (i32.const 0)))
    (local.set $r (call $ui_button (i32.const 1208) (i32.const 6) (i32.const 3) (i32.const 0)
                                   (i32.const 0)))
    (call $ok (local.get $r))
    (if (i32.gt_s (local.get $r) (i32.const 0))
      (then (global.set $asking (i32.const 1))))
    (if (global.get $asking)
      (then
        ;; a dialog answers -1 for no button, and its end -1 always
        (local.set $p (call $ui_dialog (i32.const 1216) (i32.const 7) (i32.const 1224)
                                       (i32.const 13) (i32.const 3) (i32.const 0)
                                       (i32.const 0)))
        (call $ok (call $ui_label (i32.const 1240) (i32.const 9) (i32.const 0) (i32.const 0)))
        (drop (call $ui_dialog (i32.const 0) (i32.const 0) (i32.const 0) (i32.const 0)
                               (i32.const 0) (i32.const 0) (i32.const 0)))
        (if (i32.ge_s (local.get $p) (i32.const 0))
          (then
            (global.set $asking (i32.const 0))
            (global.set $picks (i32.add (global.get $picks)
                                        (i32.add (local.get $p) (i32.const 1))))))))
    (local.set $f (call $ui_focus (i32.const 0) (i32.const 0) (i32.const 2096) (i32.const 32)))
    (global.set $frames (i32.add (global.get $frames) (i32.const 1)))
    (drop (call $pmem (i32.const 0) (global.get $frames) (i32.const 1)))
    (drop (call $pmem (i32.const 1) (global.get $cur) (i32.const 1)))
    (drop (call $pmem (i32.const 2) (global.get $opened) (i32.const 1)))
    (drop (call $pmem (i32.const 3) (global.get $len) (i32.const 1)))
    (drop (call $pmem (i32.const 4) (global.get $dones) (i32.const 1)))
    (drop (call $pmem (i32.const 5) (local.get $f) (i32.const 1)))
    (drop (call $pmem (i32.const 7) (global.get $picks) (i32.const 1)))))
