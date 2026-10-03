;; Files Wasm: a compiled cart's writable files (moy-spec SPEC.md 16.12),
;; across a restart.
;;
;; This file is the cart's SOURCE; main.wasm is built from it with
;; tools/wasm_cart.py and never committed. The manifest declares "saves/" and
;; "options.cfg" writable, and the cart ships options.cfg ("volume=5"),
;; readme.txt and saves/slot0.sav.
;;
;; It runs in two turns over one store, and _init tells them apart by whether
;; saves/slot1.sav has been written. The FIRST writes: options.cfg over its
;; shipped default, saves/slot1.sav (300 bytes, byte i = 7i mod 256),
;; saves/Case.sav and saves/case.sav, which a FAT card must keep apart; and it
;; checks what write must refuse, what erase answers twice, and list. The
;; SECOND, after the console restarted, finds every one of them as it was,
;; then erases them all -- so the run after it is a first again.
;;
;; It checks itself and reports through pmem, where a test reads it: slot 0
;; the turn (1 or 2), slot 1 how many checks failed, slot 2 a bit per failed
;; check, slot 3 how many paths list found under "saves/". The screen is
;; green when every check passed and red when one did not.
(module
  (import "moy" "read" (func $read (param i32 i32 i32 i32 i32) (result i32)))
  (import "moy" "write" (func $write (param i32 i32 i32 i32) (result i32)))
  (import "moy" "erase" (func $erase (param i32 i32) (result i32)))
  (import "moy" "list" (func $list (param i32 i32 i32 i32 i32) (result i32)))
  (import "moy" "pmem" (func $pmem (param i32 i32 i32) (result i32)))
  (import "moy" "cls" (func $cls (param i32)))
  (import "moy" "print" (func $print (param i32 i32 i32 i32 i32)))

  (memory (export "memory") 17 17)
  (data (i32.const 1024) "options.cfg")
  (data (i32.const 1040) "saves/slot1.sav")
  (data (i32.const 1056) "volume=9!")
  (data (i32.const 1072) "readme.txt")
  (data (i32.const 1088) "saves")
  (data (i32.const 1104) "saves/../readme.txt")
  (data (i32.const 1128) "saves/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
  (data (i32.const 1200) "saves/big.bin")
  (data (i32.const 1216) "saves/Case.sav")
  (data (i32.const 1232) "saves/case.sav")
  (data (i32.const 1248) "saves/tmp.sav")
  (data (i32.const 1264) "saves/")
  (data (i32.const 1280) "FILES TURN 1 OK")
  (data (i32.const 1296) "FILES TURN 2 OK")
  (data (i32.const 1312) "FILES FAILED")

  (global $turn (mut i32) (i32.const 0))
  (global $bad (mut i32) (i32.const 0))
  (global $mask (mut i32) (i32.const 0))
  (global $listed (mut i32) (i32.const 0))

  ;; check k: what an import answered against what the rule says
  (func $want (param $k i32) (param $got i32) (param $want i32)
    (if (i32.ne (local.get $got) (local.get $want))
      (then
        (global.set $bad (i32.add (global.get $bad) (i32.const 1)))
        (global.set $mask (i32.or (global.get $mask)
                                  (i32.shl (i32.const 1) (local.get $k)))))))

  (func $size (param $p i32) (param $n i32) (result i32)
    (call $read (local.get $p) (local.get $n) (i32.const 0) (i32.const 0) (i32.const 0)))

  ;; how many paths begin with "saves/", walking list until it answers -1
  (func $count (result i32) (local $i i32)
    (block $done
      (loop $each
        (br_if $done (i32.lt_s (call $list (i32.const 1264) (i32.const 6) (local.get $i)
                                            (i32.const 0) (i32.const 0))
                               (i32.const 0)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br_if $done (i32.ge_u (local.get $i) (i32.const 64)))
        (br $each)))
    (local.get $i))

  ;; how many of the 300 bytes at 8192 are not byte i = 7i mod 256
  (func $wrong (result i32) (local $i i32) (local $n i32)
    (block $done
      (loop $each
        (br_if $done (i32.ge_u (local.get $i) (i32.const 300)))
        (if (i32.ne (i32.load8_u offset=8192 (local.get $i))
                    (i32.and (i32.mul (local.get $i) (i32.const 7)) (i32.const 255)))
          (then (local.set $n (i32.add (local.get $n) (i32.const 1)))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $each)))
    (local.get $n))

  (func $first (local $i i32)
    (block $done
      (loop $each
        (br_if $done (i32.ge_u (local.get $i) (i32.const 300)))
        (i32.store8 offset=12288 (local.get $i) (i32.mul (local.get $i) (i32.const 7)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $each)))
    (call $want (i32.const 0) (call $size (i32.const 1024) (i32.const 11)) (i32.const 8))
    (call $want (i32.const 1) (call $write (i32.const 1024) (i32.const 11) (i32.const 1056)
                                           (i32.const 9)) (i32.const 0))
    (call $want (i32.const 2) (call $size (i32.const 1024) (i32.const 11)) (i32.const 9))
    (call $want (i32.const 3) (call $write (i32.const 1072) (i32.const 10) (i32.const 1056)
                                           (i32.const 9)) (i32.const -1))
    (call $want (i32.const 4) (call $write (i32.const 1088) (i32.const 5) (i32.const 1056)
                                           (i32.const 9)) (i32.const -1))
    (call $want (i32.const 5) (call $write (i32.const 1104) (i32.const 19) (i32.const 1056)
                                           (i32.const 9)) (i32.const -1))
    (call $want (i32.const 6) (call $write (i32.const 1128) (i32.const 65) (i32.const 1056)
                                           (i32.const 9)) (i32.const -1))
    (call $want (i32.const 7) (call $write (i32.const 1200) (i32.const 13) (i32.const 0)
                                           (i32.const 1048577)) (i32.const -2))
    (call $want (i32.const 8) (call $write (i32.const 1040) (i32.const 15) (i32.const 12288)
                                           (i32.const 300)) (i32.const 0))
    (call $want (i32.const 9) (call $write (i32.const 1216) (i32.const 14) (i32.const 1222)
                                           (i32.const 1)) (i32.const 0))
    (call $want (i32.const 10) (call $write (i32.const 1232) (i32.const 14) (i32.const 1238)
                                            (i32.const 2)) (i32.const 0))
    (call $want (i32.const 11) (call $write (i32.const 1248) (i32.const 13) (i32.const 1056)
                                            (i32.const 1)) (i32.const 0))
    (call $want (i32.const 12) (call $erase (i32.const 1248) (i32.const 13)) (i32.const 0))
    (call $want (i32.const 13) (call $erase (i32.const 1248) (i32.const 13)) (i32.const -1))
    (call $want (i32.const 14) (call $erase (i32.const 1072) (i32.const 10)) (i32.const -1))
    (call $want (i32.const 15) (call $size (i32.const 1216) (i32.const 14)) (i32.const 1))
    (call $want (i32.const 16) (call $size (i32.const 1232) (i32.const 14)) (i32.const 2))
    ;; the first path in bytewise order is "saves/Case.sav", 14 bytes
    (call $want (i32.const 17) (call $list (i32.const 1264) (i32.const 6) (i32.const 0)
                                           (i32.const 4096) (i32.const 32)) (i32.const 14))
    (call $want (i32.const 18) (i32.load8_u (i32.const 4102)) (i32.const 67))
    (global.set $listed (call $count))
    (call $want (i32.const 19) (global.get $listed) (i32.const 4)))

  (func $second
    (call $want (i32.const 0) (call $read (i32.const 1040) (i32.const 15) (i32.const 0)
                                          (i32.const 8192) (i32.const 300)) (i32.const 300))
    (call $want (i32.const 1) (call $wrong) (i32.const 0))
    (call $want (i32.const 2) (call $size (i32.const 1024) (i32.const 11)) (i32.const 9))
    (call $want (i32.const 3) (call $size (i32.const 1216) (i32.const 14)) (i32.const 1))
    (call $want (i32.const 4) (call $size (i32.const 1232) (i32.const 14)) (i32.const 2))
    (global.set $listed (call $count))
    (call $want (i32.const 5) (global.get $listed) (i32.const 4))
    ;; then everything goes, and the shipped files are what is left
    (call $want (i32.const 6) (call $erase (i32.const 1024) (i32.const 11)) (i32.const 0))
    (call $want (i32.const 7) (call $size (i32.const 1024) (i32.const 11)) (i32.const 8))
    (call $want (i32.const 8) (call $erase (i32.const 1040) (i32.const 15)) (i32.const 0))
    (call $want (i32.const 9) (call $erase (i32.const 1216) (i32.const 14)) (i32.const 0))
    (call $want (i32.const 10) (call $erase (i32.const 1232) (i32.const 14)) (i32.const 0))
    (call $want (i32.const 11) (call $count) (i32.const 1)))

  (func (export "_init")
    (if (call $size (i32.const 1040) (i32.const 15))
      (then (global.set $turn (i32.const 2)) (call $second))
      (else (global.set $turn (i32.const 1)) (call $first)))
    (drop (call $pmem (i32.const 0) (global.get $turn) (i32.const 1)))
    (drop (call $pmem (i32.const 1) (global.get $bad) (i32.const 1)))
    (drop (call $pmem (i32.const 2) (global.get $mask) (i32.const 1)))
    (drop (call $pmem (i32.const 3) (global.get $listed) (i32.const 1))))

  (func (export "_update") (param $dt f32))

  (func (export "_draw")
    (if (global.get $bad)
      (then
        (call $cls (i32.const 8))
        (call $print (i32.const 1312) (i32.const 12) (i32.const 8) (i32.const 8) (i32.const 7)))
      (else
        (call $cls (i32.const 11))
        (call $print (select (i32.const 1280) (i32.const 1296)
                             (i32.eq (global.get $turn) (i32.const 1)))
                     (i32.const 15) (i32.const 8) (i32.const 8) (i32.const 0)))))
)
