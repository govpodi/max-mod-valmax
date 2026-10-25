//! Artificial fixture records only. No actual profile or Windows credentials.
use aes_gcm::{Aes256Gcm, KeyInit, Nonce, aead::{Aead, Payload}};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use valmax_core::session_archive::{self as codec, ArchiveError as Error, ArchivePolicy};

const PASSWORD: &str = "Synthetic-Test-Password-123";
fn fixture() -> Value { serde_json::from_str(include_str!("session_archive_fixtures.json")).unwrap() }
fn policy() -> ArchivePolicy { serde_json::from_value(fixture()["policy"].clone()).unwrap() }
fn payload() -> Value { fixture()["payload"].clone() }
fn archive() -> Vec<u8> { STANDARD.decode(fixture()["archive"].as_str().unwrap()).unwrap() }
fn split(data: &[u8]) -> (Value, Vec<u8>) {
    let count = u16::from_be_bytes([data[8], data[9]]) as usize;
    (serde_json::from_slice(&data[10..10+count]).unwrap(), data[10+count..].to_vec())
}
fn frame(header: &Value, ciphertext: &[u8]) -> Vec<u8> {
    let encoded = serde_json::to_vec(header).unwrap();
    let mut result = codec::MAGIC.to_vec(); result.extend_from_slice(&(encoded.len() as u16).to_be_bytes());
    result.extend_from_slice(&encoded); result.extend_from_slice(ciphertext); result
}
fn reject_payload(value: &Value, expected: Error) { assert_eq!(codec::validate_payload(value, &policy()).unwrap_err(), expected); }

#[test]
fn python_generated_archive_decrypts_exactly() {
    assert_eq!(fixture()["producer"], "Python modkit/session_archive.py; artificial records only");
    assert_eq!(codec::decrypt(&archive(), PASSWORD, &policy()).unwrap(), payload());
}

#[test]
fn native_roundtrip_randomized_no_clear_records_and_exact_unicode_password() {
    let first = codec::encrypt(&payload(), "Synthetic-é-Password", &policy()).unwrap();
    let second = codec::encrypt(&payload(), "Synthetic-é-Password", &policy()).unwrap();
    assert_ne!(first, second);
    assert_eq!(codec::decrypt(&first, "Synthetic-é-Password", &policy()).unwrap(), payload());
    assert_eq!(codec::decrypt(&first, "Synthetic-e\u{301}-Password", &policy()), Err(Error::AuthenticationFailed));
    for needle in [b"synthetic_auth_7@MXMOD".as_slice(), b"SYNTHETIC-NOT-A-REAL-SESSION", PASSWORD.as_bytes()] {
        assert!(!first.windows(needle.len()).any(|window| window == needle));
    }
    let (h1, _) = split(&first); let (h2, _) = split(&second);
    assert_ne!(h1["kdf"]["salt"], h2["kdf"]["salt"]); assert_ne!(h1["nonce"], h2["nonce"]);
}

#[test]
fn wrong_password_and_tamper_are_same_finite_error() {
    let original = archive();
    assert_eq!(codec::decrypt(&original, "Different-Synthetic-Password", &policy()), Err(Error::AuthenticationFailed));
    let mut changed = original.clone(); *changed.last_mut().unwrap() ^= 1;
    assert_eq!(codec::decrypt(&changed, PASSWORD, &policy()), Err(Error::AuthenticationFailed));
    assert_eq!(Error::AuthenticationFailed.to_string(), "authentication_failed");
}

#[test]
fn malformed_passwords_fail_before_archive_or_kdf() {
    for password in ["".to_owned(), "short".to_owned(), "x".repeat(1025), format!("\0{PASSWORD}"), "я".repeat(513)] {
        assert_eq!(codec::encrypt(&payload(), &password, &policy()), Err(Error::InvalidPassword));
        assert_eq!(codec::decrypt(b"bad", &password, &policy()), Err(Error::InvalidPassword));
    }
    assert!(codec::encrypt(&payload(), "пароль", &policy()).is_ok()); // 6 scalar values, 12 UTF-8 bytes.
}

#[test]
fn framing_truncation_tail_length_and_untrusted_kdf_rejected() {
    let original = archive();
    let mut tail = original.clone(); tail.push(0);
    for data in [Vec::new(), b"PK\x03\x04bad".to_vec(), original[..original.len()-1].to_vec(), tail] {
        assert_eq!(codec::decrypt(&data, PASSWORD, &policy()), Err(Error::InvalidArchive));
    }
    assert_eq!(codec::decrypt(&vec![0;codec::MAX_ARCHIVE_BYTES+1], PASSWORD, &policy()), Err(Error::TooLarge));
    let (base, ciphertext) = split(&original);
    for (key, value) in [("n",json!(1u64<<40)),("n",json!(true)),("n",json!("32768")),("r",json!(4096)),("p",json!(-1)),("name",json!("other"))] {
        let mut header = base.clone(); header["kdf"][key] = value;
        assert_eq!(codec::decrypt(&frame(&header,&ciphertext),PASSWORD,&policy()),Err(Error::InvalidArchive));
    }
}

#[test]
fn header_exact_keys_types_versions_and_canonical_encoding() {
    let (base, ciphertext) = split(&archive());
    for (key, value, expected) in [("schema",json!(true),Error::InvalidArchive),("sourceVersion",json!("26.35.2"),Error::UnsupportedVersion),
        ("sourceClient",json!("other"),Error::UnsupportedVersion),("cipher",json!("AES-128-GCM"),Error::InvalidArchive),
        ("plaintextBytes",json!(1.0),Error::InvalidArchive),("nonce",json!(STANDARD.encode(b"short")),Error::InvalidArchive)] {
        let mut header = base.clone(); header[key] = value;
        assert_eq!(codec::decrypt(&frame(&header,&ciphertext),PASSWORD,&policy()),Err(expected));
    }
    let mut header = base.clone(); header["kdf"]["salt"] = json!(STANDARD.encode([0u8;17]));
    assert_eq!(codec::decrypt(&frame(&header,&ciphertext),PASSWORD,&policy()),Err(Error::TooLarge));
    let mut header = base.clone(); header["extra"] = json!(1);
    assert_eq!(codec::decrypt(&frame(&header,&ciphertext),PASSWORD,&policy()),Err(Error::InvalidArchive));
    for raw in [serde_json::to_string_pretty(&base).unwrap(), serde_json::to_string(&base).unwrap().replace("\"schema\":1", "\"schema\":1,\"schema\":1")] {
        let mut bytes = codec::MAGIC.to_vec(); bytes.extend_from_slice(&(raw.len() as u16).to_be_bytes()); bytes.extend_from_slice(raw.as_bytes()); bytes.extend_from_slice(&ciphertext);
        assert_eq!(codec::decrypt(&bytes,PASSWORD,&policy()),Err(Error::InvalidArchive));
    }
}

#[test]
fn strict_json_rejects_duplicate_keys_at_any_level_nonfinite_trailing_and_deep() {
    for raw in [b"{\"x\":1,\"x\":2}".as_slice(), b"{\"a\":{\"b\":true,\"b\":false}}",b"{\"x\":NaN}",b"{\"x\":Infinity}", b"{\"x\":1e9999}",b"[] []", b"\xff", br#""\ud800""#] {
        assert_eq!(codec::parse_json(raw,"invalid_archive"),Err(Error::InvalidArchive));
    }
    let deep = format!("{}0{}","[".repeat(70),"]".repeat(70));
    assert_eq!(codec::parse_json(deep.as_bytes(),"invalid_archive"),Err(Error::InvalidArchive));
    assert_eq!(codec::parse_json(b"broken","not_an_allowed_error"),Err(Error::InvalidPayload));
    assert_eq!(codec::parse_json("{\"имя\":\"значение\"}".as_bytes(),"invalid_payload").unwrap()["имя"],"значение");
}

#[test]
fn strict_json_partial_secret_error_paths_remain_finite() {
    // Exercise RAII exits after secret strings have entered an array/map, while
    // reading a map value, and after the complete value on decoder.end(). These
    // assertions verify behavior; they do not claim a heap-remnant measurement.
    for raw in [br#"["SYNTHETIC-SECRET",{"x":1,"x":2}]"#.as_slice(),
        br#"{"a":"SYNTHETIC-SECRET","b":[0,]}"#, br#"{"SYNTHETIC-KEY": }"#,
        br#"{"a":{"nested":"SYNTHETIC-SECRET"},"a":0}"#,
        br#"{"a":"SYNTHETIC-SECRET"} trailing"#, br#"["SYNTHETIC-SECRET"] {}"#] {
        assert_eq!(codec::parse_json(raw,"invalid_archive"),Err(Error::InvalidArchive));
    }
}

#[test]
fn policy_deny_default_case_sensitive_namespace_and_duplicates() {
    assert_eq!(codec::validate_payload(&payload(), &ArchivePolicy::default()),Err(Error::PolicyViolation));
    for p in [ArchivePolicy {credential_names:vec!["a".into(),"A".into()],..Default::default()},
        ArchivePolicy {allowed_file_paths:vec!["a".into(),"A".into()],..Default::default()}] {
        assert_eq!(p.validate(),Err(Error::PolicyViolation));
    }
    for prefix in ["", "*", "/", "MXMOD", "1AB/", "AB/", "MOD@/"] {
        let p = ArchivePolicy {credential_prefixes:vec![prefix.into()],..Default::default()};
        assert_eq!(p.validate(),Err(Error::PolicyViolation));
    }
    let p=ArchivePolicy {credential_prefixes:vec!["MXMOD/".into()],..Default::default()};
    let mut value=payload(); value["files"]=json!([]);
    value["credentials"][0]["name"]=json!("MXMOD/synthetic"); assert!(codec::validate_payload(&value,&p).is_ok());
    for name in ["MXMOD","MXMOD/","mxmod/synthetic","MXMOD-other","OTHER/synthetic"] {
        value["credentials"][0]["name"]=json!(name); assert_eq!(codec::validate_payload(&value,&p),Err(Error::PolicyViolation));
    }
}

#[test]
fn policy_patterns_are_literal_bounded_positive_uint64() {
    for pattern in [".*",r"auth_(a+)+@MXMOD",r"auth_\d+@MXMOD",r"[1-9][0-9]{0,19}",r"../auth_[1-9][0-9]{0,19}",r"auth.[1-9][0-9]{0,19}"] {
        let p=ArchivePolicy {file_path_patterns:vec![pattern.into()],..Default::default()};
        assert_eq!(p.validate(),Err(Error::PolicyViolation));
    }
    let p=ArchivePolicy {credential_name_patterns:vec![r"synthetic\.auth_[1-9][0-9]{0,19}@MXMOD".into()],
        file_path_patterns:vec![r"auth_[1-9][0-9]{0,19}".into()],..Default::default()};
    for id in ["1","9223372036854775807","18446744073709551615"] {
        let mut value=payload(); value["credentials"][0]["name"]=json!(format!("synthetic.auth_{id}@MXMOD")); value["files"][0]["path"]=json!(format!("auth_{id}"));
        assert!(codec::validate_payload(&value,&p).is_ok());
    }
    for id in ["0","01","-1","18446744073709551616","999999999999999999999"] {
        let mut value=payload(); value["credentials"][0]["name"]=json!(format!("synthetic.auth_{id}@MXMOD"));
        assert_eq!(codec::validate_payload(&value,&p),Err(Error::PolicyViolation));
    }
    let p=ArchivePolicy {credential_name_patterns:vec![r"synthetic\.auth_[1-9][0-9]{0,18}@MXMOD".into()],..Default::default()};
    let mut value=payload(); value["files"]=json!([]); value["credentials"][0]["name"]=json!("syntheticXauth_1@MXMOD");
    assert_eq!(codec::validate_payload(&value,&p),Err(Error::PolicyViolation));
    value["credentials"][0]["name"]=json!("synthetic.auth_18446744073709551615@MXMOD");
    assert_eq!(codec::validate_payload(&value,&p),Err(Error::PolicyViolation));
}

#[test]
fn path_restrictions_and_record_schema_casefold_uniqueness() {
    for path in ["../outside","/absolute","a//b","a\\b","a:b","a%20b","a "," a","a.","NUL","aux.json","COM1.txt","a/LpT9","a|b","a?b","a\"b","a*b","файл"] {
        let mut value=payload(); value["files"][0]["path"]=json!(path); reject_payload(&value,Error::InvalidPayload);
    }
    for field in ["files","credentials"] {
        let mut value=payload(); let item=value[field][0].clone(); value[field].as_array_mut().unwrap().push(item);
        reject_payload(&value,Error::InvalidPayload);
        let mut value=payload(); value[field][0]["extra"]=json!(true); reject_payload(&value,Error::InvalidPayload);
    }
    let mut value=payload(); value["credentials"][0]["name"]=json!("synthetic_Common@MXMOD"); assert!(codec::validate_payload(&value,&policy()).is_ok());
    value["credentials"].as_array_mut().unwrap().push(json!({"name":"SYNTHETIC_COMMON@MXMOD","blob":""})); reject_payload(&value,Error::InvalidPayload);
}

#[test]
fn payload_exact_schema_version_and_types() {
    for (key,value) in [("schema",json!(true)),("schema",json!(1.0)),("credentials",json!({})),("files",json!(null)),("sourceVersion",json!(26351))] {
        let mut p=payload();p[key]=value;reject_payload(&p,Error::InvalidPayload);
    }
    let mut p=payload();p["extra"]=json!(1);reject_payload(&p,Error::InvalidPayload);
    let mut p=payload();p["sourceVersion"]=json!("27.0.0");reject_payload(&p,Error::UnsupportedVersion);
}

#[test]
fn canonical_base64_and_aggregate_capacity() {
    for data in ["?","YQ", "YQ===", "YQ==\n", "YR==", "é"] {
        let mut p=payload();p["credentials"][0]["blob"]=json!(data);reject_payload(&p,Error::InvalidPayload);
    }
    let mut p=payload();p["files"]=json!([]);p["credentials"][0]["blob"]=json!(STANDARD.encode(vec![0;codec::MAX_SECRET_BYTES]));
    assert!(codec::validate_payload(&p,&policy()).is_ok());
    p["files"]=json!([{"path":"auth_7","data":"YQ=="}]);reject_payload(&p,Error::TooLarge);
    let mut p=payload();p["credentials"]=json!(vec![p["credentials"][0].clone();codec::MAX_CREDENTIALS+1]);reject_payload(&p,Error::TooLarge);
    let mut p=payload();p["files"]=json!(vec![p["files"][0].clone();codec::MAX_FILES+1]);reject_payload(&p,Error::TooLarge);
}

#[test]
fn authenticated_malformed_plaintext_rejected_after_crypto_and_receiver_policy_rechecked() {
    let (mut header, _)=split(&archive());
    let salt=STANDARD.decode(header["kdf"]["salt"].as_str().unwrap()).unwrap();
    let params=scrypt::Params::new(15,8,1,32).unwrap();let mut key=[0;32];scrypt::scrypt(PASSWORD.as_bytes(),&salt,&params,&mut key).unwrap();
    let cipher=Aes256Gcm::new_from_slice(&key).unwrap();
    for raw in [b"{\"schema\":1,\"schema\":1}".as_slice(),b"{\"x\":NaN}",b"\xff"] {
        let mut nonce=[0u8;12];getrandom::getrandom(&mut nonce).unwrap();header["nonce"]=json!(STANDARD.encode(nonce));header["plaintextBytes"]=json!(raw.len());
        let aad=frame(&header,&[]);let ciphertext=cipher.encrypt(Nonce::from_slice(&nonce),Payload{msg:raw,aad:&aad}).unwrap();let mut encoded=aad;encoded.extend_from_slice(&ciphertext);
        assert_eq!(codec::decrypt(&encoded,PASSWORD,&policy()),Err(Error::InvalidPayload));
    }
    assert_eq!(codec::decrypt(&archive(),PASSWORD,&ArchivePolicy::default()),Err(Error::PolicyViolation));
}

#[test]
fn serde_policy_unknown_fields_rejected_and_roundtrip_preserves_all_five_fields() {
    assert!(serde_json::from_value::<ArchivePolicy>(json!({"allowAll":true})).is_err());
    let input=fixture()["policy"].clone();let p:ArchivePolicy=serde_json::from_value(input.clone()).unwrap();
    assert_eq!(serde_json::to_value(p).unwrap(),input);
}
