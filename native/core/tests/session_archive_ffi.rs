//! Real in-process C ABI boundary; synthetic data only.
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::{ptr, slice};
use valmax_core::{ffi, session_archive};

fn request(value: &Value) -> Value {
    serde_json::from_slice(&ffi::dispatch(&serde_json::to_vec(value).unwrap())).unwrap()
}

#[test]
fn request_parser_rejects_nested_duplicates_unknown_policy_and_operation() {
    for input in [
        br#"{"operation":"version","unexpected":"must reject"}"#.as_slice(),
        br#"{"operation":"version","operation":"version"}"#,
        br#"{"operation":"session.validate","policy":{"credential_names":[],"credential_names":[]},"payload":{}}"#,
        br#"{"operation":"session.validate","policy":{"allowAll":true},"payload":{}}"#,
        br#"{"operation":"not-supported"}"#,
    ] {
        let response:Value=serde_json::from_slice(&ffi::dispatch(input)).unwrap();
        assert_eq!(response,json!({"schema":1,"ok":false,"error":"invalid_request"}));
    }
}

#[test]
fn full_allowed_secret_roundtrip_crosses_six_mib_transport_boundary() {
    // This catches incorrectly applying plaintext's 6 MiB ceiling to the
    // outer base64 request: the v1 archive's wrapped JSON is larger than 6 MiB.
    let payload=json!({"schema":1,"sourceClient":"MAX Desktop","sourceVersion":"26.35.1",
        "credentials":[{"name":"synthetic@MXMOD","blob":STANDARD.encode(vec![0x53;session_archive::MAX_SECRET_BYTES])}],"files":[]});
    let policy=json!({"credential_names":["synthetic@MXMOD"]});
    let encrypted=request(&json!({"operation":"session.encrypt","payload":payload,"password":"Synthetic-Boundary-Password","policy":policy}));
    assert_eq!(encrypted["ok"],true);
    let message=json!({"operation":"session.decrypt","archive":encrypted["result"]["archive"],"password":"Synthetic-Boundary-Password","policy":policy});
    let encoded=serde_json::to_vec(&message).unwrap();assert!(encoded.len()>session_archive::MAX_PLAINTEXT_BYTES);
    let decoded:Value=serde_json::from_slice(&ffi::dispatch(&encoded)).unwrap();
    assert_eq!(decoded["ok"],true);assert_eq!(decoded["result"]["payload"],payload);
}

#[test]
fn exported_abi_allocation_release_and_early_argument_errors() {
    let input=br#"{"operation":"version"}"#;
    let mut result=ffi::CoreBuffer{data:ptr::null_mut(),len:0,capacity:0};
    unsafe {
        assert_eq!(ffi::valmax_core_dispatch(input.as_ptr(),input.len(),ptr::null_mut()),1);
        assert_eq!(ffi::valmax_core_dispatch(ptr::null(),input.len(),&mut result),1);
        assert!(result.data.is_null());
        assert_eq!(ffi::valmax_core_dispatch(input.as_ptr(),0,&mut result),1);
        assert_eq!(ffi::valmax_core_dispatch(input.as_ptr(),ffi::MAX_REQUEST_BYTES+1,&mut result),1);
        assert_eq!(ffi::valmax_core_dispatch(input.as_ptr(),input.len(),&mut result),0);
        let decoded:Value=serde_json::from_slice(slice::from_raw_parts(result.data,result.len)).unwrap();
        assert_eq!(decoded["result"]["abi"],1);assert!(result.capacity>=result.len);
        ffi::valmax_core_buffer_free(&mut result);
        assert!(result.data.is_null());assert_eq!((result.len,result.capacity),(0,0));
        ffi::valmax_core_buffer_free(&mut result);ffi::valmax_core_buffer_free(ptr::null_mut());
    }
}

#[test]
fn finite_error_response_never_contains_input_or_exception_text() {
    let marker="SYNTHETIC-SECRET-MUST-NOT-APPEAR";
    for input in [json!({"operation":"session.encrypt","policy":{},"payload":{},"password":marker}),
        json!({"operation":"session.decrypt","policy":{},"archive":marker,"password":marker})] {
        let output=ffi::dispatch(&serde_json::to_vec(&input).unwrap());
        let value:Value=serde_json::from_slice(&output).unwrap();
        assert_eq!(value["ok"],false);assert_eq!(value.as_object().unwrap().len(),3);
        assert!(!String::from_utf8(output).unwrap().contains(marker));
    }
}
