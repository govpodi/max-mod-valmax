//! Explicit native full-profile recovery, owned by the standalone transfer
//! helper. No Runtime route, profile discovery, automatic launch or HTTP server.
//! The transfer mutex spans closed-client admission through commit/rollback.
//! Cancellation is honored before mutation; once keys change, resolve the whole
//! transaction before returning. Scrypt and a blocking filesystem call are not
//! interruptible. Whole-tree checks remain point-in-time, not a hostile-writer
//! freeze. Retained old/staged trees are not recursively removed by this code.
use crate::{
    mdata_credential_transaction::CredentialTransaction,
    mdata_credentials::RecoveryCredentials,
    mdata_import::ImportHost,
    mdata_recovery_prepare,
    mdata_recovery_state::{self, RecoveryMarker},
    transfer_lock::{LockError, TransferLease},
};
use serde::Serialize;
use std::{fmt, path::Path};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RecoveryError(&'static str);
impl RecoveryError {
    pub fn code(self) -> &'static str {
        self.0
    }
}
impl fmt::Display for RecoveryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}
impl std::error::Error for RecoveryError {}
pub type Result<T> = std::result::Result<T, RecoveryError>;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct RecoveryResult {
    pub schema: u8,
    pub result: &'static str,
    pub files: usize,
}
fn cancel(host: &mut dyn ImportHost) -> Result<()> {
    if host.cancelled() {
        Err(RecoveryError("cancelled"))
    } else {
        Ok(())
    }
}
fn closed(host: &mut dyn ImportHost) -> Result<()> {
    host.check_closed().map_err(|e| RecoveryError(e.code()))?;
    cancel(host)
}
fn with_lease<L>(
    lease: std::result::Result<L, LockError>,
    work: impl FnOnce() -> Result<RecoveryResult>,
) -> Result<RecoveryResult> {
    let _lease = lease.map_err(|e| RecoveryError(e.code()))?;
    work()
}

/// Restore only the backup named by an existing, exact marker, after explicit
/// user confirmation. The host supplies native process scans on this worker;
/// a UI boolean is not a closed-client proof. The credential adapter is passed
/// explicitly so synthetic tests never call the real Windows store.
pub fn execute(
    root: &Path,
    marker: &RecoveryMarker,
    password: &str,
    credentials: &dyn RecoveryCredentials,
    host: &mut dyn ImportHost,
) -> Result<RecoveryResult> {
    with_lease(TransferLease::acquire(), || {
        execute_held(root, marker, password, credentials, host, |root, marker| {
            mdata_recovery_state::clear(root, marker).map_err(|e| RecoveryError(e.code()))
        })
    })
}

fn execute_held(
    root: &Path,
    marker: &RecoveryMarker,
    password: &str,
    credentials: &dyn RecoveryCredentials,
    host: &mut dyn ImportHost,
    clear_marker: impl FnOnce(&Path, &RecoveryMarker) -> Result<()>,
) -> Result<RecoveryResult> {
    #[cfg(not(windows))]
    {
        let _ = (root, marker, password, credentials, host, clear_marker);
        Err(RecoveryError("io_error"))
    }
    #[cfg(windows)]
    {
        cancel(host)?;
        host.wait_closed().map_err(|e| RecoveryError(e.code()))?;
        closed(host)?;
        let prepared = mdata_recovery_prepare::prepare(root, marker, password)
            .map_err(|e| RecoveryError(e.code()))?;
        closed(host)?;
        let files = prepared.file_count();
        let keys = prepared
            .credentials()
            .map_err(|e| RecoveryError(e.code()))?;
        let mut keys = CredentialTransaction::prepare(credentials, keys)
            .map_err(|e| RecoveryError(e.code()))?;
        // Handoff disarms automatic deletion before any credential changes.
        // A subsequent error retains the private tree and exact marker.
        let (mut roots, source) = prepared
            .into_root_swap()
            .map_err(|e| RecoveryError(e.code()))?;
        closed(host)?;
        source.revalidate().map_err(|e| RecoveryError(e.code()))?;
        roots.revalidate().map_err(|e| RecoveryError(e.code()))?;
        keys.apply().map_err(|e| RecoveryError(e.code()))?;
        // No cancellation point from here until a complete commit or rollback.
        let publish = (|| {
            source.revalidate().map_err(|e| RecoveryError(e.code()))?;
            roots.swap().map_err(|e| RecoveryError(e.code()))?;
            source.revalidate().map_err(|e| RecoveryError(e.code()))?;
            roots.revalidate().map_err(|e| RecoveryError(e.code()))?;
            roots.commit().map_err(|e| RecoveryError(e.code()))
        })();
        if let Err(error) = publish {
            // Always attempt both, even if key rollback failed. Both profile
            // trees and the marker survive an incomplete rollback.
            let key_failed = keys.rollback().is_err();
            let root_failed = roots.rollback().is_err();
            return Err(if key_failed || root_failed {
                RecoveryError("rollback_failed")
            } else {
                error
            });
        }
        keys.commit()
            .map_err(|_| RecoveryError("recovery_required"))?;
        // Commit is final. Failure to clear the exact marker is reported as
        // recovery_required and must not roll back the newly published tree.
        clear_marker(root, marker).map_err(|_| RecoveryError("recovery_required"))?;
        Ok(RecoveryResult {
            schema: 1,
            result: "restored",
            files,
        })
    }
}

#[cfg(all(test, windows))]
#[path = "mdata_recovery_apply_tests.rs"]
mod tests;
