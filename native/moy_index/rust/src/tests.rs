//! The crate's own tests, for `cargo test` and Miri (build.sh test). The
//! interface suite, the random walk and the fuzz that every twin answers to run
//! over the C ABI from Python and C (tests/test_moy_index*.py,
//! native/moy_index/fuzz_index.c); these reach what those cannot see from
//! outside: the safe core's invariants, refused allocations at every point,
//! and the unsafe boundary under Miri.

use super::*;
use std::alloc::System;
use std::cell::Cell;
use std::vec;
use std::vec::Vec as StdVec;

std::thread_local! {
    /// Refuse the allocation this many calls on, on this thread; 0 is off.
    static FAIL_IN: Cell<u32> = const { Cell::new(0) };
    static FAILED: Cell<bool> = const { Cell::new(false) };
}

/// The system allocator, refusing one allocation on a per-thread countdown.
struct Refusing;

fn refuse_now() -> bool {
    FAIL_IN
        .try_with(|f| {
            let n = f.get();
            if n == 0 {
                return false;
            }
            f.set(n - 1);
            if n == 1 {
                FAILED.with(|d| d.set(true));
            }
            n == 1
        })
        .unwrap_or(false)
}

// SAFETY: System's, except that it sometimes answers NULL, which is legal.
unsafe impl GlobalAlloc for Refusing {
    unsafe fn alloc(&self, l: Layout) -> *mut u8 {
        if refuse_now() {
            return core::ptr::null_mut();
        }
        System.alloc(l)
    }
    unsafe fn alloc_zeroed(&self, l: Layout) -> *mut u8 {
        if refuse_now() {
            return core::ptr::null_mut();
        }
        System.alloc_zeroed(l)
    }
    unsafe fn dealloc(&self, p: *mut u8, l: Layout) {
        System.dealloc(p, l)
    }
    unsafe fn realloc(&self, p: *mut u8, l: Layout, n: usize) -> *mut u8 {
        if refuse_now() {
            return core::ptr::null_mut();
        }
        System.realloc(p, l, n)
    }
}

#[global_allocator]
static TEST_ALLOC: Refusing = Refusing;

/// The host's imports, for `Host`: malloc-like, the size held so a free with
/// the wrong one is caught.
#[no_mangle]
extern "C" fn moy_index_host_alloc(n: usize) -> *mut u8 {
    let l = Layout::from_size_align(n.max(1) + 16, 16).unwrap();
    // SAFETY: a non-zero layout; the size goes in the first word.
    unsafe {
        let p = System.alloc_zeroed(l);
        if p.is_null() {
            return p;
        }
        p.cast::<usize>().write(n);
        p.add(16)
    }
}

#[no_mangle]
extern "C" fn moy_index_host_free(p: *mut u8, n: usize) {
    // SAFETY: `p` came from moy_index_host_alloc above.
    unsafe {
        let base = p.sub(16);
        assert_eq!(base.cast::<usize>().read(), n, "freed with the wrong size");
        System.dealloc(base, Layout::from_size_align(n.max(1) + 16, 16).unwrap());
    }
}

const fn handle(slot: u32, gen: u32) -> u32 {
    (gen << SLOT_BITS) | slot
}

fn handles(ix: &Index) -> StdVec<u32> {
    (0..ix.slots()).map(|s| ix.at(s)).filter(|&h| h != 0).collect()
}

#[test]
fn a_handle_is_slot_and_generation_and_never_zero() {
    let mut ix = Index::new();
    assert_eq!(ix.intern(b"/carts/a.moy"), Ok(handle(0, 1)));
    assert_eq!(ix.intern(b"/carts/b.moy"), Ok(handle(1, 1)));
    assert_eq!(ix.intern(b"/carts/a.moy"), Ok(handle(0, 1)));
    assert_eq!(ix.find(b"/carts/b.moy"), handle(1, 1));
    assert_eq!(ix.find(b"/carts/c.moy"), 0);
    assert!(!ix.valid(0));
    assert_eq!(ix.path(0), Err(Error::Stale));
    assert_eq!(ix.count(), 2);
}

#[test]
fn release_makes_every_copy_stale_and_the_slot_returns_lowest_first() {
    let mut ix = Index::new();
    let a = ix.intern(b"a").unwrap();
    let b = ix.intern(b"b").unwrap();
    let c = ix.intern(b"c").unwrap();
    ix.release(b).unwrap();
    ix.release(a).unwrap();
    assert_eq!(ix.release(a), Err(Error::Stale));
    assert!(!ix.valid(a) && !ix.valid(b) && ix.valid(c));
    assert_eq!(ix.path(b), Err(Error::Stale));
    assert_eq!(ix.intern(b"d"), Ok(handle(0, 2)));
    assert_eq!(ix.intern(b"e"), Ok(handle(1, 2)));
    assert_eq!(ix.intern(b"f"), Ok(handle(3, 1)));
    assert_eq!(handles(&ix), vec![handle(0, 2), handle(1, 2), c, handle(3, 1)]);
    assert_eq!(ix.path(handle(1, 2)), Ok(&b"e"[..]));
    assert_eq!(ix.find(b"b"), 0);
}

#[test]
fn paths_are_bytes_empty_and_nul_included() {
    let mut ix = Index::new();
    let e = ix.intern(b"").unwrap();
    let n = ix.intern(b"a\0b").unwrap();
    let u = ix.intern("/carts/\u{17e}aba.moy".as_bytes()).unwrap();
    assert_eq!(ix.path(e), Ok(&b""[..]));
    assert_eq!(ix.path(n), Ok(&b"a\0b"[..]));
    assert_eq!(ix.find(b"a"), 0);
    assert_eq!(ix.path(u), Ok("/carts/\u{17e}aba.moy".as_bytes()));
}

#[test]
fn a_full_table_refuses_and_takes_a_row_again_once_one_goes() {
    let mut ix = Index::new();
    let mut hs = StdVec::new();
    for i in 0..SLOTS {
        hs.push(ix.intern(std::format!("/c/{i}").as_bytes()).unwrap());
    }
    assert_eq!(ix.intern(b"/c/one more"), Err(Error::Full));
    assert_eq!(ix.intern(b"/c/7"), Ok(hs[7]));
    ix.release(hs[100]).unwrap();
    assert_eq!(ix.intern(b"/c/one more"), Ok(handle(100, 2)));
    assert_eq!(ix.count(), SLOTS);
}

#[test]
#[cfg_attr(miri, ignore)] // 262143 reuses: minutes under Miri
fn the_generation_wraps_to_one() {
    let mut ix = Index::new();
    for g in 1..=GEN_MAX {
        let h = ix.intern(b"x").unwrap();
        assert_eq!(h, handle(0, g));
        ix.release(h).unwrap();
    }
    assert_eq!(ix.intern(b"x"), Ok(handle(0, 1)));
}

#[test]
fn a_refused_allocation_changes_nothing() {
    // Every allocation an intern makes, refused in turn, at sizes that grow
    // the slots and rebuild the table.
    for n in [0usize, 7, 8, 12, 16, 40] {
        for k in 1..=4 {
            let mut ix = Index::new();
            for i in 0..n {
                ix.intern(std::format!("p{i}").as_bytes()).unwrap();
            }
            let before = handles(&ix);
            FAILED.with(|d| d.set(false));
            FAIL_IN.with(|f| f.set(k));
            let got = ix.intern(b"new");
            FAIL_IN.with(|f| f.set(0));
            if FAILED.with(|d| d.get()) {
                assert_eq!(got, Err(Error::NoMem), "n={n} k={k}");
                assert_eq!(handles(&ix), before);
                assert_eq!(ix.find(b"new"), 0);
                for i in 0..n {
                    let p = std::format!("p{i}");
                    assert_eq!(ix.path(ix.find(p.as_bytes())), Ok(p.as_bytes()));
                }
                assert!(ix.intern(b"new").is_ok());
            } else {
                assert!(got.is_ok());
            }
        }
    }
}

/// A small deterministic generator (xorshift32), so Miri and the host walk
/// the same programs.
struct Rng(u32);

impl Rng {
    fn next(&mut self) -> u32 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 17;
        self.0 ^= self.0 << 5;
        self.0
    }
    fn below(&mut self, n: u32) -> u32 {
        self.next() % n
    }
}

/// The table the obvious way: a list of (generation, path) per slot.
#[derive(Default)]
struct Model {
    rows: StdVec<(u32, Option<StdVec<u8>>)>,
}

impl Model {
    fn find(&self, p: &[u8]) -> u32 {
        self.rows
            .iter()
            .enumerate()
            .find(|(_, (_, r))| r.as_deref() == Some(p))
            .map_or(0, |(s, (g, _))| handle(s as u32, *g))
    }
    fn live(&self, h: u32) -> Option<usize> {
        let s = (h & SLOT_MASK) as usize;
        match self.rows.get(s) {
            Some((g, Some(_))) if *g == h >> SLOT_BITS => Some(s),
            _ => None,
        }
    }
    fn intern(&mut self, p: &[u8]) -> Result<u32, Error> {
        let h = self.find(p);
        if h != 0 {
            return Ok(h);
        }
        let s = match self.rows.iter().position(|(_, r)| r.is_none()) {
            Some(s) => s,
            None if self.rows.len() < SLOTS as usize => {
                self.rows.push((1, None));
                self.rows.len() - 1
            }
            None => return Err(Error::Full),
        };
        self.rows[s].1 = Some(p.to_vec());
        Ok(handle(s as u32, self.rows[s].0))
    }
    fn release(&mut self, h: u32) -> Result<(), Error> {
        let s = self.live(h).ok_or(Error::Stale)?;
        self.rows[s].1 = None;
        self.rows[s].0 = if self.rows[s].0 >= GEN_MAX { 1 } else { self.rows[s].0 + 1 };
        Ok(())
    }
}

fn walk(seed: u32, steps: u32, paths: u32) {
    let mut rng = Rng(seed);
    let mut ix = Index::new();
    let mut m = Model::default();
    let mut seen: StdVec<u32> = StdVec::new();
    for _ in 0..steps {
        let p = std::format!("/sd/carts/{}.moy", rng.below(paths));
        match rng.below(6) {
            0 | 1 => {
                FAILED.with(|d| d.set(false));
                FAIL_IN.with(|f| f.set(if rng.below(8) == 0 { 1 + rng.below(3) } else { 0 }));
                let got = ix.intern(p.as_bytes());
                FAIL_IN.with(|f| f.set(0));
                if got == Err(Error::NoMem) {
                    assert!(FAILED.with(|d| d.get()));
                    assert_eq!(m.find(p.as_bytes()), 0);
                } else {
                    assert_eq!(got, m.intern(p.as_bytes()));
                    seen.push(got.unwrap());
                }
            }
            2 => assert_eq!(ix.find(p.as_bytes()), m.find(p.as_bytes())),
            3 => {
                let h = match seen.len() {
                    0 => rng.next(),
                    n => seen[rng.below(n as u32) as usize] ^ (rng.below(4) << (rng.below(32))),
                };
                assert_eq!(ix.release(h), m.release(h));
            }
            4 => {
                let h = if seen.is_empty() { 0 } else { seen[rng.below(seen.len() as u32) as usize] };
                let want = m.live(h).map(|s| m.rows[s].1.as_deref().unwrap());
                assert_eq!(ix.path(h).ok(), want);
                assert_eq!(ix.valid(h), want.is_some());
            }
            _ => {
                let want: StdVec<u32> = m
                    .rows
                    .iter()
                    .enumerate()
                    .filter(|(_, (_, r))| r.is_some())
                    .map(|(s, (g, _))| handle(s as u32, *g))
                    .collect();
                assert_eq!(handles(&ix), want);
                assert_eq!(ix.count() as usize, want.len());
            }
        }
    }
}

#[test]
fn a_random_walk_answers_as_the_model_does() {
    let (seeds, steps) = if cfg!(miri) { (2, 600) } else { (40, 4000) };
    for seed in 1..=seeds {
        walk(seed, steps, 48);
    }
}

#[test]
fn the_c_abi_round_trips_through_raw_pointers() {
    // SAFETY: what a C caller promises: a live table, valid paths, writable
    // out-pointers.
    unsafe {
        let ix = moy_index_new();
        assert!(!ix.is_null());
        let mut h = 0u32;
        let p = b"/carts/a.moy";
        assert_eq!(moy_index_intern(ix, p.as_ptr().cast(), p.len(), &mut h), 0);
        assert_eq!(h, handle(0, 1));
        assert_eq!(moy_index_intern(ix, core::ptr::null(), 0, &mut h), 0);
        assert_eq!(h, handle(1, 1));
        assert_eq!(moy_index_find(ix, p.as_ptr().cast(), p.len()), handle(0, 1));
        let (mut q, mut n) = (core::ptr::null::<c_char>(), 0usize);
        assert_eq!(moy_index_path(ix, handle(0, 1), &mut q, &mut n), 0);
        assert_eq!(core::slice::from_raw_parts(q.cast::<u8>(), n), p);
        assert_eq!(moy_index_path(ix, handle(0, 2), &mut q, &mut n), Error::Stale as c_int);
        assert_eq!(moy_index_valid(ix, handle(1, 1)), 1);
        assert_eq!(moy_index_release(ix, handle(1, 1)), 0);
        assert_eq!(moy_index_release(ix, handle(1, 1)), Error::Stale as c_int);
        assert_eq!(moy_index_valid(ix, 0xffff_ffff), 0);
        assert_eq!(moy_index_count(ix), 1);
        assert_eq!(moy_index_slots(ix), 2);
        assert_eq!(moy_index_at(ix, 0), handle(0, 1));
        assert_eq!(moy_index_at(ix, 1), 0);
        assert_eq!(moy_index_at(ix, 5000), 0);
        moy_index_free(ix);
        moy_index_free(core::ptr::null_mut());
    }
}

#[test]
fn the_host_allocator_frees_with_the_size_it_allocated() {
    // SAFETY: GlobalAlloc's contract: a non-zero layout, freed once with it.
    unsafe {
        for (n, a) in [(1usize, 1usize), (24, 4), (4096, 8)] {
            let l = Layout::from_size_align(n, a).unwrap();
            let p = Host.alloc(l);
            assert!(!p.is_null() && (p as usize) % a == 0);
            assert!(core::slice::from_raw_parts(p, n).iter().all(|&b| b == 0));
            Host.dealloc(p, l);
        }
        assert!(Host.alloc(Layout::from_size_align(8, 64).unwrap()).is_null());
    }
}
