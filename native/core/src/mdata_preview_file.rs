//! Read-only inspection of an explicitly selected compact MData archive.
//! Filesystem validation belongs here; authentication/projection remain in the
//! pure preview module. No profile discovery, credentials access or import.

use crate::{
    mdata_preview::{self, Preview, PreviewError},
    selected_path::selected_file_url,
    settings_io::{SafeStorePath, StoreError},
};
use std::fmt;
use zeroize::Zeroizing;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PreviewFileError {
    UnsafePath,
    IoError,
    Busy,
    TooLarge,
    InvalidArchive,
    Preview(PreviewError),
}
impl PreviewFileError {
    pub fn code(self) -> &'static str {
        match self {
            Self::UnsafePath => "unsafe_path",
            Self::IoError => "io_error",
            Self::Busy => "busy",
            Self::TooLarge => "too_large",
            Self::InvalidArchive => "invalid_archive",
            Self::Preview(error) => error.code(),
        }
    }
}
impl fmt::Display for PreviewFileError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for PreviewFileError {}

fn store_error(error: StoreError) -> PreviewFileError {
    match error {
        StoreError::UnsafePath => PreviewFileError::UnsafePath,
        StoreError::StoreBusy => PreviewFileError::Busy,
        // read_bounded uses InvalidStore solely for its size bound. This mapper
        // is not intended for JSON settings validation or write operations.
        StoreError::InvalidStore => PreviewFileError::TooLarge,
        _ => PreviewFileError::IoError,
    }
}

/// Call on a worker thread: authentication includes bounded scrypt work.
/// Parents remain pinned while the archive is read by an independently held
/// regular-file handle without write/delete sharing. Only a fully authenticated
/// terminal+EOF can produce the public preview. No partial rows are returned.
/// The encrypted buffer is zeroized on drop as a best-effort measure; this is
/// not a promise that all allocator/library copies are erased.
pub fn inspect(file_url: &str, password: &str) -> Result<Preview, PreviewFileError> {
    let path = selected_file_url(file_url).map_err(|_| PreviewFileError::UnsafePath)?;
    if path
        .components()
        .any(|part| part.as_os_str().eq_ignore_ascii_case("ONEME"))
    {
        return Err(PreviewFileError::UnsafePath);
    }
    if !path
        .extension()
        .is_some_and(|extension| extension.eq_ignore_ascii_case("mdata"))
    {
        return Err(PreviewFileError::InvalidArchive);
    }
    let safe = SafeStorePath::new(&path).map_err(store_error)?;
    let held = safe
        .begin(false)
        .map_err(store_error)?
        .ok_or(PreviewFileError::IoError)?;
    let bytes = Zeroizing::new(
        held.read_bounded(mdata_preview::MAX_ARCHIVE_BYTES + 1)
            .map_err(store_error)?
            .ok_or(PreviewFileError::IoError)?,
    );
    mdata_preview::inspect(&bytes, password).map_err(PreviewFileError::Preview)
}
