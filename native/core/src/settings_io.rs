//! Windows-only guarded settings IO. Callers supply an explicit path; this
//! module never discovers profiles. Existing ancestors remain open without
//! delete sharing. Child opens are relative to those handles, never re-resolved
//! through absolute ancestor paths. Files are validated by handle, and replacement renames
//! the still-open, flushed temporary file (no pathname reopen race).
use std::{fmt, fs::File, io::{Read, Write}, path::{Path, PathBuf}};
use sha2::{Digest, Sha256};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StoreError { InvalidSettings, InvalidRevision, RevisionConflict, InvalidStore, UnsafePath, StoreBusy, FileExists, IoError }
impl StoreError {
    pub fn code(self) -> &'static str { match self {
        Self::InvalidSettings => "invalid_settings", Self::InvalidRevision => "invalid_revision",
        Self::RevisionConflict => "revision_conflict", Self::InvalidStore => "invalid_store",
        Self::UnsafePath => "unsafe_path", Self::StoreBusy => "store_busy", Self::FileExists => "file_exists", Self::IoError => "io_error",
    }}
}
impl fmt::Display for StoreError { fn fmt(&self, f:&mut fmt::Formatter<'_>)->fmt::Result { f.write_str(self.code()) } }
impl std::error::Error for StoreError {}
pub type Result<T> = std::result::Result<T, StoreError>;
pub fn sha256(data: &[u8]) -> String { format!("{:x}", Sha256::digest(data)) }

#[derive(Clone, Debug)]
pub struct SafeStorePath { path: PathBuf }
impl SafeStorePath {
    pub fn new(path: impl AsRef<Path>) -> Result<Self> {
        let text = path.as_ref().to_str().ok_or(StoreError::UnsafePath)?;
        let bytes = text.as_bytes();
        if bytes.len() < 4 || !bytes[0].is_ascii_alphabetic() || bytes[1] != b':'
            || !matches!(bytes[2], b'/'|b'\\') || text.contains('\0') || text.len()>32000 {
            return Err(StoreError::UnsafePath);
        }
        for part in text[3..].split(['/', '\\']) {
            let stem=part.split('.').next().unwrap_or("").to_ascii_uppercase();
            let device=matches!(stem.as_str(), "CON"|"PRN"|"AUX"|"NUL")
                || stem.len()==4 && (stem.starts_with("COM")||stem.starts_with("LPT")) && (b'1'..=b'9').contains(&stem.as_bytes()[3]);
            if part.is_empty() || matches!(part,"."|"..") || part.ends_with(['.',' ']) || device
                || part.chars().any(|c| c<' ' || ":<>\"|?*".contains(c)) { return Err(StoreError::UnsafePath); }
        }
        Ok(Self { path: PathBuf::from(text.replace('/', "\\")) })
    }
    pub fn path(&self) -> &Path { &self.path }
    pub fn begin(&self, create: bool) -> Result<Option<GuardedStore>> {
        #[cfg(windows)] { win::begin(self,create) }
        #[cfg(not(windows))] { let _=create; Err(StoreError::IoError) }
    }
}

pub struct GuardedStore { path: PathBuf, _parents: Vec<File> }
impl GuardedStore {
    pub fn path(&self) -> &Path { &self.path }
    pub fn read_bounded(&self, maximum: usize) -> Result<Option<Vec<u8>>> {
        #[cfg(windows)] { win::read(self,maximum) }
        #[cfg(not(windows))] { let _=maximum; Err(StoreError::IoError) }
    }
    pub fn writer_lock(&self) -> Result<WriterLock> {
        #[cfg(windows)] { win::lock(&self.path) }
        #[cfg(not(windows))] { Err(StoreError::IoError) }
    }
    /// Caller holds writer_lock and validates both JSON and expected revision
    /// in revalidate. Revalidation occurs immediately before atomic replacement.
    pub fn atomic_replace(&self, payload: &[u8], revalidate: impl FnOnce()->Result<()>) -> Result<()> {
        #[cfg(windows)] { win::publish(self,payload,true,revalidate) }
        #[cfg(not(windows))] { let _=(payload,revalidate); Err(StoreError::IoError) }
    }
    /// Publish a new file by its still-open temporary handle. The kernel rename
    /// refuses an existing destination, including one created during the write.
    /// Callers select the path explicitly; this never creates parent directories.
    pub fn atomic_create(&self, payload: &[u8]) -> Result<()> {
        #[cfg(windows)] { win::publish(self,payload,false,|| Ok(())) }
        #[cfg(not(windows))] { let _=payload; Err(StoreError::IoError) }
    }
}

pub struct WriterLock {
    #[cfg(windows)] handle: *mut std::ffi::c_void,
    _not_send: std::marker::PhantomData<std::rc::Rc<()>>,
}
#[cfg(windows)] impl Drop for WriterLock { fn drop(&mut self) { unsafe { win::ReleaseMutex(self.handle); win::CloseHandle(self.handle); } } }

#[cfg(windows)] mod win {
    use super::*;
    use std::{ffi::c_void, os::windows::{ffi::OsStrExt,io::{AsRawHandle,FromRawHandle}}, mem, ptr};
    type Handle = *mut c_void;
    const OPEN_REPARSE:u32=0x00200000;
    const BACKUP_SEMANTICS:u32=0x02000000;
    const READ:u32=0x80000000;
    const WRITE:u32=0x40000000;
    const DELETE:u32=0x00010000;
    #[repr(C)] #[derive(Default,Copy,Clone,PartialEq,Eq)] struct FileTime { low:u32,high:u32 }
    #[repr(C)] #[derive(Default,Copy,Clone)] struct Info {
        attributes:u32, creation:FileTime, access:FileTime, write:FileTime,
        volume:u32,size_high:u32,size_low:u32,links:u32,index_high:u32,index_low:u32,
    }
    impl Info { fn identity(&self)->(u32,u32,u32) { (self.volume,self.index_high,self.index_low) } }
    #[link(name="kernel32")]
    unsafe extern "system" {
        fn CreateFileW(name:*const u16,access:u32,share:u32,security:*const c_void,creation:u32,flags:u32,template:Handle)->Handle;
        pub(super) fn CloseHandle(handle:Handle)->i32;
        fn GetLastError()->u32;
        fn GetFileType(handle:Handle)->u32;
        fn GetFileInformationByHandle(handle:Handle,info:*mut Info)->i32;
        fn GetFinalPathNameByHandleW(handle:Handle,buffer:*mut u16,length:u32,flags:u32)->u32;
        fn SetFileInformationByHandle(handle:Handle,class:u32,buffer:*const c_void,length:u32)->i32;
        fn CreateMutexW(security:*const c_void,owned:i32,name:*const u16)->Handle;
        fn WaitForSingleObject(handle:Handle,milliseconds:u32)->u32;
        pub(super) fn ReleaseMutex(handle:Handle)->i32;
        fn LCMapStringEx(locale:*const u16,flags:u32,source:*const u16,source_length:i32,destination:*mut u16,destination_length:i32,version:*const c_void,reserved:*const c_void,sort:isize)->i32;
    }
    #[repr(C)] struct IoStatus { status:usize, information:usize }
    #[repr(C)] struct UnicodeString { length:u16,maximum:u16,buffer:*mut u16 }
    #[repr(C)] struct ObjectAttributes { length:u32,root:Handle,name:*mut UnicodeString,attributes:u32,security:*mut c_void,quality:*mut c_void }
    #[link(name="ntdll")]
    unsafe extern "system" {
        fn NtSetInformationFile(handle:Handle,status:*mut IoStatus,information:*const c_void,length:u32,class:u32)->i32;
        fn RtlNtStatusToDosError(status:i32)->u32;
        fn NtCreateFile(handle:*mut Handle,access:u32,attributes:*mut ObjectAttributes,status:*mut IoStatus,allocation:*const i64,file_attributes:u32,share:u32,disposition:u32,options:u32,ea:*const c_void,ea_length:u32)->i32;
    }
    fn wide(path:&Path)->Vec<u16> { path.as_os_str().encode_wide().chain(Some(0)).collect() }
    fn failure(code:u32)->StoreError { if code==32 || code==33 { StoreError::StoreBusy } else { StoreError::IoError } }
    fn open(path:&Path,access:u32,share:u32,creation:u32,flags:u32)->Result<Option<File>> {
        let name=wide(path);let h=unsafe { CreateFileW(name.as_ptr(),access,share,ptr::null(),creation,flags|OPEN_REPARSE,ptr::null_mut()) };
        if h as isize == -1 {
            let code=unsafe { GetLastError() };
            return if creation==3 && matches!(code,2|3) { Ok(None) } else { Err(failure(code)) };
        }
        Ok(Some(unsafe { File::from_raw_handle(h) }))
    }
    fn open_at(parent:&File,name:&std::ffi::OsStr,access:u32,share:u32,create:bool,directory:bool)->Result<Option<File>> {
        let mut name:Vec<u16>=name.encode_wide().collect();
        if name.is_empty() || name.len()>32766 || name.iter().any(|&c|matches!(c,0|47|92|58)) { return Err(StoreError::UnsafePath); }
        let mut text=UnicodeString {length:(name.len()*2) as u16,maximum:(name.len()*2) as u16,buffer:name.as_mut_ptr()};
        let mut object=ObjectAttributes {length:mem::size_of::<ObjectAttributes>() as u32,root:parent.as_raw_handle(),name:&mut text,attributes:0x40,security:ptr::null_mut(),quality:ptr::null_mut()};
        let mut io=IoStatus {status:0,information:0};let mut handle=ptr::null_mut();
        let status=unsafe { NtCreateFile(&mut handle,access|0x00100000,&mut object,&mut io,ptr::null(),0x80,share,if create {2}else{1},OPEN_REPARSE|0x20|if directory {1}else{0},ptr::null(),0) };
        if status<0 {
            let code=unsafe { RtlNtStatusToDosError(status) };
            return if !create&&matches!(code,2|3) {Ok(None)} else if matches!(code,267|4390|1920|1921) {Err(StoreError::UnsafePath)} else {Err(failure(code))};
        }
        Ok(Some(unsafe {File::from_raw_handle(handle)}))
    }
    fn normalized(text:&str)->Result<String> {
        // CPython 3.14 ntpath.normcase uses Windows invariant LCMapStringEx,
        // not Unicode String::to_lowercase (notably different for capital İ).
        let source:Vec<u16>=text.replace('/',"\\").trim_end_matches('\\').encode_utf16().collect();
        let locale=[0u16];
        let n=unsafe {LCMapStringEx(locale.as_ptr(),0x100,source.as_ptr(),source.len() as i32,ptr::null_mut(),0,ptr::null(),ptr::null(),0)};
        if n<=0 {return Err(StoreError::UnsafePath);}
        let mut output=vec![0u16;n as usize];
        if unsafe {LCMapStringEx(locale.as_ptr(),0x100,source.as_ptr(),source.len() as i32,output.as_mut_ptr(),n,ptr::null(),ptr::null(),0)}!=n {return Err(StoreError::UnsafePath);}
        String::from_utf16(&output).map_err(|_|StoreError::UnsafePath)
    }
    fn validate(file:&File,path:&Path,directory:bool)->Result<Info> {
        let h=file.as_raw_handle(); let mut info=Info::default();
        if unsafe { GetFileType(h) } != 1 || unsafe { GetFileInformationByHandle(h,&mut info) }==0 { return Err(StoreError::UnsafePath); }
        if info.attributes&0x400!=0 || (info.attributes&0x10!=0)!=directory || !directory&&info.links!=1 { return Err(StoreError::UnsafePath); }
        let mut final_path=vec![0u16;32768];let n=unsafe { GetFinalPathNameByHandleW(h,final_path.as_mut_ptr(),final_path.len() as u32,0) } as usize;
        if n==0 || n>=final_path.len() { return Err(StoreError::UnsafePath); }
        let actual=String::from_utf16(&final_path[..n]).map_err(|_|StoreError::UnsafePath)?;
        let actual=actual.strip_prefix("\\\\?\\").unwrap_or(&actual);
        if normalized(actual)?!=normalized(path.to_str().ok_or(StoreError::UnsafePath)?)? { return Err(StoreError::UnsafePath); }
        Ok(info)
    }
    pub(super) fn begin(source:&SafeStorePath,create:bool)->Result<Option<GuardedStore>> {
        let mut handles=Vec::new();
        let parents:Vec<_>=source.path.ancestors().skip(1).collect();
        for parent in parents.into_iter().rev() {
            let candidate=match handles.last() {
                None=>open(parent,0x81,3,3,BACKUP_SEMANTICS)?,
                Some(previous)=>open_at(previous,parent.file_name().ok_or(StoreError::UnsafePath)?,0x81,3,false,true)?,
            };
            let handle=match candidate {
                Some(file)=>file,
                None if !create=>return Ok(None),
                None=> {
                    let previous=handles.last().ok_or(StoreError::UnsafePath)?;
                    let name=parent.file_name().ok_or(StoreError::UnsafePath)?;
                    match open_at(previous,name,0x81,3,true,true) {
                        Ok(Some(file))=>file,
                        // Another trusted operation may have created the same
                        // directory. Reopen relative to the pinned parent and
                        // apply exactly the same no-reparse validation.
                        Err(StoreError::IoError)=>open_at(previous,name,0x81,3,false,true)?.ok_or(StoreError::IoError)?,
                        _=>return Err(StoreError::IoError),
                    }
                },
            };
            validate(&handle,parent,true)?; handles.push(handle);
        }
        Ok(Some(GuardedStore { path:source.path.clone(), _parents:handles }))
    }
    pub(super) fn read(store:&GuardedStore,maximum:usize)->Result<Option<Vec<u8>>> {
        let path=&store.path;
        let Some(file)=open_at(store._parents.last().ok_or(StoreError::UnsafePath)?,path.file_name().ok_or(StoreError::UnsafePath)?,READ,1,false,false)? else { return Ok(None); };
        let before=validate(&file,path,false)?;
        let length=((before.size_high as u64)<<32)|before.size_low as u64;
        if length>maximum as u64 { return Err(StoreError::InvalidStore); }
        let mut data=Vec::new();(&file).take(maximum.saturating_add(1) as u64).read_to_end(&mut data).map_err(|_|StoreError::IoError)?;
        let after=validate(&file,path,false)?;
        if before.identity()!=after.identity() || before.write!=after.write || data.len() as u64!=length { return Err(StoreError::UnsafePath); }
        if data.len()>maximum { return Err(StoreError::InvalidStore); }
        Ok(Some(data))
    }
    pub(super) fn lock(path:&Path)->Result<WriterLock> {
        // Same namespace and normalization as Python quick_reply_store._writer_lock.
        let key=sha256(normalized(path.to_str().ok_or(StoreError::UnsafePath)?)?.as_bytes());
        let name=wide(Path::new(&format!("Local\\MAXModQuickReplies-{key}")));
        let handle=unsafe { CreateMutexW(ptr::null(),0,name.as_ptr()) };
        if handle.is_null() { return Err(StoreError::IoError); }
        match unsafe { WaitForSingleObject(handle,2000) } {
            0|0x80=>Ok(WriterLock { handle,_not_send:std::marker::PhantomData }),
            status=> { unsafe { CloseHandle(handle); } Err(if status==0x102 {StoreError::StoreBusy}else{StoreError::IoError}) },
        }
    }
    struct Temporary { file:File, committed:bool }
    impl Drop for Temporary {
        fn drop(&mut self) { if !self.committed {
            let delete:u8=1;
            unsafe { SetFileInformationByHandle(self.file.as_raw_handle(),4,&delete as *const _ as *const c_void,1); }
        }}
    }
    #[repr(C)] struct Rename { replace:u32, root:Handle, length:u32, name:[u16;1] }
    pub(super) fn publish(store:&GuardedStore,payload:&[u8],replace:bool,revalidate:impl FnOnce()->Result<()>)->Result<()> {
        let mut nonce=[0u8;16];getrandom::getrandom(&mut nonce).map_err(|_|StoreError::IoError)?;
        let suffix:String=nonce.iter().map(|b|format!("{b:02x}")).collect();
        let name=store.path.file_name().and_then(|v|v.to_str()).ok_or(StoreError::UnsafePath)?;
        let temporary=store.path.with_file_name(format!(".{name}.{suffix}.tmp"));
        let file=open_at(store._parents.last().ok_or(StoreError::UnsafePath)?,temporary.file_name().ok_or(StoreError::UnsafePath)?,READ|WRITE|DELETE,0,true,false)?.ok_or(StoreError::IoError)?;
        let mut temp=Temporary { file,committed:false };
        let before=validate(&temp.file,&temporary,false)?;
        temp.file.write_all(payload).map_err(|_|StoreError::IoError)?;
        temp.file.sync_all().map_err(|_|StoreError::IoError)?;
        if validate(&temp.file,&temporary,false)?.identity()!=before.identity() { return Err(StoreError::UnsafePath); }
        revalidate()?;
        // Handle-based rename retains exclusive ownership from creation through
        // commit; cleanup deletes that exact handle only, never an arbitrary path.
        let target:Vec<u16>=wide(Path::new(store.path.file_name().ok_or(StoreError::UnsafePath)?));
        let offset=mem::offset_of!(Rename,name);
        let size=offset+target.len()*2;
        let mut buffer=vec![0usize;size.div_ceil(mem::size_of::<usize>())];
        let raw=buffer.as_mut_ptr() as *mut Rename;
        unsafe {
            (*raw).replace=u32::from(replace);(*raw).root=store._parents.last().ok_or(StoreError::UnsafePath)?.as_raw_handle();(*raw).length=((target.len()-1)*2) as u32;
            ptr::copy_nonoverlapping(target.as_ptr(),(raw as *mut u8).add(offset) as *mut u16,target.len());
            let mut io=IoStatus {status:0,information:0};
            let status=NtSetInformationFile(temp.file.as_raw_handle(),&mut io,raw as *const c_void,size as u32,10);
            if status<0 {
                let code=RtlNtStatusToDosError(status);
                return Err(if !replace && matches!(code,80|183) { StoreError::FileExists } else {failure(code)});
            }
        }
        temp.committed=true;
        validate(&temp.file,&store.path,false)?;
        temp.file.sync_all().map_err(|_|StoreError::IoError)?;
        Ok(())
    }
}
