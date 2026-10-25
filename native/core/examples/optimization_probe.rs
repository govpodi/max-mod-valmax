//! Synthetic interop driver. Never included in an installed package.
use std::{io::{Read,Write},path::PathBuf};
use serde_json::{Value,json};
use valmax_core::optimization_store::OptimizationSettingsStore;
fn main() {
    let mut input=Vec::new();std::io::stdin().take(16385).read_to_end(&mut input).unwrap();assert!(input.len()<=16384);
    let request:Value=serde_json::from_slice(&input).unwrap();
    let root=PathBuf::from(request["root"].as_str().unwrap());
    // The probe is intentionally restricted to an already-created test fixture.
    let actual=root.canonicalize().unwrap();let base=PathBuf::from("C:/ValMAXBuild/tmp").canonicalize().unwrap();
    assert!(actual.starts_with(&base)&&actual!=base);
    assert!(root.file_name().unwrap().to_str().unwrap().starts_with("optimization-interop-"));
    let s=OptimizationSettingsStore::new(actual.join("prefs.json").to_string_lossy().trim_start_matches("\\\\?\\")).unwrap();
    let mut outputs=Vec::new();
    for action in request["actions"].as_array().unwrap() {
        let result=match action["operation"].as_str().unwrap() {
            "snapshot"=>s.snapshot().map(|x|x.value()),
            "replace"=>s.replace(action["revision"].as_str().unwrap(),&action["settings"]).map(|x|x.value()),
            "activate"=>s.activate(root.join("runtime"),action["existingClient"].as_bool().unwrap()).map(|x|x.value()),
            _=>panic!("unsupported synthetic action"),
        };
        outputs.push(match result {Ok(value)=>json!({"ok":true,"value":value}),Err(error)=>json!({"ok":false,"error":error.code()})});
    }
    std::io::stdout().write_all(&serde_json::to_vec(&outputs).unwrap()).unwrap();
}
