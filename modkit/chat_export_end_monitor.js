// Selected-export metadata only. No message content, identifiers, protected
// URLs, request IDs, account data or native addresses ever leave this script.
// The host verified the program manifest plus this contract's Qt/code guards.
'use strict';
const valmaxChatEndMonitor = (() => {
    const spec = VALMAX_CHAT_END_SPEC;
    const PREFIX = 'valmax-export-', LENGTH = 46, STRIDE = 0x1a8;
    const MAX_COUNT = 100000, MAX_VIEWS = 256, MAX_CACHED = 4, TTL = 2000;
    const bound = new Map(), jobs = new Map(), unbound = new Map(), active = new Map();
    const latest = new Map();
    const modules = new Map(), listeners = [], retained = [];
    let ready = false, error = 'waiting_for_modules', generation = 0;
    function pathEqual(left, right) {
        return typeof left === 'string' && typeof right === 'string' &&
            left.replace(/\//g, '\\').toLowerCase() === right.replace(/\//g, '\\').toLowerCase();
    }
    function readable(address, length, executable = false) {
        if (address.isNull()) return false;
        const range = Process.findRangeByAddress(address);
        return range !== null && range.protection.includes('r') && (!executable || range.protection.includes('x')) &&
            address.add(length).compare(range.base.add(range.size)) <= 0;
    }
    function bytes(address, length) {
        return Array.from(new Uint8Array(address.readByteArray(length)))
            .map(value => value.toString(16).padStart(2, '0')).join('');
    }
    function validSpec() {
        return spec && spec.schema === 1 && spec.contract === 'valmax-chat-end-v1' &&
            spec.max && spec.qt && spec.qt.sha256 === '22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa' &&
            spec.qt.imageSize === 10395648 && Number.isSafeInteger(spec.max.imageSize) && spec.max.imageSize >= 79654912 &&
            typeof spec.max.modulePath === 'string' && typeof spec.qt.modulePath === 'string' &&
            !spec.max.modulePath.includes('\0') && !spec.qt.modulePath.includes('\0') &&
            Array.isArray(spec.max.anchors) && spec.max.anchors.length === 7 &&
            Array.isArray(spec.max.vtables) && spec.max.vtables.length === 2 &&
            Array.isArray(spec.qt.anchors) && spec.qt.anchors.length === 4 &&
            Array.isArray(spec.qt.exports) && spec.qt.exports.length === 3;
    }
    function validateModule(module, pin, qt) {
        if (!pathEqual(module.path, pin.modulePath) || module.size !== pin.imageSize) return false;
        for (const anchor of pin.anchors) {
            if (!anchor || !Number.isSafeInteger(anchor.rva) || anchor.rva < 0 ||
                    typeof anchor.expected !== 'string' || !/^(?:[0-9a-f]{2}){5,1000}$/.test(anchor.expected)) return false;
            const address = module.base.add(anchor.rva), length = anchor.expected.length / 2;
            if (!readable(address, length, true) || bytes(address, length) !== anchor.expected) return false;
        }
        if (qt) {
            for (const entry of pin.exports) {
                const address = module.findExportByName(entry.name);
                if (address === null || !address.equals(module.base.add(entry.rva))) return false;
            }
        } else {
            for (const slot of pin.vtables) {
                const address = module.base.add(slot.rva);
                if (!readable(address, 8) || !address.readPointer().equals(module.base.add(slot.target))) return false;
            }
        }
        return true;
    }
    function isView(self) {
        const maximum = modules.get('max.exe');
        return maximum !== undefined && readable(self, 8) &&
            self.readPointer().equals(maximum.base.add(0x376b768));
    }
    function emit(jobId, fields) {
        try {send({kind: 'chat-export-history', jobId, ...fields});} catch (_) {}
    }
    function invalidate(jobId) {
        emit(jobId, {event: 'page', direction: 'center', count: 0, pendingDone: false,
            moreBackward: true, moreForward: true});
    }
    function forget(key) {
        const record = bound.get(key);
        if (record !== undefined) {
            invalidate(record.jobId);
            if (jobs.get(record.jobId) === key) jobs.delete(record.jobId);
            bound.delete(key);
        }
        unbound.delete(key);
        latest.delete(key);
        const contexts = active.get(key);
        if (contexts !== undefined) for (const record of contexts) record.dead = true;
    }
    function prune() {
        const now = Date.now();
        for (const [key, record] of unbound) if (now - record.at > TTL) unbound.delete(key);
    }
    function mark(self, name) {
        // Only the exact native View type reaches the QString length/prefix
        // checks. Ordinary QObject names are never dereferenced or recorded.
        if (!isView(self) || !readable(name, 24) || name.add(16).readS64().toString() !== String(LENGTH)) return null;
        const data = name.add(8).readPointer();
        if (!readable(data, LENGTH * 2) || data.readUtf16String(PREFIX.length) !== PREFIX) return null;
        const id = data.add(PREFIX.length * 2).readUtf16String(32);
        return typeof id === 'string' && /^[0-9a-f]{32}$/.test(id) ? id : null;
    }
    function bind(self, jobId) {
        const key = self.toString();
        if (!isView(self)) return;
        const prior = bound.get(key);
        if (prior !== undefined && prior.jobId === jobId) return;
        prune();
        const cached = unbound.get(key);
        forget(key);
        const other = jobs.get(jobId);
        if (other !== undefined && other !== key) forget(other);
        if (bound.size >= MAX_VIEWS) return; // Bounded; no assumed native readiness.
        bound.set(key, {jobId}); jobs.set(jobId, key);
        emit(jobId, {event: 'bound'});
        // This object can synchronously deliver its first page inside the
        // factory, before QML gets a chance to assign the export marker.
        unbound.delete(key);
        if (cached !== undefined && Date.now() - cached.at <= TTL)
            for (const value of cached.pages) emit(jobId, value);
    }
    function terminalSource(invocation, kind) {
        // The pinned Presenter prefix keeps its lambda capture in nonvolatile
        // RDI through the virtual call at 11e1856. The exact E9 IView thunk
        // preserves both RDI and returnAddress. Other callers are not evidence.
        // Only the two enum fields are read, never the capture's request IDs,
        // weak pointers, vector/message items or URLs.
        try {
            const maximum = modules.get('max.exe');
            if (invocation.returnAddress === undefined ||
                    !invocation.returnAddress.equals(maximum.base.add(0x11e185a)) ||
                    invocation.context === undefined || invocation.context.rdi === undefined) return false;
            const fields = invocation.context.rdi.add(0x38);
            return readable(fields, 8) && fields.readU32() === 1 && fields.add(4).readU32() === kind;
        } catch (_) {return false;}
    }
    function callbackMetadata(args, invocation) {
        const self = args[0].sub(0x10), key = self.toString();
        if (!isView(self)) return null;
        const kind = args[2].toInt32();
        if (kind < 0 || kind > 2 || !readable(args[1], 16) || !readable(args[3].add(8), 4)) return null;
        // Vector HEADER pointers only. Neither pointer is dereferenced; IDs,
        // timestamps and message bodies are not read, even for a marked view.
        const begin = args[1].readPointer(), end = args[1].add(8).readPointer();
        if (begin.isNull() !== end.isNull() || end.compare(begin) < 0) return null;
        const delta = end.sub(begin);
        if (delta.compare(ptr(MAX_COUNT * STRIDE)) > 0) return null;
        const length = delta.toInt32();
        if (length < 0 || length % STRIDE) return null;
        const pending = args[3].add(8).readU32();
        if (pending !== 0 && pending !== 1) return null;
        if (!readable(self.add(0x670), 0x11)) return null;
        // Presenter updates these flags before calling IView::onMessages.
        // Capture now: a recursive callback can change them before onLeave.
        const backward = self.add(0x670).readU8(), forward = self.add(0x680).readU8();
        if ((backward !== 0 && backward !== 1) || (forward !== 0 && forward !== 1)) return null;
        return {self, key, kind, count: length / STRIDE,
            pendingDone: pending === 1 && terminalSource(invocation, kind),
            moreBackward: backward === 1, moreForward: forward === 1, dead: false};
    }
    function finishMetadata(record) {
        if (!ready || record.dead || latest.get(record.key) !== record.generation || !isView(record.self)) return;
        const value = {event: 'page', direction: ['backward', 'center', 'forward'][record.kind],
            count: record.count, pendingDone: record.pendingDone,
            moreBackward: record.moreBackward, moreForward: record.moreForward};
        const owner = bound.get(record.key);
        if (owner !== undefined) {emit(owner.jobId, value); return;}
        prune();
        if (!unbound.has(record.key) && unbound.size >= MAX_VIEWS) unbound.delete(unbound.keys().next().value);
        const cache = unbound.get(record.key) || {at: Date.now(), pages: []};
        cache.at = Date.now(); cache.pages.push(value);
        if (cache.pages.length > MAX_CACHED) cache.pages.shift();
        unbound.set(record.key, cache);
    }
    function detach() {
        ready = false;
        for (const record of bound.values()) invalidate(record.jobId);
        bound.clear(); jobs.clear(); unbound.clear();
        for (const contexts of active.values()) for (const record of contexts) record.dead = true;
        active.clear();
        latest.clear();
        for (const listener of listeners.splice(0)) {try {listener.detach();} catch (_) {}}
    }
    function install() {
        if (ready || !modules.has('max.exe') || !modules.has('qt6core.dll')) return;
        const maximum = modules.get('max.exe'), qt = modules.get('qt6core.dll');
        try {
            if (!validateModule(maximum, spec.max, false) || !validateModule(qt, spec.qt, true)) {
                error = 'module_mismatch'; return;
            }
            listeners.push(Interceptor.attach(qt.base.add(0xf1c00), {
                onEnter(args) {
                    this.self = null; this.jobId = null;
                    try {
                        if (!ready || !isView(args[0])) return;
                        this.self = args[0]; this.jobId = mark(this.self, args[1]);
                    } catch (_) {this.self = null;}
                },
                onLeave() {
                    if (this.self === null || !ready) return;
                    try {
                        if (this.jobId === null) forget(this.self.toString());
                        else bind(this.self, this.jobId);
                    } catch (_) {}
                }
            }));
            listeners.push(Interceptor.attach(qt.base.add(0xeabd0), {
                onEnter(args) {
                    const key = args[0].toString();
                    const contexts = active.get(key);
                    if (contexts !== undefined) for (const record of contexts) record.dead = true;
                    forget(key);
                }
            }));
            listeners.push(Interceptor.attach(maximum.base.add(0xe27a00), {
                onEnter(args) {
                    this.record = null; this.key = null; this.generation = null;
                    if (!ready) return;
                    try {
                        const self = args[0].sub(0x10), key = self.toString();
                        if (!isView(self) || (!latest.has(key) && latest.size >= MAX_VIEWS)) return;
                        if (++generation > Number.MAX_SAFE_INTEGER) {detach(); error = 'generation_limit'; return;}
                        this.key = key; this.generation = generation; latest.set(key, generation);
                        const record = callbackMetadata(args, this);
                        if (record === null || (!active.has(record.key) && active.size >= MAX_VIEWS)) {
                            const owner = bound.get(key); if (owner !== undefined) invalidate(owner.jobId);
                            return;
                        }
                        const contexts = active.get(record.key) || new Set();
                        if (contexts.size >= 8) {
                            const owner = bound.get(key); if (owner !== undefined) invalidate(owner.jobId);
                            return;
                        }
                        record.generation = generation;
                        contexts.add(record); active.set(record.key, contexts); this.record = record;
                    } catch (_) {
                        const owner = this.key === null ? undefined : bound.get(this.key);
                        if (owner !== undefined) invalidate(owner.jobId);
                    }
                },
                onLeave() {
                    const record = this.record;
                    if (record === null) {
                        if (this.key !== null && latest.get(this.key) === this.generation) latest.delete(this.key);
                        return;
                    }
                    try {finishMetadata(record);} catch (_) {}
                    finally {
                        const contexts = active.get(record.key);
                        if (contexts !== undefined) {
                            contexts.delete(record); if (!contexts.size) active.delete(record.key);
                        }
                        if (latest.get(record.key) === record.generation) latest.delete(record.key);
                    }
                }
            }));
            ready = true; error = null;
        } catch (_) {detach(); error = 'hook_failed';}
    }
    function status() {return {ready, error, bound: bound.size, cached: unbound.size, active: active.size};}
    if (Process.platform !== 'windows' || Process.arch !== 'x64' || Process.pointerSize !== 8) {
        error = 'unsupported_platform'; return {status};
    }
    if (!validSpec()) {error = 'invalid_specification'; return {status};}
    const observer = Process.attachModuleObserver({
        onAdded(module) {
            const name = module.name.toLowerCase();
            if (name !== 'max.exe' && name !== 'qt6core.dll') return;
            modules.set(name, module); install();
        },
        onRemoved(module) {
            const name = module.name.toLowerCase(), known = modules.get(name);
            if (known !== undefined && known.base.equals(module.base)) {
                detach(); modules.delete(name); error = 'module_unloaded';
            }
        }
    });
    retained.push(observer);
    return {status};
})();
