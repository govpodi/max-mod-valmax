// ValMAX's four global ghost controls. No chat/account/message arguments are
// dereferenced, serialized, logged or emitted. The host verifies disk SHA-256.
// Replacements run at public void entries, before any coroutine is allocated.
'use strict';
const valmaxGhost = (() => {
    const PIN = '352aed752b8802646d2a927dcd9de4031046083963f6bddaf4b32b99f75622e6';
    const ZERO = '0'.repeat(64);
    const state = {ready: false, revision: ZERO,
        settings: {enabled: false, hideTyping: false, hideRead: false, hideOnline: false, hideStories: false},
        blocked: {typing: 0, read: 0, presence: 0, stories: 0}, error: null};
    const FLAGS = ['enabled', 'hideTyping', 'hideRead', 'hideOnline', 'hideStories'];
    // Keep callbacks and NativeFunctions strongly reachable for the script's
    // whole lifetime, including the period after a module has been unloaded.
    const retained = [];
    let liveModule = null;
    function object(value) {return value !== null && typeof value === 'object' && !Array.isArray(value);}
    function keys(value, expected) {
        return object(value) && Object.keys(value).length === expected.length &&
            expected.every(key => Object.prototype.hasOwnProperty.call(value, key));
    }
    function policy(value) {
        if (!keys(value, ['revision', 'settings']) || typeof value.revision !== 'string' ||
            !/^[a-f0-9]{64}$/.test(value.revision) ||
            !(keys(value.settings, FLAGS) || keys(value.settings, FLAGS.slice(0, 3))) ||
            !Object.keys(value.settings).every(key => typeof value.settings[key] === 'boolean')) return false;
        const selected = {enabled: value.settings.enabled, hideTyping: value.settings.hideTyping,
            hideRead: value.settings.hideRead, hideOnline: value.settings.hideOnline === true,
            hideStories: value.settings.hideStories === true};
        if (value.revision === state.revision &&
            FLAGS.some(key => selected[key] !== state.settings[key])) return false;
        state.revision = value.revision;
        state.settings = selected;
        return true;
    }
    function snapshot() {
        return {ready: state.ready, features: {hideTyping: state.ready, hideRead: state.ready,
                hideOnline: state.ready, hideStories: state.ready},
            revision: state.revision, enabled: state.settings.enabled,
            hideTyping: state.settings.hideTyping, hideRead: state.settings.hideRead,
            hideOnline: state.settings.hideOnline, hideStories: state.settings.hideStories,
            blocked: {...state.blocked}, error: state.error};
    }
    function pathEqual(left, right) {
        return typeof left === 'string' && typeof right === 'string' &&
            left.replace(/\//g, '\\').toLowerCase() === right.replace(/\//g, '\\').toLowerCase();
    }
    function bytes(pointer, count) {
        return Array.from(new Uint8Array(pointer.readByteArray(count)))
            .map(value => value.toString(16).padStart(2, '0')).join('');
    }
    function executable(pointer, count) {
        const range = Process.findRangeByAddress(pointer);
        return range !== null && range.protection.indexOf('x') !== -1 &&
            range.protection.indexOf('r') !== -1 &&
            pointer.add(count).compare(range.base.add(range.size)) <= 0;
    }
    const spec = MONITOR.ghost;
    const expected = [
        {feature: 'hideTyping', counter: 'typing', rva: 0x12a8b80,
            expected: '4883ec28458bc84c8bc2488d542448e83184defe4883c428c3',
            arguments: ['pointer', 'int64', 'int'], vtableRva: 0x190ca98, slot: 8,
            thunkRva: 0x264fe, thunkExpected: 'e97d262801'},
        {feature: 'hideRead', counter: 'read', rva: 0x9ca190,
            expected: '4883ec38498b40084d8b084c8bc2488d542450c7442428000000004889442420e89af870ff4883c438c3',
            arguments: ['pointer', 'int64', 'pointer'], vtableRva: 0x18cd300, slot: 8,
            thunkRva: 0x979b, thunkExpected: 'e9f0099c00'},
        {feature: 'hideRead', counter: 'read', rva: 0xa94050,
            expected: '4883ec28458bc84c8bc2488d542448e829a35aff4883c428c3',
            arguments: ['pointer', 'int64', 'int'], selectorExcept: 1,
            vtableRva: 0x18d4478, slot: 0x20, thunkRva: 0xd1bc9, thunkExpected: 'e982249c00'},
        {feature: 'hideStories', counter: 'stories', rva: 0x1241f70,
            expected: '4883ec380f10024d8bc8488d5424484c8d4424200f29442420e82a90defe4883c438c3',
            arguments: ['pointer', 'pointer', 'int64'], vtableRva: 0x190a4f0, slot: 0x58,
            thunkRva: 0xb5bd1, thunkExpected: 'e99ac31801'}
    ];
    const online = {
        login: {rva: 0x1531ab0, expected: '4883c120e9073aadfe', vtableRva: 0x189d5c8, slot: 8,
            thunkRva: 0xcf54, thunkExpected: 'e9574b5201'},
        encoder: {rva: 0x683550, expected: '4883ec28803900488d4220488bc8c6442430c3488d5424307505c6442430c2e851c0a1ff4883c428c3'},
        ping: {rva: 0x15432aa, expected: '888424c8000000488d542450488bcbe862f0ffff',
            serializerRva: 0x15431c0,
            serializerExpected: '48895c2408574881ece0000000488b05ac117d004833c448898424d0000000488bda488bf9488954242833c038812401000075104889024889420848894210e9d30000000f57c0f30f7f4424304889442440488b150f0a8b00488d4c2450e82c68b4fe90488d05bd3d42004889442450488d0599a8350048898424b000000048c78424b80000000b000000488d056eaf340048898424a8000000488d44245048898424c0000000488d9424a8000000488d4c2450e8af48affe488d05183d420048898424a8000000c78424cc0000000100000080bf2401000000740c83bf20010000000f94c0eb0232c0888424c8000000488d542450488bcbe862f0ffff90488d8c2480000000e880b8b1fe488d4c2468e8820fb3fe90488bc3488b8c24d00000004833cce82061b9fe488b9c24f00000004881c4e00000005fc3',
            vtableRva: 0x1966e58, slot: 0x48, thunkRva: 0x694ac, thunkExpected: 'e90f9d4d01'}
    };
    function exact(left, right) {
        return keys(left, Object.keys(right)) && Object.keys(right).every(key => left[key] === right[key]);
    }
    function validSpec() {
        return object(spec) && spec.schema === 1 && spec.moduleSha256 === PIN &&
            spec.moduleSize === 32905040 && spec.imageSize === 0x1f77000 &&
            typeof spec.modulePath === 'string' && /^([a-z]:[\\/]).+[\\/]core\.dll$/i.test(spec.modulePath) &&
            !spec.modulePath.includes('\0') && Array.isArray(spec.wrappers) && spec.wrappers.length === expected.length &&
            expected.every((pin, index) => {
                const item = spec.wrappers[index];
                return object(item) && Object.keys(pin).every(key => key === 'arguments'
                    ? Array.isArray(item[key]) && item[key].join('|') === pin[key].join('|')
                    : item[key] === pin[key]);
            }) && keys(spec.online, ['login', 'encoder', 'ping']) &&
            Object.keys(online).every(key => exact(spec.online[key], online[key]));
    }
    function install(module) {
        if (liveModule !== null) return;
        if (!pathEqual(module.path, spec.modulePath) || module.size !== spec.imageSize) {
            state.error = 'module_mismatch'; return;
        }
        const prepared = [], replaced = [], attached = [];
        try {
            // Validate the complete set before touching any entry. These
            // are code/vtable reads only, never caller argument reads.
            for (const pin of expected) {
                const address = module.base.add(pin.rva), thunk = module.base.add(pin.thunkRva);
                if (!executable(address, pin.expected.length / 2) || !executable(thunk, 5) ||
                    bytes(address, pin.expected.length / 2) !== pin.expected ||
                    bytes(thunk, 5) !== pin.thunkExpected ||
                    !module.base.add(pin.vtableRva + pin.slot).readPointer().equals(thunk)) {
                    state.error = 'module_mismatch'; return;
                }
                const original = new NativeFunction(address, 'void', pin.arguments,
                    {abi: 'win64', scheduling: 'cooperative', exceptions: 'propagate'});
                const callback = new NativeCallback(function (self, chat, detail) {
                    const selected = state.settings;
                    if (state.ready && selected.enabled && selected[pin.feature] &&
                        (pin.selectorExcept === undefined || detail !== pin.selectorExcept)) {
                        if (state.blocked[pin.counter] < 1000000) state.blocked[pin.counter]++;
                        return;
                    }
                    // Frida's documented synchronous delegation bypasses this
                    // replacement. Preserve all 64 chat-ID bits and pointer args.
                    original(self, chat, detail);
                }, 'void', pin.arguments, 'win64');
                prepared.push({address, original, callback});
            }
            for (const pin of Object.values(online)) {
                const address = module.base.add(pin.rva);
                if (!executable(address, pin.expected.length / 2) || bytes(address, pin.expected.length / 2) !== pin.expected) {
                    state.error = 'module_mismatch'; return;
                }
                if (pin === online.login) {
                    const thunk = module.base.add(pin.thunkRva);
                    if (!executable(thunk, 5) || bytes(thunk, 5) !== pin.thunkExpected ||
                        !module.base.add(pin.vtableRva + pin.slot).readPointer().equals(thunk)) {
                        state.error = 'module_mismatch'; return;
                    }
                }
            }
            const ping = online.ping, thunk = module.base.add(ping.thunkRva), serializer = module.base.add(ping.serializerRva);
            if (!executable(serializer, ping.serializerExpected.length / 2) ||
                bytes(serializer, ping.serializerExpected.length / 2) !== ping.serializerExpected ||
                !executable(thunk, 5) || bytes(thunk, 5) !== ping.thunkExpected ||
                !module.base.add(ping.vtableRva + ping.slot).readPointer().equals(thunk)) {
                state.error = 'module_mismatch'; return;
            }
            // Login serializes exactly one member through this unique entry.
            // Delegate to the same native bool encoder with our own false byte;
            // preserve the caller's output object and every other Login field.
            const falseByte = Memory.alloc(1); falseByte.writeU8(0);
            const encode = new NativeFunction(module.base.add(online.encoder.rva), 'void', ['pointer', 'pointer'],
                {abi: 'win64', scheduling: 'cooperative', exceptions: 'propagate'});
            const loginAddress = module.base.add(online.login.rva);
            const loginOriginal = new NativeFunction(loginAddress, 'void', ['pointer', 'pointer'],
                {abi: 'win64', scheduling: 'cooperative', exceptions: 'propagate'});
            const loginCallback = new NativeCallback(function(self, output) {
                if (state.ready && state.settings.enabled && state.settings.hideOnline) {
                    encode(falseByte, output);
                    if (state.blocked.presence < 1000000) state.blocked.presence++;
                    return;
                }
                loginOriginal(self, output);
            }, 'void', ['pointer', 'pointer'], 'win64');
            prepared.push({address: loginAddress, original: loginOriginal, callback: loginCallback});
            for (const item of prepared) {
                Interceptor.replace(item.address, item.callback);
                replaced.push(item.address);
            }
            attached.push(Interceptor.attach(module.base.add(ping.rva), {
                onEnter() {
                    if (state.ready && state.settings.enabled && state.settings.hideOnline) {
                        // AL is the computed interactive bool at this exact
                        // instruction. Preserve all other bits/registers and
                        // let native Ping serialization/enqueue finish normally.
                        this.context.rax = this.context.rax.and('0xffffffffffffff00');
                        if (state.blocked.presence < 1000000) state.blocked.presence++;
                    }
                }
            }));
            Interceptor.flush();
            retained.push(...prepared, ...attached, falseByte, encode);
            liveModule = module.base;
            state.ready = true;
            state.error = null;
        } catch (_) {
            state.ready = false;
            state.error = 'hook_failed';
            for (const listener of attached) {try {listener.detach();} catch (_) {}}
            for (const address of replaced) {try {Interceptor.revert(address);} catch (_) {}}
            try {Interceptor.flush();} catch (_) {}
            retained.push(...prepared);
        }
    }
    function receive() {
        recv('valmax-ghost-settings', message => {
            // Frida messages include their routing `type` field. Keep the
            // policy object exact, excluding only this documented envelope.
            if (keys(message, ['type', 'revision', 'settings']) && message.type === 'valmax-ghost-settings')
                policy({revision: message.revision, settings: message.settings});
            receive();
        });
    }
    if (spec === undefined || spec === null) return {snapshot}; // Service script: not applicable.
    if (!policy(MONITOR.ghostSettings)) {state.error = 'invalid_settings'; return {snapshot};}
    if (Process.platform !== 'windows' || Process.arch !== 'x64' || Process.pointerSize !== 8) {
        state.error = 'unsupported_platform'; return {snapshot};
    }
    if (!validSpec()) {state.error = 'invalid_specification'; return {snapshot};}
    state.error = 'waiting_for_core';
    const moduleObserver = Process.attachModuleObserver({
        onAdded(module) {if (module.name.toLowerCase() === 'core.dll') install(module);},
        onRemoved(module) {
            if (liveModule !== null && module.base.equals(liveModule)) {
                state.ready = false; liveModule = null; state.error = 'module_unloaded';
            }
        }
    });
    retained.push(moduleObserver);
    receive();
    return {snapshot};
})();
function ghostSnapshot() {return valmaxGhost.snapshot();}
