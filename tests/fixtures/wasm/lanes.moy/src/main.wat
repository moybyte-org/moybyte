;; The timed lanes (native/moy_wasm/moy_wasm_session.h): moy_audio renders a
;; 441 Hz square wave into the ring, moy_timer asks to be called every
;; millisecond, and _update publishes what they did. RUNAWAY, the word at 1024
;; (0 here, its twin lanes_runaway.moy's 1), makes moy_audio spin forever
;; after its 200th call, which the deadline monitor must strike.
;;
;; pmem: 0 moy_audio's calls, 1 its frames, 2 moy_timer's calls, 3 the
;; largest gap between two timer calls in microseconds, 4 the stack pointer
;; moy_audio found (its lane's, 8192 less its buffer).
;;
;; Memory: the session's stack below 4096, the audio lane's 4096..8192, the
;; timer lane's 8192..12288, the lanes' words from 1024. One page.
(module
  (import "moy" "pmem" (func $pmem (param i32 i32 i32) (result i32)))

  (memory (export "memory") 1 1)
  (global $sp (export "__stack_pointer") (mut i32) (i32.const 4096))

  (data (i32.const 1024) "\00\00\00\00")

  (func (export "moy_lane_stack") (param $lane i32) (result i32)
    (if (result i32) (local.get $lane)
      (then (i32.const 12288))
      (else (i32.const 8192))))

  ;; 1028 calls, 1032 frames, 1036 the phase
  (func (export "moy_audio") (param $buf i32) (param $n i32) (result i32)
        (local $i i32) (local $ph i32)
    (i32.store (i32.const 1028) (i32.add (i32.load (i32.const 1028)) (i32.const 1)))
    (i32.store (i32.const 1056) (global.get $sp))
    (if (i32.and (i32.load (i32.const 1024))
                 (i32.gt_u (i32.load (i32.const 1028)) (i32.const 200)))
      (then (loop $spin (br $spin))))
    (local.set $ph (i32.load (i32.const 1036)))
    (block $done
      (loop $each
        (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
        (i32.store16 (i32.add (local.get $buf) (i32.shl (local.get $i) (i32.const 1)))
                     (select (i32.const 3000) (i32.const -3000)
                             (i32.lt_u (local.get $ph) (i32.const 25))))
        (local.set $ph (i32.add (local.get $ph) (i32.const 1)))
        (if (i32.ge_u (local.get $ph) (i32.const 50)) (then (local.set $ph (i32.const 0))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $each)))
    (i32.store (i32.const 1036) (local.get $ph))
    (i32.store (i32.const 1032) (i32.add (i32.load (i32.const 1032)) (local.get $n)))
    (local.get $n))

  ;; 1040 calls, 1044 the last t, 1048 the largest gap
  (func (export "moy_timer") (param $t i32) (result i32) (local $gap i32)
    (if (i32.load (i32.const 1040))
      (then
        (local.set $gap (i32.sub (local.get $t) (i32.load (i32.const 1044))))
        (if (i32.gt_u (local.get $gap) (i32.load (i32.const 1048)))
          (then (i32.store (i32.const 1048) (local.get $gap))))))
    (i32.store (i32.const 1044) (local.get $t))
    (i32.store (i32.const 1040) (i32.add (i32.load (i32.const 1040)) (i32.const 1)))
    (i32.const 1000))

  (func (export "_init"))

  (func (export "_update") (param f32)
    (drop (call $pmem (i32.const 0) (i32.load (i32.const 1028)) (i32.const 1)))
    (drop (call $pmem (i32.const 1) (i32.load (i32.const 1032)) (i32.const 1)))
    (drop (call $pmem (i32.const 2) (i32.load (i32.const 1040)) (i32.const 1)))
    (drop (call $pmem (i32.const 3) (i32.load (i32.const 1048)) (i32.const 1)))
    (drop (call $pmem (i32.const 4) (i32.load (i32.const 1056)) (i32.const 1))))

  (func (export "_draw"))
)
