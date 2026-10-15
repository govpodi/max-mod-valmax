// Process-local smoke-test guard. This is instrumentation, not an OS sandbox.
// Configuration is prepended by smoke_test.py. No credentials or payloads logged.
'use strict';
const keep = [];
const hooked = new Set();
const counters = {};
const namespaceValues = {};
const configProbes = {};
const deviceAliases = [];
try {
  const kernel = Process.getModuleByName('kernelbase.dll');
  const query = new NativeFunction(kernel.getExportByName('QueryDosDeviceW'),'uint',['pointer','pointer','uint']);
  for (const drive of ['C:','D:','E:']) {
    const buffer=Memory.alloc(4096);
    if (query(Memory.allocUtf16String(drive),buffer,2048)>0) deviceAliases.push([buffer.readUtf16String().toLowerCase(),drive.toLowerCase()]);
  }
} catch (error) { event('guard-error',{where:'device aliases',error:String(error)}); }
function event(type, details = {}) { send({type, ...details}); }
function count(name) { counters[name] = (counters[name] || 0) + 1; }
function countNamespace(value, kind) {
  if(!value)return;
  const upper=value.toUpperCase();
  for(const marker of ['ONEME','MXMOD'])if(upper.includes(marker))count(kind+'_namespace_'+marker);
}
function hookReplace(module, name, result, args, impl) {
  const address = module.findExportByName(name);
  if (address === null || hooked.has(address.toString())) return;
  const original = new SystemFunction(address, result, args);
  const callback = new NativeCallback(function (...values) {
    return impl.call(this, original, values);
  }, result, args);
  Interceptor.replace(address, callback);
  hooked.add(address.toString()); keep.push(callback);
  event('hook', {module: module.name, name});
}
function fail(error, value, name) {
  return function () { this.lastError = error; count(name); return value; };
}
function protectedPath(path) {
  if (!path) return false;
  let normal = path.replace(/^\\\?\?\\/, '').replace(/^\\\\\?\\/, '').replace(/\//g, '\\').toLowerCase();
  for (const [device,drive] of deviceAliases) if (normal.startsWith(device+'\\')) normal=drive+normal.slice(device.length);
  if (/^\\device\\harddiskvolume|^volume\{/i.test(normal)) return true; // Unknown alias: fail closed.
  const parts=[];
  for (const part of normal.split('\\')) {if(part==='..')parts.pop();else if(part!=='.')parts.push(part);}
  normal=parts.join('\\');
  const profile = GUARD.hostProfile.replace(/\//g, '\\').toLowerCase();
  return normal === profile || normal.startsWith(profile + '\\');
}
function originalResult(context, original, args) {
  const result = original(...args); context.lastError = result.lastError; return result.value;
}
const moduleObserver = Process.attachModuleObserver({onAdded(module) {
  const name = module.name.toLowerCase();
  if (name === 'ntdll.dll') {
    // Block direct file opens in the host profile, including Qt's NtCreateFile path.
    for (const [symbol, types] of [
      ['NtCreateFile', ['pointer','uint','pointer','pointer','pointer','uint','uint','uint','uint','pointer','uint']],
      ['NtOpenFile', ['pointer','uint','pointer','pointer','uint','uint']]
    ]) hookReplace(module, symbol, 'int', types, function (original, args) {
      try {
        const attrs = args[2];
        if (!attrs.isNull()) {
          const rootHandle=attrs.add(8).readPointer();
          const str = attrs.add(16).readPointer();
          if (!str.isNull()) {
            const length = str.readU16();
            const data = str.add(8).readPointer();
            let path=data.isNull()?'':data.readUtf16String(length/2);
            countNamespace(path,'file');
            if (!rootHandle.isNull() && !path.startsWith('\\')) {
              const getPath=new NativeFunction(Process.getModuleByName('kernelbase.dll').getExportByName('GetFinalPathNameByHandleW'),'uint',['pointer','pointer','uint','uint']);
              const buffer=Memory.alloc(8192), used=getPath(rootHandle,buffer,4096,0);
              if (used===0 || used>=4096) {count('unresolved_relative_file_denied');return -1073741790;}
              path=buffer.readUtf16String()+'\\'+path;
            }
            if(path.toUpperCase().includes('ONEME') && (counters.oneme_path_observations||0)<10){
              const synthetic=path.toLowerCase().replace(/\\/g,'/').includes(GUARD.profile.toLowerCase());
              event('namespace-path',{marker:'ONEME',folderSegment:path.split(/[\\/]/).some(part=>part.toUpperCase()==='ONEME'),hostProfile:protectedPath(path),syntheticPath:synthetic?path:null});
              count('oneme_path_observations');
            }
            if (protectedPath(path)) {
              count('host_profile_file_denied'); return -1073741790; // STATUS_ACCESS_DENIED
            }
          }
        }
      } catch (error) { event('guard-error', {where:symbol,error:String(error)}); return -1073741790; }
      return originalResult(this, original, args);
    });
  }
  if (name === 'advapi32.dll') {
    for (const suffix of ['A','W']) {
      hookReplace(module, 'CredRead'+suffix, 'int', ['pointer','uint','uint','pointer'], function (_original,args) {
        try {countNamespace(suffix==='W'?args[0].readUtf16String():args[0].readUtf8String(),'credential');}catch(_){}
        if (!args[3].isNull()) args[3].writePointer(ptr(0));
        this.lastError = 1168; count('credential_read_denied'); return 0;
      });
      hookReplace(module, 'CredEnumerate'+suffix, 'int', ['pointer','uint','pointer','pointer'], function (_original,args) {
        if (!args[2].isNull()) args[2].writeU32(0);
        if (!args[3].isNull()) args[3].writePointer(ptr(0));
        this.lastError = 1168; count('credential_enumerate_denied'); return 0;
      });
      hookReplace(module, 'CredWrite'+suffix, 'int', ['pointer','uint'], fail(5,0,'credential_write_denied'));
      hookReplace(module, 'CredDelete'+suffix, 'int', ['pointer','uint','uint'], fail(1168,0,'credential_delete_denied'));
      // Preserve host registry settings during smoke tests.
      hookReplace(module, 'RegCreateKeyEx'+suffix, 'int', ['pointer','pointer','uint','pointer','uint','uint','pointer','pointer','pointer'], function (_original,args) {
        if (!args[7].isNull()) args[7].writePointer(ptr(0)); count('registry_write_denied'); return 5;
      });
      hookReplace(module, 'RegSetValueEx'+suffix, 'int', ['pointer','pointer','uint','uint','pointer','uint'], function () {count('registry_write_denied'); return 5;});
      for (const [symbol,types] of [
        ['RegDeleteValue',['pointer','pointer']],['RegDeleteKey',['pointer','pointer']],
        ['RegDeleteKeyEx',['pointer','pointer','uint','uint']],['RegDeleteTree',['pointer','pointer']],
        ['RegSetKeyValue',['pointer','pointer','pointer','uint','pointer','uint']]
      ]) hookReplace(module,symbol+suffix,'int',types,function(){count('registry_write_denied');return 5;});
    }
  }
  if (GUARD.offline && name === 'ws2_32.dll') {
    for (const [symbol, types] of [
      ['connect',['pointer','pointer','int']],
      ['WSAConnect',['pointer','pointer','int','pointer','pointer','pointer','pointer']],
      ['send',['pointer','pointer','int','int']],
      ['sendto',['pointer','pointer','int','int','pointer','int']],
      ['WSASend',['pointer','pointer','uint','pointer','uint','pointer','pointer']],
      ['WSASendTo',['pointer','pointer','uint','pointer','uint','pointer','int','pointer','pointer']]
    ]) hookReplace(module,symbol,'int',types,fail(10013,-1,'network_'+symbol+'_denied'));
    for (const symbol of ['getaddrinfo','GetAddrInfoW']) {
      hookReplace(module,symbol,'int',['pointer','pointer','pointer','pointer'],function (_original,args) {
        if (!args[3].isNull()) args[3].writePointer(ptr(0)); this.lastError=11001; count('dns_denied'); return 11001;
      });
    }
    // ConnectEx/WSASendMsg are extension pointers, not necessarily DLL exports.
    hookReplace(module,'WSAIoctl','int',['pointer','uint','pointer','uint','pointer','uint','pointer','pointer','pointer'],function(original,args){
      if (args[1]===0xc8000006 || args[1]===0xc8000024) {this.lastError=10013;count('network_extension_denied');return -1;}
      return originalResult(this,original,args);
    });
  }
  if(GUARD.offline && name==='dnsapi.dll') {
    for(const symbol of ['DnsQuery_A','DnsQuery_W','DnsQuery_UTF8'])hookReplace(module,symbol,'int',['pointer','uint','uint','pointer','pointer','pointer'],function(){count('dnsapi_denied');return 9005;});
    hookReplace(module,'DnsQueryEx','int',['pointer','pointer','pointer'],function(){count('dnsapi_denied');return 9005;});
  }
  if (GUARD.offline && name === 'mswsock.dll') hookReplace(module,'ConnectEx','int',['pointer','pointer','int','pointer','uint','pointer','pointer'],fail(10013,0,'network_ConnectEx_denied'));
  if (GUARD.offline && name === 'winhttp.dll') hookReplace(module,'WinHttpSendRequest','int',['pointer','pointer','uint','pointer','uint','uint','pointer'],fail(12029,0,'network_winhttp_denied'));
  if (GUARD.offline && name === 'wininet.dll') for (const suffix of ['A','W']) hookReplace(module,'HttpSendRequest'+suffix,'int',['pointer','pointer','uint','pointer','uint'],fail(12029,0,'network_wininet_denied'));
  if (name === 'qt6core.dll') {
    const assign = new NativeFunction(module.getExportByName('??4QString@@QEAAAEAV0@PEBD@Z'),'pointer',['pointer','pointer']);
    const location = module.getExportByName('?writableLocation@QStandardPaths@@SA?AVQString@@W4StandardLocation@1@@Z');
    Interceptor.attach(location, {
      onEnter(args) { this.output=args[0]; this.location=args[1].toInt32(); },
      onLeave() {
        // Map every per-user standard location to a precreated test directory.
        const path = GUARD.profile + '/qt/' + this.location;
        assign(this.output, Memory.allocUtf8String(path)); count('qt_path_redirected');
      }
    });
    event('hook',{module:module.name,name:'QStandardPaths::writableLocation'});
    const debug = module.findExportByName('?qt_message_output@@YAXW4QtMsgType@@AEBVQMessageLogContext@@AEBVQString@@@Z');
    if (debug !== null) Interceptor.attach(debug,{onEnter(args){
      if(!GUARD.offline){count('qt_messages_omitted');return;}
      try {
        const text=args[2], size=Number(text.add(16).readS64());
        if (size > 0 && size < 8192) event('qt-message',{level:args[0].toInt32(),text:text.add(8).readPointer().readUtf16String(size)});
      } catch (_) {}
    }});
  }
  if (name === 'config.dll') {
    for (const [key, symbol] of [
      ['maskLogs','?needMaskLogs@Config@@YA_NXZ'],
      ['sendErrorLogs','?sendErrorLogsToCrashServer@Config@@YA_NXZ'],
      ['autoUpdate','?getAutoUpdateEnabled@Config@@YA_NXZ'],
      ['roomsAutoUpdate','?getRoomsAutoUpdateEnabled@Config@@YA_NXZ']
    ]) {
      const address=module.findExportByName(symbol);
      // Short patched return thunks cannot safely be intercepted by Frida.
      // Invoke these public, argument-free getters after startup instead.
      if (address !== null) configProbes[key]=new NativeFunction(address,'uchar',[]);
    }
    // Only public, static identity strings are observed; no account data.
    for (const [key, symbol] of [
      ['appFolder','?getAppFolderName@Config@@YA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ'],
      ['instance','?getSingleInstanceId@Config@@YA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ'],
      ['pipe','?getLocalServerPipeName@Config@@YA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ']
    ]) {
      const address=module.findExportByName(symbol);
      if(address!==null) Interceptor.attach(address,{
        onEnter(args){this.output=args[0];},
        onLeave(){
          try {
            const size=Number(this.output.add(16).readU64()), capacity=Number(this.output.add(24).readU64());
            if(size>0 && size<128){
              const value=(capacity<16?this.output:this.output.readPointer()).readUtf8String(size);
              namespaceValues[key]=value; event('namespace-value',{key,value});
            }
          }catch(error){event('guard-error',{where:'namespace observation',error:String(error)});}
        }
      });
    }
  }
  if (name === 'storage.dll') Interceptor.attach(module.base.add(0x43d900),{onLeave(retval){event('account-latch',{value:retval.toInt32()&255});}});
  if (name === 'tracernative.dll') for(const symbol of ['tracer_init','tracer_upload_async','tracer_install_crash_handler','tracer_collect_user_sample']) {
    const address=module.findExportByName(symbol);
    if(address!==null){
      if(address.readU8()===0xc3){event('sdk-stub',{symbol});count('sdk_stub_'+symbol);}
      else Interceptor.attach(address,{onEnter(){count('sdk_'+symbol);event('sdk-call',{symbol});}});
    }
  }
}});
rpc.exports = {
  summary() {
    const configValues={};
    for(const [key,probe] of Object.entries(configProbes)){
      event('getter-probe-start',{key});configValues[key]=probe();event('getter-probe-end',{key,value:configValues[key]});
    }
    let sdkObjectPresent=null;
    const sdk=Process.findModuleByName('tracernative.dll');
    if(sdk!==null)sdkObjectPresent=!sdk.base.add(0x1ef070).readPointer().add(8).readPointer().isNull();
    return {pid:Process.id,counters,hooks:hooked.size,namespaceValues,configValues,sdkObjectPresent};
  },
  // Exercise the guards without fetching a credential, opening a file, or sending data.
  selftest() {
    const advapi=Process.findModuleByName('advapi32.dll') || Module.load('advapi32.dll');
    const read=new NativeFunction(advapi.getExportByName('CredReadW'),'int',['pointer','uint','uint','pointer']);
    const output=Memory.alloc(Process.pointerSize); output.writePointer(ptr(0));
    const before=counters.credential_read_denied||0;
    const returned=read(Memory.allocUtf16String('CodexMaxModGuardSelfTest'),1,0,output);
    if (returned !== 0 || !output.readPointer().isNull()) throw Error('credential guard selftest failed');
    if ((counters.credential_read_denied||0)!==before+1) throw Error('credential hook was not exercised');
    return {credentialReadDenied:true};
  }
};
event('guard-ready',{pid:Process.id});
