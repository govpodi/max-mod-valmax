//! Synthetic JSON-lines interoperability harness. No file/profile access or spawn.
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::io::{self, BufRead, Write};
use valmax_core::launcher_config as c;

fn dispatch(input: &Value) -> Value {
    let result = match input["op"].as_str().unwrap_or("") {
        "make" => c::make_config(),
        "validate" => match input["data"].as_str().and_then(|s| STANDARD.decode(s).ok()) {
            Some(bytes) => c::validate_config(&bytes),
            None => Err(c::ConfigError::InvalidConfig),
        },
        "path" => c::config_path(input["root"].as_str().unwrap_or(""))
            .map(|path| json!(path.to_string_lossy())),
        _ => Err(c::ConfigError::InvalidConfig),
    };
    match result {
        Ok(value) => json!({"ok":true,"value":value}),
        Err(error) => json!({"ok":false,"error":error.code()}),
    }
}

fn main() {
    let mut output = io::BufWriter::new(io::stdout().lock());
    for line in io::stdin().lock().lines() {
        let line = line.expect("synthetic stdin");
        if line.len() > 128 * 1024 {
            break;
        }
        let input: Value = serde_json::from_str(&line).expect("synthetic JSON");
        serde_json::to_writer(&mut output, &dispatch(&input)).unwrap();
        output.write_all(b"\n").unwrap();
        output.flush().unwrap();
    }
}
