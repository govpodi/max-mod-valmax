//! Selected-chat, read-only local retention source for the API2 exporter.
//! Only durable marker and decryptable content intersections enter this stream.
use crate::{
    export_render::{self, LocalDeletedRow},
    export_storage::Cancellation,
    retention_store::{DeletedText, Error as RetentionError, RetentionStore},
};
use std::{collections::VecDeque, path::Path};

const PAGE_SIZE: usize = 100;

fn source_error(error: RetentionError) -> export_render::Error {
    match error {
        RetentionError::StaleCursor => export_render::Error::SourceChanged,
        RetentionError::UnsafePath => export_render::Error::UnsafePath,
        RetentionError::NoSpace => export_render::Error::NoSpace,
        _ => export_render::Error::IoError,
    }
}

pub(crate) struct LocalDeletedStream {
    store: RetentionStore,
    owner: i64,
    chat: i64,
    cancel: Cancellation,
    cursor: Option<String>,
    revision: Option<String>,
    rows: VecDeque<DeletedText>,
    exhausted: bool,
    final_verified: bool,
    failed: bool,
    pages: u64,
}

impl LocalDeletedStream {
    /// An empty selected route leaves the existing API2 archive unchanged.
    /// Prefetch also catches a broken local store before starting publication.
    pub(crate) fn selected(
        profile: &Path,
        owner: i64,
        chat: i64,
        cancel: Cancellation,
    ) -> export_render::Result<Option<Self>> {
        let mut stream = Self {
            store: RetentionStore::new(profile).map_err(source_error)?,
            owner,
            chat,
            cancel,
            cursor: None,
            revision: None,
            rows: VecDeque::new(),
            exhausted: false,
            final_verified: false,
            failed: false,
            pages: 0,
        };
        stream.refill()?;
        Ok((!stream.rows.is_empty()).then_some(stream))
    }

    fn refill(&mut self) -> export_render::Result<()> {
        while self.rows.is_empty() && !self.final_verified {
            self.cancel.check()?;
            if self.exhausted {
                if self.store.visibility_revision().map_err(source_error)?
                    != *self.revision.as_ref().ok_or(export_render::Error::IoError)?
                {
                    return Err(export_render::Error::SourceChanged);
                }
                self.final_verified = true;
                break;
            }
            self.pages += 1;
            if self.pages > export_render::MAX_LOCAL_DELETED_ROWS + 1 {
                return Err(export_render::Error::LimitExceeded);
            }
            let prior = self.cursor.clone();
            let page = self
                .store
                .list_deleted_text(self.owner, self.chat, prior.as_deref(), PAGE_SIZE)
                .map_err(source_error)?;
            if self.revision.as_ref().is_some_and(|value| value != &page.revision) {
                return Err(export_render::Error::SourceChanged);
            }
            self.revision.get_or_insert_with(|| page.revision.clone());
            if page.next_cursor.as_ref().is_some_and(|next| prior.as_ref() == Some(next)) {
                return Err(export_render::Error::IoError);
            }
            self.rows = page.items.into();
            self.cursor = page.next_cursor;
            self.exhausted = self.cursor.is_none();
            self.cancel.check()?;
        }
        Ok(())
    }
}

impl Iterator for LocalDeletedStream {
    type Item = export_render::Result<LocalDeletedRow>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.failed {
            return None;
        }
        if let Err(error) = self.refill() {
            self.failed = true;
            return Some(Err(error));
        }
        self.rows.pop_front().map(|row| {
            Ok(LocalDeletedRow {
                observed_deleted_at_ms: row.deleted_at_ms,
                text: row.text,
            })
        })
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use std::{fs, path::PathBuf};

    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let base = Path::new("E:/ValMAXBuild/fixtures/synthetic/local-retention-export-p11");
            fs::create_dir_all(base).unwrap();
            let mut random = [0_u8; 16];
            getrandom::getrandom(&mut random).unwrap();
            let child = random.iter().map(|part| format!("{part:02x}")).collect::<String>();
            let root = base.join(child);
            fs::create_dir(&root).unwrap();
            Self(root)
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            assert_eq!(self.0.parent(), Some(Path::new(
                "E:/ValMAXBuild/fixtures/synthetic/local-retention-export-p11")));
            assert_eq!(self.0.file_name().unwrap().to_str().unwrap().len(), 32);
            assert!(!fs::symlink_metadata(&self.0).unwrap().file_type().is_symlink());
            fs::remove_dir_all(&self.0).unwrap();
        }
    }

    #[test]
    fn stream_returns_only_selected_durable_text_and_survives_store_reopen() {
        let fixture = Fixture::new();
        let store = RetentionStore::new(&fixture.0).unwrap();
        assert!(LocalDeletedStream::selected(&fixture.0, 11, 22, Cancellation::default())
            .unwrap().is_none());
        store.capture_text(11, 22, 33, 1_000, "Synthetic retained text").unwrap();
        store.mark_received_delete(11, 22, &[33], 2_000).unwrap();
        store.capture_text(55, 22, 33, 1_100, "Other synthetic account").unwrap();
        store.mark_received_delete(55, 22, &[33], 2_100).unwrap();
        drop(store);
        let rows = LocalDeletedStream::selected(&fixture.0, 11, 22, Cancellation::default())
            .unwrap().unwrap().collect::<export_render::Result<Vec<_>>>().unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].text.as_str(), "Synthetic retained text");
        assert_eq!(rows[0].observed_deleted_at_ms, 2_000);
        assert!(LocalDeletedStream::selected(&fixture.0, 11, 66, Cancellation::default())
            .unwrap().is_none());
    }

    #[test]
    fn mutation_or_cancellation_aborts_stream_instead_of_publishing_prefix() {
        let fixture = Fixture::new();
        let store = RetentionStore::new(&fixture.0).unwrap();
        store.capture_text(11, 22, 33, 1_000, "Synthetic first").unwrap();
        store.mark_received_delete(11, 22, &[33], 2_000).unwrap();
        let mut stream = LocalDeletedStream::selected(&fixture.0, 11, 22,
            Cancellation::default()).unwrap().unwrap();
        assert!(stream.next().unwrap().is_ok());
        store.capture_text(11, 22, 34, 1_100, "Synthetic later").unwrap();
        store.mark_received_delete(11, 22, &[34], 2_100).unwrap();
        assert!(matches!(stream.next(), Some(Err(export_render::Error::SourceChanged))));
        assert!(stream.next().is_none());
        let cancelled = Cancellation::default();
        cancelled.cancel();
        assert!(matches!(LocalDeletedStream::selected(&fixture.0, 11, 22, cancelled),
            Err(export_render::Error::Cancelled)));
    }
}
