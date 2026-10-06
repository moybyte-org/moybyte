//! The store's index, the Rust twin (`../moy_index.h` has the contract,
//! `runtime/moy_index.py` is the reference).
//!
//! Rows live in a vector indexed by slot that grows by doubling to `SLOTS`; a
//! row's path is its own allocation, hashed once (FNV-1a). A path finds its
//! slot through an open-addressed table of slot + 1 entries, kept at most
//! three-quarters full, tombstones included. Every allocation is fallible and
//! goes through the host's two imports (`Host`, the global allocator): a
//! refused one is `NoMem` and leaves the table's rows as they were.
//!
//! The table is safe Rust. `unsafe` is the C ABI at the bottom of this file
//! (raw pointers in, a heap-allocated `Index` out) and the allocator.
#![no_std]

extern crate alloc;
#[cfg(test)]
extern crate std;

use alloc::boxed::Box;
use alloc::vec::Vec;
use core::alloc::{GlobalAlloc, Layout};
use core::ffi::{c_char, c_int};

pub const SLOT_BITS: u32 = 12;
pub const SLOTS: u32 = 1 << SLOT_BITS;
pub const GEN_MAX: u32 = (1 << 18) - 1;
const SLOT_MASK: u32 = SLOTS - 1;

const EMPTY: u16 = 0;
const TOMB: u16 = 0xffff;

/// A refusal, numbered as `moy_index.h` numbers it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error {
    /// The handle names no live row.
    Stale = 1,
    /// Every slot is taken: the store's ENOSPC.
    Full = 2,
    /// The host's allocator refused; nothing changed.
    NoMem = 3,
}

struct Row {
    hash: u32,
    path: Box<[u8]>,
}

struct Slot {
    gen: u32,
    row: Option<Row>,
}

pub struct Index {
    slots: Vec<Slot>, // one per slot ever taken: the high-water mark
    tab: Vec<u16>,    // a power of two long, or empty before the first row
    live: u32,
    free_lo: u32,  // every slot below it is live
    tab_fill: u32, // live entries plus tombstones
}

fn hash_of(p: &[u8]) -> u32 {
    let mut h: u32 = 2166136261;
    for &b in p {
        h ^= u32::from(b);
        h = h.wrapping_mul(16777619);
    }
    h
}

/// Puts slot `s` in the first free entry of `hash`'s probe run; true when that
/// entry was empty rather than a tombstone.
fn put(tab: &mut [u16], hash: u32, s: usize) -> bool {
    let mask = tab.len() - 1;
    let mut i = hash as usize & mask;
    while tab[i] != EMPTY && tab[i] != TOMB {
        i = (i + 1) & mask;
    }
    let fresh = tab[i] == EMPTY;
    tab[i] = (s + 1) as u16;
    fresh
}

fn copy(p: &[u8]) -> Result<Box<[u8]>, Error> {
    let mut v = Vec::new();
    v.try_reserve_exact(p.len()).map_err(|_| Error::NoMem)?;
    v.extend_from_slice(p);
    Ok(v.into_boxed_slice())
}

impl Default for Index {
    fn default() -> Self {
        Self::new()
    }
}

impl Index {
    pub const fn new() -> Self {
        Index { slots: Vec::new(), tab: Vec::new(), live: 0, free_lo: 0, tab_fill: 0 }
    }

    fn handle_of(&self, s: usize) -> u32 {
        (self.slots[s].gen << SLOT_BITS) | s as u32
    }

    /// The slot `h` names and its row, or None. A generation is never 0, so
    /// neither is a live handle.
    fn row_of(&self, h: u32) -> Option<(usize, &Row)> {
        let s = (h & SLOT_MASK) as usize;
        let slot = self.slots.get(s)?;
        match &slot.row {
            Some(r) if slot.gen == h >> SLOT_BITS => Some((s, r)),
            _ => None,
        }
    }

    fn lookup(&self, p: &[u8], hash: u32) -> Option<usize> {
        if self.tab.is_empty() {
            return None;
        }
        let mask = self.tab.len() - 1;
        let mut i = hash as usize & mask;
        loop {
            let e = self.tab[i];
            if e == EMPTY {
                return None;
            }
            if e != TOMB {
                let s = usize::from(e) - 1;
                if let Some(r) = &self.slots[s].row {
                    if r.hash == hash && *r.path == *p {
                        return Some(s);
                    }
                }
            }
            i = (i + 1) & mask;
        }
    }

    /// Room for one more entry: rebuilt (grown, or rid of its tombstones) when
    /// the next insert would take it past three-quarters full.
    fn tab_reserve(&mut self) -> Result<(), Error> {
        if (self.tab_fill + 1) * 4 <= self.tab.len() as u32 * 3 {
            return Ok(());
        }
        let mut cap = 8usize;
        while cap < (self.live as usize + 1) * 2 {
            cap <<= 1;
        }
        let mut tab = Vec::new();
        tab.try_reserve_exact(cap).map_err(|_| Error::NoMem)?;
        tab.resize(cap, EMPTY);
        for (s, slot) in self.slots.iter().enumerate() {
            if let Some(r) = &slot.row {
                put(&mut tab, r.hash, s);
            }
        }
        self.tab = tab;
        self.tab_fill = self.live;
        Ok(())
    }

    fn slots_reserve(&mut self) -> Result<(), Error> {
        let n = self.slots.len();
        if n < self.slots.capacity() {
            return Ok(());
        }
        self.slots.try_reserve_exact(if n == 0 { 8 } else { n }).map_err(|_| Error::NoMem)
    }

    /// The row for `p`, made in the lowest free slot when absent.
    pub fn intern(&mut self, p: &[u8]) -> Result<u32, Error> {
        let hash = hash_of(p);
        if let Some(s) = self.lookup(p, hash) {
            return Ok(self.handle_of(s));
        }
        let fresh = self.live as usize == self.slots.len();
        if fresh && self.slots.len() == SLOTS as usize {
            return Err(Error::Full);
        }
        if fresh {
            self.slots_reserve()?;
        }
        self.tab_reserve()?;
        let row = Row { hash, path: copy(p)? };
        let s = if fresh {
            self.slots.push(Slot { gen: 1, row: None });
            self.free_lo = self.slots.len() as u32;
            self.slots.len() - 1
        } else {
            let mut s = self.free_lo as usize;
            while self.slots[s].row.is_some() {
                s += 1;
            }
            self.free_lo = s as u32 + 1;
            s
        };
        self.slots[s].row = Some(row);
        self.live += 1;
        if put(&mut self.tab, hash, s) {
            self.tab_fill += 1;
        }
        Ok(self.handle_of(s))
    }

    /// The row's handle, or 0 when `p` has none.
    pub fn find(&self, p: &[u8]) -> u32 {
        self.lookup(p, hash_of(p)).map_or(0, |s| self.handle_of(s))
    }

    pub fn path(&self, h: u32) -> Result<&[u8], Error> {
        self.row_of(h).map(|(_, r)| &*r.path).ok_or(Error::Stale)
    }

    pub fn valid(&self, h: u32) -> bool {
        self.row_of(h).is_some()
    }

    /// The row goes; `h`, and every copy of it, is stale.
    pub fn release(&mut self, h: u32) -> Result<(), Error> {
        let (s, hash) = self.row_of(h).map(|(s, r)| (s, r.hash)).ok_or(Error::Stale)?;
        let mask = self.tab.len() - 1;
        let mut i = hash as usize & mask;
        while usize::from(self.tab[i]) != s + 1 {
            i = (i + 1) & mask;
        }
        if self.tab[(i + 1) & mask] == EMPTY {
            self.tab[i] = EMPTY; // the end of a probe run needs no tombstone
            self.tab_fill -= 1;
        } else {
            self.tab[i] = TOMB;
        }
        let slot = &mut self.slots[s];
        slot.row = None;
        slot.gen = if slot.gen >= GEN_MAX { 1 } else { slot.gen + 1 };
        self.live -= 1;
        self.free_lo = self.free_lo.min(s as u32);
        Ok(())
    }

    pub fn count(&self) -> u32 {
        self.live
    }

    /// Bounds the walk of the live rows in slot order.
    pub fn slots(&self) -> u32 {
        self.slots.len() as u32
    }

    /// The handle of the row in `slot`, or 0 when it is free.
    pub fn at(&self, slot: u32) -> u32 {
        match self.slots.get(slot as usize) {
            Some(Slot { gen, row: Some(_) }) => (gen << SLOT_BITS) | slot,
            _ => 0,
        }
    }
}

// -- the host's allocator -------------------------------------------------------

extern "C" {
    fn moy_index_host_alloc(n: usize) -> *mut u8;
    fn moy_index_host_free(p: *mut u8, n: usize);
}

/// Every byte the index holds, from the host's two imports. The host's blocks
/// are aligned as malloc's; nothing here asks for more than a pointer's.
pub struct Host;

const HOST_ALIGN: usize = core::mem::align_of::<u64>();

// SAFETY: the host's alloc returns `n` zeroed bytes aligned for any C type, or
// NULL, and its free takes back exactly what alloc gave with the same `n`.
unsafe impl GlobalAlloc for Host {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        if layout.align() > HOST_ALIGN {
            return core::ptr::null_mut();
        }
        moy_index_host_alloc(layout.size())
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        self.alloc(layout)
    }

    unsafe fn dealloc(&self, p: *mut u8, layout: Layout) {
        moy_index_host_free(p, layout.size())
    }
}

// The library's own runtime: absent when the crate runs inside a std binary,
// its tests (`cfg(test)`) and cargo-fuzz's target (`cfg(fuzzing)`).
#[cfg(not(any(test, fuzzing)))]
#[global_allocator]
static HOST: Host = Host;

/// A panic is `abort()`: the board's crash path, the browser's trap, the
/// host's SIGABRT. Every index into the table is in range by construction.
#[cfg(not(any(test, fuzzing)))]
#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {
    extern "C" {
        fn abort() -> !;
    }
    // SAFETY: the C library's abort takes nothing and never returns.
    unsafe { abort() }
}

/// The hosted targets' prebuilt `compiler_builtins` is built to unwind and its
/// tables name the personality routine; nothing unwinds under
/// `panic = "abort"`, so an empty one satisfies the link.
#[cfg(all(not(any(test, fuzzing)), not(target_os = "none")))]
#[no_mangle]
pub extern "C" fn rust_eh_personality() {}

// -- the C ABI ------------------------------------------------------------------
//
// Every pointer argument is the caller's promise, as in the C twin: `ix` from
// moy_index_new and not yet freed, `path` valid for `len` bytes, out-pointers
// writable. Handles are plain integers and any value is legal.

/// # Safety
/// `p` must be valid for `n` bytes; it may be NULL when `n` is 0.
unsafe fn bytes<'a>(p: *const c_char, n: usize) -> &'a [u8] {
    if n == 0 {
        &[]
    } else {
        core::slice::from_raw_parts(p.cast(), n)
    }
}

/// An empty table, or NULL.
#[no_mangle]
pub extern "C" fn moy_index_new() -> *mut Index {
    // SAFETY: `Index` is not zero-sized; a non-NULL block is initialised
    // before it is handed out, and moy_index_free gives it back as a Box.
    unsafe {
        let p = alloc::alloc::alloc(Layout::new::<Index>()).cast::<Index>();
        if !p.is_null() {
            p.write(Index::new());
        }
        p
    }
}

/// # Safety
/// `ix` is NULL or came from moy_index_new and is not used again.
#[no_mangle]
pub unsafe extern "C" fn moy_index_free(ix: *mut Index) {
    if !ix.is_null() {
        drop(Box::from_raw(ix));
    }
}

/// # Safety
/// See the C ABI's preamble.
#[no_mangle]
pub unsafe extern "C" fn moy_index_intern(
    ix: *mut Index,
    path: *const c_char,
    len: usize,
    h: *mut u32,
) -> c_int {
    match (*ix).intern(bytes(path, len)) {
        Ok(v) => {
            *h = v;
            0
        }
        Err(e) => e as c_int,
    }
}

/// # Safety
/// See the C ABI's preamble.
#[no_mangle]
pub unsafe extern "C" fn moy_index_find(ix: *const Index, path: *const c_char, len: usize) -> u32 {
    (*ix).find(bytes(path, len))
}

/// # Safety
/// See the C ABI's preamble. The bytes stay valid until the next call that
/// changes the table.
#[no_mangle]
pub unsafe extern "C" fn moy_index_path(
    ix: *const Index,
    h: u32,
    path: *mut *const c_char,
    len: *mut usize,
) -> c_int {
    match (*ix).path(h) {
        Ok(p) => {
            *path = p.as_ptr().cast();
            *len = p.len();
            0
        }
        Err(e) => e as c_int,
    }
}

/// # Safety
/// See the C ABI's preamble.
#[no_mangle]
pub unsafe extern "C" fn moy_index_valid(ix: *const Index, h: u32) -> c_int {
    c_int::from((*ix).valid(h))
}

/// # Safety
/// See the C ABI's preamble.
#[no_mangle]
pub unsafe extern "C" fn moy_index_release(ix: *mut Index, h: u32) -> c_int {
    match (*ix).release(h) {
        Ok(()) => 0,
        Err(e) => e as c_int,
    }
}

/// # Safety
/// See the C ABI's preamble.
#[no_mangle]
pub unsafe extern "C" fn moy_index_count(ix: *const Index) -> u32 {
    (*ix).count()
}

/// # Safety
/// See the C ABI's preamble.
#[no_mangle]
pub unsafe extern "C" fn moy_index_slots(ix: *const Index) -> u32 {
    (*ix).slots()
}

/// # Safety
/// See the C ABI's preamble.
#[no_mangle]
pub unsafe extern "C" fn moy_index_at(ix: *const Index, slot: u32) -> u32 {
    (*ix).at(slot)
}

#[cfg(test)]
mod tests;
