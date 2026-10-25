//! Explicit recovery preparation only: authenticate the exact marker's backup,
//! stage it privately, apply the current config and keep input pinned. Does not
//! write credentials, publish a profile, clear its marker, or launch anything.
use crate::{
    mdata_profile_stage::{self, PreparedRecovery, StageError},
    mdata_recovery_archive::{RecoveryArchive, RecoveryArchiveError},
    mdata_recovery_state::RecoveryMarker,
};
use std::{fmt, path::Path};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PrepareError {
    InvalidRequest,
    Source(RecoveryArchiveError),
    Stage(StageError),
}
impl PrepareError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidRequest => "invalid_request",
            Self::Source(error) => error.code(),
            Self::Stage(error) => error.code(),
        }
    }
}
impl fmt::Display for PrepareError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for PrepareError {}
impl From<RecoveryArchiveError> for PrepareError {
    fn from(error: RecoveryArchiveError) -> Self {
        Self::Source(error)
    }
}
impl From<StageError> for PrepareError {
    fn from(error: StageError) -> Self {
        Self::Stage(error)
    }
}
pub type Result<T> = std::result::Result<T, PrepareError>;

/// Deliberately no Debug/Serialize/Clone. Ownership of the private output and
/// encrypted input remains here until a future transaction or explicit discard.
pub struct PreparedBackup {
    profile: PreparedRecovery,
    source: RecoveryArchive,
}
impl PreparedBackup {
    pub fn credentials(&self) -> Result<crate::mdata_credential_transaction::RecoveryKeys> {
        self.source.revalidate()?;
        let keys = self.profile.credentials()?;
        self.source.revalidate()?;
        Ok(keys)
    }
    pub fn file_count(&self) -> usize {
        self.profile.file_count()
    }
    pub fn revalidate(&self) -> Result<()> {
        self.source.revalidate()?;
        self.profile.revalidate()?;
        self.source.revalidate()?;
        Ok(())
    }
    pub fn discard(&mut self) -> Result<()> {
        self.profile.discard().map_err(Into::into)
    }
    #[cfg(windows)]
    pub(crate) fn into_root_swap(
        self,
    ) -> Result<(crate::mdata_profile_stage::RootSwap, RecoveryArchive)> {
        self.source.revalidate()?;
        let swap = self.profile.into_root_swap()?;
        self.source.revalidate()?;
        Ok((swap, self.source))
    }
}

/// Run on a worker. The eventual recovery coordinator must additionally own
/// the transfer lock and closed-client checks before it applies this result.
pub fn prepare(root: &Path, expected: &RecoveryMarker, password: &str) -> Result<PreparedBackup> {
    let mut source = RecoveryArchive::open(root, expected)?;
    let parent = root.parent().ok_or(PrepareError::InvalidRequest)?;
    let mut staged = match mdata_profile_stage::decode(&mut source, password, parent) {
        Ok(stage) => stage,
        Err(error) => {
            // Never hide an unsuccessful cleanup behind an input error.
            if error == StageError::CleanupFailed {
                return Err(error.into());
            }
            source.revalidate()?;
            return Err(error.into());
        }
    };
    if let Err(error) = source.verify_complete() {
        staged.discard()?;
        return Err(error.into());
    }
    let mut profile = staged.prepare_recovery()?;
    if let Err(error) = source.revalidate() {
        profile.discard()?;
        return Err(error.into());
    }
    Ok(PreparedBackup { profile, source })
}

#[cfg(all(test, windows))]
#[path = "mdata_recovery_prepare_tests.rs"]
mod tests;
