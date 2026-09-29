;; par across the cores, checked by the cart itself. Every _draw hands par
;; eight items that each fill 8 KiB of region A, then runs the same eight one
;; after another into region B by calling the item itself, and counts the
;; bytes where A and B differ: a board that ran the items at once on its
;; cores must leave exactly what running them in order leaves. An item also
;; keeps its running value on its own stack, below the stack pointer par gave
;; it, so two items handed one stack would disagree too. The frame's number
;; reaches the items through memory (1016), never through a global: an item
;; uses no mutable global but its stack pointer.
;;
;; pmem: 0 the bytes that differed, over every frame; 1 the frames; 2 the
;; items whose stack pointer was not the top of their own 2 KiB, as each
;; recorded it at 1024 + 4i; 3 the frames after which the caller's stack
;; pointer was not back; 4 the last frame's checksum of A.
;;
;; The frame is region A and the start of B, one index a byte, blitted
;; through the cart's palette. Memory: the stacks from 4096 (8 x 2 KiB), A
;; from 65536, B from 131072, 64 KiB each. Four pages.
(module
  (import "moy" "par" (func $par (param i32 i32 i32 i32)))
  (import "moy" "blit" (func $blit (param i32 i32)))
  (import "moy" "pmem" (func $pmem (param i32 i32 i32) (result i32)))

  (memory (export "memory") 4 4)
  (global $sp (export "__stack_pointer") (mut i32) (i32.const 4096))
  (global $frame (mut i32) (i32.const 0))

  ;; region (65536 or 131072) + 8192 i: sixteen rounds of an LCG seeded by
  ;; the item, the frame and the round, the value carried through the stack
  (func $item (export "_par") (param $i i32) (param $region i32)
        (local $j i32) (local $r i32) (local $v i32) (local $at i32)
    (if (i32.eq (local.get $region) (i32.const 65536))
      (then (i32.store offset=1024 (i32.shl (local.get $i) (i32.const 2)) (global.get $sp))))
    (local.set $at (i32.add (local.get $region) (i32.shl (local.get $i) (i32.const 13))))
    (i32.store (i32.sub (global.get $sp) (i32.const 16))
               (i32.add (i32.mul (local.get $i) (i32.const 2654435761))
                        (i32.load (i32.const 1016))))
    (block $rounds
      (loop $round
        (br_if $rounds (i32.ge_u (local.get $r) (i32.const 16)))
        (local.set $j (i32.const 0))
        (block $bytes
          (loop $byte
            (br_if $bytes (i32.ge_u (local.get $j) (i32.const 8192)))
            (local.set $v (i32.add (i32.mul (i32.load (i32.sub (global.get $sp) (i32.const 16)))
                                            (i32.const 1103515245))
                                   (i32.const 12345)))
            (i32.store (i32.sub (global.get $sp) (i32.const 16)) (local.get $v))
            (i32.store8 (i32.add (local.get $at) (local.get $j))
                        (i32.xor (i32.load8_u (i32.add (local.get $at) (local.get $j)))
                                 (i32.shr_u (local.get $v) (i32.const 24))))
            (local.set $j (i32.add (local.get $j) (i32.const 1)))
            (br $byte)))
        (local.set $r (i32.add (local.get $r) (i32.const 1)))
        (br $round))))

  (func (export "_init"))
  (func (export "_update") (param f32))

  (func (export "_draw") (local $k i32) (local $diff i32) (local $sum i32) (local $bad i32)
    (global.set $frame (i32.add (global.get $frame) (i32.const 1)))
    (i32.store (i32.const 1016) (global.get $frame))
    (call $par (i32.const 8) (i32.const 65536) (i32.const 4096) (i32.const 2048))
    (local.set $k (i32.const 0))
    (block $sps
      (loop $each_sp
        (br_if $sps (i32.ge_u (local.get $k) (i32.const 8)))
        (if (i32.ne (i32.load offset=1024 (i32.shl (local.get $k) (i32.const 2)))
                    (i32.add (i32.const 4096)
                             (i32.shl (i32.add (local.get $k) (i32.const 1)) (i32.const 11))))
          (then (local.set $bad (i32.add (local.get $bad) (i32.const 1)))))
        (local.set $k (i32.add (local.get $k) (i32.const 1)))
        (br $each_sp)))
    (if (i32.ne (global.get $sp) (i32.const 4096))
      (then (drop (call $pmem (i32.const 3)
                        (i32.add (call $pmem (i32.const 3) (i32.const 0) (i32.const 0))
                                 (i32.const 1))
                        (i32.const 1)))))
    (local.set $k (i32.const 0))
    (block $items
      (loop $each
        (br_if $items (i32.ge_u (local.get $k) (i32.const 8)))
        (call $item (local.get $k) (i32.const 131072))
        (local.set $k (i32.add (local.get $k) (i32.const 1)))
        (br $each)))
    (local.set $k (i32.const 0))
    (block $done
      (loop $cmp
        (br_if $done (i32.ge_u (local.get $k) (i32.const 65536)))
        (local.set $sum (i32.add (i32.mul (local.get $sum) (i32.const 31))
                                 (i32.load8_u offset=65536 (local.get $k))))
        (if (i32.ne (i32.load8_u offset=65536 (local.get $k))
                    (i32.load8_u offset=131072 (local.get $k)))
          (then (local.set $diff (i32.add (local.get $diff) (i32.const 1)))))
        (local.set $k (i32.add (local.get $k) (i32.const 1)))
        (br $cmp)))
    (drop (call $pmem (i32.const 0)
                (i32.add (call $pmem (i32.const 0) (i32.const 0) (i32.const 0)) (local.get $diff))
                (i32.const 1)))
    (drop (call $pmem (i32.const 1) (global.get $frame) (i32.const 1)))
    (drop (call $pmem (i32.const 2)
                (i32.add (call $pmem (i32.const 2) (i32.const 0) (i32.const 0)) (local.get $bad))
                (i32.const 1)))
    (drop (call $pmem (i32.const 4) (local.get $sum) (i32.const 1)))
    (call $blit (i32.const 65536) (i32.const 0)))
)
