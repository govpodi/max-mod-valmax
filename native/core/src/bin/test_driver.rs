//! Explicit synthetic compatibility driver; never included in the installed product.
use std::io::{self, Read, Write};
use zeroize::Zeroize;

fn main() {
    let mut input = Vec::new();
    if io::stdin().take((valmax_core::ffi::MAX_REQUEST_BYTES + 1) as u64).read_to_end(&mut input).is_err() {
        std::process::exit(2);
    }
    let mut output = valmax_core::ffi::dispatch(&input);
    input.zeroize();
    let result = io::stdout().write_all(&output);
    output.zeroize();
    if result.is_err() { std::process::exit(3); }
}
