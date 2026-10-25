use valmax_core::api2_history_contract::{
    BoundScope, Error, FrameHeader, HISTORY_OPCODE, LOGIN_OPCODE, MessageIdentity, MessageStamp,
    PageEvidence, ResponseMatch, SelectedScope, envelope_request,
};

fn header(command: u8, sequence: u16, opcode: u16, coefficient: u8, length: usize) -> FrameHeader {
    FrameHeader::parse(&[
        11,
        command,
        sequence.to_be_bytes()[0],
        sequence.to_be_bytes()[1],
        opcode.to_be_bytes()[0],
        opcode.to_be_bytes()[1],
        coefficient,
        (length >> 16) as u8,
        (length >> 8) as u8,
        length as u8,
    ])
    .unwrap()
}

fn bound_scope() -> BoundScope {
    SelectedScope::parse("9007199254740993", "-9007199254740995")
        .unwrap()
        .bind_login(
            header(1, 2, LOGIN_OPCODE, 2, 38_503),
            2,
            9_007_199_254_740_993,
        )
        .unwrap()
}

#[test]
fn exact_selected_scope_and_login_response_bind_large_ids_without_js_number() {
    let selected = SelectedScope::parse("9007199254740993", "-9007199254740995").unwrap();
    assert_eq!(
        selected.credential_target().as_str(),
        "access_token_9007199254740993@MXMOD"
    );
    assert_eq!(format!("{selected:?}"), "<selected MAX scope>");
    let bound = selected
        .bind_login(
            header(1, 2, LOGIN_OPCODE, 2, 38_503),
            2,
            9_007_199_254_740_993,
        )
        .unwrap();
    assert_eq!(bound.account_id(), 9_007_199_254_740_993);
    assert_eq!(bound.chat_id(), -9_007_199_254_740_995);
    assert_eq!(format!("{bound:?}"), "<bound MAX scope>");
}

#[test]
fn malformed_scope_and_other_account_cannot_bind() {
    for account in [
        "",
        "0",
        "01",
        "+1",
        "-1",
        "1.0",
        "９",
        "9223372036854775808",
    ] {
        assert!(matches!(
            SelectedScope::parse(account, "-5"),
            Err(Error::InvalidAccount)
        ));
    }
    for chat in ["", "0", "-0", "02", "+2", "2.0", "9223372036854775808"] {
        assert!(matches!(
            SelectedScope::parse("1", chat),
            Err(Error::InvalidChat)
        ));
    }
    assert!(SelectedScope::parse("9223372036854775807", "-9223372036854775808").is_ok());
    assert!(matches!(
        SelectedScope::parse("42", "-5").unwrap().bind_login(
            header(1, 2, LOGIN_OPCODE, 2, 40),
            2,
            43
        ),
        Err(Error::AccountMismatch)
    ));
    assert!(matches!(
        SelectedScope::parse("42", "-5").unwrap().bind_login(
            header(1, 3, LOGIN_OPCODE, 2, 40),
            2,
            42
        ),
        Err(Error::InvalidHeader)
    ));
    assert!(matches!(
        SelectedScope::parse("42", "-5").unwrap().bind_login(
            header(3, 2, LOGIN_OPCODE, 0, 10),
            2,
            42
        ),
        Err(Error::ServerError)
    ));
}

#[test]
fn v11_header_has_one_byte_lz4_coefficient_and_three_byte_length() {
    let parsed = header(1, 2, LOGIN_OPCODE, 2, 38_503);
    assert_eq!(parsed.version, 11);
    assert_eq!(parsed.command, 1);
    assert_eq!(parsed.sequence, 2);
    assert_eq!(parsed.opcode, LOGIN_OPCODE);
    assert_eq!(parsed.compression_coefficient, 2);
    assert_eq!(parsed.body_bytes, 38_503);
    assert_eq!(
        parsed.matches_response(2, LOGIN_OPCODE).unwrap(),
        ResponseMatch::Success
    );
    assert_eq!(
        parsed.matches_response(3, LOGIN_OPCODE).unwrap(),
        ResponseMatch::Unrelated
    );
    assert_eq!(
        parsed.matches_response(2, HISTORY_OPCODE).unwrap(),
        ResponseMatch::Unrelated
    );
    assert!(matches!(
        header(2, 2, LOGIN_OPCODE, 0, 5).matches_response(2, LOGIN_OPCODE),
        Err(Error::UnexpectedCommand)
    ));
    assert!(matches!(
        FrameHeader::parse(&[10, 1, 0, 2, 0, 19, 0, 0, 0, 0]),
        Err(Error::InvalidHeader)
    ));
    assert!(matches!(
        FrameHeader::parse(&[11, 1, 0]),
        Err(Error::InvalidHeader)
    ));
    assert!(matches!(
        FrameHeader::parse(&[11, 1, 0, 2, 0, 19, 0, 0x20, 0, 1]),
        Err(Error::FrameTooLarge)
    ));
}

#[test]
fn outgoing_frame_is_bounded_and_kept_in_a_wiping_buffer() {
    let packed = [0x81, 0xa2, b'i', b'd', 0x2a]; // synthetic MessagePack map
    let frame = envelope_request(3, HISTORY_OPCODE, &packed).unwrap();
    assert_eq!(&frame[..10], &[11, 0, 0, 3, 0, 49, 0, 0, 0, 5]);
    assert_eq!(&frame[10..], &packed);
    assert!(matches!(
        envelope_request(0, HISTORY_OPCODE, &packed),
        Err(Error::InvalidRequest)
    ));
    assert!(matches!(
        envelope_request(1, HISTORY_OPCODE, &vec![0; 2 * 1024 * 1024 + 1]),
        Err(Error::InvalidRequest)
    ));
}

#[test]
fn history_window_is_correlated_and_empty_is_only_window_evidence() {
    let bound = bound_scope();
    let window = bound.history_window(3, 1000, 3).unwrap();
    assert_eq!(window.chat_id(), -9_007_199_254_740_995);
    assert_eq!(window.sequence(), 3);
    assert_eq!(window.from_ms(), 1000);
    assert_eq!(window.backward(), 3);
    assert_eq!(format!("{window:?}"), "<selected MAX history window>");
    let response = header(1, 3, HISTORY_OPCODE, 2, 613);
    let stamps = [
        MessageStamp {
            id: MessageIdentity::Text("101"),
            time_ms: 999,
        },
        MessageStamp {
            id: MessageIdentity::Text("102"),
            time_ms: 999,
        },
        MessageStamp {
            id: MessageIdentity::Unsigned(103),
            time_ms: 900,
        },
    ];
    assert!(matches!(
        window.validate_page(response, None, true, &stamps),
        Ok(PageEvidence::AdvanceCandidate {
            next_from_ms: 900,
            count: 3
        })
    ));
    assert!(matches!(
        window.validate_page(response, None, true, &[]),
        Ok(PageEvidence::EmptyWindow)
    ));
    assert!(matches!(
        window.validate_page(response, None, false, &[]),
        Err(Error::MalformedPage)
    ));
    assert!(matches!(
        window.validate_page(header(1, 4, HISTORY_OPCODE, 0, 1), None, true, &[]),
        Err(Error::InvalidHeader)
    ));
    assert!(matches!(
        window.validate_page(header(3, 3, HISTORY_OPCODE, 0, 1), None, true, &[]),
        Err(Error::ServerError)
    ));
    assert!(matches!(
        window.validate_page(response, Some(-6), true, &[]),
        Err(Error::ScopeMismatch)
    ));
}

#[test]
fn no_progress_duplicate_and_out_of_window_rows_never_suggest_an_eof() {
    let bound = bound_scope();
    let window = bound.history_window(4, 900, 2).unwrap();
    let response = header(1, 4, HISTORY_OPCODE, 0, 20);
    assert!(matches!(
        window.validate_page(
            response,
            None,
            true,
            &[MessageStamp {
                id: MessageIdentity::Text("1"),
                time_ms: 900
            },]
        ),
        Ok(PageEvidence::NoTimeProgress { count: 1 })
    ));
    assert!(matches!(
        window.validate_page(
            response,
            None,
            true,
            &[
                MessageStamp {
                    id: MessageIdentity::Text("1"),
                    time_ms: 900
                },
                MessageStamp {
                    id: MessageIdentity::Text("1"),
                    time_ms: 899
                },
            ]
        ),
        Err(Error::MalformedPage)
    ));
    assert!(matches!(
        window.validate_page(
            response,
            None,
            true,
            &[MessageStamp {
                id: MessageIdentity::Unsigned(2),
                time_ms: 901
            },]
        ),
        Err(Error::MalformedPage)
    ));
    assert!(matches!(
        window.validate_page(
            response,
            None,
            true,
            &[
                MessageStamp {
                    id: MessageIdentity::Text("2"),
                    time_ms: 900
                },
                MessageStamp {
                    id: MessageIdentity::Text("3"),
                    time_ms: 899
                },
                MessageStamp {
                    id: MessageIdentity::Text("4"),
                    time_ms: 898
                },
            ]
        ),
        Err(Error::MalformedPage)
    ));
    assert!(matches!(
        bound.history_window(0, 900, 2),
        Err(Error::InvalidRequest)
    ));
    assert!(matches!(
        bound.history_window(1, -1, 2),
        Err(Error::InvalidRequest)
    ));
    assert!(matches!(
        bound.history_window(1, 900, 201),
        Err(Error::InvalidRequest)
    ));
}
