use super::*;
use serde_json::json;

fn credentials(entries: &[(&str, bool)]) -> BTreeMap<String, Zeroizing<Vec<u8>>> {
    entries
        .iter()
        .map(|(name, nonempty)| {
            (
                (*name).to_owned(),
                Zeroizing::new(if *nonempty { vec![0x53] } else { Vec::new() }),
            )
        })
        .collect()
}
fn manifest(paths: &[&str]) -> Value {
    json!({"files": paths.iter().map(|path| json!({"path":path,"size":0})).collect::<Vec<_>>()})
}

#[test]
fn owned_names_are_exact_and_web_owner_uses_greedy_prefix_and_checked_xor() {
    assert!(allowed_name("access_token_9223372036854775807@MXMOD"));
    for name in [
        "access_token_0@MXMOD",
        "access_token_01@MXMOD",
        "access_token_9223372036854775808@MXMOD",
        "Access_token_1@MXMOD",
        "access_token_1@mxmod",
        "history_storage_key_1@ONEME",
        "x-1_1@MXMOD",
        "x-1_01@MXMOD",
        "x-1_9223372036854775808@MXMOD",
        "x\n-1_0@MXMOD",
    ] {
        assert!(!allowed_name(name));
        assert_eq!(owner_id(name), Err(ProfileError::InvalidPayload));
    }
    assert_eq!(owner_id("multi_state_enabled_Common@MXMOD"), Ok(None));
    assert_eq!(
        owner_id("space _dash-7_2-name-42_1@MXMOD"),
        Ok(Some("43".into()))
    );
    assert_eq!(
        owner_id(&format!("{}-42_0@MXMOD", "x".repeat(192))),
        Ok(Some("42".into()))
    );
    assert!(!allowed_name(&format!("{}-42_0@MXMOD", "x".repeat(193))));
    assert!(!allowed_name("é-42_0@MXMOD"));
}

#[test]
fn inclusion_keeps_legacy_case_rules_separate_from_safe_paths() {
    for path in [
        "logs/a",
        "user42/CRASHDUMPS/a",
        "user42/logſ/a",
        "mdata-result.json",
        ".mdata-part/a",
        "a.MDATA/x",
        "a.MAXSESSION",
    ] {
        assert!(!included(path));
    }
    for path in [
        "MDATA-result.json",
        ".MDATA-part/a",
        "user42/mdata-result.json",
        "user42/file.mdata",
        "user42/history/Тест.db",
    ] {
        assert!(safe_relative(path));
    }
    assert!(included("../history.db"));
    for path in [
        "../history.db",
        "user42//x",
        "a:stream",
        "CON.txt",
        "a/",
        "a\\b",
    ] {
        assert!(!safe_relative(path));
    }
}

#[test]
fn profile_ids_and_target_order_are_bounded_without_login_account_limit() {
    assert_eq!(
        profile_ids(
            manifest(&[
                "AUTH_2",
                "USER10/history/a.db",
                "user2/a",
                "user0/a",
                "user9223372036854775808/a"
            ])["files"]
                .as_array()
                .unwrap()
        )
        .unwrap(),
        BTreeSet::from(["10".into(), "2".into()])
    );
    let many: Vec<_> = (1..=63)
        .map(|id| json!({"path":format!("user{id}/settings"),"size":0}))
        .collect();
    assert_eq!(profile_ids(&many).unwrap().len(), 63);
    let mut too_many = many;
    too_many.push(json!({"path":"auth_64","size":0}));
    assert_eq!(profile_ids(&too_many), Err(ProfileError::TooLarge));
    let result = targets(&["2".into(), "10".into(), "2".into()]).unwrap();
    assert_eq!(&result[..2], &COMMON_NAMES);
    assert_eq!(result[2], "access_token_10@MXMOD");
    assert_eq!(result[6], "access_token_2@MXMOD");
    assert_eq!(result[10], "access_token_2@MXMOD");
    assert_eq!(
        targets(&vec!["1".into(); 64]),
        Err(ProfileError::InvalidPayload)
    );
    assert_eq!(targets(&["01".into()]), Err(ProfileError::InvalidPayload));
    assert!(credential_policy().validate().is_ok());
}

#[test]
fn storage_requires_nonempty_database_keys_but_empty_profile_is_valid() {
    assert_eq!(validate_storage(&manifest(&[]), &credentials(&[])), Ok(()));
    let history = manifest(&["USER42/HISTORY/sub/part.DB"]);
    for c in [
        credentials(&[]),
        credentials(&[("history_storage_key_42@MXMOD", false)]),
        credentials(&[("access_token_42@MXMOD", true)]),
    ] {
        assert_eq!(
            validate_storage(&history, &c),
            Err(ProfileError::InvalidPayload)
        );
    }
    assert_eq!(
        validate_storage(
            &history,
            &credentials(&[("history_storage_key_42@MXMOD", true)])
        ),
        Ok(())
    );
    assert_eq!(
        validate_storage(
            &manifest(&["app/cache/app.db"]),
            &credentials(&[(COMMON_NAMES[0], false)])
        ),
        Err(ProfileError::InvalidPayload)
    );
    assert_eq!(
        validate_storage(
            &manifest(&["APP/cache/app.DB"]),
            &credentials(&[(COMMON_NAMES[0], true)])
        ),
        Ok(())
    );
    // Python's '.+\\.db' does not regard the literal basename '.db' as a DB.
    assert_eq!(
        validate_storage(&manifest(&["user42/history/.db"]), &credentials(&[])),
        Ok(())
    );
    assert_eq!(
        validate_storage(
            &manifest(&["user9223372036854775808/history/x.db"]),
            &credentials(&[])
        ),
        Err(ProfileError::InvalidPayload)
    );
}

#[test]
fn web_credential_cannot_establish_its_own_known_owner() {
    let web = credentials(&[("web-42_1@MXMOD", true)]);
    assert_eq!(
        validate_storage(&manifest(&[]), &web),
        Err(ProfileError::InvalidPayload)
    );
    assert_eq!(
        validate_storage(&manifest(&["user43/settings"]), &web),
        Ok(())
    );
    assert_eq!(
        validate_storage(
            &manifest(&[]),
            &credentials(&[("web-42_1@MXMOD", true), ("calls_session_43@MXMOD", false)])
        ),
        Ok(())
    );
    assert_eq!(
        validate_storage(
            &manifest(&[]),
            &credentials(&[("unsupported_Common@MXMOD", true)])
        ),
        Err(ProfileError::InvalidPayload)
    );
    assert_eq!(
        validate_storage(&json!({}), &credentials(&[])),
        Err(ProfileError::InvalidPayload)
    );
    assert_eq!(
        profile_ids(&[json!({"path":4})]),
        Err(ProfileError::InvalidPayload)
    );
}

#[test]
fn synthetic_python_differential_fixture() {
    // The fixture generator executes only selected pure Python definitions,
    // never their filesystem/WinCred adapters. Root builds run on Windows.
    let path = std::env::var_os("VALMAX_MDATA_PROFILE_FIXTURE")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| "C:/ValMAXBuild/fixtures/mdata-profile/contract.json".into());
    let bytes = std::fs::read(path).expect("run native/core/tests/mdata_profile_oracle.py first");
    assert!(bytes.len() < 2 * 1024 * 1024);
    let cases: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(cases["schema"], 1);
    for case in cases["names"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        assert_eq!(allowed_name(name), case["allowed"].as_bool().unwrap());
        let actual = match owner_id(name) {
            Ok(Some(id)) => json!({"owner":id}),
            Ok(None) => json!({"owner":null}),
            Err(e) => json!({"error":e.code()}),
        };
        assert_eq!(actual, case["result"]);
    }
    for case in cases["paths"].as_array().unwrap() {
        let path = case["path"].as_str().unwrap();
        assert_eq!(included(path), case["included"].as_bool().unwrap());
        assert_eq!(safe_relative(path), case["safe"].as_bool().unwrap());
    }
    for case in cases["profileIds"].as_array().unwrap() {
        let actual = match profile_ids(case["files"].as_array().unwrap()) {
            Ok(ids) => json!({"ids":ids}),
            Err(e) => json!({"error":e.code()}),
        };
        assert_eq!(actual, case["result"]);
    }
    for case in cases["targets"].as_array().unwrap() {
        let ids: Vec<String> = serde_json::from_value(case["ids"].clone()).unwrap();
        let actual = match targets(&ids) {
            Ok(names) => json!({"names":names}),
            Err(e) => json!({"error":e.code()}),
        };
        assert_eq!(actual, case["result"]);
    }
    for case in cases["storage"].as_array().unwrap() {
        let values = case["credentialStates"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(name, present)| {
                (
                    name.clone(),
                    Zeroizing::new(if present.as_bool().unwrap() {
                        vec![0x53]
                    } else {
                        Vec::new()
                    }),
                )
            })
            .collect();
        let actual = match validate_storage(&case["manifest"], &values) {
            Ok(()) => json!({"ok":true}),
            Err(e) => json!({"error":e.code()}),
        };
        assert_eq!(actual, case["result"]);
    }
}
