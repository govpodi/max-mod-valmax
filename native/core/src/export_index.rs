//! Worker-owned, ephemeral SQLite spool for an explicitly requested export.
//!
//! The real disk database lives in an owned random sibling directory under the
//! user's selected export parent. Pinned ancestors and a no-delete file lease
//! protect its identity. MEMORY journalling/temp intermediates keep content out
//! of the process/global TEMP directory. This disposable database has no crash
//! recovery promise; the coordinator bounds transactions and owns its schema.
use crate::{
    export_render::{Error, Result},
    export_storage::{Cancellation, OwnedExportSpool},
};
use rusqlite::{Connection, InterruptHandle};
use std::path::Path;

pub(crate) struct ExportIndex {
    connection: Option<Connection>,
    spool: OwnedExportSpool,
}

impl ExportIndex {
    pub(crate) fn new(destination: &Path, cancel: Cancellation) -> Result<Self> {
        #[cfg(not(windows))]
        {
            let _ = (destination, cancel);
            Err(Error::UnsupportedPlatform)
        }
        #[cfg(windows)]
        {
            use rusqlite::{OpenFlags, config::DbConfig, limits::Limit};
            cancel.check()?;
            let mut spool = OwnedExportSpool::new(destination, &cancel)?;
            // Existing owned file only: no CREATE, URI, shared cache, caller
            // VFS, TEMP fallback or arbitrary database filename.
            let connection = match Connection::open_with_flags(
                spool.path(),
                OpenFlags::SQLITE_OPEN_READ_WRITE
                    | OpenFlags::SQLITE_OPEN_PRIVATE_CACHE
                    | OpenFlags::SQLITE_OPEN_NO_MUTEX,
            ) {
                Ok(connection) => connection,
                Err(error) => {
                    return Err(if spool.cleanup().is_err() {
                        Error::CleanupFailed
                    } else {
                        sql_error(error)
                    });
                }
            };
            let mut result = Self {
                connection: Some(connection),
                spool,
            };
            let setup = (|| {
                let connection = result.connection();
                verify_opened_file(connection, &result.spool)?;
                // TEMP_STORE=1 permits a per-connection MEMORY override. Do not
                // silently allow a future build that forces disk temp files.
                let temp_override: bool = connection
                    .query_row(
                        "SELECT sqlite_compileoption_used('TEMP_STORE=1')",
                        [],
                        |r| r.get(0),
                    )
                    .map_err(sql_error)?;
                if !temp_override {
                    return Err(Error::IoError);
                }
                connection
                    .set_db_config(DbConfig::SQLITE_DBCONFIG_DEFENSIVE, true)
                    .map_err(sql_error)?;
                connection
                    .set_db_config(DbConfig::SQLITE_DBCONFIG_TRUSTED_SCHEMA, false)
                    .map_err(sql_error)?;
                for (limit, value) in [
                    (Limit::SQLITE_LIMIT_LENGTH, 4 * 1024 * 1024),
                    (Limit::SQLITE_LIMIT_SQL_LENGTH, 64 * 1024),
                    (Limit::SQLITE_LIMIT_COLUMN, 32),
                    (Limit::SQLITE_LIMIT_VARIABLE_NUMBER, 32),
                    (Limit::SQLITE_LIMIT_ATTACHED, 0),
                    (Limit::SQLITE_LIMIT_WORKER_THREADS, 0),
                ] {
                    connection.set_limit(limit, value).map_err(sql_error)?;
                }
                // Named main DB remains on disk. Journals and SQL intermediates
                // stay in memory: no plaintext under global TEMP. The coordinator
                // uses <=200-row/2-MiB transactions and an indexed streaming order;
                // trusted SQL must not request VACUUM or unbounded temp sorts.
                // cache_size is a page-cache target, not a total-allocation bound.
                connection
                    .execute_batch(
                        "PRAGMA temp_store=MEMORY; PRAGMA main.cache_size=-8192; \
                     PRAGMA temp.cache_size=-2048; PRAGMA mmap_size=0; \
                     PRAGMA busy_timeout=0; PRAGMA journal_mode=MEMORY;",
                    )
                    .map_err(sql_error)?;
                let observed = cancel.clone();
                connection
                    .progress_handler(1000, Some(move || observed.check().is_err()))
                    .map_err(sql_error)?;
                cancel.check()
            })();
            if let Err(error) = setup {
                return Err(if result.close_inner().is_err() {
                    Error::CleanupFailed
                } else {
                    error
                });
            }
            Ok(result)
        }
    }

    /// Trusted worker SQL only; do not pass externally supplied SQL or paths.
    pub(crate) fn connection(&self) -> &Connection {
        self.connection
            .as_ref()
            .expect("live owned export connection")
    }

    pub(crate) fn connection_mut(&mut self) -> &mut Connection {
        self.connection
            .as_mut()
            .expect("live owned export connection")
    }

    /// May be signalled from another thread; cancel the shared flag first so
    /// subsequent SQL is also cancelled. This does not preempt a blocked OS I/O.
    #[allow(dead_code)]
    pub(crate) fn interrupt_handle(&self) -> InterruptHandle {
        self.connection().get_interrupt_handle()
    }

    /// Call before publishing the HTML workspace. A busy SQLite close or any
    /// unknown/replaced/busy owned file prevents a falsely successful export.
    pub(crate) fn close(mut self) -> Result<()> {
        self.close_inner()
    }

    fn close_inner(&mut self) -> Result<()> {
        let mut failed = false;
        if let Some(connection) = self.connection.take() {
            if let Err((connection, _)) = connection.close() {
                drop(connection);
                failed = true;
            }
        }
        if self.spool.cleanup().is_err() {
            failed = true;
        }
        if failed {
            Err(Error::CleanupFailed)
        } else {
            Ok(())
        }
    }
}

impl Drop for ExportIndex {
    fn drop(&mut self) {
        let _ = self.close_inner();
    }
}

#[cfg(windows)]
fn verify_opened_file(connection: &Connection, spool: &OwnedExportSpool) -> Result<()> {
    use std::{ffi::c_void, fs::File, os::windows::io::BorrowedHandle};
    let mut handle: *mut c_void = std::ptr::null_mut();
    // Public SQLite API returns a borrowed live Win32 handle while this
    // worker-owned connection remains alive. Duplicate it, never take ownership
    // of SQLite's handle or depend on its private winFile layout.
    let code = unsafe {
        rusqlite::ffi::sqlite3_file_control(
            connection.handle(),
            c"main".as_ptr(),
            rusqlite::ffi::SQLITE_FCNTL_WIN32_GET_HANDLE,
            (&mut handle as *mut *mut c_void).cast(),
        )
    };
    if code != rusqlite::ffi::SQLITE_OK || handle.is_null() || handle as isize == -1 {
        return Err(Error::UnsafePath);
    }
    let copy = unsafe { BorrowedHandle::borrow_raw(handle) }
        .try_clone_to_owned()
        .map_err(|_| Error::IoError)?;
    spool.verify_database(&File::from(copy))
}

pub(crate) fn sql_error(error: rusqlite::Error) -> Error {
    use rusqlite::ErrorCode;
    match error.sqlite_error_code() {
        Some(ErrorCode::DiskFull) => Error::NoSpace,
        Some(ErrorCode::OperationInterrupted) => Error::Cancelled,
        _ => Error::IoError,
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use std::{ffi::c_void, fs, path::PathBuf};

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetFinalPathNameByHandleW(
            handle: *mut c_void,
            path: *mut u16,
            length: u32,
            flags: u32,
        ) -> u32;
        fn GetFileSizeEx(handle: *mut c_void, size: *mut i64) -> i32;
        fn GetFileAttributesW(path: *const u16) -> u32;
        fn CreateFileW(
            path: *const u16,
            access: u32,
            share: u32,
            security: *const c_void,
            disposition: u32,
            flags: u32,
            template: *mut c_void,
        ) -> *mut c_void;
        fn CloseHandle(handle: *mut c_void) -> i32;
        fn GetLastError() -> u32;
    }

    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let mut bytes = [0u8; 16];
            getrandom::getrandom(&mut bytes).unwrap();
            let name: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
            let path = PathBuf::from("C:/ValMAXBuild/fixtures/export-index").join(name);
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }
        fn destination(&self) -> PathBuf {
            self.0.join("selected-output")
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            // Empty only; a failure never recursively removes unknown data.
            if !std::thread::panicking() {
                fs::remove_dir(&self.0).unwrap();
            }
        }
    }

    fn physical_file(connection: &Connection) -> (PathBuf, u64) {
        // Public SQLite file-control API; only observe its borrowed OS handle.
        // Do not close it or rely on private winFile layout.
        let mut handle: *mut c_void = std::ptr::null_mut();
        let code = unsafe {
            rusqlite::ffi::sqlite3_file_control(
                connection.handle(),
                c"main".as_ptr(),
                rusqlite::ffi::SQLITE_FCNTL_WIN32_GET_HANDLE,
                (&mut handle as *mut *mut c_void).cast(),
            )
        };
        assert_eq!(code, rusqlite::ffi::SQLITE_OK);
        assert!(!handle.is_null() && handle as isize != -1);
        let mut buffer = vec![0u16; 32768];
        let length = unsafe {
            GetFinalPathNameByHandleW(handle, buffer.as_mut_ptr(), buffer.len() as u32, 0)
        };
        assert!(length > 0 && (length as usize) < buffer.len());
        let mut size = 0i64;
        assert_ne!(unsafe { GetFileSizeEx(handle, &mut size) }, 0);
        assert!(size > 0);
        (
            PathBuf::from(String::from_utf16(&buffer[..length as usize]).unwrap()),
            size as u64,
        )
    }

    fn wide(path: &std::path::Path) -> Vec<u16> {
        use std::os::windows::ffi::OsStrExt;
        path.as_os_str().encode_wide().chain(Some(0)).collect()
    }

    #[test]
    fn export_index_disk_database_stays_in_selected_parent_and_closes_owned_files() {
        let fixture = Fixture::new();
        let index = ExportIndex::new(&fixture.destination(), Cancellation::default()).unwrap();
        let expected = index.spool.path().to_owned();
        let directory = expected.parent().unwrap().to_owned();
        let db = index.connection();
        assert_eq!(
            db.query_row("PRAGMA temp_store", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            2
        );
        assert_eq!(
            db.query_row("PRAGMA main.cache_size", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            -8192
        );
        assert_eq!(
            db.query_row("PRAGMA trusted_schema", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            0
        );
        assert_eq!(
            db.query_row("PRAGMA journal_mode", [], |r| r.get::<_, String>(0))
                .unwrap(),
            "memory"
        );
        assert!(
            db.execute_batch("ATTACH DATABASE ':memory:' AS outside")
                .is_err()
        );
        // Lower only this test connection's page cache to force a real spill
        // with 256 KiB of synthetic data rather than allocate a huge fixture.
        db.execute_batch("PRAGMA main.cache_size=4; CREATE TABLE sample(value BLOB); INSERT INTO sample VALUES(zeroblob(262144));").unwrap();
        let (path, size) = physical_file(db);
        assert!(size >= 262144);
        assert_eq!(path, fs::canonicalize(&expected).unwrap());
        assert_eq!(
            fs::canonicalize(directory.parent().unwrap()).unwrap(),
            fs::canonicalize(&fixture.0).unwrap()
        );
        assert_eq!(path.file_name().unwrap(), "index.sqlite");
        assert!(
            directory
                .file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with(".valmax-index-")
        );
        assert_eq!(fs::read_dir(&directory).unwrap().count(), 1);
        // The rollback journal remains in memory while a write transaction is
        // active; the selected directory still contains only the disk database.
        db.execute_batch("BEGIN; UPDATE sample SET value=zeroblob(131072);")
            .unwrap();
        assert_eq!(fs::read_dir(&directory).unwrap().count(), 1);
        db.execute_batch("ROLLBACK").unwrap();
        let filename = wide(&path);
        assert_ne!(unsafe { GetFileAttributesW(filename.as_ptr()) }, u32::MAX);
        // No share-delete means a second delete-capable handle cannot replace
        // the opened file. This does not claim denial of all read/write opens.
        let deletion = unsafe {
            CreateFileW(
                filename.as_ptr(),
                0x10000,
                7,
                std::ptr::null(),
                3,
                0x80,
                std::ptr::null_mut(),
            )
        };
        if deletion as isize != -1 {
            unsafe {
                CloseHandle(deletion);
            }
        }
        assert_eq!(deletion as isize, -1);
        assert_eq!(unsafe { GetLastError() }, 32);
        index.close().unwrap();
        assert_eq!(unsafe { GetFileAttributesW(filename.as_ptr()) }, u32::MAX);
        assert!(matches!(unsafe { GetLastError() }, 2 | 3));
        assert!(!directory.exists());
        assert_eq!(fs::read_dir(&fixture.0).unwrap().count(), 0);
    }

    #[test]
    fn export_index_transaction_rollback_preserves_committed_rows() {
        let fixture = Fixture::new();
        let mut index = ExportIndex::new(&fixture.destination(), Cancellation::default()).unwrap();
        index
            .connection()
            .execute_batch(
                "CREATE TABLE sample(id TEXT PRIMARY KEY); INSERT INTO sample VALUES('one');",
            )
            .unwrap();
        {
            let tx = index.connection_mut().transaction().unwrap();
            tx.execute("INSERT INTO sample VALUES('two')", []).unwrap();
            assert!(tx.execute("INSERT INTO sample VALUES('one')", []).is_err());
            // Rollback on drop, just as a failed coordinator batch.
        }
        assert_eq!(
            index
                .connection()
                .query_row("SELECT count(*) FROM sample", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            1
        );
    }

    #[test]
    fn export_index_cancelled_before_open_and_progress_interrupt() {
        let fixture = Fixture::new();
        let cancel = Cancellation::default();
        cancel.cancel();
        assert!(matches!(
            ExportIndex::new(&fixture.destination(), cancel),
            Err(Error::Cancelled)
        ));
        assert_eq!(fs::read_dir(&fixture.0).unwrap().count(), 0);
        let cancel = Cancellation::default();
        let index = ExportIndex::new(&fixture.destination(), cancel.clone()).unwrap();
        cancel.cancel();
        let error = index.connection().query_row::<i64, _, _>(
            "WITH RECURSIVE n(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM n WHERE x<1000000000) SELECT sum(x) FROM n", [], |r| r.get(0),
        ).unwrap_err();
        assert_eq!(sql_error(error), Error::Cancelled);
    }

    #[test]
    fn export_index_cross_thread_interrupt_is_finite_and_handle_outlives_connection() {
        let fixture = Fixture::new();
        let index = ExportIndex::new(&fixture.destination(), Cancellation::default()).unwrap();
        let interrupt = index.interrupt_handle();
        let (sender, receiver) = std::sync::mpsc::sync_channel(1);
        let mut started = false;
        index
            .connection()
            .progress_handler(
                1000,
                Some(move || {
                    if !started {
                        started = true;
                        sender.send(()).unwrap();
                    }
                    false
                }),
            )
            .unwrap();
        let worker = std::thread::spawn(move || {
            receiver
                .recv_timeout(std::time::Duration::from_secs(5))
                .unwrap();
            interrupt.interrupt();
            interrupt
        });
        let error = index.connection().query_row::<i64, _, _>(
            "WITH RECURSIVE n(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM n WHERE x<1000000000) SELECT sum(x) FROM n", [], |r| r.get(0),
        ).unwrap_err();
        assert_eq!(sql_error(error), Error::Cancelled);
        let interrupt = worker.join().unwrap();
        drop(index);
        interrupt.interrupt(); // Rusqlite makes this a no-op after close.
    }

    #[test]
    fn export_index_unexpected_file_is_preserved_and_cleanup_failure_visible() {
        let fixture = Fixture::new();
        let index = ExportIndex::new(&fixture.destination(), Cancellation::default()).unwrap();
        let path = index.spool.path().to_owned();
        let directory = path.parent().unwrap().to_owned();
        let unexpected = directory.join("synthetic-unexpected.txt");
        fs::write(&unexpected, b"synthetic foreign entry").unwrap();
        assert_eq!(index.close(), Err(Error::CleanupFailed));
        assert!(!path.exists());
        assert_eq!(fs::read(&unexpected).unwrap(), b"synthetic foreign entry");
        // The test owns this injected fixture; production cleanup did not touch it.
        fs::remove_file(&unexpected).unwrap();
        fs::remove_dir(&directory).unwrap();
    }
}
