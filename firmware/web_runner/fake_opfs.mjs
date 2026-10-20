// A FAKE OPFS for node: the directory and file handles moy_store.mjs uses,
// both write paths (a sync access handle, the createWritable fallback), a
// reload injected between any two changes, and the whole tree to and from a
// plain object, so a store can outlive one node process the way OPFS outlives
// a tab. store_test.mjs and system_persist_test.mjs drive the store over it.

const enc = new TextEncoder(), dec = new TextDecoder();

// `crash.at`, when set to {left: n}, counts every change the fake makes to its
// contents and throws once it has allowed `left` of them -- a reload at that
// exact point, with the store left as far as it got (truncate and write are
// two changes, as they are on a real sync access handle).
export const crash = { at: null };
function change() {
    if (crash.at && crash.at.left-- <= 0) throw new Error("the page went away here");
}

export function makeDir(sync) {
    const children = new Map();
    const dir = {
        kind: "directory",
        async getDirectoryHandle(name, opts) {
            let c = children.get(name);
            if (!c) {
                if (!opts || !opts.create) throw new Error("NotFoundError: " + name);
                change();
                c = makeDir(sync);
                children.set(name, c);
            }
            if (c.kind !== "directory") throw new Error("TypeMismatchError: " + name);
            return c;
        },
        async getFileHandle(name, opts) {
            let c = children.get(name);
            if (!c) {
                if (!opts || !opts.create) throw new Error("NotFoundError: " + name);
                change();
                c = makeFile(sync);
                children.set(name, c);
            }
            if (c.kind !== "file") throw new Error("TypeMismatchError: " + name);
            return c;
        },
        async removeEntry(name, opts) {
            const c = children.get(name);
            if (!c) throw new Error("NotFoundError: " + name);
            if (c.kind === "directory" && !(opts && opts.recursive) && c._size())
                throw new Error("InvalidModificationError");
            // A recursive removal is not atomic on a real filesystem: entry by
            // entry, so a reload can land half way through one.
            if (c.kind === "directory") {
                const inner = [];
                for await (const [k] of c.entries()) inner.push(k);
                for (const k of inner) await c.removeEntry(k, { recursive: true });
            }
            change();
            children.delete(name);
        },
        async *entries() { for (const [k, v] of children) yield [k, v]; },
        _size() { return children.size; },
    };
    return dir;
}

function makeFile(sync) {
    const fh = {
        kind: "file",
        data: new Uint8Array(0),
        async getFile() {
            return { text: async () => dec.decode(fh.data),
                     arrayBuffer: async () => fh.data.slice().buffer };
        },
    };
    if (sync) {
        fh.createSyncAccessHandle = async () => ({
            truncate(n) { change(); fh.data = fh.data.slice(0, n); },
            write(bytes, o) {
                change();
                const at = (o && o.at) || 0;
                const out = new Uint8Array(Math.max(fh.data.length, at + bytes.length));
                out.set(fh.data, 0);
                out.set(bytes, at);
                fh.data = out;
            },
            flush() { }, close() { },
        });
    } else {
        fh.createWritable = async () => ({
            async write(bytes) { change(); fh.data = bytes; },
            async close() { },
        });
    }
    return fh;
}

export const fakeNav = (sync, root = makeDir(sync)) =>
    ({ storage: { getDirectory: async () => root } });

// The tree under `dir` as {"a/b": base64}, and back: what a second process
// reads is what the first one left, byte for byte.
export async function dumpTree(dir, prefix = "", out = {}) {
    for await (const [name, h] of dir.entries()) {
        const rel = prefix ? prefix + "/" + name : name;
        if (h.kind === "directory") await dumpTree(h, rel, out);
        else out[rel] = Buffer.from(h.data).toString("base64");
    }
    return out;
}

export async function loadTree(tree, sync = true) {
    const root = makeDir(sync);
    for (const rel of Object.keys(tree)) {
        const parts = rel.split("/");
        let d = root;
        for (const seg of parts.slice(0, -1)) d = await d.getDirectoryHandle(seg, { create: true });
        const fh = await d.getFileHandle(parts[parts.length - 1], { create: true });
        fh.data = new Uint8Array(Buffer.from(tree[rel], "base64"));
    }
    return root;
}
