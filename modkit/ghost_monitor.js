// ValMAX's four global ghost controls. The unread UI bridge reads only native
// account/chat identity, read cursors and counters into bounded session memory.
// No message content, credentials or native values are logged or emitted.
// Receipt replacements remain at public void entries before coroutines.
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
    let liveCore = null, pendingMain = null, liveMain = null, unreadRuntime = null;
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
        if (unreadRuntime !== null &&
            (state.settings.enabled && state.settings.hideRead) !== (selected.enabled && selected.hideRead))
            unreadRuntime.clear();
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
        {feature: 'hideRead', counter: 'read', rva: 0xa940c0,
            expected: '4883ec28458bc84c8bc2488d542448e8befe62ff4883c428c3',
            arguments: ['pointer', 'int64', 'int'], selectorExcept: 1,
            vtableRva: 0x18d4478, slot: 8, thunkRva: 0x365c, thunkExpected: 'e95f0aa900'},
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
    // BEGIN_UNREAD_UI_BRIDGE
    const unreadPin = {"schema":1,"readRva":32428864,"decrementRva":32428988,"incomingRva":32430240,"staleRva":32430481,"countRva":32430807,"destructorRva":32413664,"chatDeleteRva":32427296,"vtableRva":65994744,"guards":[{"rva":32428864,"expected":"48895c24184889542410555657415641574881eca00000004d8bf94d8bf0488bf1488db90801000048897c2460488bcfe8f7435eff85c00f85eb01000081be54010000ffffff7f0f84c6010000488d8e880000004c8d8424d8000000488d542468e8664f1afe488b28488b4d18488bd9492bdfb800000000480f48d8483bd90f84ae0000004885db750748ff8e60010000837d24017510488bc3482b451848018658010000eb05384520740c4885db750748ff8e680100004c8b46284d8b08488b965801000033c04885d2480f48d04889442458488d4c2478e86280ffff84c07445488d05772d00024889442420488b8424d8000000488944242848895c24304889542438488b86600100004889442440488b86680100004889442448488d4424204889442458488d542420498bc841ff511048895d18410f10060f114530488b9c24d800000048899c24d0000000488d8c24d0000000e81e1d1bfe482386f80000004803c0488b96e0000000488b4cc2084c8b86d0000000493bc87442488b04c2483b5910740b483bc87433488b4908ebef4885c9490f44c8493bc87421498b0648394118750a498b460848394120743a410f10060f1141184c017928eb2c488d8ec80000004c8d8424d8000000488d542468e8a3bf1bfe488b08410f10060f11411848c7412800000000488bcfe83e425eff488bcee85fe412fe488b9c24e00000004881c4a0000000415f415e5f5e5dc3c78654010000feffff7fb906000000e811425effccb905000000e806425effcc"},{"rva":32430240,"expected":"48895c24105556574154415541564157488d6c24d94881ecd0000000488b05fdae6b024833c448894517498b5820458be14d8bf04c8bfa488bf94885db0f88a101000080ba80050000000f8594010000488b4968488d5587488b01ff50104d8b5f1841bd01000000440fb60544b377004c8bd0498bf3498b4b0880791900752e4c8b084c3949207506410fb6c0eb09b8ff000000410f4dc584c07906488b4910eb06488bf1488b098079190074d5807e19000f852c010000488b4e2049390a7506410fb6c0eb09b8ff000000410f4dc584c00f880c010000493bf30f840301000048395e28740a41b8ff000000450f4dc54584c00f89ea000000b902000000ff1573f4c00284c00f8482020000488b46284c8d4da7488945874c8d4587488d056c27000248895da7488d559748894597488d4def48c7459f3f000000e8599c16fe488378180f7605488b08eb03488bc8488b40104c8d0de82600024889459f4c8d4597488d057627000248894d9748894587488d5587488d056b26000248c7458f0e0000004889442428b902000000c74424203b010000ff15d3f3c002488b55074883fa0f0f86e4010000488b4def48ffc24881fa001000007218488b41f8482bc84883e9084883f91f77114883c227488bc8e882c018fee9b2010000b905000000cd29498b37488d4d8748897587e826171bfe482387f80000004533d24c8b87e0000000488bc84c8b8fd00000004803c9498b54c808493bd174184803c0498b0cc0483b7210740e483bd17406488b5208ebef498bd249639f600200004885d2490f44d1493bd17408482b5a28490f48da498b06483bd80f842e0100004183fc010f85de000000498b8f400500004885c90f9fc24885db750948ff8f60010000eb0c4885c0750748ff876001000041837e0c017529488bc3492b06480187580100004885db750948ff8f68010000eb2f4d3916752a48ff8768010000eb2141385608741b488b87680100004885c97f0548ffc8eb0348ffc048898768010000488b8f58010000498b074885c94c8b4728490f48ca488945ef488b876001000048894dff488d4def4d8b0848895df7488945074c8955e7e8e478ffff84c074610f1045ef488d053d2700020f104dff0f1145b7f20f108768010000f20f1145d70f114dc7eb2f4c8b4728488d4d874889758748895d8f4d8b084c8955e7e8be78ffff84c0741b0f104587488d053f2700020f1145b7488945af488d45af488945e7488d55af498bc841ff511049891e488b4d174833cce8bf0e19fe488b9c24180100004881c4d0000000415f415e415d415c5f5e5dc3"},{"rva":32413664,"expected":"48895c2408574883ec208bda488bf9e806cb14fef6c301740dba78010000488bcfe8f40219fe488b5c2430488bc74883c4205fc3"},{"rva":32427296,"expected":"48895c2410488974242055574156488bec4883ec70488bda4c8bf1488db90001000048897d30488bcfe81e4a5eff85c00f8555020000418b864c0100003dffffff7f0f842f02000048895d20488d4d20e845241bfe492386b0000000498b9698000000488bc84803c9488b74ca084d8b8688000000493bf074184803c0488b0cc2483b5e10740d483bf17406488b7608ebef33f64885f60f84bd01000032db493bf00f849b01000048837e1800744c49ff8e58010000498b96580100004d8b46204d8b0848c745f800000000488d4d20e89b86ffff84c07417488d05e8330002488945c0488955c8488d45c0488945f8488d55c0498bc841ff51108b462483f801750d48837e18007406488b4618eb17385e200f847900000083f8017510488b46184885c074074929865001000049ff8e60010000498b96600100004d8b46204d8b08498b8e5001000033c04885c9480f48c848894db0488955b8488945f8488d4db0e84886ffff84c0741b488d059d330002488945c00f1045b00f1145c8488d45c0488945f8488d55c0498bc841ff511041389e680100007405385e20750e385e21740b41389e690100007402b301498b4e40488b01488b5628ff5018488b461048894520488d4d20e8cb221bfe488bc849238eb00000004803c9498b9698000000488b04ca483974ca087522483bc67512498b8688000000488904ca488944ca08eb17488b4608488944ca08eb0c483bc67507488b06488904ca488b1649ff8e90000000488b4608488910488b460848894208ba40000000488bcee898cb18fe90488bcfe807485eff84db7413498d4ef8e8e1d815feeb08488bcfe8f0475eff4c8d5c2470498b5b28498b7338498be3415e5f5dc3ffc84189864c010000b906000000e8ce475effccb905000000e8c3475effcc"}],"missingRva":32430298,"selfInfo":{"readerVtableRva":26219000,"readerSlot":16,"readerThunkRva":68946,"modelOffset":16,"modelVtableRva":25914384,"modelSlot":24,"modelThunkRva":261261,"getterRva":17654624,"guards":[{"rva":68946,"expected":"e909560c01"},{"rva":17654624,"expected":"40534883ec20488b4910488bda488b01ff5018488bc34883c4205bc3"},{"rva":261261,"expected":"e92e436e00"},{"rva":7487424,"expected":"40535556574883ec28488d6928488bf9488bcd488bf2e86a9899ff488b47384885c07404f0ff4008488b5f30488bcd488b7f38e8a42295ff488b034889064885ff742cbbffffffff8bc3f00fc1470883f801751b488b07488bcfff10f00fc15f0c83fb017509488b07488bcfff5008488bc64883c4285f5e5d5bc3"}]}};
    // Session-only UI counter metadata. No packet, model object, message text,
    // account ID, chat ID or cursor leaves this script or is written to disk.
    function unreadBridge(module, pin, active, core) {
        const ledgers = new Map(), baselines = new Map(), incoming = new Map();
        const listeners = [], scratch = Memory.alloc(64 * 0x40), slots = new Map();
        const selfId = new NativeFunction(core.base.add(pin.selfInfo.getterRva), 'pointer', ['pointer', 'pointer'],
            {abi: 'win64', scheduling: 'cooperative', exceptions: 'propagate'});
        let epoch = 0;
        const MAX_COUNT = 0x7fffffff, MAX_CHATS = 4096;
        function ownerKey(owner) {return owner.toString();}
        function chatKey(chat) {return chat.toString();}
        function ownerMap(table, owner) {
            const key = ownerKey(owner);
            if (!table.has(key)) {
                if (table.size >= 64) table.delete(table.keys().next().value);
                table.set(key, new Map());
            }
            return table.get(key);
        }
        function set(table, owner, chat, value) {
            const values = ownerMap(table, owner), key = chatKey(chat);
            if (!values.has(key) && values.size >= MAX_CHATS) values.delete(values.keys().next().value);
            values.set(key, value);
        }
        function get(table, owner, chat) {
            const values = table.get(ownerKey(owner));
            return values ? values.get(chatKey(chat)) : undefined;
        }
        function retire(owner, chat) {
            const values = ledgers.get(ownerKey(owner));
            if (values) values.delete(chatKey(chat));
        }
        function numeric(value) {
            const number = Number(value.toString());
            return Number.isInteger(number) && number >= 0 && number <= MAX_COUNT ? number : null;
        }
        function current(thread) {
            const stack = incoming.get(thread);
            return stack && stack.length ? stack[stack.length - 1] : null;
        }
        function same(frame, context) {
            return frame !== null && frame.epoch === epoch && frame.owner.equals(context.rdi) &&
                frame.chat.equals(context.r15) && frame.state.equals(context.r14);
        }
        function slot(thread) {
            if (!slots.has(thread)) {
                if (slots.size === 64) return null;
                slots.set(thread, slots.size);
            }
            return scratch.add(slots.get(thread) * 0x40);
        }
        function node(thread, credit) {
            const address = slot(thread);
            if (address === null) return null;
            address.add(0x28).writeS64(credit);
            return address;
        }
        function serverCursor(owner, chat, thread) {
            try {
                const output = slot(thread), model = pin.selfInfo;
                if (output === null) return null;
                const reader = owner.add(0x68).readPointer();
                if (!reader.readPointer().equals(core.base.add(model.readerVtableRva)) ||
                    !reader.readPointer().add(model.readerSlot).readPointer().equals(core.base.add(model.readerThunkRva))) return null;
                const account = reader.add(model.modelOffset).readPointer();
                if (!account.readPointer().equals(core.base.add(model.modelVtableRva)) ||
                    !account.readPointer().add(model.modelSlot).readPointer().equals(core.base.add(model.modelThunkRva))) return null;
                const ownId = output.add(0x30);
                if (!selfId(reader, ownId).equals(ownId)) return null;
                const id = ownId.readS64();
                if (id.compare(0) <= 0) return null;
                const head = chat.add(0x18).readPointer();
                let candidate = head.add(8).readPointer();
                for (let depth = 0; depth < 128; depth++) {
                    if (candidate.equals(head) || candidate.add(0x19).readU8() !== 0) return null;
                    const order = candidate.add(0x20).readS64().compare(id);
                    if (order === 0) return candidate.add(0x28).readS64();
                    candidate = candidate.add(order > 0 ? 0 : 0x10).readPointer();
                }
            } catch (_) {return null;}
            return null;
        }
        function clear() {epoch++; ledgers.clear(); baselines.clear();}
        const hooks = [
            [pin.decrementRva, function () {
                if (!active()) return;
                const c = this.context, owner = c.rsi, state = c.rbp, pair = c.r14;
                const before = numeric(c.rcx), after = numeric(c.rbx);
                if (before === null || after === null || after >= before) return;
                const chat = state.add(0x10).readS64(), id = pair.readS64(), cursor = pair.add(8).readS64();
                const baseline = get(baselines, owner, chat);
                // No guessed initial cursor and no invented remaining count.
                if (!baseline || baseline.cursor === null || cursor.compare(baseline.cursor) <= 0) return;
                let value = get(ledgers, owner, chat);
                if (value && (value.base.compare(baseline.cursor) !== 0 || cursor.compare(value.cursor) <= 0)) return;
                const credit = (value ? value.credit : 0) + before - after;
                if (credit > MAX_COUNT) {retire(owner, chat); return;}
                set(ledgers, owner, chat, {credit, base: baseline.cursor, id, cursor, raw: baseline.raw});
            }],
            [pin.incomingRva, {onEnter(args) {
                const frame = {epoch, owner: args[0], chat: args[1], state: args[2], valid: false,
                    serverCursor: null, ledger: null};
                if (active()) {
                    const chat = frame.chat.readS64();
                    frame.id = chat;
                    frame.serverCursor = serverCursor(frame.owner, frame.chat, this.threadId);
                    const raw = frame.chat.add(0x260).readS32();
                    if (frame.serverCursor !== null && raw >= 0)
                        set(baselines, frame.owner, chat, {cursor: frame.serverCursor, raw});
                    frame.ledger = get(ledgers, frame.owner, chat) || null;
                    if (frame.ledger && (frame.chat.add(0x580).readU8() !== 0 || frame.serverCursor === null || frame.serverCursor.compare(frame.ledger.base) !== 0 ||
                        raw < frame.ledger.raw || frame.state.add(0x18).readS64().compare(frame.ledger.id) !== 0 ||
                        frame.state.add(0x20).readS64().compare(frame.ledger.cursor) !== 0)) {
                        retire(frame.owner, chat); frame.ledger = null;
                    }
                    frame.valid = frame.ledger !== null;
                }
                let stack = incoming.get(this.threadId);
                if (!stack) {stack = []; incoming.set(this.threadId, stack);}
                stack.push(frame); this.unreadFrame = frame;
            }, onLeave() {
                const stack = incoming.get(this.threadId);
                if (stack) {
                    const at = stack.lastIndexOf(this.unreadFrame);
                    if (at >= 0) stack.splice(at, 1);
                    if (!stack.length) incoming.delete(this.threadId);
                }
            }}],
            [pin.missingRva, function () {
                const frame = current(this.threadId);
                // Without a proven ledger, show the actual server counter.
                // Only a temporary register changes; the UI cursor stays put.
                if (active() && same(frame, this.context))
                    this.context.rbx = ptr('0xffffffffffffffff');
            }],
            [pin.countRva, function () {
                const c = this.context, frame = current(this.threadId);
                if (!active() || !same(frame, c)) return;
                const raw = frame.chat.add(0x260).readS32();
                if (raw < 0) {retire(frame.owner, frame.id); return;}
                if (frame.serverCursor !== null)
                    set(baselines, frame.owner, frame.id, {cursor: frame.serverCursor, raw});
                let value = frame.valid ? frame.ledger : null;
                if (value && raw < value.raw) {retire(frame.owner, frame.id); value = null;}
                if (value) {
                    value.raw = raw;
                    const replacement = node(this.threadId, value.credit);
                    c.rdx = replacement === null ? c.r9 : replacement;
                } else {
                    // Conservative fallback also skips transient native pending
                    // subtraction, which may overlap a partial remote ACK.
                    c.rdx = c.r9;
                }
            }],
            [pin.destructorRva, {onEnter(args) {
                ledgers.delete(ownerKey(args[0])); baselines.delete(ownerKey(args[0]));
            }}],
            [pin.chatDeleteRva, {onEnter(args) {
                const owner = args[0].sub(8), chat = new Int64(args[1].toString());
                retire(owner, chat);
                const values = baselines.get(ownerKey(owner));
                if (values) values.delete(chatKey(chat));
            }}]
        ];
        return {
            install() {
                try {
                    for (const [rva, callbacks, coreSite] of hooks)
                        listeners.push(Interceptor.attach((coreSite ? core : module).base.add(rva), callbacks));
                } catch (error) {
                    for (const listener of listeners.splice(0)) listener.detach();
                    clear(); throw error;
                }
            },
            detach() {for (const listener of listeners.splice(0)) listener.detach(); clear();},
            clear,
            // Keep every native scratch node alive until the script unloads,
            // even when policy/dtor removes its ledger while native reads RDX.
            scratch
        };
    }
    // END_UNREAD_UI_BRIDGE
    function exact(left, right) {
        return keys(left, Object.keys(right)) && Object.keys(right).every(key => left[key] === right[key]);
    }
    function samePin(left, right) {
        if (Array.isArray(right)) return Array.isArray(left) && left.length === right.length &&
            right.every((value, index) => samePin(left[index], value));
        if (object(right)) return keys(left, Object.keys(right)) &&
            Object.keys(right).every(key => samePin(left[key], right[key]));
        return left === right;
    }
    function validUnreadSpec() {
        const pin = spec.unread;
        if (!object(pin) || !Object.keys(unreadPin).every(key => samePin(pin[key], unreadPin[key])) ||
            typeof pin.moduleSha256 !== 'string' || !/^[a-f0-9]{64}$/.test(pin.moduleSha256) ||
            !Number.isInteger(pin.moduleSize) || pin.moduleSize <= 0 ||
            !Number.isInteger(pin.imageSize) || pin.imageSize <= unreadPin.vtableRva + 16 ||
            !pathEqual(pin.modulePath, spec.modulePath.replace(/core\.dll$/i, 'max.exe'))) return false;
        return true;
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
            Object.keys(online).every(key => exact(spec.online[key], online[key])) && validUnreadSpec();
    }
    function installUnread(module, core) {
        const pin = spec.unread;
        if (!pathEqual(module.path, pin.modulePath) || module.size !== pin.imageSize) {
            state.error = 'module_mismatch'; return false;
        }
        for (const guard of pin.guards) {
            const address = module.base.add(guard.rva);
            if (!executable(address, guard.expected.length / 2) || bytes(address, guard.expected.length / 2) !== guard.expected) {
                state.error = 'module_mismatch'; return false;
            }
        }
        if (!module.base.add(pin.vtableRva).readPointer().equals(module.base.add(pin.destructorRva)) ||
            !module.base.add(pin.vtableRva + 8).readPointer().equals(module.base.add(pin.readRva))) {
            state.error = 'module_mismatch'; return false;
        }
        try {
            const runtime = unreadBridge(module, pin,
                () => state.ready && state.settings.enabled && state.settings.hideRead, core);
            runtime.install(); retained.push(runtime); unreadRuntime = runtime; liveMain = module.base;
            Interceptor.flush(); return true;
        } catch (_) {state.error = 'hook_failed'; return false;}
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
            const model = spec.unread.selfInfo;
            for (const guard of model.guards) {
                const address = module.base.add(guard.rva);
                if (!executable(address, guard.expected.length / 2) || bytes(address, guard.expected.length / 2) !== guard.expected) {
                    state.error = 'module_mismatch'; return;
                }
            }
            if (!module.base.add(model.readerVtableRva + model.readerSlot).readPointer().equals(module.base.add(model.readerThunkRva)) ||
                !module.base.add(model.modelVtableRva + model.modelSlot).readPointer().equals(module.base.add(model.modelThunkRva))) {
                state.error = 'module_mismatch'; return;
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
            liveModule = module.base; liveCore = module;
            if (pendingMain !== null && !installUnread(pendingMain, module)) throw new Error('unread_bridge_failed');
            state.ready = unreadRuntime !== null;
            state.error = state.ready ? null : 'waiting_for_core';
        } catch (_) {
            state.ready = false;
            state.error = state.error === 'module_mismatch' ? state.error : 'hook_failed';
            liveModule = null; liveCore = null;
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
        onAdded(module) {
            if (module.name.toLowerCase() === 'max.exe') {
                pendingMain = module;
                if (liveCore !== null && unreadRuntime === null && installUnread(module, liveCore)) {
                    state.ready = true; state.error = null;
                }
            }
            if (module.name.toLowerCase() === 'core.dll') install(module);
        },
        onRemoved(module) {
            if (pendingMain !== null && module.base.equals(pendingMain.base)) pendingMain = null;
            if (liveModule !== null && module.base.equals(liveModule)) {
                state.ready = false; liveModule = null; liveCore = null; state.error = 'module_unloaded';
                if (unreadRuntime !== null) {unreadRuntime.detach(); unreadRuntime = null; liveMain = null;}
            }
            if (liveMain !== null && module.base.equals(liveMain)) {
                state.ready = false; unreadRuntime.detach(); unreadRuntime = null; liveMain = null;
                pendingMain = null; state.error = 'module_unloaded';
            }
        }
    });
    retained.push(moduleObserver);
    receive();
    return {snapshot, invalidateUnreadCredits() {if (unreadRuntime !== null) unreadRuntime.clear();}};
})();
function ghostSnapshot() {return valmaxGhost.snapshot();}
// Existing deletion monitor calls this without any routing or message values.
function invalidateUnreadCredits() {
    if (typeof valmaxGhost.invalidateUnreadCredits === 'function') valmaxGhost.invalidateUnreadCredits();
}
