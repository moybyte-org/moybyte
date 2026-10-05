//! The Rust toolchain probe (#224 sprint 1a): a `no_std` C-ABI library whose
//! few functions each pull in one thing a kernel will need from the toolchain.
//! `moy_rs_probe` is the trivial one; the rest show what a target lowers.
#![no_std]

#[cfg(test)]
extern crate std;

use core::ptr;
use core::sync::atomic::{AtomicU32, Ordering};

#[cfg(not(test))]
extern "C" {
    fn abort() -> !;
}

/// A panic is `abort()`: the firmware's crash path, the browser's trap, the
/// host's SIGABRT.
#[cfg(not(test))]
#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {
    // SAFETY: libc's abort takes no arguments and never returns.
    unsafe { abort() }
}

/// The hosted targets ship `compiler_builtins` built for `panic = "unwind"`, and
/// its unwind tables name the personality routine. Nothing unwinds here (the
/// profile is `abort`), so an empty symbol satisfies the linker. The bare-metal
/// targets' prebuilt core is `abort`-built and has no such reference.
#[cfg(all(not(test), not(target_os = "none")))]
#[no_mangle]
pub extern "C" fn rust_eh_personality() {}

/// The trivial export: `3 * x + 1`, wrapping.
#[no_mangle]
pub extern "C" fn moy_rs_probe(x: i32) -> i32 {
    x.wrapping_mul(3).wrapping_add(1)
}

/// 64-bit multiply, divide and remainder: the compiler-runtime symbols
/// (`__muldi3`, `__udivdi3`, `__umoddi3`) the target's libgcc also defines.
#[no_mangle]
pub extern "C" fn moy_rs_probe_u64(a: u64, b: u64) -> u64 {
    let b = b | 1;
    a.wrapping_mul(b).wrapping_add(a / b).wrapping_add(a % b)
}

/// Single-precision arithmetic: hardware on the S3 and P4, so the target's float
/// ABI (ilp32f on the P4) is exercised.
#[no_mangle]
pub extern "C" fn moy_rs_probe_f32(x: f32, y: f32) -> f32 {
    x * y + x
}

/// A 32-bit atomic read-modify-write: lowers to an instruction, a libcall or
/// nothing, per target.
///
/// # Safety
/// `p` must be valid, aligned and not accessed non-atomically meanwhile.
#[no_mangle]
pub unsafe extern "C" fn moy_rs_probe_atomic(p: *mut u32) -> u32 {
    AtomicU32::from_ptr(p).fetch_add(1, Ordering::SeqCst)
}

/// An unaligned 32-bit load: what the S3 does without hardware support.
///
/// # Safety
/// `p` must be valid for four bytes.
#[no_mangle]
pub unsafe extern "C" fn moy_rs_probe_load_u32(p: *const u8) -> u32 {
    ptr::read_unaligned(p as *const u32)
}

/// A checked slice index: out of range is a panic, so an `abort()`.
///
/// # Safety
/// `p` must be valid for `n` bytes.
#[no_mangle]
pub unsafe extern "C" fn moy_rs_probe_at(p: *const u8, n: usize, i: usize) -> u8 {
    core::slice::from_raw_parts(p, n)[i]
}

/// The sum of `n` bytes.
///
/// # Safety
/// `p` must be valid for `n` bytes.
#[no_mangle]
pub unsafe extern "C" fn moy_rs_probe_sum(p: *const u8, n: usize) -> u32 {
    core::slice::from_raw_parts(p, n)
        .iter()
        .fold(0u32, |a, &b| a.wrapping_add(b as u32))
}

/// One byte past the end: undefined behaviour, here only for the sanitizer and
/// Miri checks.
///
/// # Safety
/// None: this is the bug.
#[cfg(feature = "bug")]
#[no_mangle]
pub unsafe extern "C" fn moy_rs_probe_oob(p: *const u8, n: usize) -> u8 {
    *p.add(n)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn trivial() {
        assert_eq!(moy_rs_probe(41), 124);
        assert_eq!(moy_rs_probe_u64(1000, 7), 1000 * 7 + 142 + 6);
        assert_eq!(moy_rs_probe_f32(2.0, 3.0), 8.0);
    }

    #[test]
    fn atomic_and_loads() {
        let mut a = 5u32;
        assert_eq!(unsafe { moy_rs_probe_atomic(&mut a) }, 5);
        assert_eq!(a, 6);
        let buf = [1u8, 2, 3, 4, 5, 6, 7, 8];
        let v = unsafe { moy_rs_probe_load_u32(buf.as_ptr().add(1)) };
        assert_eq!(v, u32::from_le_bytes([2, 3, 4, 5]));
        assert_eq!(unsafe { moy_rs_probe_sum(buf.as_ptr(), 8) }, 36);
        assert_eq!(unsafe { moy_rs_probe_at(buf.as_ptr(), 8, 7) }, 8);
    }

    #[cfg(feature = "bug")]
    #[test]
    fn the_bug() {
        let buf = [1u8, 2, 3, 4];
        let _ = unsafe { moy_rs_probe_oob(buf.as_ptr(), buf.len()) };
    }
}
