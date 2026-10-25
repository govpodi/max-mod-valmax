//! Lexical decoding of an explicitly selected local Windows file URL.
//! No environment lookup, filesystem access, directory creation or discovery.
//! Callers must subsequently pin and validate the actual selected objects.

use crate::settings_io::SafeStorePath;
use std::{fmt, path::PathBuf};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SelectedPathError;
impl SelectedPathError {
    pub fn code(self) -> &'static str {
        "unsafe_path"
    }
}
impl fmt::Display for SelectedPathError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for SelectedPathError {}

fn decoded(url: &str) -> Result<String, SelectedPathError> {
    if url.len() > 32_768 || url.contains(['?', '#']) || url.bytes().any(|b| b < 32 || b == 127) {
        return Err(SelectedPathError);
    }
    let source = url
        .strip_prefix("file:///")
        .ok_or(SelectedPathError)?
        .as_bytes();
    let mut bytes = Vec::with_capacity(source.len());
    let mut offset = 0;
    while offset < source.len() {
        if source[offset] == b'%' {
            let nibble = |b: u8| (b as char).to_digit(16).map(|v| v as u8);
            if offset + 2 >= source.len() {
                return Err(SelectedPathError);
            }
            bytes.push(
                nibble(source[offset + 1]).ok_or(SelectedPathError)? * 16
                    + nibble(source[offset + 2]).ok_or(SelectedPathError)?,
            );
            offset += 3;
        } else {
            bytes.push(source[offset]);
            offset += 1;
        }
    }
    if bytes.iter().any(|&b| b < 32 || b == 127) {
        return Err(SelectedPathError);
    }
    String::from_utf8(bytes).map_err(|_| SelectedPathError)
}

/// Decode exactly once. Extensions and protected namespaces are caller policy.
/// Authorities, raw queries/fragments, malformed escapes, non-UTF-8, UNC,
/// traversal, ADS, device names and ambiguous Windows components are rejected.
pub fn selected_file_url(url: &str) -> Result<PathBuf, SelectedPathError> {
    let safe = SafeStorePath::new(decoded(url)?).map_err(|_| SelectedPathError)?;
    Ok(safe.path().to_owned())
}

/// A directory selection additionally permits a drive root and one trailing
/// separator. Repeated separators remain invalid. Existence and object type
/// are deliberately left to the caller's guarded filesystem operation.
pub fn selected_directory_url(url: &str) -> Result<PathBuf, SelectedPathError> {
    let mut text = decoded(url)?;
    if text.len() > 3 && text.ends_with(['/', '\\']) {
        text.pop();
        if text.ends_with(['/', '\\']) {
            return Err(SelectedPathError);
        }
    }
    let bytes = text.as_bytes();
    if bytes.len() == 3
        && bytes[0].is_ascii_alphabetic()
        && bytes[1] == b':'
        && matches!(bytes[2], b'/' | b'\\')
    {
        return Ok(PathBuf::from(text.replace('/', "\\")));
    }
    let safe = SafeStorePath::new(text).map_err(|_| SelectedPathError)?;
    Ok(safe.path().to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_once_unicode_and_escaped_filename() {
        assert_eq!(
            selected_file_url("file:///C:/chosen/%D0%90%20%23%25%252e.MDATA").unwrap(),
            PathBuf::from("C:\\chosen\\А #%%2e.MDATA")
        );
        assert_eq!(
            selected_file_url("file:///C:/chosen/А.mdata").unwrap(),
            PathBuf::from("C:\\chosen\\А.mdata")
        );
    }

    #[test]
    fn rejects_nonlocal_and_ambiguous_urls_before_any_io() {
        for url in [
            "",
            "C:/chosen/a.mdata",
            "https://example/a.mdata",
            "file://localhost/C:/a.mdata",
            "file:////server/share/a.mdata",
            "file:///C:/a.mdata?x",
            "file:///C:/a.mdata#x",
            "file:///C:/a%",
            "file:///C:/a%0",
            "file:///C:/a%GG",
            "file:///C:/a%ff.mdata",
            "file:///C:/%00.mdata",
            "file:///C:/a%7f.mdata",
            "file:///C:/a\n.mdata",
            "file:///C:/../a.mdata",
            "file:///C:/%2e%2e/a.mdata",
            "file:///C:/a//b.mdata",
            "file:///C:/a.mdata:stream",
            "file:///C:/NUL.mdata",
            "file:///C:/a./b.mdata",
            "file:///C:/a /b.mdata",
            "file:///C:/a.mdata/",
            "file:///C:/",
        ] {
            assert_eq!(selected_file_url(url), Err(SelectedPathError), "{url}");
        }
        assert_eq!(
            selected_file_url(&format!("file:///C:/{}", "a".repeat(32_768))),
            Err(SelectedPathError)
        );
    }

    #[test]
    fn directory_root_and_single_trailing_separator() {
        for url in ["file:///C:/", "file:///C:/chosen", "file:///C:/chosen/"] {
            assert!(selected_directory_url(url).is_ok(), "{url}");
        }
        assert_eq!(
            selected_directory_url("file:///C:/chosen/"),
            selected_directory_url("file:///C:/chosen")
        );
        for url in [
            "file:///C://",
            "file:///C:/chosen//",
            "file:///C:/chosen/../",
            "file:///C:",
        ] {
            assert_eq!(selected_directory_url(url), Err(SelectedPathError), "{url}");
        }
    }
}
