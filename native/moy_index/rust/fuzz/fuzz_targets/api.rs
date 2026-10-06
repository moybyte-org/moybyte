//! The API fuzzed against a model, as native/moy_index/fuzz_index.c fuzzes it
//! over the C ABI: an input is a program of op bytes and operands, every answer
//! is checked, and an allocation is refused on a countdown an op sets, so the
//! out-of-memory paths run and must leave the table as it was.
#![no_main]

use std::alloc::{GlobalAlloc, Layout, System};
use std::collections::{BTreeSet, HashMap};
use std::sync::atomic::{AtomicU32, Ordering::Relaxed};

use libfuzzer_sys::fuzz_target;
use moy_index_rs::{Error, Index, GEN_MAX, SLOTS, SLOT_BITS};

static FAIL_IN: AtomicU32 = AtomicU32::new(0);
static FAILED: AtomicU32 = AtomicU32::new(0);

struct Refusing;

fn refuse() -> bool {
    let n = FAIL_IN.load(Relaxed);
    if n == 0 {
        return false;
    }
    FAIL_IN.store(n - 1, Relaxed);
    if n == 1 {
        FAILED.store(1, Relaxed);
    }
    n == 1
}

// SAFETY: System's, except that it sometimes answers NULL, which is legal.
unsafe impl GlobalAlloc for Refusing {
    unsafe fn alloc(&self, l: Layout) -> *mut u8 {
        if refuse() { std::ptr::null_mut() } else { System.alloc(l) }
    }
    unsafe fn dealloc(&self, p: *mut u8, l: Layout) {
        System.dealloc(p, l)
    }
    unsafe fn realloc(&self, p: *mut u8, l: Layout, n: usize) -> *mut u8 {
        if refuse() { std::ptr::null_mut() } else { System.realloc(p, l, n) }
    }
}

#[global_allocator]
static A: Refusing = Refusing;

const fn handle(s: usize, g: u32) -> u32 {
    (g << SLOT_BITS) | s as u32
}

/// The table the obvious way, with a map from path to slot and the free slots
/// in order, so a burst up to a full table stays cheap.
#[derive(Default)]
struct Model {
    rows: Vec<(u32, Option<Vec<u8>>)>,
    at: HashMap<Vec<u8>, usize>,
    free: BTreeSet<usize>,
}

impl Model {
    fn find(&self, p: &[u8]) -> u32 {
        self.at.get(p).map_or(0, |&s| handle(s, self.rows[s].0))
    }
    fn live(&self, h: u32) -> Option<usize> {
        let s = (h & (SLOTS - 1)) as usize;
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
        let s = match self.free.pop_first() {
            Some(s) => s,
            None if self.rows.len() < SLOTS as usize => {
                self.rows.push((1, None));
                self.rows.len() - 1
            }
            None => return Err(Error::Full),
        };
        self.rows[s].1 = Some(p.to_vec());
        self.at.insert(p.to_vec(), s);
        Ok(handle(s, self.rows[s].0))
    }
    fn release(&mut self, h: u32) -> Result<(), Error> {
        let s = self.live(h).ok_or(Error::Stale)?;
        let (g, p) = std::mem::take(&mut self.rows[s]);
        self.at.remove(&p.unwrap());
        self.rows[s].0 = if g >= GEN_MAX { 1 } else { g + 1 };
        self.free.insert(s);
        Ok(())
    }
    fn handles(&self) -> Vec<u32> {
        self.rows.iter().enumerate().filter(|(_, (_, r))| r.is_some())
            .map(|(s, (g, _))| handle(s, *g)).collect()
    }
}

fn handles(ix: &Index) -> Vec<u32> {
    (0..ix.slots()).map(|s| ix.at(s)).filter(|&h| h != 0).collect()
}

/// A path: one of 48 short ones, or 0..55 raw bytes from the input.
fn path(d: &mut impl Iterator<Item = u8>) -> Vec<u8> {
    let b = d.next().unwrap_or(0);
    if b < 200 {
        format!("/sd/carts/{}.moy", b % 48).into_bytes()
    } else {
        d.take(usize::from(b - 200)).collect()
    }
}

fn intern(ix: &mut Index, m: &mut Model, p: &[u8], fail: u32) -> Option<u32> {
    FAILED.store(0, Relaxed);
    FAIL_IN.store(fail, Relaxed);
    let got = ix.intern(p);
    FAIL_IN.store(0, Relaxed);
    if got == Err(Error::NoMem) {
        assert_eq!(FAILED.load(Relaxed), 1);
        assert_eq!(ix.find(p), 0);
        assert_eq!(handles(ix), m.handles());
        return None;
    }
    assert_eq!(got, m.intern(p));
    got.ok()
}

fuzz_target!(|data: &[u8]| {
    let (mut ix, mut m, mut seen) = (Index::new(), Model::default(), vec![0u32]);
    let mut d = data.iter().copied();
    while let Some(op) = d.next() {
        match op & 7 {
            0 | 1 => {
                let p = path(&mut d);
                let fail = if op & 0x80 != 0 { 1 + u32::from((op >> 3) & 3) } else { 0 };
                seen.extend(intern(&mut ix, &mut m, &p, fail));
            }
            2 => {
                let p = path(&mut d);
                assert_eq!(ix.find(&p), m.find(&p));
            }
            3 | 4 => {
                let b = d.next().unwrap_or(0);
                let h = seen[usize::from(b) % seen.len()] ^ if op & 0x80 != 0 { 1 << (b & 31) } else { 0 };
                if op & 7 == 3 {
                    assert_eq!(ix.release(h), m.release(h));
                } else {
                    let want = m.live(h).map(|s| m.rows[s].1.as_deref().unwrap());
                    assert_eq!(ix.path(h).ok(), want);
                    assert_eq!(ix.valid(h), want.is_some());
                }
            }
            5 => {
                assert_eq!(handles(&ix), m.handles());
                assert_eq!(ix.count() as usize, m.handles().len());
            }
            _ => {
                // A burst of new rows: up to past a full table.
                let n = u32::from(d.next().unwrap_or(0)) * 32;
                for i in 0..n {
                    let p = format!("/f/{}", i);
                    seen.extend(intern(&mut ix, &mut m, p.as_bytes(), 0));
                }
            }
        }
    }
});
