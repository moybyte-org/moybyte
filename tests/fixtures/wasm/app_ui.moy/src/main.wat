;; App UI Wasm: the public UI API's fixture page (docs/ui_api_2026-10.md) as a
;; compiled app, through the `ui` rows it imports from module "moybyte.app".
;; tests/fixtures/ui_page/page_py.moy and page_lua.moy draw the same page with
;; the same calls, and tests/test_ui_public.py holds the three to the same
;; pixels. It imports every `ui` row and needs no permission: the rows are
;; every compiled app's, served in C, so the run makes no upcall.
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed. Into pmem: 0 frames, 1 sound,
;; 2 volume, 3 the theme row (from 0), 4 saves, 5 keys taken, 6 the swatch's
;; state, 7 the focused id's length, 8 the last row answer that failed,
;; negated (0: none), 9 a hash of the page's rows 40 to 159 (every eighth
;; frame), which a board's run is held to the host's by.
(module
  (import "moy" "rect" (func $rect (param i32 i32 i32 i32 i32)))
  (import "moy" "pmem" (func $pmem (param i32 i32 i32) (result i32)))
  (import "moy" "pix" (func $pix (param i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_page" (func $ui_page
    (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_label" (func $ui_label (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_button" (func $ui_button
    (param i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_toggle" (func $ui_toggle
    (param i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_slider" (func $ui_slider
    (param i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_list" (func $ui_list
    (param i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_part" (func $ui_part
    (param i32 i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_focus" (func $ui_focus (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_keys_taken" (func $ui_keys_taken (result i32)))
  (import "moybyte.app" "ui_at" (func $ui_at (param i32 i32 i32 i32) (result i32)))
  ;; the parts' rows, which tests/fixtures/wasm/app_parts.moy draws
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
  (import "moybyte.app" "ui_icon" (func $ui_icon (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_progress" (func $ui_progress
    (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_table" (func $ui_table
    (param i32 i32 i32 i32 i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_scroll" (func $ui_scroll (param i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_field" (func $ui_field
    (param i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_dialog" (func $ui_dialog
    (param i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_tabs" (func $ui_tabs
    (param i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_grid" (func $ui_grid (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_card" (func $ui_card
    (param i32 i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_popover" (func $ui_popover (param i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_menu" (func $ui_menu
    (param i32 i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_tool" (func $ui_tool
    (param i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_bar" (func $ui_bar (param i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_rule" (func $ui_rule (result i32)))
  (import "moybyte.app" "ui_text_view" (func $ui_text_view
    (param i32 i32 i32 i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (import "moybyte.app" "ui_table_part" (func $ui_table_part (param i32 i32) (result i32)))

  (memory (export "memory") 1 1)

  (data (i32.const 1024) "UI page")
  (data (i32.const 1040) "Hello")
  (data (i32.const 1056) "Sound")
  (data (i32.const 1072) "Volume")
  (data (i32.const 1088) "Theme")
  (data (i32.const 1104) "Moy\00Night\00Berry")
  (data (i32.const 1136) "swatch")
  (data (i32.const 1152) "Save")
  ;; the parts' table: its one column (a part), its rows' cells, and its
  ;; override block -- id "parts", no hint, the flags (wash absent, part,
  ;; bar) and the part's width 0 and rows 17 tall
  (data (i32.const 1200) "\00")
  (data (i32.const 1208) "a\00b\00c\00")
  (data (i32.const 1216) "\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\05\00\00\00"
    "\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff\ff"
    "parts\ff\ff\ff\ff\38\00\00\00\00\00\00\00\11\00\00\00")

  ;; 2048: the page's rect (16 bytes); 2064: the part's (20); 2096: the
  ;; focused id (32); 2200: the table's answers (8); 2208: a part's cell (16).
  (global $sound (mut i32) (i32.const 1))
  (global $vol (mut i32) (i32.const 5))
  (global $cur (mut i32) (i32.const 1))
  (global $saves (mut i32) (i32.const 0))
  (global $frames (mut i32) (i32.const 0))

  ;; A row's answer: below 0 is a failure, kept in pmem 8.
  (func $ok (param $r i32)
    (if (i32.lt_s (local.get $r) (i32.const 0))
      (then (drop (call $pmem (i32.const 8) (i32.sub (i32.const 0) (local.get $r))
                              (i32.const 1))))))

;; A hash of the screen's indices over rows y0 .. y0 + h, the full width.
  (func $hash (param $y0 i32) (param $h i32) (result i32)
    (local $i i32) (local $j i32) (local $acc i32)
    (block $done
      (loop $rows
        (br_if $done (i32.ge_s (local.get $j) (local.get $h)))
        (local.set $i (i32.const 0))
        (block $rowdone
          (loop $cols
            (br_if $rowdone (i32.ge_s (local.get $i) (i32.const 320)))
            (local.set $acc
              (i32.add (i32.mul (local.get $acc) (i32.const 31))
                       (call $pix (local.get $i) (i32.add (local.get $y0) (local.get $j))
                                  (i32.const -1))))
            (local.set $i (i32.add (local.get $i) (i32.const 1)))
            (br $cols)))
        (local.set $j (i32.add (local.get $j) (i32.const 1)))
        (br $rows)))
    (local.get $acc))

  (func (export "_init"))

  (func (export "_update") (param $dt f32))

  (func (export "_draw")
    (local $x i32) (local $y i32) (local $w i32) (local $h i32) (local $f i32) (local $i i32)
    (call $ok (call $ui_page (i32.const 1024) (i32.const 7) (i32.const 0) (i32.const 0)
                             (i32.const 2048) (i32.const 16)))
    (call $ok (call $ui_label (i32.const 1040) (i32.const 5) (i32.const 0) (i32.const 0)))
    (global.set $sound (call $ui_toggle (i32.const 1056) (i32.const 5) (global.get $sound)
                                        (i32.const 0) (i32.const 0)))
    (global.set $vol (call $ui_slider (i32.const 1072) (i32.const 6) (global.get $vol)
                                      (i32.const 0) (i32.const 10) (i32.const 0) (i32.const 0)))
    (global.set $cur (call $ui_list (i32.const 1088) (i32.const 5) (i32.const 1104)
                                    (i32.const 15) (global.get $cur) (i32.const 0)
                                    (i32.const 0)))
    (call $ok (call $ui_part (i32.const 1136) (i32.const 6) (i32.const 0) (i32.const 16)
                             (i32.const 0) (i32.const 0) (i32.const 2064) (i32.const 20)))
    (local.set $x (i32.load (i32.const 2064)))
    (local.set $y (i32.load (i32.const 2068)))
    (local.set $w (i32.load (i32.const 2072)))
    (local.set $h (i32.load (i32.const 2076)))
    (call $rect (i32.add (local.get $x) (i32.const 2)) (i32.add (local.get $y) (i32.const 2))
                (i32.sub (local.get $w) (i32.const 4)) (i32.sub (local.get $h) (i32.const 4))
                (i32.const 8))
    (call $ok (call $ui_at (i32.const 200) (i32.const 214) (i32.const 100) (i32.const 20)))
    (if (call $ui_button (i32.const 1152) (i32.const 4) (i32.const 1) (i32.const 0)
                         (i32.const 0))
      (then (global.set $saves (i32.add (global.get $saves) (i32.const 1)))))
    (call $ok (call $ui_at (i32.const 230) (i32.const 2) (i32.const 80) (i32.const 34)))
    (call $ok (call $ui_table (i32.const 0) (i32.const 0) (i32.const 1200) (i32.const 1)
                              (i32.const 1208) (i32.const 6) (i32.const 1)
                              (i32.const 1216) (i32.const 57) (i32.const 2200) (i32.const 8)))
    (block $parts
      (loop $next
        (local.set $i (call $ui_table_part (i32.const 2208) (i32.const 16)))
        (br_if $parts (i32.lt_s (local.get $i) (i32.const 0)))
        (call $rect (i32.add (i32.load (i32.const 2208)) (i32.const 4))
                    (i32.add (i32.load (i32.const 2212)) (i32.const 3))
                    (i32.const 20)
                    (i32.sub (i32.load (i32.const 2220)) (i32.const 6))
                    (i32.add (i32.const 9) (local.get $i)))
        (br $next)))
    (if (i32.eqz (i32.and (global.get $frames) (i32.const 7)))
      (then (drop (call $pmem (i32.const 9) (call $hash (i32.const 40) (i32.const 120))
                        (i32.const 1)))))
    (local.set $f (call $ui_focus (i32.const 0) (i32.const 0) (i32.const 2096) (i32.const 32)))
    (global.set $frames (i32.add (global.get $frames) (i32.const 1)))
    (drop (call $pmem (i32.const 0) (global.get $frames) (i32.const 1)))
    (drop (call $pmem (i32.const 1) (global.get $sound) (i32.const 1)))
    (drop (call $pmem (i32.const 2) (global.get $vol) (i32.const 1)))
    (drop (call $pmem (i32.const 3) (global.get $cur) (i32.const 1)))
    (drop (call $pmem (i32.const 4) (global.get $saves) (i32.const 1)))
    (drop (call $pmem (i32.const 5) (call $ui_keys_taken) (i32.const 1)))
    (drop (call $pmem (i32.const 6) (i32.load (i32.const 2080)) (i32.const 1)))
    (drop (call $pmem (i32.const 7) (local.get $f) (i32.const 1)))))
