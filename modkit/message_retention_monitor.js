// Preserve the client's existing rows for the verified server-delete path.
// Only routing IDs leave this script, through the dedicated marker-store event.
// Native ownership, coroutine allocation and the packet ACK path remain intact.
'use strict';
const valmaxMessageRetention = (() => {
    const EXPECTED = [
    {
        "name": "core.dll",
        "moduleSha256": "352aed752b8802646d2a927dcd9de4031046083963f6bddaf4b32b99f75622e6",
        "moduleSize": 32905040,
        "imageSize": 32993280,
        "pins": [
            {
                "rva": 22258080,
                "expected": "48895c241855565741564157488dac24709affffb890660000e88105aefe482be0488b05b8a17d004833c4488985806500004c8bfa4889542450488d95b04200"
            },
            {
                "rva": 12598704,
                "expected": "48895c24205556574881eca0060000488b05ba0511014833c44889842498060000498bf0488bfa488bd948899424900600004c89442430498bd0488d4c2438e8"
            },
            {
                "rva": 12381408,
                "expected": "48895c24185556574154415541564157488dac2450faffff4881ecb0060000488b057a5614014833c4488985a80500004d8be14c894c2470498bd848895c2458"
            },
            {
                "rva": 22257616,
                "expected": "48895c2418488974242057b810230000e85a07aefe482be0488b0591a37d004833c44889842400230000488bf2488bf94889542428c74424200000000033d241"
            },
            {
                "rva": 12531744,
                "expected": "488b457f488983d800000044888be00000004c394b200f84a3010000488b0a488b0141b8cf000000488d559fff5008807dc700744e807dbf05750e488d459f80"
            },
            {
                "rva": 12382044,
                "expected": "ff533890488b4c24204885c97454488b442430482bc148c1f803488d14c5000000004881fa00100000721e488b41f8482bc84883e9084883f91f77064883c227"
            },
            {
                "rva": 12531376,
                "expected": "40555356574154415541564157488d6c24f94881ecf8000000488b05b00c12014833c4488945ef4d8bf14c894424204c8bea488bd948894c246048895424684c"
            },
            {
                "rva": 12531549,
                "expected": "498b00488901498b4008488941084d89084d8948084c39090f8494020000488d4b4048894c24404c89094c894908498b06488901498b4608488941084d890e4d"
            },
            {
                "rva": 12580432,
                "expected": "488b4360488b4830488b01488d9390000000ff502090488b08488b014c8b4368488d93a0000000ff500890488b83980000004889842480000000bfffffffff48"
            },
            {
                "rva": 12536368,
                "expected": "48895c24104889742418574881ec90000000488b0537f911014833c44889842480000000488bd948894c2440488d05a59cce00488901488d054b9ece00488941"
            }
        ],
        "slots": [
            {
                "vtableRva": 26128256,
                "slot": 112,
                "thunkRva": 414906,
                "thunkExpected": "e9e14c4d01"
            },
            {
                "vtableRva": 26075880,
                "slot": 32,
                "thunkRva": 609201,
                "thunkExpected": "e92aa1b300"
            },
            {
                "vtableRva": 26076936,
                "slot": 56,
                "thunkRva": 29111,
                "thunkExpected": "e9f4cbbf00"
            }
        ]
    },
    {
        "name": "storage.dll",
        "moduleSha256": "9e6c7e50912b5b9bae66fc5d0388b9ab917dba4bb1243786bf6dab758d01fcc7",
        "moduleSize": 13024592,
        "imageSize": 13066240,
        "pins": [
            {
                "rva": 3959360,
                "expected": "48895c24084c894424185556574883ec40488b05a86380004833c44889442438498bf8488bf2488be9b9e0010000e85a32c4ff488bd84889442420488d05bef7"
            },
            {
                "rva": 3957328,
                "expected": "48894c24085356574154415641574881ec48010000488b05946b80004833c44889842438010000488bbc248001000048897c24280fb787b000000066ffc06683"
            },
            {
                "rva": 3958370,
                "expected": "4c8bb7980000004c8bbfa00000004d3bf7743e4d8b06ba01000000488d8f20010000e87418c6ff488d8f20010000e84a97c4ff488d8f20010000e868a4c4ff48"
            },
            {
                "rva": 4092880,
                "expected": "4c8bdc49895b18574881eca0000000488b051a5a7e004833c44889842498000000488bda488bf948894c2450488d0511bc5900498943a0488d055afa59004989"
            },
            {
                "rva": 1793904,
                "expected": "488b12488b4908e965cde6ffcccccccc488b12488b4908e9d6e4e4ffcccccccce93c32e7ffcccccccccccccccccccccc48837938000f94c0c3cccccccccccccc"
            },
            {
                "rva": 1820067,
                "expected": "498b4d08488b014c8bcd4c8bc3488d542420ff50089048895c24404c8b6424204c896424484c8b7424284c897424500f57c0f30f7f4424204d8b07498b400841"
            },
            {
                "rva": 1879648,
                "expected": "488bc44c894018555356574154415541564157488da8f8feffff4881ecc80100000f2970a80f297898488b05701fa0004833c4488985980000004c894da84889"
            },
            {
                "rva": 1881048,
                "expected": "488b9520010000488b7da0488bcfe8fdc3e3ffc74424403f000000488b0f488b01488b55a8ff901001000090488b5dc84885db742b418bc6f00fc1430883f801"
            },
            {
                "rva": 2238923,
                "expected": "4c8d4dc74c8d45df488b554f488b12e87fc8deff90498d4424104c8b7d474989074d8967084885c07514e9950000004c8be7b81000000049890749897f08498b"
            },
            {
                "rva": 2262588,
                "expected": "488d0575b47500488901488951184c8941204c8941284c8941304c8941384c8941404c8941484c8941504c8941584c8941604c8941684c8941704c8941784c89"
            },
            {
                "rva": 2357568,
                "expected": "33c048890248894208488b81980000004885c07404f0ff4008488b8190000000488902488b819800000048894208488bc2c3cccccccccccccccccccccccccccc"
            },
            {
                "rva": 2560546,
                "expected": "488d8b8800000048894c242848892948896908498b06488901498b46084889410849892e49896e084839290f84c50000000f57c00f1183b00000000f1183c000"
            },
            {
                "rva": 2421168,
                "expected": "4c8bdc4d894318534883ec60488d4168488bda488d51484c8d49584c8d4138498d4b0849894bc8498d4b1849894bc0498d4be0498943b8e89b73dbff488b4424"
            },
            {
                "rva": 2417318,
                "expected": "498b4f084885c97404f0ff4108498b0f48898c2480000000498b4f0848898c2488000000488d4e10884424304889542428488d44245048894424204c8d4c2460"
            },
            {
                "rva": 3934992,
                "expected": "4c8d4424304c894424404c89294c89690848895110488941180f57c0f30f7f4424304885d20f8439020000488d05968a5c00488906488d05048b5c0048894608"
            },
            {
                "rva": 4000656,
                "expected": "40555356574154415541564157488dac2498f9ffff4881ec68070000488b054dc27f004833c448898550060000498bd94d8be04c894590488bf2488955984c8be948894c2440488b3a48897c2470488b89a8000000410f10010f294580488b01488d558048895424204d8b8dd80000004c8bc7488d95f8000000ff50104c8d85f8000000488bd3498b8dd8000000e8dd3affff498b8db8000000498b04244889442430498b542408482bd048b8d504e3adec73484d48f7ea48c1fa07488bc248c1e83f4803d04889542438488b014c8d4c24304d8b85d8000000488bd6ff5018498d4540488985a0000000488bd0488d8d70030000e8f02dc4ff90488378180f7603488b004c8bc0488bd7488d8d00040000e80b3cc4ff90488b95880300004883fa0f763448ffc2488b8d700300004881fa00100000721c488b41f8482bc84883e9084883f91f0f87100100004883c227488bc8e87804c5ffc6451000c6859000000000c6856001000000488d8d70030000e8ed70c5ff90488378180f7603488b004c8bc0488bd7488d8d50040000e88e3bc4ff90488b95880300004883fa0f763448ffc2488b8d700300004881fa00100000721c488b41f8482bc84883e9084883f91f0f87f60900004883c227488bc8e8fb03c5ff48c785800300000000000048c785880300000f000000c6857003000000498b3c244d8b7c2408493bff0f8405080000488b7424304c8b742430904c8dbf980000004d85ff744880bf2001000002753f488d855004000048894424284c897c24204d8b8dd80000004c8d8510010000488b542470488d8df0000000e83bfaffff488b3041b401eb0ab905000000cd294532e4498d4d40e859dac3ff488b10488975a0448865a88b4c2439894da90fb74c243d66894dad0fb64c243f884daf488995c0000000488b4808482bca48b8a38b2ebae8a28b2e48f7e948c1fa04488bc248c1e83f4803d0488995c8000000488d45a04889442420448b8dd00600004c8bc7488d95c0000000488d8d00040000e8ac7ac5ff448baf28010000488d8d00040000e853efc3ff85c00f8415020000488b4750488985d0000000488b4f58482bc848c1f90648898dd80000000f1047200f29442450488d85d000000048894424204c8d4c24504c8b074c8b642440498b9424d8000000488d8d50040000e8e9fbffff4d85ff0f845d01000080bf20010000010f8550010000498b07488d88980000004885c9744080b820010000027537488d8550040000488944242848894c24204d8b8c24d80000004c8d8510010000488b542470488d4c2478e8dcf8ffff4c8b3041b401eb034532e4488d8d00040000e8aefbc3ff488d8d00040000e88348c3ff498b1f488b8da0000000e8e3d8c3ff488b104c8974243044886424388b4c2439894c24390fb74c243d66894c243d0fb64c243f884c243f488995e0000000488b4808482bca48b8a38b2ebae8a28b2e48f7e948c1fa04488bc248c1e83f4803d0488995e8000000488d442430488944242041b9010000004c8bc3488d95e0000000488d8d00040000e83179c5ff488d8d00040000e8dfedc3ff4d8b07498b4050488945b0498b4858482bc848c1f90648894db8410f1040200f29442450488d45b048894424204c8d4c24504d8b00488b542440488b92d8000000488d8d50040000e883faffff488d8d00040000e8b0fac3ff488d8d00040000e88547c3ff488d87a80100004183fd020f850a0500004c8bc74c8b6590498b542408488bc8e8174ac3ff498b4c24084881e9a8010000e8d501c4ff498144240858feffffe9de0400004183fd02749e4c8b7c2440807d10000f857e010000498b97d8000000488d8de0030000e8ad16c4ff90488378180f7603488b004c8bc04c8b642470498bd4488d8d90030000e8bf37c4ff488d9590030000488d4dc0807d10000f84db000000e8c74bc5ff488b85b0030000488945e0488b85b8030000488b95c00300004533ed4c89adb80300004c89adc0030000488945e8488b4df0488955f04885c97424e8f8b8c3ff8b85c80300008945f80fb685cc0300008845fc0fb685cd0300008845fdeb2d8b85c80300008945f80fb685cc0300008845fc0fb685cd0300008845fd488d85d0030000488d4d00483bc87453488b5d004c8b4308488d5500488d4d00e85a13c4ff48895b0848891b48895b10488b4d00488b85d00300004889450048898dd0030000488b85d8030000488945084c89add8030000eb09e82892c4ffc6451001488d8d90030000e80ab9c4ff90488b95f80300004883fa0f763b48ffc2488b8de00300004881fa00100000721c488b41f8482bc84883e9084883f91f0f87220500004883c227488bc8e827ffc4ffeb054c8b6424704c8b07ba01000000488d4dc0e8ab6cc5ff488d4dc0e828d4c3ff84c00f84ec02000080bd90000000000f8596010000488d8de0030000e8b20bc3ff90488378180f7603488b004c8bc0498bd4488d8d20030000e81936c4ff488d9520030000488d4d4080bd90000000000f84f0000000e81e4ac5ff488b854003000048894560488b8548030000488b95500300004533e44c89a5480300004c89a55003000048894568488b4d70488955704885c97424e84fb7c3ff8b85580300008945780fb6855c03000088457c0fb6855d03000088457deb308b85580300008945780fb6855c03000088457c0fb6855d03000088457d488d8560030000488d8d80000000483bc87468488b9d800000004c8b4308488d9580000000488d8d80000000e8a511c4ff48895b0848891b48895b10488b8d80000000488b85600300004889858000000048898d60030000488b8568030000488985880000004c89a568030000eb0ce86a90c4ffc68590000000014c8b85600300004d8b4008488d9560030000488d8d60030000e83d11c4ffba48000000488b8d60030000e885fdc4ff488b8d500300004885c97405e858b6c3ff488d8d20030000e817ccc4ff90488d8de0030000e80accc4ff498b9fd8000000498d4f40e843d4c3ff488b0848894d80488b5008482bd148b8a38b2ebae8a28b2e48f7ea48c1fa04488bc248c1e83f4803d048895588488d454048894424204c8bcb4c8d4580488d55c0488d8d70010000e8d071c4ff900f2885900100000f29442450488d454048894424204c8d4c24504c8b8570010000498b97d8000000488d8de0030000e89991c4ff488bd0488d8dc0010000e8d4a2c3ff488d8de0030000e8bc44c4ff80bd9801000000740f4c8b85900100004c894520b201eb0632d24c8b4520807f2800740f488b4720488985b0000000b001eb0232c033c984c0480f458db000000033c084d2490f45c0483bc17e42488d9730010000488d8da0020000e84efdc2ff498b8fb8000000488b014c8d8d700100004d8b87d8000000488b5598ff5010488d9570010000488bcfe8df31c5ff90488d8d70010000e821fdc3ff488d4dc0e8b1f5c3ff488d4dc0e88942c3ff488d8d00040000e89cf5c3ff488d8d00040000e87142c3ff488d87a8010000488bf84c8b65904d8b7c24084c8b6c2440493bff0f8506f8ffff498b342441beffffffff493bf70f843c0400000f1f00488b466848894580488b4e70482bc848c1f90448894d88488d5580488d4db0e8d690c3ff90488b5db04885db0f84c303000080bb28030000000f84b6030000488b8380010000488985700100000f1083880100000f118578010000488b8398010000488985880100000f1083a00100000f298590010000488d93b0010000488d8da0010000e8fa1dc5ff90488d93d0010000488d8dc0010000e8922bc5ff9033ff4889bdd80100000f57c0660f7f85e0010000488b93f0010000482b93e80100004883fa10726f48c1fa04488d8dd8010000e8bc7dc4ff4c8b83f0010000488b85d8010000488b93e8010000493bd0743e0f1f40006666660f1f84000000000048893848897808488b4a084885c97404f0ff4108488b0a488908488b4a08488948084883c0104883c210493bd075d1488985e0010000488b8300020000488985f00100000f1083080200000f1185f8010000488d85080200004889442478488d9318020000c68590020000ff480fbe83a00200004883c00174424883e8017435488d8d080200004883f801741a4c8bc233d2e83066c5ffc6859002000002eb1cb905000000cd29e88809c4ffc6859002000001eb07c68590020000008b83a8020000898598020000488d85a00200004889442478c685e00200000080bbf002000000741a488d93b0020000488d8da0020000e8e614c4ffc685e0020000018b83f80200008985e8020000488b83fc020000488985ec0200000f1083040300000f1185f40200008b8314030000898504030000488b831803000048898508030000488b832003000048898510030000488b95700100004c8bc2488d8db0000000e8e3d2c3ff488d9570010000488d8da0040000e8d00fc3ff900f57c0f30f7f44245048897c2460b9a8010000e88d84c3ff488bd848894424504889442458488d88a801000048894c2460488d4424504889442478488dbda004000048895d2048895d28488d442450488945300f1f440000488bd7488bcbe8c649c5ff4881c3a801000048895d284881c7a8010000488d8548060000483bf875d748895c24584c8d0dbbf9c3ffbaa801000041b801000000488d8da0040000e8cbefc4ff0f2885b0000000660f7f4520c7442420010000004c8d4d204c8d442450488b5598498bcde80970c4ff90488d4c2450e8d9a3c4ff9080bde0020000007418488d8dc0020000e8fac6c4ff488d8da8020000e87a54c4ff480fbe85900200004883c00174504883e801744a4883f8017438488d8d78020000e891a3c4ff80bd7002000000740c488d8d50020000e8b3c6c4ff80bd4802000000741a488d8d28020000e89ec6c4ffeb0c488d8d08020000e859a3c4ff488d8dd8010000e86b36c3ff488d8dc0010000e8d73fc4ff488d8da0010000e86cc6c4ff90488b5db84885db742b418bc6f00fc1430883f801751e488b03488bcbff10418bc6f00fc1430c83f8017509488b03488bcbff50084881c6a8010000493bf70f85c7fbffff4c8b85900400004d8b4008488d9590040000488d8d90040000e8010bc4ffba48000000488b8d90040000e849f7c4ff488b9d800400004885db742b418bc6f00fc1430883f801751e488b03488bcbff10418bc6f00fc1430c83f8017509488b03488bcbff5008488d8d50040000e8b5c5c4ff9080bd6001000000740d488d8d10010000e891b0c4ff9080bd9000000000740a488d4d40e87eb0c4ff90807d1000740a488d4dc0e86eb0c4ff904c8b85400400004d8b4008488d9540040000488d8d40040000e8550ac4ffba48000000488b8d40040000e89df6c4ff488b9d300400004885db742a418bc6f00fc1430883f801751d488b03488bcbff10f0440fc1730c4183fe017509488b03488bcbff5008488d8d00040000e80ac5c4ff488b8d500600004833cce87d60c5ff4881c468070000415f415e415d415c5f5e5b5dc3"
            },
            {
                "rva": 4001184,
                "expected": "4c8dbf980000004d85ff744880bf2001000002753f488d855004000048894424"
            },
            {
                "rva": 4003193,
                "expected": "488d87a8010000488bf84c8b65904d8b7c24084c8b6c2440493bff0f8506f8ff"
            },
            {
                "rva": 4003248,
                "expected": "488b466848894580488b4e70482bc848c1f90448894d88488d5580488d4db0e8"
            },
            {
                "rva": 4004313,
                "expected": "4881c6a8010000493bf70f85c7fbffff4c8b85900400004d8b4008488d959004"
            }
        ],
        "slots": [
            {
                "vtableRva": 9976504,
                "slot": 32,
                "thunkRva": 59746,
                "thunkExpected": "e9d90f2300"
            },
            {
                "vtableRva": 9982040,
                "slot": 8,
                "thunkRva": 55591,
                "thunkExpected": "e9c4502600"
            },
            {
                "vtableRva": 9980360,
                "slot": 8,
                "thunkRva": 11191,
                "thunkExpected": "e9f4c52400"
            }
        ]
    }
];
    const LIMITS = {"maxMessageIds": 4096, "maxScopes": 16, "maxThreads": 128, "maxKnownMessages": 65536, "maxKnownBatchIds": 4096, "maxKnownBatchRecords": 256, "maxOwnerInstances": 128, "maxRows": 4096};
    const state = {installed: false, enabled: false, available: true,
        retainedEvents: 0, retainedMessages: 0, error: 'waiting_for_modules'};
    const held = [], modules = new Map(), scopes = new Map(), rowScopes = new Map();
    const owners = new Map(), known = new Set();
    let unloaded = false;
    const config = typeof VALMAX_RETENTION === 'undefined' ? null : VALMAX_RETENTION;
    function object(value) {return value !== null && typeof value === 'object' && !Array.isArray(value);}
    function keys(value, names) {
        return object(value) && Object.keys(value).length === names.length &&
            names.every(name => Object.prototype.hasOwnProperty.call(value, name));
    }
    function equal(value, expected) {
        if (Array.isArray(expected)) return Array.isArray(value) && value.length === expected.length &&
            expected.every((item, index) => equal(value[index], item));
        if (object(expected)) return keys(value, Object.keys(expected)) &&
            Object.keys(expected).every(name => equal(value[name], expected[name]));
        return value === expected;
    }
    function pathEqual(left, right) {
        return typeof left === 'string' && typeof right === 'string' &&
            left.replace(/\//g, '\\').toLowerCase() === right.replace(/\//g, '\\').toLowerCase();
    }
    function validSpec() {
        if (!keys(config, ['specification', 'retentionEnabled', 'storageAvailable', 'knownMessages']) ||
            typeof config.retentionEnabled !== 'boolean' || config.storageAvailable !== true) return false;
        const spec = config.specification;
        return keys(spec, ['schema', 'modules', 'limits', 'historySyncVerified']) && spec.schema === 1 &&
            spec.historySyncVerified === false && equal(spec.limits, LIMITS) &&
            Array.isArray(spec.modules) && spec.modules.length === EXPECTED.length &&
            EXPECTED.every((expected, index) => {
                const item = spec.modules[index];
                return keys(item, [...Object.keys(expected), 'modulePath']) &&
                    Object.keys(expected).every(name => equal(item[name], expected[name])) &&
                    typeof item.modulePath === 'string' && !item.modulePath.includes('\0') &&
                    /^([a-z]:[\\/]).+[\\/](core|storage)\.dll$/i.test(item.modulePath) &&
                    pathEqual(item.modulePath.slice(-expected.name.length), expected.name);
            });
    }
    function active() {return state.installed && state.available && state.error === null;}
    function filtering() {return active() && state.enabled;}
    function snapshot() {
        return {ready: active(), enabled: state.enabled, retainedEvents: state.retainedEvents,
            retainedMessages: state.retainedMessages,
            error: !state.available ? 'storage_unavailable' : state.error, historySyncVerified: false};
    }
    function fail(code) {state.error = code;}
    function span(pointer, size, writable = false, executable = false) {
        if (pointer.isNull() || !Number.isInteger(size) || size < 1 || size > 2097152 ||
            pointer.add(size).compare(pointer) <= 0) return false;
        const range = Process.findRangeByAddress(pointer);
        return range !== null && range.protection.includes('r') &&
            (!writable || range.protection.includes('w')) &&
            (!executable || range.protection.includes('x')) &&
            pointer.compare(range.base) >= 0 && pointer.add(size).compare(range.base.add(range.size)) <= 0;
    }
    function bytes(pointer, count) {
        return Array.from(new Uint8Array(pointer.readByteArray(count)))
            .map(value => value.toString(16).padStart(2, '0')).join('');
    }
    function positiveId(pointer) {
        const value = pointer.readS64().toString();
        return /^[1-9][0-9]{0,18}$/.test(value) && value !== '9223372036854775807' ? value : null;
    }
    function chatId(pointer) {
        const value = pointer.readS64().toString();
        return /^-?[1-9][0-9]{0,18}$/.test(value) && value !== '9223372036854775807' ? value : null;
    }
    function canonicalId(value, positive) {
        if (typeof value !== 'string' || !/^-?[1-9][0-9]{0,18}$/.test(value) ||
            (positive && value[0] === '-')) return false;
        const negative = value[0] === '-', digits = negative ? value.slice(1) : value;
        return digits.length < 19 || (digits.length === 19 &&
            digits <= (negative ? '9223372036854775808' : '9223372036854775807'));
    }
    function key(account, chat, id) {return account + '|' + chat + '|' + id;}
    function validateKnown(records, batch) {
        if (!Array.isArray(records) || records.length > (batch ? LIMITS.maxKnownBatchRecords : LIMITS.maxKnownMessages)) return null;
        const values = new Set(); let count = 0;
        for (const record of records) {
            if (!keys(record, ['accountId', 'chatId', 'messageIds']) ||
                !canonicalId(record.accountId, true) || !canonicalId(record.chatId, false) ||
                !Array.isArray(record.messageIds) || record.messageIds.length < 1 || record.messageIds.length > 256) return null;
            for (const id of record.messageIds) {
                if (!canonicalId(id, true)) return null;
                if (batch && ++count > LIMITS.maxKnownBatchIds) return null;
                values.add(key(record.accountId, record.chatId, id));
                if (values.size > LIMITS.maxKnownMessages) return null;
            }
        }
        return values;
    }
    function mergeKnown(values) {
        let additions = 0;
        for (const value of values) if (!known.has(value)) additions++;
        if (known.size + additions > LIMITS.maxKnownMessages) {fail('known_capacity'); return false;}
        for (const value of values) known.add(value);
        return true;
    }
    function ownerEnter(args) {this.retentionOwner = args[0];}
    function ownerLeave() {
        const pointer = this.retentionOwner;
        try {
            if (!pointer || !span(pointer, 0xe0) ||
                !pointer.readPointer().equals(modules.get('core.dll').base.add(0x18de708))) return;
            if (!owners.has(pointer.toString()) && owners.size >= LIMITS.maxOwnerInstances) {fail('scope_guard'); return;}
            // Join is deliberately lazy: storage initialization can finish
            // after this constructor. Destructor removes this allocation.
            owners.set(pointer.toString(), pointer);
        } catch (_) {fail('memory_guard');}
    }
    function ownerDestroyed(args) {owners.delete(args[0].toString());}
    function accountForHistory(history) {
        const core = modules.get('core.dll').base, storage = modules.get('storage.dll').base;
        if (!span(history, 0xe0) || !history.readPointer().equals(storage.add(0x9895d8))) return null;
        const database = history.add(0x30).readPointer(), control = history.add(0x38).readPointer();
        if (database.isNull() || control.isNull()) return null;
        let account = null;
        for (const pointer of owners.values()) {
            if (!span(pointer, 0xe0) || !pointer.readPointer().equals(core.add(0x18de708))) continue;
            const current = positiveId(pointer.add(0xd8));
            if (current === null) continue;
            const data = pointer.add(0x30).readPointer();
            if (!span(data, 0xa0) || !data.readPointer().equals(storage.add(0x983ab8))) continue;
            const historyOwner = data.add(0x90).readPointer();
            if (!span(historyOwner, 0x98) || !historyOwner.readPointer().equals(storage.add(0x985058))) continue;
            const factory = historyOwner.add(0x88).readPointer();
            if (!span(factory, 0x58) || !factory.readPointer().equals(storage.add(0x9849c8))) continue;
            if (!factory.add(0x48).readPointer().equals(database) || !factory.add(0x50).readPointer().equals(control)) continue;
            if (account !== null && account !== current) return null; // Ambiguous account is never first-match.
            account = current;
        }
        return account;
    }
    function rowEnter(args) {
        if (!filtering()) return;
        const thread = this.threadId;
        if (!Number.isInteger(thread) || thread < 0) {fail('scope_guard'); return;}
        let stack = rowScopes.get(thread);
        if (!stack) {
            if (rowScopes.size >= LIMITS.maxThreads) {fail('scope_guard'); return;}
            stack = []; rowScopes.set(thread, stack);
        }
        if (stack.length >= LIMITS.maxScopes) {fail('scope_guard'); return;}
        const scope = {thread, eligible: false, skipped: new Set(), retained: new Set(), received: new Set()};
        stack.push(scope); this.retentionRows = scope;
        try {
            const history = args[0], vector = args[2], sp = this.context.sp;
            if (!span(sp.add(0x28), 4) || sp.add(0x28).readS32() !== 0 || !span(vector, 24)) return;
            const account = accountForHistory(history), chat = chatId(history.add(0xd8));
            if (account === null || chat === null) return;
            const begin = vector.readPointer(), end = vector.add(8).readPointer(), cap = vector.add(16).readPointer();
            if (begin.equals(end)) return;
            if (begin.isNull() || (begin.toUInt32() & 7) !== 0 || begin.compare(end) >= 0 || end.compare(cap) > 0 ||
                end.compare(begin.add(LIMITS.maxRows * 0x1a8)) > 0) return;
            const length = Number(end.sub(begin).toString());
            if (length % 0x1a8 !== 0 || !span(begin, length)) return;
            Object.assign(scope, {eligible:true, account, chat, history, vector, begin, end,
                sp:sp.add(-0x7a8), bp:sp.add(-0x6a8)});
        } catch (_) {scope.eligible = false; fail('memory_guard');}
    }
    function rowScope(sp, bp, thread, row) {
        const stack = rowScopes.get(thread), scope = stack && stack[stack.length - 1];
        if (!scope || !scope.eligible || !sp.equals(scope.sp) || !bp.equals(scope.bp)) return null;
        if (row.compare(scope.begin) < 0 || row.compare(scope.end) >= 0 ||
            Number(row.sub(scope.begin).toString()) % 0x1a8 !== 0) return null;
        if (!scope.vector.readPointer().equals(scope.begin) || !scope.vector.add(8).readPointer().equals(scope.end)) return null;
        return scope;
    }
    function firstRowGate(history, row, bp, sp) {
        if (!filtering()) return 0;
        try {
            const scope = rowScope(sp, bp, Process.getCurrentThreadId(), row);
            if (!scope || !history.equals(scope.history) || !span(row, 0x12c)) return 0;
            const id = positiveId(row), status = row.add(0x128).readS32();
            if (id === null) return 0;
            const receipt = key(scope.account, scope.chat, id);
            if (status === 2 && known.has(receipt)) {
                const pointerKey = row.toString(), alreadySkipped = scope.skipped.has(pointerKey), alreadyMarked = scope.retained.has(id);
                try {scope.skipped.add(pointerKey); scope.retained.add(id);}
                catch (error) {
                    if (!alreadySkipped) scope.skipped.delete(pointerKey);
                    if (!alreadyMarked) scope.retained.delete(id);
                    throw error;
                }
                return 1;
            } else if (status === 0 || status === 1) {
                if (!known.has(receipt)) {
                    if (known.size >= LIMITS.maxKnownMessages) {fail('known_capacity'); return 0;}
                    known.add(receipt); scope.received.add(id);
                }
            }
        } catch (_) {fail('memory_guard');}
        return 0;
    }
    function secondRowGate(history, row, bp, sp) {
        // An earlier first-pass decision must remain paired even if the host
        // switches the setting or storage fails in the middle of this call.
        try {
            const scope = rowScope(sp, bp, Process.getCurrentThreadId(), row);
            if (scope && history.equals(scope.history) && scope.skipped.has(row.toString())) return 1;
        } catch (_) {fail('memory_guard');}
        return 0;
    }
    function sendBatches(kind, scope, ids) {
        const array = Array.from(ids);
        for (let at = 0; at < array.length; at += 256)
            send({kind, schema:1, accountId:scope.account, chatId:scope.chat, messageIds:array.slice(at, at + 256)});
    }
    function rowLeave() {
        const scope = this.retentionRows;
        if (!scope) return;
        const stack = rowScopes.get(scope.thread);
        if (!stack || stack[stack.length - 1] !== scope) {fail('scope_guard'); return;}
        stack.pop(); if (!stack.length) rowScopes.delete(scope.thread);
        if (!scope.eligible) return;
        try {
            sendBatches('received-messages', scope, scope.received);
            sendBatches('retained-delete', scope, scope.retained);
            if (scope.retained.size) {
                state.retainedEvents = Math.min(1000000, state.retainedEvents + 1);
                state.retainedMessages = Math.min(1000000, state.retainedMessages + scope.retained.size);
            }
        } catch (_) {fail('emit_failed');}
    }
    // This native branch mechanism is independently exercised by the owned
    // C++ fixture: context.pc assignment does not redirect Interceptor hooks.
    // Preserve every GPR, FLAGS and all sixteen XMM registers before calling
    // the routing-only predicate; restore with a JMP, never a synthetic RET.
    const gateRegisters = ['rax','rcx','rdx','rbx','rbp','rsi','rdi',
        'r8','r9','r10','r11','r12','r13','r14','r15'];
    function gateXmm(writer, register, offset, load) {
        const output = [0xf3];
        if (register >= 8) output.push(0x44);
        output.push(0x0f, load ? 0x6f : 0x7f, 0x84 | ((register & 7) << 3), 0x24);
        for (let index = 0; index < 4; index++) output.push((offset >>> (8 * index)) & 255);
        writer.putBytes(output);
    }
    function restoreGate(writer, destination) {
        for (let index = 15; index >= 0; index--) gateXmm(writer, index, 0x20 + index * 16, true);
        writer.putBytes([0x48,0x8b,0x84,0x24,0x20,0x01,0x00,0x00]);
        writer.putMovRegReg('rsp','rax');
        for (let index = gateRegisters.length - 1; index >= 0; index--) writer.putPopReg(gateRegisters[index]);
        writer.putBytes([0x9d]);
        writer.putJmpAddress(destination);
    }
    function allocateRowGate(gate, advance, predicate, prefix, rowRegister, instructions, installed) {
        let memory = null;
        // Module layout can exhaust the small nearby region. The second
        // bounded attempt still stays well inside a signed32-bit E9 redirect.
        for (const maxDistance of [0x1000000,0x70000000]) {
            try {memory = Memory.alloc(Process.pageSize, {near:gate,maxDistance});}
            catch (_) {memory = null;}
            if (memory !== null && !memory.isNull()) break;
        }
        if (memory === null || memory.isNull()) throw new Error('gate_allocation');
        const redirectOrigin = gate.add(5), forward = memory.compare(redirectOrigin) >= 0;
        const distance = forward ? memory.sub(redirectOrigin) : redirectOrigin.sub(memory);
        if (distance.compare(ptr(forward ? 0x7fffffff : 0x80000000)) > 0)
            throw new Error('gate_allocation');
        const pass = memory.add(0x800);
        held.push(memory,predicate);
        let relocated = 0, count = 0;
        Memory.patchCode(pass, 0x200, writable => {
            const writer = new X86Writer(writable, {pc:pass}), relocator = new X86Relocator(gate,writer);
            try {
                while (relocated < 16) {
                    const next = relocator.readOne(), instruction = relocator.input;
                    if (next === 0 || next > 19 || instruction.mnemonic !== instructions[count] ||
                        !instruction.address.equals(gate.add(relocated))) throw new Error('gate_relocation');
                    relocated = next; count++;
                }
                if (relocated !== 19 || count !== instructions.length) throw new Error('gate_relocation');
                relocator.writeAll(); writer.putJmpAddress(gate.add(relocated)); writer.flush();
            } finally {relocator.dispose(); writer.dispose();}
        });
        Memory.patchCode(memory, 0x800, writable => {
            const writer = new X86Writer(writable, {pc:memory});
            try {
                writer.putBytes([0x9c]);
                for (const register of gateRegisters) writer.putPushReg(register);
                writer.putMovRegReg('rax','rsp');
                writer.putBytes([0x48,0x83,0xe4,0xf0]);
                writer.putBytes([0x48,0x81,0xec,0x30,0x01,0x00,0x00]);
                writer.putBytes([0x48,0x89,0x84,0x24,0x20,0x01,0x00,0x00]);
                for (let index = 0; index < 16; index++) gateXmm(writer,index,0x20 + index * 16,false);
                writer.putBytes([0xfc]); // callback ABI DF=0; saved native FLAGS are restored.
                writer.putMovRegReg('rcx','r13'); writer.putMovRegReg('rdx',rowRegister);
                writer.putMovRegReg('r8','rbp'); writer.putMovRegReg('r9','rax'); writer.putAddRegImm('r9',128);
                writer.putMovRegAddress('rax',predicate); writer.putCallReg('rax'); writer.putTestRegReg('eax','eax');
                writer.putJccNearLabel('je','pass','no-hint'); restoreGate(writer,advance);
                writer.putLabel('pass'); restoreGate(writer,pass); writer.flush();
            } finally {writer.dispose();}
        });
        if (bytes(gate,32) !== prefix || Memory.protect(memory,Process.pageSize,'r-x') !== true)
            throw new Error('gate_original');
        Interceptor.replaceFast(gate,memory); installed.push(gate); Interceptor.flush();
        const branch = Instruction.parse(gate);
        const destination = branch.mnemonic === 'jmp' && /^0x[0-9a-f]+$/i.test(branch.opStr) ? ptr(branch.opStr) : null;
        // FAST reserves >=16 bytes even for a 5-byte redirect. Both whole
        // instruction prefixes are exactly19 bytes; untouched suffix proves
        // that no instruction outside the prepared relocation was overwritten.
        if (branch.size !== 5 || destination === null || !destination.equals(memory) ||
            bytes(gate,32).slice(relocated * 2) !== prefix.slice(relocated * 2)) throw new Error('gate_overwrite');
    }
    function eventEnter(args) {
        if (!filtering()) return;
        const thread = this.threadId;
        if (!Number.isInteger(thread) || thread < 0) {fail('scope_guard'); return;}
        let stack = scopes.get(thread);
        if (!stack) {
            if (scopes.size >= LIMITS.maxThreads) {fail('scope_guard'); return;}
            stack = []; scopes.set(thread, stack);
        }
        if (stack.length >= LIMITS.maxScopes) {fail('scope_guard'); return;}
        // Every nested event gets a barrier, including TTL/invalid events. It
        // must never inherit an eligible outer event for the same account.
        const scope = {eligible: false, processor: null, thread};
        stack.push(scope); this.retentionScope = scope;
        try {
            const core = modules.get('core.dll').base, event = args[0], owningReader = args[1];
            if (!span(event, 0x23ea) || !event.readPointer().equals(core.add(0x18eaf80)) ||
                !span(owningReader, 16)) return;
            // Read TTL before CommonEvent::getResponse moves/resets its DTO.
            const ttlPresent = event.add(0x23e9).readU8();
            if (ttlPresent !== 0 && ttlPresent !== 1) return;
            if (ttlPresent === 1) {
                const ttl = event.add(0x23e8).readU8();
                if (ttl !== 0) return; // true and invalid representations both pass through.
            }
            // Comment/post deletion has a different owning path; do not touch.
            if (event.add(0x2378).readU8() !== 0) return;
            const adapter = owningReader.readPointer();
            if (!span(adapter, 0x20) || !adapter.readPointer().equals(core.add(0x18de2e8))) return;
            const processor = adapter.add(0x18).readPointer();
            if (!span(processor, 0xe0) || !processor.readPointer().equals(core.add(0x18de708))) return;
            scope.processor = processor; scope.eligible = true;
        } catch (_) {
            // Bad/unknown event memory affects this event only; original runs.
            scope.eligible = false;
        }
    }
    function eventLeave() {
        const scope = this.retentionScope;
        if (!scope) return;
        const stack = scopes.get(scope.thread);
        if (!stack || stack[stack.length - 1] !== scope) {fail('scope_guard'); return;}
        stack.pop(); if (stack.length === 0) scopes.delete(scope.thread);
    }
    function processorEnter(args) {
        // A delete plus new messages can leave the raw unread total unchanged.
        // Invalidate the session-only ghost offset even while retention is off.
        if (typeof invalidateUnreadCredits === 'function') invalidateUnreadCredits();
        if (!filtering()) return;
        const stack = scopes.get(this.threadId), scope = stack && stack[stack.length - 1];
        if (!scope || !scope.eligible) return;
        try {
            const core = modules.get('core.dll').base;
            // This exact synchronous call, not an arbitrary or delayed virtual call.
            if (!this.returnAddress.equals(core.add(0xbcef5f)) || !args[0].equals(scope.processor)) return;
            const processor = args[0], chat = args[1], vector = args[2];
            if (!span(processor, 0xe0) || !processor.readPointer().equals(core.add(0x18de708)) ||
                !span(chat, 0x620) || !span(vector, 24, true)) return;
            const account = positiveId(processor.add(0xd8)), conversation = chatId(chat);
            if (account === null || conversation === null) return;
            const begin = vector.readPointer(), end = vector.add(8).readPointer(), cap = vector.add(16).readPointer();
            if (begin.equals(end)) return; // Do not emit a marker for an already empty event.
            if (begin.isNull() || (begin.toUInt32() & 7) !== 0 || begin.compare(end) >= 0 ||
                end.compare(cap) > 0 || end.compare(begin.add(LIMITS.maxMessageIds * 8)) > 0) return;
            const length = Number(end.sub(begin).toString());
            if (length % 8 !== 0 || !span(begin, length)) return;
            const ids = [], unique = new Set();
            for (let at = 0; at < length; at += 8) {
                const id = positiveId(begin.add(at));
                if (id === null) return;
                if (!unique.has(id)) {unique.add(id); ids.push(id);}
            }
            // Only end changes. Original code still owns begin/cap, moves the
            // vector, skips SQL status writes and frees the same allocation.
            vector.add(8).writePointer(begin);
            try {
                send({kind: 'retained-delete', schema: 1, accountId: account, chatId: conversation, messageIds: ids});
            } catch (_) {
                // Synchronous send failure can still restore this unconsumed arg.
                vector.add(8).writePointer(end); fail('emit_failed'); return;
            }
            state.retainedEvents = Math.min(1000000, state.retainedEvents + 1);
            state.retainedMessages = Math.min(1000000, state.retainedMessages + ids.length);
        } catch (_) {fail('memory_guard');}
    }
    function install() {
        if (state.installed || unloaded || modules.size !== EXPECTED.length) return;
        const attached = [], replaced = [];
        try {
            for (let index = 0; index < EXPECTED.length; index++) {
                const expected = EXPECTED[index], spec = config.specification.modules[index];
                const module = modules.get(expected.name);
                if (!pathEqual(module.path, spec.modulePath) || module.size !== expected.imageSize) {
                    fail('module_mismatch'); return;
                }
                for (const pin of expected.pins) {
                    const address = module.base.add(pin.rva), count = pin.expected.length / 2;
                    if (!span(address, count, false, true) || bytes(address, count) !== pin.expected) {
                        fail('module_mismatch'); return;
                    }
                }
                for (const slot of expected.slots) {
                    const thunk = module.base.add(slot.thunkRva), address = module.base.add(slot.vtableRva + slot.slot);
                    if (!span(thunk, 5, false, true) || bytes(thunk, 5) !== slot.thunkExpected ||
                        !span(address, 8) || !address.readPointer().equals(thunk)) {
                        fail('module_mismatch'); return;
                    }
                }
            }
            const base = modules.get('core.dll').base;
            attached.push(Interceptor.attach(base.add(0x153a1a0), {onEnter: eventEnter, onLeave: eventLeave}));
            attached.push(Interceptor.attach(base.add(0xc03db0), {onEnter: processorEnter}));
            attached.push(Interceptor.attach(base.add(0xbf36b0), {onEnter:ownerEnter, onLeave:ownerLeave}));
            attached.push(Interceptor.attach(base.add(0xbf4a30), {onEnter:ownerDestroyed}));
            const storage = modules.get('storage.dll').base;
            attached.push(Interceptor.attach(storage.add(0x3d0b90), {onEnter:rowEnter, onLeave:rowLeave}));
            const first = new NativeCallback(firstRowGate,'int',['pointer','pointer','pointer','pointer'],'win64');
            const second = new NativeCallback(secondRowGate,'int',['pointer','pointer','pointer','pointer'],'win64');
            held.push(first,second);
            const pins = EXPECTED.find(item => item.name === 'storage.dll').pins;
            allocateRowGate(storage.add(0x3d0da0),storage.add(0x3d1579),first,
                pins.find(pin => pin.rva === 0x3d0da0).expected,'rdi',['lea','test','je','cmp'],replaced);
            allocateRowGate(storage.add(0x3d15b0),storage.add(0x3d19d9),second,
                pins.find(pin => pin.rva === 0x3d15b0).expected,'rsi',['mov','mov','mov','sub','sar'],replaced);
            Interceptor.flush(); held.push(...attached);
            state.installed = true; state.error = null;
        } catch (_) {
            for (const gate of replaced.reverse()) {try {Interceptor.revert(gate);} catch (_) {}}
            for (const hook of attached) {try {hook.detach();} catch (_) {}}
            try {Interceptor.flush();} catch (_) {}
            held.push(...attached); fail('hook_failed');
        }
    }
    function receiveSettings() {
        recv('valmax-retention-settings', message => {
            if (keys(message, ['type', 'enabled']) && message.type === 'valmax-retention-settings' &&
                typeof message.enabled === 'boolean') state.enabled = message.enabled;
            receiveSettings();
        });
    }
    function receiveStorage() {
        recv('valmax-retention-storage', message => {
            if (keys(message, ['type', 'available']) && message.type === 'valmax-retention-storage' &&
                typeof message.available === 'boolean') state.available = message.available;
            receiveStorage();
        });
    }
    function receiveKnown() {
        recv('valmax-retention-known', message => {
            if (keys(message, ['type', 'schema', 'records']) && message.type === 'valmax-retention-known' && message.schema === 1) {
                const values = validateKnown(message.records, true);
                if (values !== null) mergeKnown(values);
            }
            receiveKnown();
        });
    }
    if (Process.platform !== 'windows' || Process.arch !== 'x64' || Process.pointerSize !== 8) {
        fail('unsupported_platform'); return {snapshot};
    }
    if (!validSpec()) {fail('invalid_specification'); return {snapshot};}
    const initialKnown = validateKnown(config.knownMessages, false);
    if (initialKnown === null || !mergeKnown(initialKnown)) {fail('invalid_specification'); return {snapshot};}
    state.enabled = config.retentionEnabled;
    held.push(Process.attachModuleObserver({
        onAdded(module) {
            const name = module.name.toLowerCase();
            if (!EXPECTED.some(item => item.name === name)) return;
            if (modules.has(name) && !modules.get(name).base.equals(module.base)) {fail('module_mismatch'); return;}
            modules.set(name, module); install();
        },
        onRemoved(module) {
            const current = modules.get(module.name.toLowerCase());
            if (current && current.base.equals(module.base)) {
                unloaded = true; state.installed = false; scopes.clear(); rowScopes.clear(); owners.clear(); fail('module_unloaded');
            }
        }
    }));
    receiveSettings(); receiveStorage(); receiveKnown();
    return {snapshot};
})();
function messageRetentionSnapshot() {return valmaxMessageRetention.snapshot();}
