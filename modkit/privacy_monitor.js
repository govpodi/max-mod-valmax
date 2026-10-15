// Read-only observer: no request modification, no credential or message logging.
// MONITOR is injected by privacy_server.py after verifying the mod manifest.
'use strict';
const listeners = [];
const installed = new Set();
const stats = {tracerHooks: 0, transportHooks: 0};
let tracerCounts = Object.create(null);
let connections = Object.create(null);
let packets = Object.create(null);
let connectionKinds = 0;
let packetKinds = 0;
let omittedPackets = 0;
let omittedBytes = 0;
let failures = 0;
let fatalCount = 0;
function fatalStack(context) {
    if (fatalCount++ >= 4) return;
    const frames = [];
    try {
        for (const address of Thread.backtrace(context, Backtracer.ACCURATE).slice(0, 24)) {
            const module = Process.findModuleByAddress(address);
            if (module && /^[a-zA-Z0-9_.-]{1,64}$/.test(module.name)) frames.push({module: module.name, rva: address.sub(module.base).toUInt32()});
        }
    } catch (_) {}
    // No log format, context strings, message text or function arguments.
    send({kind: 'fatal', frames});
}
function samePath(a, b) {return a.replace(/\//g, '\\').toLowerCase() === b.replace(/\//g, '\\').toLowerCase();}
function inside(path) {const prefix = MONITOR.root.replace(/\//g, '\\').toLowerCase() + '\\';return path.replace(/\//g, '\\').toLowerCase().startsWith(prefix);}
function attach(address, onEnter, instruction = false) {
    const key = address.toString();
    if (installed.has(key)) return false;
    // A function callback is an instruction probe. Object callbacks install
    // function return machinery even when the JS only supplies onEnter.
    try {listeners.push(Interceptor.attach(address, instruction ? onEnter : {onEnter})); installed.add(key); return true;}
    catch (_) {failures++; return false;}
}
function endpoint(pointer, length) {
    if (pointer.isNull() || length < 8) return null;
    const family = pointer.readU16();
    const port = (pointer.add(2).readU8() << 8) | pointer.add(3).readU8();
    if (family === 2 && length >= 16) {
        const octets = [];
        for (let i = 0; i < 4; i++) octets.push(pointer.add(4 + i).readU8());
        return {host: octets.join('.'), port};
    }
    if (family === 23 && length >= 28) {
        const groups = [];
        for (let i = 0; i < 16; i += 2) groups.push(((pointer.add(8 + i).readU8() << 8) | pointer.add(9 + i).readU8()).toString(16));
        return {host: groups.join(':'), port};
    }
    return null;
}
const observer = Process.attachModuleObserver({onAdded(module) {
    const name = module.name.toLowerCase();
    // Temporary diagnostic: native fail-fast paths can bypass Qt's logger.
    // These are function entries; collect only the existing module/RVA stack.
    if (['ucrtbase.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'kernelbase.dll'].includes(name)) {
        for (const symbol of module.enumerateExports()) {
            if (symbol.type !== 'function') continue;
            const abortExport = name !== 'kernelbase.dll' &&
                ['abort', 'terminate', '_invoke_watson', '_invalid_parameter_noinfo_noreturn'].includes(symbol.name);
            const stdTerminateExport = ['vcruntime140.dll', 'vcruntime140_1.dll'].includes(name) &&
                symbol.name === '__std_terminate';
            if (abortExport || stdTerminateExport || (name === 'kernelbase.dll' && symbol.name === 'RaiseFailFastException'))
                attach(symbol.address, function () {fatalStack(this.context);});
        }
    }
    if (name === 'qt6core.dll' && inside(module.path)) {
        for (const symbol of module.enumerateExports()) {
            if (symbol.type !== 'function') continue;
            if (symbol.name.startsWith('?fatal@QMessageLogger@@')) attach(symbol.address, function () {fatalStack(this.context);});
            if (symbol.name.startsWith('?qt_message_output@@')) attach(symbol.address, function (args) {if (args[0].toInt32() === 3) fatalStack(this.context);});
        }
    }
    if (name === MONITOR.tracerModule.toLowerCase() && samePath(module.path, MONITOR.tracerPath)) {
        for (const entry of MONITOR.tracerCallsites) {
            const address = module.base.add(entry.rva);
            try {
                const bytes = Array.from(new Uint8Array(address.readByteArray(entry.expected.length / 2))).map(x => x.toString(16).padStart(2, '0')).join('');
                if (bytes !== entry.expected) {failures++;continue;}
                if (attach(address, function () {tracerCounts[entry.symbol] = (tracerCounts[entry.symbol] || 0) + 1;}, true)) stats.tracerHooks++;
            } catch (_) {failures++;}
        }
    }
    if (name === 'network.dll' && MONITOR.packetHook && samePath(module.path, MONITOR.networkPath)) {
        const entry = MONITOR.packetHook;
        const address = module.base.add(entry.rva);
        try {
            const code = Array.from(new Uint8Array(address.readByteArray(6))).map(x => x.toString(16).padStart(2, '0')).join('');
            if (code !== entry.expected) {failures++;return;}
            if (attach(address, function () {
                try {
                    const pointer = this.context.rdx;
                    const length = Number(this.context.r8.toString());
                    if (!Number.isSafeInteger(length) || pointer.isNull() || length < 10 || length > 64 * 1024 * 1024) return;
                    const header = new Uint8Array(pointer.readByteArray(10));
                    const bodySize = (header[7] << 16) | (header[8] << 8) | header[9];
                    if (bodySize !== length - 10) return;
                    const opcode = (header[4] << 8) | header[5];
                    // For other opcodes only ten header bytes are read, never
                    // message/auth/file bodies or their arbitrary map keys.
                    if (opcode === 6 && header[6] === 0 && length <= 16384) {
                        send({kind: 'session-init'}, pointer.readByteArray(length));
                    } else {
                        // Batch header-only counters; high traffic must not
                        // create one cross-process message for every packet.
                        const key = String(opcode);
                        if (packets[key] || packetKinds < 256) {
                            if (!packets[key]) {
                                packets[key] = {metadata: {protocol: 'msgpack', opcode, fields: [], bytes: length}, count: 0, bytesTotal: 0, minBytes: length, maxBytes: length};
                                packetKinds++;
                            }
                            const item = packets[key];
                            if (item.count < 1000000) {
                                item.count++;
                                item.bytesTotal += length;
                                item.minBytes = Math.min(item.minBytes, length);
                                item.maxBytes = Math.max(item.maxBytes, length);
                                item.metadata.bytes = item.minBytes;
                            } else {omittedPackets++; omittedBytes += length;}
                        } else {
                            omittedPackets++; omittedBytes += length;
                        }
                    }
                } catch (_) {failures++;}
            }, true)) stats.transportHooks++;
        } catch (_) {failures++;}
    }
    if (name === 'ws2_32.dll') {
        for (const symbol of ['connect', 'WSAConnect']) {
            const address = module.findExportByName(symbol);
            if (!address) continue;
            if (attach(address, function (args) {
                try {
                    const value = endpoint(args[1], args[2].toInt32());
                    if (value) {
                        if (value.port === 18973 && (value.host === '127.0.0.1' || value.host === '0:0:0:0:0:0:0:1')) return;
                        const key = value.host + '|' + value.port;
                        if (connections[key] || connectionKinds < 100) {
                            if (!connections[key]) {connections[key] = 0;connectionKinds++;}
                            connections[key]++;
                        }
                    }
                } catch (_) {failures++;}
            })) stats.transportHooks++;
        }
    }
}});
function takeSnapshot() {
        const result = {stats, tracerCounts, connections, packets: Object.values(packets), omittedPackets, omittedBytes, failures};
        if (typeof ghostSnapshot === 'function') result.ghost = ghostSnapshot();
        if (typeof messageRetentionSnapshot === 'function') result.retention = messageRetentionSnapshot();
        tracerCounts = Object.create(null);connections = Object.create(null);packets = Object.create(null);failures = 0;
        connectionKinds = 0;packetKinds = 0;
        omittedPackets = 0;omittedBytes = 0;
        return result;
}
function waitSnapshot() {
    recv('privacy-snapshot', function (message) {
        if (typeof message.requestId === 'string' && /^[a-f0-9]{32}$/.test(message.requestId))
            send({kind: 'privacy-snapshot', requestId: message.requestId, snapshot: takeSnapshot()});
        waitSnapshot();
    });
}
waitSnapshot();
rpc.exports = {snapshot: takeSnapshot};
