//! Bounded direct-child enumeration of the retained staging directory handle.
//! SDK 10.0.26100.0: FILE_ID_EXTD_DIR_INFO (WinBase.h), classes 19/20
//! (minwinbase.h). No path reopen, descendant traversal, or mutation.
use super::{Directory, Identity, Result, StageError};
use std::{cmp::Ordering, ffi::c_void, os::windows::io::AsRawHandle};

const BUFFER_BYTES: usize = 64 * 1024;
const HEADER_BYTES: usize = 88;
const MAX_CHILDREN: usize = 10_000 + 32_768;
const MAX_ENTRIES: usize = MAX_CHILDREN + 2; // Optional "." and "..".
const FILE_ID_EXTD_DIRECTORY_INFO: u32 = 19;
const FILE_ID_EXTD_DIRECTORY_RESTART_INFO: u32 = 20;
const ERROR_NO_MORE_FILES: u32 = 18;

pub(in super::super) struct Child {
    pub name: String,
    pub directory: bool,
    pub identity: Identity,
}

struct Page {
    children: Vec<Child>,
    entries: usize,
    dots: u8,
}

fn word(bytes: &[u8], offset: usize) -> Result<u32> {
    let value: [u8; 4] = bytes
        .get(offset..offset + 4)
        .ok_or(StageError::ProfileChanged)?
        .try_into()
        .map_err(|_| StageError::ProfileChanged)?;
    Ok(u32::from_le_bytes(value))
}

/// GetFileInformationByHandleEx provides no byte count. A successful call must
/// contain at least one complete record; NextEntryOffset=0 ends this page, not
/// the enumeration. The caller separately requires ERROR_NO_MORE_FILES.
fn parse_page(bytes: &[u8], volume: u64) -> Result<Page> {
    let mut page = Page {
        children: Vec::new(),
        entries: 0,
        dots: 0,
    };
    let mut offset = 0usize;
    loop {
        let record = bytes.get(offset..).ok_or(StageError::ProfileChanged)?;
        if record.len() < HEADER_BYTES {
            return Err(StageError::ProfileChanged);
        }
        let next = word(record, 0)? as usize;
        let attributes = word(record, 56)?;
        let name_bytes = word(record, 60)? as usize;
        if name_bytes == 0 || name_bytes > 510 || name_bytes % 2 != 0 {
            return Err(StageError::ProfileChanged);
        }
        let end = HEADER_BYTES
            .checked_add(name_bytes)
            .ok_or(StageError::ProfileChanged)?;
        if end > record.len()
            || (next != 0 && (next % 8 != 0 || next < end || next >= record.len()))
        {
            return Err(StageError::ProfileChanged);
        }
        if next != 0 && record.len() - next < HEADER_BYTES {
            return Err(StageError::ProfileChanged);
        }
        // ReparsePointTag is undefined without this attribute (per the SDK).
        // DEVICE is not an ordinary staged file/directory type either.
        if attributes & (0x400 | 0x40) != 0 {
            return Err(StageError::UnsafePath);
        }
        let directory = attributes & 0x10 != 0;
        let encoded: Vec<u16> = record[HEADER_BYTES..end]
            .chunks_exact(2)
            .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
            .collect();
        let name = String::from_utf16(&encoded).map_err(|_| StageError::UnsafePath)?;
        page.entries += 1;
        if page.entries > MAX_ENTRIES {
            return Err(StageError::TooLarge);
        }
        let dot = match name.as_str() {
            "." => 1,
            ".." => 2,
            _ => 0,
        };
        if dot != 0 {
            if !directory || page.dots & dot != 0 {
                return Err(StageError::ProfileChanged);
            }
            page.dots |= dot;
        } else {
            if !super::valid_name(&name) {
                return Err(StageError::UnsafePath);
            }
            let id: [u8; 16] = record[72..88]
                .try_into()
                .map_err(|_| StageError::ProfileChanged)?;
            if id == [0; 16] {
                return Err(StageError::ProfileChanged);
            }
            if page.children.len() >= MAX_CHILDREN {
                return Err(StageError::TooLarge);
            }
            page.children.push(Child {
                name,
                directory,
                identity: Identity { volume, id },
            });
        }
        if next == 0 {
            return Ok(page);
        }
        offset = offset.checked_add(next).ok_or(StageError::ProfileChanged)?;
    }
}

/// Fallible merge sort of indices: no comparator panic/fallback ordering and
/// no quadratic scan for Windows-ordinal duplicates across returned pages.
fn verify_unique(children: &[Child]) -> Result<()> {
    let mut order: Vec<usize> = (0..children.len()).collect();
    let mut scratch = vec![0usize; order.len()];
    let mut width = 1usize;
    while width < order.len() {
        for base in (0..order.len()).step_by(width * 2) {
            let middle = (base + width).min(order.len());
            let end = (middle + width).min(order.len());
            let (mut left, mut right) = (base, middle);
            for slot in &mut scratch[base..end] {
                if left < middle
                    && (right == end
                        || super::compare_names(
                            &children[order[left]].name,
                            &children[order[right]].name,
                        )? != Ordering::Greater)
                {
                    *slot = order[left];
                    left += 1;
                } else {
                    *slot = order[right];
                    right += 1;
                }
            }
        }
        std::mem::swap(&mut order, &mut scratch);
        width *= 2;
    }
    for pair in order.windows(2) {
        if super::compare_names(&children[pair[0]].name, &children[pair[1]].name)?
            == Ordering::Equal
        {
            return Err(StageError::ProfileChanged);
        }
    }
    Ok(())
}

fn append_page(
    children: &mut Vec<Child>,
    entries: &mut usize,
    dots: &mut u8,
    page: Page,
) -> Result<()> {
    let total = entries
        .checked_add(page.entries)
        .ok_or(StageError::TooLarge)?;
    if total > MAX_ENTRIES || children.len() + page.children.len() > MAX_CHILDREN {
        return Err(StageError::TooLarge);
    }
    if *dots & page.dots != 0 {
        return Err(StageError::ProfileChanged);
    }
    *entries = total;
    *dots |= page.dots;
    children.extend(page.children);
    Ok(())
}

fn enumeration_finished(error: u32) -> Result<()> {
    if error == ERROR_NO_MORE_FILES {
        Ok(())
    } else {
        Err(StageError::IoError)
    }
}

pub(super) fn inventory(directory: &Directory) -> Result<Vec<Child>> {
    directory.verify()?;
    let identity = directory.identity();
    // u64 storage supplies the 8-byte alignment required for directory records.
    let mut buffer = vec![0u64; BUFFER_BYTES / 8];
    let mut children = Vec::new();
    let (mut entries, mut dots) = (0usize, 0u8);
    let result = (|| {
        for call in 0..=MAX_ENTRIES {
            buffer.fill(0);
            let class = if call == 0 {
                FILE_ID_EXTD_DIRECTORY_RESTART_INFO
            } else {
                FILE_ID_EXTD_DIRECTORY_INFO
            };
            if unsafe {
                super::GetFileInformationByHandleEx(
                    directory.file.as_raw_handle(),
                    class,
                    buffer.as_mut_ptr().cast::<c_void>(),
                    BUFFER_BYTES as u32,
                )
            } == 0
            {
                enumeration_finished(unsafe { super::GetLastError() })?;
                verify_unique(&children)?;
                return Ok(children);
            }
            // Initialized u64 storage is entirely valid as bytes; the parser
            // reads fixed-width fields without unaligned structure casts.
            let bytes =
                unsafe { std::slice::from_raw_parts(buffer.as_ptr().cast::<u8>(), BUFFER_BYTES) };
            append_page(
                &mut children,
                &mut entries,
                &mut dots,
                parse_page(bytes, identity.volume)?,
            )?;
        }
        Err(StageError::TooLarge)
    })();
    directory.verify()?;
    if directory.identity() != identity {
        return Err(StageError::ProfileChanged);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(name: &str, directory: bool, id: u8) -> Vec<u8> {
        let name: Vec<_> = name.encode_utf16().collect();
        let mut data = vec![0u8; HEADER_BYTES + name.len() * 2];
        data[56..60].copy_from_slice(&(if directory { 0x10u32 } else { 0x80 }).to_le_bytes());
        data[60..64].copy_from_slice(&((name.len() * 2) as u32).to_le_bytes());
        data[72..88].fill(id);
        for (slot, value) in data[HEADER_BYTES..].chunks_exact_mut(2).zip(name) {
            slot.copy_from_slice(&value.to_le_bytes());
        }
        data
    }
    fn chain(mut first: Vec<u8>, second: Vec<u8>) -> Vec<u8> {
        let next = (first.len() + 7) & !7;
        first.resize(next, 0);
        first[..4].copy_from_slice(&(next as u32).to_le_bytes());
        first.extend(second);
        first
    }
    fn rejects(data: &[u8], error: StageError) {
        match parse_page(data, 42) {
            Err(actual) => assert_eq!(actual, error),
            Ok(_) => panic!("malformed directory page accepted"),
        }
    }

    #[test]
    fn inventory_parser_layout_types_ids_and_dots() {
        let data = chain(
            entry(".", true, 0),
            chain(
                entry("..", true, 0),
                chain(entry("user42", true, 7), entry("file 😀.db", false, 9)),
            ),
        );
        let page = parse_page(&data, 123).unwrap();
        assert_eq!(page.entries, 4);
        assert_eq!(page.dots, 3);
        assert_eq!(page.children.len(), 2);
        assert!(page.children[0].directory);
        assert!(!page.children[1].directory);
        assert_eq!(page.children[1].name, "file 😀.db");
        assert!(
            page.children[0].identity
                == Identity {
                    volume: 123,
                    id: [7; 16]
                }
        );
        // The otherwise undefined reparse tag must not reject ordinary files.
        let mut ordinary = entry("ordinary", false, 1);
        ordinary[68..72].copy_from_slice(&u32::MAX.to_le_bytes());
        assert!(parse_page(&ordinary, 1).is_ok());
    }

    #[test]
    fn inventory_parser_rejects_bad_lengths_and_truncation() {
        let good = entry("file", false, 1);
        for length in 0..good.len() {
            rejects(&good[..length], StageError::ProfileChanged);
        }
        for length in [0u32, 1, 3, 512, u32::MAX] {
            let mut data = good.clone();
            data[60..64].copy_from_slice(&length.to_le_bytes());
            rejects(&data, StageError::ProfileChanged);
        }
    }

    #[test]
    fn inventory_parser_rejects_bad_next_offsets_and_missing_terminal() {
        let good = chain(entry("a", false, 1), entry("b", true, 2));
        for next in [
            1u32,
            8,
            88,
            91,
            95,
            97,
            (good.len() - 8) as u32,
            good.len() as u32,
            u32::MAX,
        ] {
            let mut data = good.clone();
            data[..4].copy_from_slice(&next.to_le_bytes());
            rejects(&data, StageError::ProfileChanged);
        }
        let mut nonterminal = entry("a", false, 1);
        nonterminal.resize(96, 0);
        nonterminal[..4].copy_from_slice(&96u32.to_le_bytes());
        rejects(&nonterminal, StageError::ProfileChanged);
        rejects(&vec![0; BUFFER_BYTES], StageError::ProfileChanged);
    }

    #[test]
    fn inventory_parser_rejects_unsafe_names_and_object_types() {
        for name in [
            "bad/child",
            ".. ",
            "a:b",
            "nul",
            "bad\0name",
            "a\n",
            "a\\b",
            "bad.",
        ] {
            rejects(&entry(name, false, 1), StageError::UnsafePath);
        }
        let mut invalid_utf16 = entry("x", false, 1);
        invalid_utf16[88..90].copy_from_slice(&0xd800u16.to_le_bytes());
        rejects(&invalid_utf16, StageError::UnsafePath);
        for attributes in [0x400u32, 0x410, 0x40] {
            let mut data = entry("x", false, 1);
            data[56..60].copy_from_slice(&attributes.to_le_bytes());
            rejects(&data, StageError::UnsafePath);
        }
        rejects(&entry(".", false, 1), StageError::ProfileChanged);
        rejects(&entry("x", false, 0), StageError::ProfileChanged);
        rejects(
            &chain(entry(".", true, 0), entry(".", true, 0)),
            StageError::ProfileChanged,
        );
    }

    #[test]
    fn inventory_parser_requires_explicit_eof_and_bounded_pages() {
        assert!(enumeration_finished(18).is_ok());
        for error in [0, 2, 5, 38, 122, 234] {
            assert_eq!(enumeration_finished(error), Err(StageError::IoError));
        }
        let mut children = Vec::new();
        let (mut entries, mut dots) = (MAX_ENTRIES, 0);
        assert_eq!(
            append_page(
                &mut children,
                &mut entries,
                &mut dots,
                parse_page(&entry("x", false, 1), 1).unwrap()
            ),
            Err(StageError::TooLarge)
        );
        assert!(children.is_empty());
        entries = 0;
        append_page(
            &mut children,
            &mut entries,
            &mut dots,
            parse_page(&entry(".", true, 0), 1).unwrap(),
        )
        .unwrap();
        assert_eq!(
            append_page(
                &mut children,
                &mut entries,
                &mut dots,
                parse_page(&entry(".", true, 0), 1).unwrap()
            ),
            Err(StageError::ProfileChanged)
        );
    }

    #[test]
    fn inventory_parser_windows_duplicates_across_pages() {
        let mut children = parse_page(&chain(entry("z", false, 1), entry("Alpha", true, 2)), 1)
            .unwrap()
            .children;
        children.extend(parse_page(&entry("aLPHA", false, 3), 1).unwrap().children);
        assert_eq!(verify_unique(&children), Err(StageError::ProfileChanged));
        let children = parse_page(&chain(entry("ß", false, 1), entry("ss", false, 2)), 1)
            .unwrap()
            .children;
        assert!(verify_unique(&children).is_ok());
        let many: Vec<_> = (0..4096)
            .rev()
            .map(|n| Child {
                name: format!("file{n:05}"),
                directory: false,
                identity: Identity {
                    volume: 1,
                    id: [1; 16],
                },
            })
            .collect();
        assert!(verify_unique(&many).is_ok());
    }
}
