//! Small, non-secret startup receipts. Explicit program helper directory only;
//! no profile discovery, environment mutation, process launch or UI dialogs.
use crate::{
    settings_io::{GuardedStore, SafeStorePath, StoreError},
    support_diagnostics::{ROLES, STAGES, STARTUP_CODES, STATES},
};
use serde_json::{Value, json};
use std::{fmt, path::Path};

pub const FILENAME: &str = "startup-status.json";
pub const DIRECTORY: &str = ".mxmod-helper-temp";
pub const ENVIRONMENT_KEY: &str = "MAX_MOD_STARTUP_OPERATION";
pub const MAX_BYTES: usize = 1024;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error {
    InvalidReceipt,
    UnsafePath,
    StoreBusy,
    IoError,
}
impl Error {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidReceipt => "invalid_receipt",
            Self::UnsafePath => "unsafe_path",
            Self::StoreBusy => "store_busy",
            Self::IoError => "io_error",
        }
    }
}
impl fmt::Display for Error {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        out.write_str(self.code())
    }
}
impl std::error::Error for Error {}
type Result<T> = std::result::Result<T, Error>;
fn storage(error: StoreError) -> Error {
    match error {
        StoreError::UnsafePath => Error::UnsafePath,
        StoreError::StoreBusy => Error::StoreBusy,
        StoreError::InvalidStore => Error::InvalidReceipt,
        _ => Error::IoError,
    }
}
pub fn valid_operation_id(value: &str) -> bool {
    value.len() == 32
        && value
            .bytes()
            .all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}
pub fn new_operation_id() -> Result<String> {
    let mut data = [0u8; 16];
    getrandom::getrandom(&mut data).map_err(|_| Error::IoError)?;
    data[6] = data[6] & 0x0f | 0x40;
    data[8] = data[8] & 0x3f | 0x80;
    Ok(data.iter().map(|v| format!("{v:02x}")).collect())
}
fn member(value: &Value, allowed: &[&str]) -> bool {
    value.as_str().is_some_and(|v| allowed.contains(&v))
}
fn validate(value: Value) -> Result<Value> {
    const FIELDS: [&str; 6] = ["schema", "operationId", "role", "stage", "state", "code"];
    if !value
        .as_object()
        .is_some_and(|map| map.len() == FIELDS.len() && FIELDS.iter().all(|v| map.contains_key(*v)))
        || value["schema"].as_u64() != Some(1)
        || !value["operationId"]
            .as_str()
            .is_some_and(valid_operation_id)
        || !member(&value["role"], ROLES)
        || !member(&value["stage"], STAGES)
        || !member(&value["state"], STATES)
        || !(if value["state"] == "error" {
            member(&value["code"], STARTUP_CODES)
        } else {
            value["code"] == "none"
        })
    {
        return Err(Error::InvalidReceipt);
    }
    Ok(value)
}
pub fn parse(data: &[u8]) -> Result<Value> {
    if data.len() > MAX_BYTES {
        return Err(Error::InvalidReceipt);
    }
    validate(
        crate::session_archive::parse_json(data, "invalid_payload")
            .map_err(|_| Error::InvalidReceipt)?,
    )
}
fn read_file(guard: &GuardedStore) -> Result<Option<(Vec<u8>, Value)>> {
    guard
        .read_bounded(MAX_BYTES)
        .map_err(storage)?
        .map(|data| {
            let value = parse(&data)?;
            Ok((data, value))
        })
        .transpose()
}

/// Holds an explicit location and public operation ID, not an active process.
/// Writers serialize using the same normalized per-file mutex as old Python.
pub struct StartupStatus {
    path: SafeStorePath,
    operation: String,
    role: String,
    stage: String,
}
impl StartupStatus {
    pub fn new(helper_directory: &Path, role: &str, operation: Option<&str>) -> Result<Self> {
        if !ROLES.contains(&role) {
            return Err(Error::InvalidReceipt);
        }
        let path = SafeStorePath::new(helper_directory.join(FILENAME)).map_err(storage)?;
        if path
            .path()
            .parent()
            .and_then(Path::file_name)
            .and_then(|s| s.to_str())
            != Some(DIRECTORY)
        {
            return Err(Error::UnsafePath);
        }
        let operation = match operation {
            Some(value) if valid_operation_id(value) => value.to_owned(),
            Some(_) => return Err(Error::InvalidReceipt),
            None => new_operation_id()?,
        };
        Ok(Self {
            path,
            operation,
            role: role.to_owned(),
            stage: "bootstrap".to_owned(),
        })
    }
    pub fn path(&self) -> &Path {
        self.path.path()
    }
    pub fn operation_id(&self) -> &str {
        &self.operation
    }
    pub fn read(&self, expected_operation: Option<&str>) -> Result<Option<Value>> {
        let expected = expected_operation.unwrap_or(&self.operation);
        if !valid_operation_id(expected) {
            return Err(Error::InvalidReceipt);
        }
        let Some(guard) = self.path.begin(false).map_err(storage)? else {
            return Ok(None);
        };
        Ok(read_file(&guard)?
            .map(|(_, value)| value)
            .filter(|value| value["operationId"] == expected))
    }
    /// Claim is an explicit new root attempt; an observer cannot claim. A stale
    /// operation cannot overwrite another launch, and the first error is sticky.
    pub fn write(&mut self, stage: &str, state: &str, code: &str, claim: bool) -> Result<bool> {
        if claim && (self.role == "observe" || state != "starting") {
            return Err(Error::InvalidReceipt);
        }
        let value = validate(
            json!({"schema":1,"operationId":self.operation,"role":self.role,
            "stage":stage,"state":state,"code":code}),
        )?;
        let data = serde_json::to_vec(&value).map_err(|_| Error::InvalidReceipt)?;
        if data.len() > MAX_BYTES {
            return Err(Error::InvalidReceipt);
        }
        let Some(guard) = self.path.begin(claim).map_err(storage)? else {
            return Ok(false);
        };
        let _lock = guard.writer_lock().map_err(storage)?;
        let current = read_file(&guard)?;
        if !claim {
            let Some((_, current_value)) = &current else {
                return Ok(false);
            };
            if current_value["operationId"] != self.operation {
                return Ok(false);
            }
            if current_value["state"] == "error" {
                return Ok(current_value == &value);
            }
        }
        let expected = current.map(|(bytes, _)| bytes);
        guard
            .atomic_replace(&data, || {
                let latest = guard.read_bounded(MAX_BYTES)?;
                if latest.as_deref().map(parse).transpose().is_err() || latest != expected {
                    return Err(StoreError::RevisionConflict);
                }
                Ok(())
            })
            .map_err(storage)?;
        self.stage = stage.to_owned();
        Ok(true)
    }
    pub fn begin(&mut self, stage: &str, claim: bool) -> Result<bool> {
        self.write(stage, "starting", "none", claim)
    }
    pub fn ready(&mut self) -> Result<bool> {
        self.write("complete", "ready", "none", false)
    }
    pub fn fail(&mut self, code: &str, stage: Option<&str>) -> Result<bool> {
        let stage = stage.unwrap_or(&self.stage).to_owned();
        self.write(&stage, "error", code, false)
    }
    /// Optional diagnostic persistence must never replace the primary startup
    /// outcome. The caller still owns that outcome and handles it separately.
    pub fn try_write(&mut self, stage: &str, state: &str, code: &str, claim: bool) -> bool {
        self.write(stage, state, code, claim).unwrap_or(false)
    }
    pub fn try_read(&self, expected_operation: Option<&str>) -> Option<Value> {
        self.read(expected_operation).unwrap_or(None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{collections::BTreeSet, fs, path::PathBuf};
    const A: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const B: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    fn receipt() -> Value {
        json!({"schema":1,"operationId":A,"role":"launcher","stage":"bootstrap","state":"starting","code":"none"})
    }
    #[test]
    fn finite_exact_schema_and_operation_ids() {
        let good = receipt();
        assert_eq!(parse(&serde_json::to_vec(&good).unwrap()).unwrap(), good);
        let mut bad = Vec::new();
        for (key, value) in [
            ("schema", json!(true)),
            ("schema", json!(1.0)),
            ("schema", json!(2)),
            ("operationId", json!("SYNTHETIC_SECRET")),
            ("role", json!("unknown")),
            ("stage", json!("wrong")),
            ("state", json!("error")),
            ("code", json!("startup_failed")),
            ("extra", json!("SYNTHETIC_SECRET")),
        ] {
            let mut value2 = good.clone();
            value2[key] = value;
            bad.push(serde_json::to_vec(&value2).unwrap());
        }
        bad.extend([
            b"[]".to_vec(),
            b"NaN".to_vec(),
            b"\xff".to_vec(),
            vec![b' '; 1025],
            b"{\"schema\":1,\"schema\":1}".to_vec(),
        ]);
        for data in bad {
            assert_eq!(parse(&data), Err(Error::InvalidReceipt));
        }
        let ids: BTreeSet<_> = (0..30).map(|_| new_operation_id().unwrap()).collect();
        assert_eq!(ids.len(), 30);
        assert!(ids.iter().all(|v| valid_operation_id(v)));
        for value in [
            "",
            "Aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "a",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\0",
        ] {
            assert!(!valid_operation_id(value));
        }
        for code in STARTUP_CODES {
            let mut v = good.clone();
            v["state"] = json!("error");
            v["code"] = json!(code);
            assert!(validate(v).is_ok());
        }
    }
    #[cfg(windows)]
    struct Fixture {
        root: PathBuf,
    }
    #[cfg(windows)]
    impl Fixture {
        fn new() -> Self {
            let root = PathBuf::from("C:/ValMAXBuild/fixtures/startup-status")
                .join(new_operation_id().unwrap());
            fs::create_dir_all(&root).unwrap();
            Self { root }
        }
        fn helper(&self) -> PathBuf {
            self.root.join(DIRECTORY)
        }
        fn status(&self, role: &str, id: &str) -> StartupStatus {
            StartupStatus::new(&self.helper(), role, Some(id)).unwrap()
        }
    }
    #[cfg(windows)]
    impl Drop for Fixture {
        fn drop(&mut self) {
            let base = fs::canonicalize("C:/ValMAXBuild/fixtures/startup-status").unwrap();
            let own = fs::canonicalize(&self.root).unwrap();
            assert!(own.starts_with(&base) && own != base);
            fs::remove_dir_all(own).unwrap();
        }
    }
    #[cfg(windows)]
    #[test]
    fn claim_stale_child_and_first_error_are_preserved() {
        let f = Fixture::new();
        let mut root = f.status("launcher", A);
        let mut child = f.status("observe", A);
        assert!(!f.helper().exists());
        assert!(root.read(None).unwrap().is_none());
        assert!(!child.begin("observer", false).unwrap());
        assert!(!f.helper().exists());
        assert_eq!(child.begin("observer", true), Err(Error::InvalidReceipt));
        assert!(root.begin("verify", true).unwrap());
        assert!(child.begin("protection", false).unwrap());
        assert!(child.fail("notification_ack_failed", None).unwrap());
        let before = fs::read(root.path()).unwrap();
        assert!(!root.fail("native_spawn_failed", None).unwrap());
        assert!(!root.ready().unwrap());
        assert!(child.fail("notification_ack_failed", None).unwrap());
        assert_eq!(fs::read(root.path()).unwrap(), before);
        let mut next = f.status("mdata", B);
        assert!(next.begin("bootstrap", true).unwrap());
        let new = fs::read(next.path()).unwrap();
        assert!(!child.fail("startup_failed", None).unwrap());
        assert_eq!(fs::read(next.path()).unwrap(), new);
        assert!(root.read(None).unwrap().is_none());
        assert!(next.ready().unwrap());
        assert_eq!(next.read(None).unwrap().unwrap()["state"], "ready");
    }
    #[cfg(windows)]
    #[test]
    fn unsafe_path_malformed_and_hardlinked_receipts_are_never_replaced() {
        let f = Fixture::new();
        assert!(matches!(
            StartupStatus::new(&f.root, "launcher", Some(A)),
            Err(Error::UnsafePath)
        ));
        let mut status = f.status("recover", A);
        fs::create_dir(f.helper()).unwrap();
        fs::write(status.path(), b"not-json").unwrap();
        assert_eq!(status.begin("bootstrap", true), Err(Error::InvalidReceipt));
        assert_eq!(fs::read(status.path()).unwrap(), b"not-json");
        fs::remove_file(status.path()).unwrap();
        let original = f.root.join("original.json");
        let bytes = serde_json::to_vec(&receipt()).unwrap();
        fs::write(&original, &bytes).unwrap();
        fs::hard_link(&original, status.path()).unwrap();
        assert_eq!(status.begin("verify", true), Err(Error::UnsafePath));
        assert_eq!(fs::read(&original).unwrap(), bytes);
    }
    #[cfg(windows)]
    #[test]
    fn concurrent_roots_keep_exact_complete_receipt() {
        let f = Fixture::new();
        let helper = f.helper();
        fs::create_dir(&helper).unwrap();
        let threads: Vec<_> = (0..8)
            .map(|_| {
                let helper = helper.clone();
                std::thread::spawn(move || {
                    let mut own = StartupStatus::new(&helper, "launcher", None).unwrap();
                    let id = own.operation_id().to_owned();
                    assert!(own.begin("verify", true).unwrap());
                    id
                })
            })
            .collect();
        let ids: BTreeSet<_> = threads.into_iter().map(|t| t.join().unwrap()).collect();
        let value = parse(&fs::read(helper.join(FILENAME)).unwrap()).unwrap();
        assert!(ids.contains(value["operationId"].as_str().unwrap()));
        assert_eq!(fs::read_dir(helper).unwrap().count(), 1);
    }
    #[cfg(windows)]
    #[test]
    fn busy_receipt_is_an_optional_failure_without_replacement() {
        use std::os::windows::fs::OpenOptionsExt;
        let f = Fixture::new();
        let mut status = f.status("launcher", A);
        assert!(status.begin("verify", true).unwrap());
        let bytes = fs::read(status.path()).unwrap();
        let held = fs::OpenOptions::new()
            .write(true)
            .share_mode(0)
            .open(status.path())
            .unwrap();
        assert_eq!(status.begin("profile", false), Err(Error::StoreBusy));
        assert!(!status.try_write("profile", "starting", "none", false));
        assert!(status.try_read(None).is_none());
        drop(held);
        assert_eq!(fs::read(status.path()).unwrap(), bytes);
        assert!(status.ready().unwrap());
    }
}
