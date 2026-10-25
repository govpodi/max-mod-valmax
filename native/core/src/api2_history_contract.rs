//! Inert protocol and account binding rules for a future selected-chat api2 reader.
//!
//! This module opens no connection, reads no credential and writes no message.
//! A caller must first prove the user's selected export job, validate the TLS
//! chain and `api2.oneme.ru` hostname, and decode bounded LZ4/MessagePack.
//! An empty history window alone is deliberately not a full-history EOF proof.

use std::{collections::HashSet, fmt};
use zeroize::Zeroizing;

pub const API2_HOST: &str = "api2.oneme.ru";
pub const VERSION: u8 = 11;
pub const HEADER_BYTES: usize = 10;
pub const MAX_FRAME_BODY: usize = 2 * 1024 * 1024;
pub const HISTORY_OPCODE: u16 = 49;
pub const LOGIN_OPCODE: u16 = 19;
pub const MAX_HISTORY_WINDOW: usize = 200;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error {
    InvalidAccount,
    InvalidChat,
    InvalidRequest,
    InvalidHeader,
    FrameTooLarge,
    UnexpectedCommand,
    ServerError,
    AccountMismatch,
    ScopeMismatch,
    MalformedPage,
}
impl fmt::Display for Error {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        out.write_str(match self {
            Self::InvalidAccount => "invalid_account",
            Self::InvalidChat => "invalid_chat",
            Self::InvalidRequest => "invalid_request",
            Self::InvalidHeader => "invalid_header",
            Self::FrameTooLarge => "frame_too_large",
            Self::UnexpectedCommand => "unexpected_command",
            Self::ServerError => "server_error",
            Self::AccountMismatch => "account_mismatch",
            Self::ScopeMismatch => "scope_mismatch",
            Self::MalformedPage => "malformed_page",
        })
    }
}
impl std::error::Error for Error {}
pub type Result<T> = std::result::Result<T, Error>;

/// Identity copied from the selected native QML model after the user's export
/// action. Construction validates form; it is not proof of that UI action.
pub struct SelectedScope {
    account_id: i64,
    chat_id: i64,
}
impl fmt::Debug for SelectedScope {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        out.write_str("<selected MAX scope>")
    }
}
impl SelectedScope {
    pub fn parse(account_id: &str, chat_id: &str) -> Result<Self> {
        let account = account_id
            .parse::<i64>()
            .map_err(|_| Error::InvalidAccount)?;
        if account <= 0 || account.to_string() != account_id {
            return Err(Error::InvalidAccount);
        }
        let chat = chat_id.parse::<i64>().map_err(|_| Error::InvalidChat)?;
        if chat == 0 || chat.to_string() != chat_id {
            return Err(Error::InvalidChat);
        }
        Ok(Self {
            account_id: account,
            chat_id: chat,
        })
    }

    /// Exact MXMOD target; no credential enumeration or value read occurs here.
    /// The eventual caller must read only after an explicit selected export.
    pub fn credential_target(&self) -> Zeroizing<String> {
        Zeroizing::new(format!("access_token_{}@MXMOD", self.account_id))
    }

    /// Bind a token-authenticated LOGIN response to the selected account.
    /// `profile_id` must come from the decoded `profile.contact.id` of this
    /// correlated response on the verified api2 TLS connection.
    pub fn bind_login(
        self,
        response: FrameHeader,
        login_sequence: u16,
        profile_id: i64,
    ) -> Result<BoundScope> {
        match response.matches_response(login_sequence, LOGIN_OPCODE)? {
            ResponseMatch::Success => {}
            ResponseMatch::ServerError => return Err(Error::ServerError),
            ResponseMatch::Unrelated => return Err(Error::InvalidHeader),
        }
        if profile_id != self.account_id {
            return Err(Error::AccountMismatch);
        }
        Ok(BoundScope {
            account_id: self.account_id,
            chat_id: self.chat_id,
        })
    }
}

/// Scope that passed the LOGIN account ID check. No `Debug` with IDs.
pub struct BoundScope {
    account_id: i64,
    chat_id: i64,
}
impl fmt::Debug for BoundScope {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        out.write_str("<bound MAX scope>")
    }
}
impl BoundScope {
    pub fn account_id(&self) -> i64 {
        self.account_id
    }
    pub fn chat_id(&self) -> i64 {
        self.chat_id
    }
    pub fn history_window(
        &self,
        sequence: u16,
        from_ms: i64,
        backward: usize,
    ) -> Result<HistoryWindow> {
        if sequence == 0 || from_ms < 0 || !(1..=MAX_HISTORY_WINDOW).contains(&backward) {
            return Err(Error::InvalidRequest);
        }
        Ok(HistoryWindow {
            chat_id: self.chat_id,
            sequence,
            from_ms,
            backward,
        })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FrameHeader {
    pub version: u8,
    pub command: u8,
    pub sequence: u16,
    pub opcode: u16,
    pub compression_coefficient: u8,
    pub body_bytes: usize,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ResponseMatch {
    Unrelated,
    Success,
    ServerError,
}
impl FrameHeader {
    /// The final four header bytes are `cof:1,len:3`, not one u32 length.
    /// Callers must read exactly `body_bytes` and cap the decompressed body.
    pub fn parse(bytes: &[u8]) -> Result<Self> {
        if bytes.len() != HEADER_BYTES || bytes[0] != VERSION {
            return Err(Error::InvalidHeader);
        }
        let body_bytes =
            usize::from(bytes[7]) << 16 | usize::from(bytes[8]) << 8 | usize::from(bytes[9]);
        if body_bytes > MAX_FRAME_BODY {
            return Err(Error::FrameTooLarge);
        }
        Ok(Self {
            version: bytes[0],
            command: bytes[1],
            sequence: u16::from_be_bytes([bytes[2], bytes[3]]),
            opcode: u16::from_be_bytes([bytes[4], bytes[5]]),
            compression_coefficient: bytes[6],
            body_bytes,
        })
    }

    pub fn matches_response(self, sequence: u16, opcode: u16) -> Result<ResponseMatch> {
        if self.sequence != sequence || self.opcode != opcode {
            return Ok(ResponseMatch::Unrelated);
        }
        match self.command {
            1 => Ok(ResponseMatch::Success),
            3 => Ok(ResponseMatch::ServerError),
            _ => Err(Error::UnexpectedCommand),
        }
    }
}

/// Build only the v11 envelope around an already encoded MessagePack request.
/// The returned buffer is wiped on drop because LOGIN requests contain a token.
pub fn envelope_request(
    sequence: u16,
    opcode: u16,
    packed_payload: &[u8],
) -> Result<Zeroizing<Vec<u8>>> {
    if sequence == 0 || packed_payload.len() > MAX_FRAME_BODY {
        return Err(Error::InvalidRequest);
    }
    let mut result = Zeroizing::new(Vec::with_capacity(HEADER_BYTES + packed_payload.len()));
    result.extend_from_slice(&[VERSION, 0]);
    result.extend_from_slice(&sequence.to_be_bytes());
    result.extend_from_slice(&opcode.to_be_bytes());
    result.push(0); // Requests are uncompressed until the encoder is verified.
    let length = packed_payload.len();
    result.extend_from_slice(&[(length >> 16) as u8, (length >> 8) as u8, length as u8]);
    result.extend_from_slice(packed_payload);
    Ok(result)
}

/// One selected-chat GET_HISTORY(49) request. IDs are kept as signed 64-bit
/// integers and must be encoded directly to MessagePack, never via JS Number.
pub struct HistoryWindow {
    chat_id: i64,
    sequence: u16,
    from_ms: i64,
    backward: usize,
}
impl fmt::Debug for HistoryWindow {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        out.write_str("<selected MAX history window>")
    }
}
impl HistoryWindow {
    pub fn chat_id(&self) -> i64 {
        self.chat_id
    }
    pub fn sequence(&self) -> u16 {
        self.sequence
    }
    pub fn from_ms(&self) -> i64 {
        self.from_ms
    }
    pub fn backward(&self) -> usize {
        self.backward
    }

    /// Checks a decoded page's identity and bounds. `messages_present` must
    /// distinguish an explicit empty array from a missing/malformed field.
    /// No result of this method alone establishes complete history.
    pub fn validate_page(
        &self,
        response: FrameHeader,
        response_chat_id: Option<i64>,
        messages_present: bool,
        messages: &[MessageStamp<'_>],
    ) -> Result<PageEvidence> {
        match response.matches_response(self.sequence, HISTORY_OPCODE)? {
            ResponseMatch::Success => {}
            ResponseMatch::ServerError => return Err(Error::ServerError),
            ResponseMatch::Unrelated => return Err(Error::InvalidHeader),
        }
        if response_chat_id.is_some_and(|id| id != self.chat_id) {
            return Err(Error::ScopeMismatch);
        }
        if !messages_present || messages.len() > self.backward {
            return Err(Error::MalformedPage);
        }
        if messages.is_empty() {
            return Ok(PageEvidence::EmptyWindow);
        }
        let mut ids = HashSet::with_capacity(messages.len());
        let mut oldest = self.from_ms;
        for message in messages {
            if message.time_ms <= 0
                || message.time_ms > self.from_ms
                || !message.id.is_valid()
                || !ids.insert(message.id)
            {
                return Err(Error::MalformedPage);
            }
            oldest = oldest.min(message.time_ms);
        }
        if oldest == self.from_ms {
            Ok(PageEvidence::NoTimeProgress {
                count: messages.len(),
            })
        } else {
            Ok(PageEvidence::AdvanceCandidate {
                next_from_ms: oldest,
                count: messages.len(),
            })
        }
    }
}

pub struct MessageStamp<'a> {
    pub id: MessageIdentity<'a>,
    pub time_ms: i64,
}

/// MAX messages may carry an integer or an ASCII string ID. This identity is
/// held only during a bounded page check and never formatted in diagnostics.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub enum MessageIdentity<'a> {
    Text(&'a str),
    Unsigned(u64),
    Negative(i64),
}
impl MessageIdentity<'_> {
    fn is_valid(self) -> bool {
        match self {
            Self::Text(value) => {
                !value.is_empty()
                    && value.len() <= 128
                    && value.bytes().all(|byte| (33..=126).contains(&byte))
            }
            Self::Unsigned(value) => value != 0,
            Self::Negative(value) => value < 0,
        }
    }
}

/// Evidence for one window, deliberately without a `Complete` variant.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum PageEvidence {
    EmptyWindow,
    NoTimeProgress { count: usize },
    AdvanceCandidate { next_from_ms: i64, count: usize },
}
