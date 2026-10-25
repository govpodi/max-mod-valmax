use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use valmax_core::launcher_config::{self as c, ConfigError};

fn bytes(value: &Value) -> Vec<u8> {
    serde_json::to_vec(value).unwrap()
}

#[test]
fn pinned_full_defaults_and_only_eight_overrides() {
    let template = include_bytes!("../../../modkit/config.defaults.json");
    assert_eq!(
        format!("{:x}", Sha256::digest(template)),
        c::TEMPLATE_SHA256
    );
    let original: Value = serde_json::from_slice(template).unwrap();
    let expected = json!({"client_friendly_name":"MAX Mod","app_folder_name":"MXMOD",
        "single_instance_id":"mxmod_instance","local_server_pipe_name":"mxmod_local_pipe",
        "auto_update_enabled":false,"rooms_auto_update_enabled":false,"need_mask_logs":true,
        "send_error_logs_to_crash_server":false});
    let generated = c::make_config().unwrap();
    assert_eq!(generated.as_object().unwrap().len(), 74);
    for (key, value) in original.as_object().unwrap() {
        assert_eq!(&generated[key], expected.get(key).unwrap_or(value));
    }
    assert_eq!(c::validate_config(&bytes(&generated)).unwrap(), generated);
}

#[test]
fn lists_and_returned_values_are_independent() {
    let mut first = c::make_config().unwrap();
    first["locale_replaces_with_ru"]
        .as_array_mut()
        .unwrap()
        .push(json!("SYNTHETIC"));
    assert!(
        !c::make_config().unwrap()["locale_replaces_with_ru"]
            .as_array()
            .unwrap()
            .contains(&json!("SYNTHETIC"))
    );
}

#[test]
fn all_required_keys_and_types_are_checked() {
    let value = c::make_config().unwrap();
    for (key, default) in value.as_object().unwrap() {
        let mut invalid = value.clone();
        invalid.as_object_mut().unwrap().remove(key);
        assert_eq!(
            c::validate_config(&bytes(&invalid)),
            Err(ConfigError::InvalidConfig),
            "missing {key}"
        );
        invalid = value.clone();
        invalid[key] = if default.is_array() {
            json!([123])
        } else if default.is_i64() || default.is_u64() {
            json!(true)
        } else {
            json!(42)
        };
        assert_eq!(
            c::validate_config(&bytes(&invalid)),
            Err(ConfigError::InvalidConfig),
            "type {key}"
        );
    }
    assert_eq!(
        c::validate_config(b"{\"app_folder_name\":\"MXMOD\"}"),
        Err(ConfigError::InvalidConfig)
    );
}

#[test]
fn critical_values_cannot_be_changed() {
    for key in [
        "client_friendly_name",
        "app_folder_name",
        "single_instance_id",
        "local_server_pipe_name",
        "auto_update_enabled",
        "rooms_auto_update_enabled",
        "need_mask_logs",
        "send_error_logs_to_crash_server",
    ] {
        for wrong in [
            Value::Null,
            json!(0),
            json!(1),
            json!("ONEME"),
            json!("other"),
        ] {
            let mut value = c::make_config().unwrap();
            value[key] = wrong;
            assert_eq!(
                c::validate_config(&bytes(&value)),
                Err(ConfigError::InvalidConfig)
            );
        }
        let mut value = c::make_config().unwrap();
        if let Some(current) = value[key].as_bool() {
            value[key] = json!(!current);
            assert_eq!(
                c::validate_config(&bytes(&value)),
                Err(ConfigError::InvalidConfig)
            );
        }
    }
}

#[test]
fn noncritical_values_and_unrelated_keys_are_preserved() {
    let mut value = c::make_config().unwrap();
    value["ping_interval"] = json!(-1);
    value["ssl_port"] = json!(u64::MAX);
    value["locale_replaces_with_ru"] = json!(["Синтетика 🦀", "", "\u{0}"]);
    value["extra"] =
        json!({"object":{"number":0.125},"boolean":true,"null":null,"array":[1,"synthetic"]});
    assert_eq!(c::validate_config(&bytes(&value)).unwrap(), value);
    value["ping_interval"] = json!(1.0);
    assert_eq!(
        c::validate_config(&bytes(&value)),
        Err(ConfigError::InvalidConfig)
    );
}

#[test]
fn bom_limit_duplicates_nonfinite_and_invalid_unicode() {
    let valid = bytes(&c::make_config().unwrap());
    let mut bom = b"\xef\xbb\xbf".to_vec();
    bom.extend_from_slice(&valid);
    assert_eq!(c::validate_config(&bom).unwrap(), c::make_config().unwrap());
    bom.resize(c::MAX_CONFIG_BYTES, b' ');
    assert!(c::validate_config(&bom).is_ok());
    bom.push(b' ');
    assert_eq!(c::validate_config(&bom), Err(ConfigError::TooLarge));
    for bad in [
        b"".as_slice(),
        b"[]",
        b"null",
        b"\xff",
        b"{\"x\":NaN}",
        b"{\"x\":Infinity}",
        b"{\"x\":1e9999}",
        b"{\"x\":\"\\ud800\"}",
        b"\xef\xbb\xbf\xef\xbb\xbf{}",
    ] {
        assert_eq!(c::validate_config(bad), Err(ConfigError::InvalidConfig));
    }
    for extra in [
        ",\"ssl_port\":443}",
        ",\"extra\":{\"key\":1,\"key\":2}}",
        ",\"ssl_\\u0070ort\":443}",
        "} false",
    ] {
        let mut bad = valid[..valid.len() - 1].to_vec();
        bad.extend_from_slice(extra.as_bytes());
        assert_eq!(c::validate_config(&bad), Err(ConfigError::InvalidConfig));
    }
}

#[test]
fn large_integers_are_never_silently_rounded_and_depth_is_bounded() {
    let valid = String::from_utf8(bytes(&c::make_config().unwrap())).unwrap();
    for token in [
        "18446744073709551616",
        "-9223372036854775809",
        "999999999999999999999999999999999999",
    ] {
        let input = format!("{},\"extra\":{token}}}", &valid[..valid.len() - 1]);
        assert_eq!(
            c::validate_config(input.as_bytes()),
            Err(ConfigError::InvalidConfig)
        );
    }
    let input = format!(
        "{},\"extra\":\"18446744073709551616 \\\" -9223372036854775809\"}}",
        &valid[..valid.len() - 1]
    );
    assert!(c::validate_config(input.as_bytes()).is_ok());
    let input = format!(
        "{},\"extra\":{}0{}}}",
        &valid[..valid.len() - 1],
        "[".repeat(65),
        "]".repeat(65)
    );
    assert_eq!(
        c::validate_config(input.as_bytes()),
        Err(ConfigError::InvalidConfig)
    );
}

#[test]
fn integer_negative_zero_preserves_python_type_but_float_zero_does_not() {
    let mut value = c::make_config().unwrap();
    value.as_object_mut().unwrap().remove("ssl_port");
    let data = String::from_utf8(bytes(&value)).unwrap();
    let prefix = &data[..data.len() - 1];
    let parsed = c::validate_config(
        format!("{prefix},\"ssl_port\":-0,\"extra\":[-0,\"-0\",-0.0]}}").as_bytes(),
    )
    .unwrap();
    assert_eq!(parsed["ssl_port"], json!(0));
    assert!(parsed["extra"][0].is_i64());
    assert_eq!(parsed["extra"][1], json!("-0"));
    assert!(parsed["extra"][2].is_f64());
    for token in ["-0.0", "-0e0", "-00"] {
        assert_eq!(
            c::validate_config(format!("{prefix},\"ssl_port\":{token}}}").as_bytes()),
            Err(ConfigError::InvalidConfig)
        );
    }
}

#[test]
fn explicit_private_path_is_lexical_and_never_oneme() {
    for (root, expected) in [
        ("C:/", "C:\\MXMOD\\config.json"),
        (
            "D:/synthetic-root",
            "D:\\synthetic-root\\MXMOD\\config.json",
        ),
        (
            "C:\\Тест 🦀\\Local\\",
            "C:\\Тест 🦀\\Local\\MXMOD\\config.json",
        ),
    ] {
        assert_eq!(
            c::config_path(Path::new(root)).unwrap(),
            PathBuf::from(expected)
        );
    }
    for root in [
        "",
        "relative",
        "C:relative",
        "/root",
        "//server/share",
        "\\\\?\\C:\\fixture",
        "C:/ONEME",
        "C:/fixture/oneme/child",
        "D:/oNeMe",
        "C:/fixture/..",
        "C:/fixture/./child",
        "C:/fixture//child",
        "C:/fixture/name:stream",
        "C:/fixture/trailing.",
        "C:/fixture/trailing ",
        "C:/fixture/NUL",
        "C:/fixture/COM1.txt",
        "C:/fixture/\u{7f}",
        "C:/fixture/\u{0}",
        "C://",
    ] {
        assert_eq!(
            c::config_path(Path::new(root)),
            Err(ConfigError::UnsafePath)
        );
    }
    // A label containing the substring is not the official ONEME directory.
    assert!(c::config_path("C:/synthetic-ONEME-label").is_ok());
}

#[test]
fn errors_are_finite_and_do_not_echo_input() {
    for (error, code) in [
        (ConfigError::TemplateMismatch, "template_mismatch"),
        (ConfigError::InvalidConfig, "invalid_config"),
        (ConfigError::TooLarge, "too_large"),
        (ConfigError::UnsafePath, "unsafe_path"),
    ] {
        assert_eq!(error.code(), code);
        assert_eq!(error.to_string(), code);
    }
    assert_eq!(
        c::validate_config(b"PRIVATE_SYNTHETIC_VALUE")
            .unwrap_err()
            .to_string(),
        "invalid_config"
    );
}
