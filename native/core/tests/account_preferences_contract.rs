use std::{fs, path::PathBuf};
use serde_json::{Value,json};
use valmax_core::{account_preferences as store, settings_io::StoreError};
fn fixture() -> Value { serde_json::from_str(include_str!("fixtures/account_preferences.json")).unwrap() }
#[test]
fn python_unicode_canonical_revision_and_numeric_account_order() {
    let data = fixture();
    let clean = store::validate_accounts(&data["input"]).unwrap();
    assert_eq!(clean, data["validated"]);
    assert_eq!(clean[0]["accountId"], "2");
    assert_eq!(String::from_utf8(store::canonical(&clean).unwrap()).unwrap(), data["canonical"].as_str().unwrap());
    assert_eq!(store::snapshot_value(&clean).unwrap(), data["snapshot"]);
    assert_eq!(store::parse(data["canonical"].as_str().unwrap().as_bytes()).unwrap(), clean);
}
#[test]
fn overnight_weekday_boundaries_match_python() {
    let data = fixture(); let hours = &data["validated"][0]["workHours"];
    for row in data["workTimes"].as_array().unwrap() {
        assert_eq!(store::is_work_time(hours,row["weekday"].as_u64().unwrap() as u8,row["minute"].as_u64().unwrap() as u16).unwrap(),row["expected"].as_bool().unwrap());
    }
    assert!(store::is_work_time(hours,0,10).is_err());
    assert!(store::is_work_time(hours,1,1440).is_err());
    let mut off = hours.clone(); off["enabled"] = false.into();
    assert!(store::is_work_time(&off,1,0).unwrap());
}
#[test]
fn exact_account_types_names_ids_and_shape() {
    for id in ["", "0", "01", "+1", "-1", "1.0", "9223372036854775808", "١"] { assert!(!store::valid_account_id(id)); }
    assert!(store::valid_account_id("9223372036854775807"));
    let base = store::defaults("7").unwrap();
    for name in ["x\n", "x\u{85}", "x\u{202e}", "x\u{2066}", &"🚀".repeat(65)] {
        let mut row = base.clone(); row["displayName"] = name.into();
        assert_eq!(store::validate_accounts(&json!([row])),Err(store::Error::InvalidAccounts));
    }
    let mut row = base.clone(); row["displayName"] = "🚀".repeat(64).into();
    assert!(store::validate_accounts(&json!([row])).is_ok());
    for (key,value) in [("accountId",json!(7)),("color",json!("red")),("ghost",json!({})),("unknown",json!(false))] {
        let mut row = base.clone(); row[key] = value;
        assert!(store::validate_accounts(&json!([row])).is_err());
    }
    assert!(store::validate_accounts(&json!([base,base])).is_err());
}
#[test]
fn invalid_work_hours_and_persisted_schema_rejected() {
    let base = store::defaults("7").unwrap()["workHours"].clone();
    for (key,value) in [("enabled",json!(1)),("days",json!([true])),("days",json!([1,1])),
        ("days",json!([0])),("days",json!([8])),("start",json!("9:00")),("start",json!("24:00")),("end",json!("09:00"))] {
        let mut hours = base.clone(); hours[key] = value; assert!(store::validate_hours(&hours).is_err());
    }
    for bytes in [b"{\"schema\":true,\"accounts\":[]}".as_slice(), b"{\"schema\":1.0,\"accounts\":[]}",
        b"{\"schema\":1,\"accounts\":[],\"extra\":1}", b"{\"schema\":1,\"accounts\":[],\"accounts\":[]}"] {
        assert_eq!(store::parse(bytes),Err(store::Error::Store(StoreError::InvalidStore)));
    }
}
#[test]
fn native_store_roundtrip_and_stale_edit() {
    let mut random = [0u8;8]; getrandom::getrandom(&mut random).unwrap();
    let root = PathBuf::from("C:/ValMAXBuild/test-data").join(format!("accounts-{:x}",u64::from_le_bytes(random)));
    let path = root.join("account-settings.json");
    let db = store::AccountPreferencesStore::new(&path).unwrap();
    let old = db.snapshot().unwrap(); assert!(!root.exists());
    let rev = old["revision"].as_str().unwrap(); let data = fixture();
    let new = db.replace(rev,&data["input"]).unwrap();
    assert_eq!(new,data["snapshot"]);
    assert_eq!(store::AccountPreferencesStore::new(&path).unwrap().snapshot().unwrap(),new);
    assert_eq!(db.replace(rev,&json!([])),Err(store::Error::Store(StoreError::RevisionConflict)));
    fs::remove_dir_all(root).unwrap();
}
