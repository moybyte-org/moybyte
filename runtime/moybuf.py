"""#186 moy_buf: GC-invisible byte storage for warm caches.

MicroPython's GC mark phase conservatively scans every word of every live
gc-heap block, so a warm byte cache (cover runs, cover bitmaps, RGB565
bakes) taxes EVERY collect even though it holds no pointers -- measured on
the T-Deck: live 638KB -> ~114ms a collect, live 1427KB -> ~243ms. alloc()
places those payloads OUTSIDE the gc heap (PSRAM via moy_alloc's C
registry) where the collector never looks; free() returns them.

Discipline (the whole risk of the feature): SINGLE OWNER. The cache that
alloc()s a buffer is the only long-lived holder; eviction calls free(); a
view must never outlive its buffer. The C side refuses to free a pointer
it did not hand out (ValueError, loud beats corrupt) and neuters the freed
view (len 0), so a stale read raises instead of reading freed RAM. When in
doubt, LEAK: skipping a free costs bounded PSRAM; freeing a buffer some
job still reads is a crash.

Host / wasm / older firmware: no moy_alloc (or one without free) -> the
payload stays plain bytes/bytearray on the gc heap and free() is a no-op.
Same code, same pixels, just gc-resident.
"""

try:
    import moy_alloc as _ma
    _ALLOC = getattr(_ma, "alloc", None)
    _FREE = getattr(_ma, "free", None) if _ALLOC is not None else None
    _STATS = getattr(_ma, "stats", None) if _ALLOC is not None else None
except ImportError:
    _ALLOC = None
    _FREE = None
    _STATS = None


def alloc(n):
    """A zeroed writable n-byte buffer off the gc heap (memoryview), or a
    plain bytearray when the platform has no off-heap allocator (host) or
    PSRAM is exhausted (degrade, never fail)."""
    if _ALLOC is not None:
        try:
            return _ALLOC(n)
        except MemoryError:
            pass
    return bytearray(n)


def take(payload):
    """Copy `payload` (bytes/bytearray) into a fresh off-heap buffer -- the
    migration idiom for payloads produced on the gc heap first (a parsed
    blob, a decoded bitmap). On a platform with no allocator the payload is
    returned AS IS (zero copies, exactly the pre-#186 behavior)."""
    if _ALLOC is not None:
        try:
            b = _ALLOC(len(payload))
            b[:] = payload
            return b
        except MemoryError:
            pass
    return payload


def free(buf):
    """Release an alloc()/take() buffer. Anything that is not a memoryview
    is gc-owned fallback storage -> no-op. Freeing twice, or a view that is
    not ours, raises ValueError from the C registry."""
    if _FREE is not None and isinstance(buf, memoryview):
        _FREE(buf)


def stats():
    """(live_buffers, live_bytes) of off-heap storage -- (0, 0) on hosts."""
    if _STATS is not None:
        return _STATS()
    return (0, 0)
