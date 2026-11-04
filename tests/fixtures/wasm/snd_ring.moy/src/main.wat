;; The render-ahead ring from a compiled cart's loop: each _update asks `snd`
;; for the room and fills it with a 441 Hz square wave, so the ring is as deep
;; as the manifest's "audio_ring" (docs/os_2026-11/hardware.md section 2).
;; pmem 0: the frames queued.
(module
  (import "moy" "snd" (func $snd (param i32 i32) (result i32)))
  (import "moy" "pmem" (func $pmem (param i32 i32 i32) (result i32)))

  (memory (export "memory") 1 1)
  (global $ph (mut i32) (i32.const 0))
  (global $sent (mut i32) (i32.const 0))

  (func (export "_init"))

  (func (export "_update") (param f32) (local $n i32) (local $i i32)
    (local.set $n (call $snd (i32.const 0) (i32.const 0)))
    (if (i32.gt_u (local.get $n) (i32.const 16384)) (then (local.set $n (i32.const 16384))))
    (block $done
      (loop $each
        (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
        (i32.store16 (i32.add (i32.const 16384) (i32.shl (local.get $i) (i32.const 1)))
                     (select (i32.const 3000) (i32.const -3000)
                             (i32.lt_u (global.get $ph) (i32.const 25))))
        (global.set $ph (i32.add (global.get $ph) (i32.const 1)))
        (if (i32.ge_u (global.get $ph) (i32.const 50)) (then (global.set $ph (i32.const 0))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $each)))
    (global.set $sent (i32.add (global.get $sent)
                               (call $snd (i32.const 16384) (local.get $n))))
    (drop (call $pmem (i32.const 0) (global.get $sent) (i32.const 1))))

  (func (export "_draw"))
)
