//! Native equivalents of modkit/optimization_store.py. Desired flags are saved
//! separately from the immutable active snapshot chosen once per client launch.
use std::{path::Path,sync::Mutex};
use serde_json::{Value,json};
use crate::settings_io::{SafeStorePath,GuardedStore,StoreError,Result,sha256};

pub const MAX_STORE_BYTES:usize=4096;
pub const ACTIVE_FILENAME:&str="optimization-active.json";
pub const KEYS:[&str;11]=["enabled","smoothWheel","listScheduling","listPrefetch","readyCache","historyCache","historyBookkeeping","photoDecodeResize","windowPosition","hiddenPageWork","profileRows"];
#[derive(Clone,Copy,Debug,PartialEq,Eq)]
pub struct Settings([bool;11]);
impl Default for Settings { fn default()->Self { Self([true;11]) } }
impl Settings {
    pub fn from_value(value:&Value)->Result<Self> { Self::validate(value,StoreError::InvalidSettings) }
    fn validate(value:&Value,error:StoreError)->Result<Self> {
        let object=value.as_object().ok_or(error)?;
        if object.len()!=KEYS.len() { return Err(error); }
        let mut flags=[false;11];
        for (i,key) in KEYS.iter().enumerate() { flags[i]=object.get(*key).and_then(Value::as_bool).ok_or(error)?; }
        Ok(Self(flags))
    }
    pub fn value(self)->Value { let mut result=serde_json::Map::new();for (key,flag) in KEYS.iter().zip(self.0) { result.insert((*key).into(),Value::Bool(flag)); }Value::Object(result) }
    pub fn canonical(self)->Vec<u8> {
        // Every key/value is fixed ASCII/bool; serde's sorted map representation
        // is byte-identical to Python sort_keys/separators UTF-8 serialization.
        serde_json::to_vec(&json!({"schema":1,"settings":self.value()})).expect("finite bool settings")
    }
    pub fn revision(self)->String { sha256(&self.canonical()) }
}
pub fn parse(data:&[u8])->Result<Settings> {
    if data.len()>MAX_STORE_BYTES { return Err(StoreError::InvalidStore); }
    let value=crate::session_archive::parse_json(data,"invalid_payload").map_err(|_|StoreError::InvalidStore)?;
    let object=value.as_object().ok_or(StoreError::InvalidStore)?;
    if object.len()!=2 || object.get("schema").and_then(Value::as_u64)!=Some(1) { return Err(StoreError::InvalidStore); }
    Settings::validate(object.get("settings").ok_or(StoreError::InvalidStore)?,StoreError::InvalidStore)
}
fn read(guard:&GuardedStore)->Result<Settings> { match guard.read_bounded(MAX_STORE_BYTES)? { Some(data)=>parse(&data),None=>Ok(Settings::default()) } }
#[derive(Clone,Debug,PartialEq)]
pub struct Snapshot { pub revision:String,pub settings:Settings,pub applied_settings:Settings,pub restart_required:bool }
impl Snapshot { pub fn value(&self)->Value { json!({"schema":1,"available":true,"revision":self.revision,"settings":self.settings.value(),"appliedSettings":self.applied_settings.value(),"restartRequired":self.restart_required}) } }
struct Activation { active:Settings,activated:bool }
pub struct OptimizationSettingsStore { path:SafeStorePath, activation:Mutex<Activation> }
impl OptimizationSettingsStore {
    pub fn new(path:impl AsRef<Path>)->Result<Self> { Ok(Self { path:SafeStorePath::new(path)?,activation:Mutex::new(Activation { active:Settings::default(),activated:false }) }) }
    fn desired(&self)->Result<Settings> { match self.path.begin(false)? {Some(guard)=>read(&guard),None=>Ok(Settings::default())} }
    fn existing_active(&self,path:&SafeStorePath)->Result<Settings> {
        if let Some(guard)=path.begin(false)? {
            if let Some(data)=guard.read_bounded(MAX_STORE_BYTES)? { return parse(&data); }
        }
        // An upgrade can remove the optional runtime snapshot while preserving
        // the profile. Recover only a missing file; a present one still wins.
        let guard=path.begin(true)?.ok_or(StoreError::IoError)?;
        let _lock=guard.writer_lock()?;
        if let Some(data)=guard.read_bounded(MAX_STORE_BYTES)? { return parse(&data); }
        let clean=self.desired()?;
        match guard.atomic_create(&clean.canonical()) {
            Ok(())=>Ok(clean),
            Err(StoreError::FileExists)=> {
                let data=guard.read_bounded(MAX_STORE_BYTES)?.ok_or(StoreError::IoError)?;
                parse(&data)
            },
            Err(error)=>Err(error),
        }
    }
    fn snapshot_value(&self,settings:Settings)->Result<Snapshot> {
        let active=self.activation.lock().map_err(|_|StoreError::IoError)?.active;
        Ok(Snapshot { revision:settings.revision(),settings,applied_settings:active,restart_required:settings!=active })
    }
    pub fn snapshot(&self)->Result<Snapshot> { self.snapshot_value(self.desired()?) }
    pub fn replace(&self,expected_revision:&str,value:&Value)->Result<Snapshot> {
        if expected_revision.len()!=64 || !expected_revision.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b)) { return Err(StoreError::InvalidRevision); }
        let settings=Settings::from_value(value)?;
        let guard=self.path.begin(true)?.ok_or(StoreError::IoError)?;
        let _lock=guard.writer_lock()?;
        let check=|| { if read(&guard)?.revision()!=expected_revision {Err(StoreError::RevisionConflict)}else{Ok(())} };
        check()?;
        guard.atomic_replace(&settings.canonical(),check)?;
        self.snapshot_value(settings)
    }
    pub fn activate(&self,runtime_directory:impl AsRef<Path>,existing_client:bool)->Result<Settings> {
        let mut state=self.activation.lock().map_err(|_|StoreError::IoError)?;
        if state.activated { return Ok(state.active); }
        let path=SafeStorePath::new(runtime_directory.as_ref().join(ACTIVE_FILENAME))?;
        let active=if existing_client {
            self.existing_active(&path)?
        } else {
            let clean=self.desired()?;
            let guard=path.begin(true)?.ok_or(StoreError::IoError)?;
            let _lock=guard.writer_lock()?;
            guard.atomic_replace(&clean.canonical(),||read(&guard).map(|_|()))?;
            clean
        };
        state.active=active;state.activated=true;Ok(active)
    }
}
