use super::*;
use std::{fs, mem::MaybeUninit, sync::atomic::{AtomicU64, Ordering}};

static NEXT: AtomicU64 = AtomicU64::new(1);
struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let parent = PathBuf::from("E:/ValMAXBuild/fixtures/synthetic/retention-store-p12");
        fs::create_dir_all(&parent).unwrap();
        let root = parent.join(format!("case-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)));
        fs::create_dir(&root).unwrap();
        Self(root)
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let parent = Path::new("E:/ValMAXBuild/fixtures/synthetic/retention-store-p12");
        assert_eq!(self.0.parent(), Some(parent));
        fs::remove_dir_all(&self.0).unwrap();
    }
}

#[test]
fn durable_receipt_requires_prior_received_id_and_remains_scoped_after_restart() {
    let fixture = Fixture::new();
    let store = RetentionStore::new(&fixture.0).unwrap();
    let missing = store.lookup(11, -22, &[31]).unwrap();
    assert!(missing.deleted_ids.is_empty());
    assert_eq!(missing.revision, sha256(b"valmax-retention-v1:0"));
    assert!(!fixture.0.join(MARKER_FILE).exists());
    store.preflight().unwrap();
    assert_eq!(store.remember(11, -22, &[31, 32]).unwrap(), 2);
    assert_eq!(store.remember(11, -22, &[31]).unwrap(), 0);
    // A routing receipt alone cannot suppress deletion or expose content.
    assert_eq!(store.mark_received_delete(11, -22, &[31, 33], 1111).unwrap(), [false, false]);
    store.capture_text(11, -22, 31, 1200, "synthetic first").unwrap();
    store.capture_text(11, -22, 31, 1201, "synthetic edited").unwrap();
    store.capture_text(11, -22, 32, 1202, "synthetic second").unwrap();
    assert!(store.lookup_text(11, -22, &[31]).unwrap().is_empty());
    assert_eq!(store.mark_received_delete(11, -22, &[31, 33], 1234).unwrap(), [true, false]);
    let first = store.lookup(11, -22, &[33, 31, 32]).unwrap();
    assert_eq!(first.deleted_ids, [31]);
    assert_eq!(first.revision, sha256(b"valmax-retention-v1:1"));
    assert!(store.lookup(12, -22, &[31]).unwrap().deleted_ids.is_empty());
    assert!(store.lookup(11, 22, &[31]).unwrap().deleted_ids.is_empty());
    assert!(store.lookup_text(12, -22, &[31]).unwrap().is_empty());
    assert!(store.lookup_text(11, 22, &[31]).unwrap().is_empty());
    let retained = store.lookup_text(11, -22, &[31]).unwrap();
    assert_eq!(retained.len(), 1);
    assert_eq!((retained[0].id, retained[0].text.as_str()), (31, "synthetic edited"));
    let archive_bytes = fs::read(fixture.0.join(CONTENT_FILE)).unwrap();
    assert!(!archive_bytes.windows(b"synthetic edited".len()).any(|part| part == b"synthetic edited"));
    store.capture_text(11, -22, 31, 1203, "synthetic after delete").unwrap();
    assert_eq!(store.lookup_text(11, -22, &[31]).unwrap()[0].text.as_str(), "synthetic edited");
    assert_eq!(store.mark_received_delete(11, -22, &[31], 9999).unwrap(), [true]);
    assert_eq!(store.lookup(11, -22, &[31]).unwrap().revision, first.revision);
    drop(store);
    let reopened = RetentionStore::new(&fixture.0).unwrap();
    assert_eq!(reopened.lookup(11, -22, &[31, 32]).unwrap().deleted_ids, [31]);
    assert_eq!(reopened.lookup_text(11, -22, &[31]).unwrap()[0].text.as_str(), "synthetic edited");
    assert_eq!(reopened.mark_received_delete(11, -22, &[32], 2345).unwrap(), [true]);
    assert_eq!(reopened.lookup(11, -22, &[31, 32]).unwrap().revision,
        sha256(b"valmax-retention-v1:2"));
}

#[test]
fn reads_exact_legacy_sqlite_schema_without_rewriting_it() {
    let fixture = Fixture::new();
    for (file, app_id, table) in [
        (MARKER_FILE, MARKER_APP_ID, MARKER_SQL),
        (RECEIVED_FILE, RECEIVED_APP_ID, RECEIVED_SQL),
    ] {
        let db = Connection::open(fixture.0.join(file)).unwrap();
        db.execute_batch("PRAGMA journal_mode=PERSIST; PRAGMA synchronous=FULL;").unwrap();
        db.execute_batch(META_SQL).unwrap();
        db.execute_batch(table).unwrap();
        db.execute("INSERT INTO retention_meta VALUES(1,1)", []).unwrap();
        db.execute_batch(&format!("PRAGMA application_id={app_id}; PRAGMA user_version=1;")).unwrap();
        if app_id == MARKER_APP_ID {
            db.execute("INSERT INTO deleted_messages VALUES('44','-55','66',777)", []).unwrap();
        } else {
            db.execute("INSERT INTO received_messages VALUES('44','-55','66')", []).unwrap();
        }
    }
    let marker = fixture.0.join(MARKER_FILE);
    let before = fs::read(&marker).unwrap();
    let store = RetentionStore::new(&fixture.0).unwrap();
    assert_eq!(store.lookup(44, -55, &[66]).unwrap().deleted_ids, [66]);
    assert_eq!(fs::read(&marker).unwrap(), before);
    // Existing marker data remains readable, but no new ACK claims a retained
    // body until a protected content snapshot exists.
    assert_eq!(store.mark_received_delete(44, -55, &[66, 67], 888).unwrap(), [false, false]);
    assert_eq!(store.lookup(44, -55, &[66]).unwrap().revision, sha256(b"valmax-retention-v1:1"));
}

#[test]
fn malformed_and_foreign_indexes_fail_closed_without_replacement() {
    let fixture = Fixture::new();
    let store = RetentionStore::new(&fixture.0).unwrap();
    assert_eq!(store.remember(11, 22, &[31, 31]), Err(Error::InvalidRequest));
    assert_eq!(store.remember(0, 22, &[31]), Err(Error::InvalidRequest));
    assert_eq!(store.lookup(11, 0, &[31]), Err(Error::InvalidRequest));
    assert_eq!(store.capture_text(11, 22, 31, 0, ""), Err(Error::InvalidRequest));
    assert!(!fixture.0.join(CONTENT_FILE).exists());
    let path = fixture.0.join(MARKER_FILE);
    fs::write(&path, b"foreign document").unwrap();
    assert_eq!(store.lookup(11, 22, &[31]), Err(Error::InvalidStore));
    assert_eq!(fs::read(&path).unwrap(), b"foreign document");
}

#[test]
fn protected_text_cannot_be_swapped_across_account_routes() {
    let fixture = Fixture::new();
    let store = RetentionStore::new(&fixture.0).unwrap();
    store.capture_text(11, -22, 31, 100, "synthetic owner eleven").unwrap();
    store.capture_text(12, -22, 31, 100, "synthetic owner twelve").unwrap();
    assert_eq!(store.mark_received_delete(11, -22, &[31], 200).unwrap(), [true]);
    assert_eq!(store.mark_received_delete(12, -22, &[31], 200).unwrap(), [true]);
    drop(store);
    let db = Connection::open(fixture.0.join(CONTENT_FILE)).unwrap();
    let stolen: Vec<u8> = db.query_row(
        "SELECT protected_text FROM retained_content WHERE account_id='11' AND chat_id='-22' AND message_id='31'",
        [], |row| row.get(0),
    ).unwrap();
    db.execute(
        "UPDATE retained_content SET protected_text=?1 WHERE account_id='12' AND chat_id='-22' AND message_id='31'",
        params![stolen],
    ).unwrap();
    drop(db);
    let reopened = RetentionStore::new(&fixture.0).unwrap();
    assert_eq!(reopened.lookup_text(11, -22, &[31]).unwrap()[0].text.as_str(), "synthetic owner eleven");
    assert!(matches!(reopened.lookup_text(12, -22, &[31]), Err(Error::InvalidStore)));
}

#[test]
fn queue_abi_receipts_only_previous_ids_and_rejects_a_changed_digest() {
    let fixture = Fixture::new();
    let profile: Vec<u16> = fixture.0.to_str().unwrap().encode_utf16().collect();
    let received = [31i64, 32];
    let mut request = RetentionRequest {
        size: 128, version: 1, kind: 1, count: received.len() as u32,
        nonce: 1, epoch: 1, generation: 1, owner: 11, chat: -22,
        deadline_tick: 0, ids_digest: digest_ids(&received),
    };
    let mut output = MaybeUninit::<RetentionAck>::uninit();
    assert_eq!(unsafe { valmax_core_retention_store_process(profile.as_ptr(), profile.len(),
        &request, received.as_ptr(), output.as_mut_ptr()) }, 0);
    let ack = unsafe { output.assume_init() };
    assert_eq!(ack.result, 0);
    assert_eq!(ack.retained, [0; 512]);
    assert_eq!(ack.request.ids_digest, request.ids_digest);
    let text = b"synthetic queue content";
    assert_eq!(unsafe { valmax_core_retention_capture_text(profile.as_ptr(), profile.len(),
        11, -22, 32, 1234, text.as_ptr(), text.len()) }, 0);
    let deleted = [32i64, 33];
    request.kind = 2;
    request.count = deleted.len() as u32;
    request.nonce = 2;
    request.ids_digest = digest_ids(&deleted);
    let mut output = MaybeUninit::<RetentionAck>::uninit();
    assert_eq!(unsafe { valmax_core_retention_store_process(profile.as_ptr(), profile.len(),
        &request, deleted.as_ptr(), output.as_mut_ptr()) }, 0);
    let ack = unsafe { output.assume_init() };
    assert_eq!(ack.result, 0);
    assert_eq!(ack.retained[0], 1);
    assert!(ack.retained[1..].iter().all(|&byte| byte == 0));
    request.ids_digest[0] ^= 1;
    let mut invalid = MaybeUninit::<RetentionAck>::uninit();
    assert_eq!(unsafe { valmax_core_retention_store_process(profile.as_ptr(), profile.len(),
        &request, deleted.as_ptr(), invalid.as_mut_ptr()) }, 1);
    request.ids_digest = digest_ids(&deleted);
    let bad_profile = [0u16];
    let mut failed = MaybeUninit::<RetentionAck>::uninit();
    assert_eq!(unsafe { valmax_core_retention_store_process(bad_profile.as_ptr(), bad_profile.len(),
        &request, deleted.as_ptr(), failed.as_mut_ptr()) }, 0);
    assert_eq!(unsafe { failed.assume_init() }.result, 1);
}

#[test]
fn bounded_batch_capture_is_atomic_scoped_and_durable() {
    let fixture = Fixture::new();
    let store = RetentionStore::new(&fixture.0).unwrap();
    let rows = [
        IncomingText { id: 31, received_at_ms: 100, order_key: None, text: "synthetic batch first" },
        IncomingText { id: 32, received_at_ms: 101, order_key: None, text: "synthetic batch second" },
        IncomingText { id: 33, received_at_ms: 102, order_key: None, text: "synthetic batch third" },
    ];
    store.capture_text_batch(11, -22, &rows).unwrap();
    assert_eq!(store.capture_text_batch(11, -22, &[rows[0], rows[0]]), Err(Error::InvalidRequest));
    assert_eq!(store.mark_received_delete(11, -22, &[31,32,33,34], 200).unwrap(),
        [true,true,true,false]);
    assert!(store.lookup_text(12, -22, &[31,32,33]).unwrap().is_empty());
    let original = fs::read(fixture.0.join(CONTENT_FILE)).unwrap();
    assert!(!original.windows(b"synthetic batch first".len())
        .any(|part| part == b"synthetic batch first"));
    drop(store);
    let reopened = RetentionStore::new(&fixture.0).unwrap();
    assert_eq!(reopened.lookup_text(11, -22, &[31,32,33]).unwrap().len(), 3);
    assert_eq!(reopened.lookup_text(11, -23, &[31,32,33]).unwrap().len(), 0);
}

#[test]
fn bounded_batch_capture_ffi_rejects_invalid_rows() {
    let fixture = Fixture::new();
    let profile: Vec<u16> = fixture.0.to_str().unwrap().encode_utf16().collect();
    let first = b"synthetic ffi first";
    let second = b"synthetic ffi second";
    let rows = [
        RetentionCaptureRow { message:31, received_at_ms:100,
            utf8_text:first.as_ptr(), text_length:first.len() },
        RetentionCaptureRow { message:32, received_at_ms:101,
            utf8_text:second.as_ptr(), text_length:second.len() },
    ];
    assert_eq!(unsafe { valmax_core_retention_capture_text_batch(
        profile.as_ptr(), profile.len(), 11, -22, rows.as_ptr(), rows.len()) }, 0);
    assert_eq!(unsafe { valmax_core_retention_capture_text_batch(
        profile.as_ptr(), profile.len(), 11, -22, rows.as_ptr(), 0) }, 1);
    let duplicate = [rows[0], rows[0]];
    assert_eq!(unsafe { valmax_core_retention_capture_text_batch(
        profile.as_ptr(), profile.len(), 11, -22, duplicate.as_ptr(), duplicate.len()) }, 1);
    let store = RetentionStore::new(&fixture.0).unwrap();
    assert_eq!(store.mark_received_delete(11, -22, &[31,32], 200).unwrap(), [true,true]);
}

#[test]
fn deleted_page_is_scoped_keyset_and_requires_both_durable_records() {
    let fixture = Fixture::new();
    let store = RetentionStore::new(&fixture.0).unwrap();
    for id in [31,32,33,34] {
        store.capture_text(11,-22,id,100,"synthetic page body").unwrap();
    }
    store.capture_text(12,-22,31,100,"synthetic other owner").unwrap();
    assert_eq!(store.mark_received_delete(11,-22,&[31,32,33],200).unwrap(),[true,true,true]);
    assert_eq!(store.mark_content_deleted(11,-22,&[34],201).unwrap(),HashSet::from([34]));
    let content_before = store.content_revision().unwrap();
    let marker_before = store.marker_revision().unwrap();
    let page1 = store.list_deleted_text(11,-22,None,2).unwrap();
    assert_eq!(page1.items.iter().map(|row| row.id).collect::<Vec<_>>(),[33]);
    let cursor = page1.next_cursor.as_ref().unwrap();
    let page2 = store.list_deleted_text(11,-22,Some(cursor),2).unwrap();
    assert_eq!(page2.items.iter().map(|row| row.id).collect::<Vec<_>>(),[32,31]);
    assert!(page2.next_cursor.is_none());
    assert!(store.list_deleted_text(12,-22,None,100).unwrap().items.is_empty());
    assert_eq!(store.list_deleted_text(12,-22,Some(cursor),2).err(),Some(Error::InvalidRequest));
    assert_eq!(store.list_deleted_text(11,-22,Some("broken"),2).err(),Some(Error::InvalidRequest));
    assert_eq!(store.mark_received_delete(11,-22,&[34],202).unwrap(),[true]);
    assert_eq!(store.content_revision().unwrap(),content_before);
    assert_ne!(store.marker_revision().unwrap(),marker_before);
    assert_eq!(store.list_deleted_text(11,-22,Some(cursor),2).err(),Some(Error::StaleCursor));
    drop(store);
    let reopened = RetentionStore::new(&fixture.0).unwrap();
    let refreshed = reopened.list_deleted_text(11,-22,None,100).unwrap();
    assert_eq!(refreshed.items.iter().map(|row| row.id).collect::<Vec<_>>(),[34,33,32,31]);
    assert!(refreshed.items.iter().all(|row| row.text.as_str()=="synthetic page body"));
    assert!(refreshed.next_cursor.is_none());
    assert_eq!(refreshed.revision,reopened.visibility_revision().unwrap());
}

#[test]
fn marker_only_count_keeps_legacy_metadata_without_claiming_text() {
    let fixture = Fixture::new();
    let store = RetentionStore::new(&fixture.0).unwrap();
    store.capture_text(11,-22,31,100,"synthetic captured").unwrap();
    assert_eq!(store.mark_received_delete(11,-22,&[31],200).unwrap(),[true]);
    with_db(&store.markers,true,MARKER_APP_ID,MARKER_SQL,|connection,_| {
        let connection = connection.ok_or(Error::InvalidStore)?;
        let transaction = connection.transaction().map_err(sql_error)?;
        transaction.execute("INSERT INTO deleted_messages VALUES('11','-22','32',201)",[])
            .map_err(sql_error)?;
        transaction.execute("UPDATE retention_meta SET revision=revision+1 WHERE singleton=1",[])
            .map_err(sql_error)?;
        transaction.commit().map_err(sql_error)
    }).unwrap();
    assert_eq!(store.marker_only_count(11,-22).unwrap(),1);
    assert_eq!(store.marker_only_count(12,-22).unwrap(),0);
    assert_eq!(store.marker_only_count(11,-23).unwrap(),0);
    assert_eq!(store.marker_only_count(0,-22),Err(Error::InvalidRequest));
    store.capture_text(11,-22,32,101,"synthetic later captured").unwrap();
    assert_eq!(store.mark_received_delete(11,-22,&[32],202).unwrap(),[true]);
    assert_eq!(store.marker_only_count(11,-22).unwrap(),0);
}

#[test]
fn passive_ffi_preflight_and_mark_pair_only_captured_text() {
    let fixture = Fixture::new();
    let profile: Vec<u16> = fixture.0.to_str().unwrap().encode_utf16().collect();
    assert_eq!(unsafe { valmax_core_retention_preflight(profile.as_ptr(), profile.len()) }, 0);
    let text = b"synthetic passive receipt";
    let row = [RetentionCaptureRow { message: 31, received_at_ms: 100,
        utf8_text: text.as_ptr(), text_length: text.len() }];
    assert_eq!(unsafe { valmax_core_retention_capture_text_batch(
        profile.as_ptr(), profile.len(), 11, -22, row.as_ptr(), row.len()) }, 0);
    let ids = [31, 32];
    let mut bits = [0xffu8];
    assert_eq!(unsafe { valmax_core_retention_mark_delete_batch(
        profile.as_ptr(), profile.len(), 11, -22,
        ids.as_ptr(), ids.len(), bits.as_mut_ptr(), bits.len()) }, 0);
    assert_eq!(bits, [1]);
    assert_eq!(unsafe { valmax_core_retention_mark_delete_batch(
        profile.as_ptr(), profile.len(), 12, -22,
        ids.as_ptr(), ids.len(), bits.as_mut_ptr(), bits.len()) }, 0);
    assert_eq!(bits, [0]);
    let store = RetentionStore::new(&fixture.0).unwrap();
    assert_eq!(store.list_deleted_text(11, -22, None, 100).unwrap().items.len(), 1);
    assert_eq!(store.list_deleted_text(12, -22, None, 100).unwrap().items.len(), 0);
}

#[test]
fn content_v1_migrates_without_losing_two_account_ciphertexts() {
    let fixture = Fixture::new();
    let path = fixture.0.join(CONTENT_FILE);
    let db = Connection::open(&path).unwrap();
    db.execute_batch("PRAGMA journal_mode=PERSIST; PRAGMA synchronous=FULL;").unwrap();
    db.execute_batch(META_SQL).unwrap();
    db.execute_batch(CONTENT_SQL_V1).unwrap();
    db.execute("INSERT INTO retention_meta VALUES(1,0)", []).unwrap();
    db.execute_batch(&format!("PRAGMA application_id={CONTENT_APP_ID}; PRAGMA user_version=1;")).unwrap();
    for (owner, text) in [(11, "synthetic old owner one"),
                          (12, "synthetic old owner two")] {
        let ciphertext = dpapi(text.as_bytes(), owner, -22, 31, true).unwrap();
        db.execute("INSERT INTO retained_content VALUES(?1,?2,?3,?4,?5,NULL)",
            params![owner.to_string(), "-22", "31", 1234, ciphertext.as_slice()]).unwrap();
    }
    drop(db);
    let store = RetentionStore::new(&fixture.0).unwrap();
    store.preflight().unwrap();
    store.preflight().unwrap();
    let db = Connection::open(&path).unwrap();
    let version: i64 = db.query_row("PRAGMA user_version", [], |row| row.get(0)).unwrap();
    assert_eq!(version, 2);
    let sql: String = db.query_row(
        "SELECT sql FROM sqlite_master WHERE name='retained_content'", [],
        |row| row.get(0)).unwrap();
    assert_eq!(sql, CONTENT_SQL);
    let index: String = db.query_row(
        "SELECT sql FROM sqlite_master WHERE name='retained_content_order_idx'", [],
        |row| row.get(0)).unwrap();
    assert_eq!(index, CONTENT_ORDER_INDEX_SQL);
    let unknown: Option<i64> = db.query_row(
        "SELECT order_key FROM retained_content WHERE account_id='11'", [],
        |row| row.get(0)).unwrap();
    assert_eq!(unknown, None);
    drop(db);
    for (owner, text) in [(11, "synthetic old owner one"),
                          (12, "synthetic old owner two")] {
        assert_eq!(store.mark_received_delete(owner, -22, &[31], 2345).unwrap(), [true]);
        let rows = store.list_deleted_text(owner, -22, None, 10).unwrap().items;
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].text.as_str(), text);
        assert_eq!(rows[0].received_at_ms, 1234);
        assert_eq!(rows[0].order_key, None);
        assert!(store.list_deleted_text_range(owner, -22, 1, 3000, None, 10)
            .unwrap().items.is_empty());
    }
    drop(store);
    let reopened = RetentionStore::new(&fixture.0).unwrap();
    assert_eq!(reopened.lookup_text(11, -22, &[31]).unwrap()[0].text.as_str(),
        "synthetic old owner one");
    assert_eq!(reopened.lookup_text(12, -22, &[31]).unwrap()[0].text.as_str(),
        "synthetic old owner two");
}

#[test]
fn malformed_content_v1_is_rejected_before_migration_without_rewriting_bytes() {
    let fixture = Fixture::new();
    let path = fixture.0.join(CONTENT_FILE);
    let db = Connection::open(&path).unwrap();
    db.execute_batch("PRAGMA journal_mode=PERSIST; PRAGMA synchronous=FULL;").unwrap();
    db.execute_batch(META_SQL).unwrap();
    db.execute_batch(CONTENT_SQL_V1).unwrap();
    db.execute_batch("CREATE INDEX unexpected_index ON retained_content(account_id)").unwrap();
    db.execute("INSERT INTO retention_meta VALUES(1,0)", []).unwrap();
    db.execute_batch(&format!("PRAGMA application_id={CONTENT_APP_ID}; PRAGMA user_version=1;")).unwrap();
    let ciphertext = dpapi(b"synthetic malformed v1 text", 11, -22, 31, true).unwrap();
    db.execute("INSERT INTO retained_content VALUES('11','-22','31',1234,?1,NULL)",
        params![ciphertext.as_slice()]).unwrap();
    drop(db);
    let before = fs::read(&path).unwrap();
    let store = RetentionStore::new(&fixture.0).unwrap();
    assert_eq!(store.preflight(), Err(Error::InvalidStore));
    assert_eq!(fs::read(&path).unwrap(), before);
    let db = Connection::open(&path).unwrap();
    let version: i64 = db.query_row("PRAGMA user_version", [], |row| row.get(0)).unwrap();
    assert_eq!(version, 1);
    let sql: String = db.query_row(
        "SELECT sql FROM sqlite_master WHERE name='retained_content'", [],
        |row| row.get(0)).unwrap();
    assert_eq!(sql, CONTENT_SQL_V1);
}

#[test]
fn capture_v2_ffi_keeps_original_order_across_edit_delete_and_restart() {
    assert_eq!(std::mem::size_of::<RetentionCaptureRowV2>(), 40);
    assert_eq!(std::mem::offset_of!(RetentionCaptureRowV2, order_key), 32);
    let fixture = Fixture::new();
    let profile: Vec<u16> = fixture.0.to_str().unwrap().encode_utf16().collect();
    let first = b"synthetic first";
    let edit = b"synthetic edit";
    let unknown = b"synthetic unknown time";
    let row = RetentionCaptureRowV2 { message:31, received_at_ms:100,
        utf8_text:first.as_ptr(), text_length:first.len(), order_key:1700000000123 };
    assert_eq!(unsafe { valmax_core_retention_capture_text_batch_v2(
        profile.as_ptr(), profile.len(), 11, -22, &row, 1) }, 0);
    let edited = RetentionCaptureRowV2 { received_at_ms:101,
        utf8_text:edit.as_ptr(), text_length:edit.len(), order_key:1700000000999, ..row };
    assert_eq!(unsafe { valmax_core_retention_capture_text_batch_v2(
        profile.as_ptr(), profile.len(), 11, -22, &edited, 1) }, 0);
    let no_time = RetentionCaptureRowV2 { message:32, received_at_ms:102,
        utf8_text:unknown.as_ptr(), text_length:unknown.len(), order_key:0 };
    assert_eq!(unsafe { valmax_core_retention_capture_text_batch_v2(
        profile.as_ptr(), profile.len(), 11, -22, &no_time, 1) }, 0);
    let bad = RetentionCaptureRowV2 { message:33, order_key:-1, ..no_time };
    assert_eq!(unsafe { valmax_core_retention_capture_text_batch_v2(
        profile.as_ptr(), profile.len(), 11, -22, &bad, 1) }, 1);
    let store = RetentionStore::new(&fixture.0).unwrap();
    assert_eq!(store.mark_received_delete(11, -22, &[31,32,33], 200).unwrap(),
        [true,true,false]);
    assert_eq!(unsafe { valmax_core_retention_capture_text_batch_v2(
        profile.as_ptr(), profile.len(), 11, -22, &row, 1) }, 0);
    drop(store);
    let reopened = RetentionStore::new(&fixture.0).unwrap();
    let rows = reopened.list_deleted_text(11, -22, None, 10).unwrap().items;
    assert_eq!(rows.len(), 2);
    let ordered = rows.iter().find(|value| value.id == 31).unwrap();
    assert_eq!(ordered.order_key, Some(1700000000123));
    assert_eq!(ordered.received_at_ms, 101);
    assert_eq!(ordered.text.as_str(), "synthetic edit");
    let fallback = rows.iter().find(|value| value.id == 32).unwrap();
    assert_eq!(fallback.order_key, None);
    assert_eq!(fallback.text.as_str(), "synthetic unknown time");
}
