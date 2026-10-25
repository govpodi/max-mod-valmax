//! Bounded read-only MAX v11 transport for a selected-chat history preview.
//!
//! The fixed `api2.oneme.ru` host is authenticated with one embedded official
//! Russian Trusted Root CA. No caller can supply a host, root, or TLS bypass.
//! The transport is crate-private and exposed through the explicit selected
//! chat preview route. The UI checks account/chat rebind; the core separately
//! binds the credential to LOGIN and checks any response chat scope. It never
//! writes a message or a packet to disk or stdout.

use crate::api2_history_contract::{
    API2_HOST, BoundScope, Error as ContractError, FrameHeader, HISTORY_OPCODE, HistoryWindow,
    LOGIN_OPCODE, MessageIdentity, MessageStamp, PageEvidence, ResponseMatch, SelectedScope,
    envelope_request,
};
use crate::login_credentials::LoginCredentials;
use lz4_flex::block::{DecompressError, decompress_into};
use rmpv::{ValueRef, decode::read_value_ref_with_max_depth};
use rustls::{
    ClientConfig, ClientConnection, RootCertStore, Stream, StreamOwned,
    pki_types::{CertificateDer, ServerName},
};
use sha2::{Digest, Sha256};
use std::{
    cell::Cell,
    fmt,
    hash::{Hash, Hasher},
    io::{self, Read, Write},
    net::{TcpStream, ToSocketAddrs},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use zeroize::Zeroizing;

const ROOT_CA_DER: &[u8] = include_bytes!("../certs/russian_trusted_root_ca.der");
const ROOT_CA_SHA256: [u8; 32] = [
    0xd2, 0x6d, 0x2d, 0x02, 0x31, 0xb7, 0xc3, 0x9f, 0x92, 0xcc, 0x73, 0x85, 0x12, 0xba, 0x54, 0x10,
    0x35, 0x19, 0xe4, 0x40, 0x5d, 0x68, 0xb5, 0xbd, 0x70, 0x3e, 0x97, 0x88, 0xca, 0x8e, 0xcf, 0x31,
];
const INIT_OPCODE: u16 = 6;
// Same request shape as the shipped MAX web client; confirmed once against a
// synthetic VIDEO in our own LOGIN-bound DIALOG on the desktop api2 endpoint.
const VIDEO_PLAY_OPCODE: u16 = 83;
const VIDEO_PLAY_DEADLINE: Duration = Duration::from_secs(30);
const MAX_VIDEO_TOKEN_BYTES: usize = 8192;
const MAX_VIDEO_SOURCE_URL_BYTES: usize = 8192;
const PREVIEW_WINDOW: usize = 3;
const MAX_DECOMPRESSED_BODY: usize = 4 * 1024 * 1024;
const MAX_VALUE_NODES: usize = 100_000;
const MAX_VALUE_DEPTH: u8 = 20;
// rmpv charges two units per nested map/array and up to three for a leaf.
// Preflight permits at most 19 ancestors plus one leaf, so 2*20+1 suffices.
// The independent preflight retains the actual 20-level structure limit.
const RMPV_DEPTH_BUDGET: usize = MAX_VALUE_DEPTH as usize * 2 + 1;
const MAX_UNRELATED_FRAMES: usize = 8;
const MAX_WIRE_BYTES_PER_REQUEST: usize = 4 * 1024 * 1024;
const MAX_TOKEN_BYTES: usize = 16 * 1024;
const SOCKET_TIMEOUT: Duration = Duration::from_secs(4);
const PREVIEW_DEADLINE: Duration = Duration::from_secs(35);
const PREVIEW_CLOCK_SKEW_MS: i64 = 10_000;
const HISTORY_WINDOW: usize = 100;
const HISTORY_MAX_WINDOWS: u16 = 1000;
// The 1,000-window ceiling can cover a very large chat. Keep enough time for
// a paced request stream plus bounded network work without extending a single
// page's independent deadline.
const HISTORY_DEADLINE: Duration = Duration::from_secs(900);
// The first api2-next opens and reads in one 60-second UI watchdog interval.
// Keep their combined transport budget below it, leaving room for job/IPC work.
const HISTORY_OPEN_DEADLINE: Duration = Duration::from_secs(20);
const HISTORY_PAGE_DEADLINE: Duration = Duration::from_secs(30);
const HISTORY_WINDOW_SPACING: Duration = Duration::from_millis(500);
const HISTORY_WAIT_SLICE: Duration = Duration::from_millis(25);
const MAX_HISTORY_TEXT_BYTES: usize = 1_048_576;
const MAX_HISTORY_ATTACHMENTS_PER_PAGE: usize = 1000;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum TransportError {
    TrustPin,
    Tls,
    Io,
    Credential,
    InvalidToken,
    Protocol,
    ServerRejected,
    AccountMismatch,
    HistoryScope,
    Clock,
    Cancelled,
    TimedOut,
    LimitReached,
}

/// Preview failures expose only this fixed stage and error category. They do
/// not retain a server body, credential target, token, account, or chat ID.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum PreviewStage {
    Connect,
    Init,
    Credential,
    LoginRequest,
    LoginFrame,
    LoginDecompress,
    LoginPreflight,
    LoginMessagepack,
    LoginTrailing,
    LoginProfile,
    LoginContact,
    LoginId,
    LoginBind,
    History,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct PreviewFailure {
    stage: PreviewStage,
    error: TransportError,
}
impl PreviewFailure {
    pub(crate) fn code(self) -> &'static str {
        use PreviewStage as Stage;
        use TransportError as Error;
        match (self.stage, self.error) {
            (Stage::Connect, Error::TrustPin) => "api2_connect_trust",
            (Stage::Connect, Error::Tls) => "api2_connect_tls",
            (Stage::Connect, Error::TimedOut) => "api2_connect_timeout",
            (Stage::Connect, Error::Cancelled) => "api2_connect_cancelled",
            (Stage::Connect, _) => "api2_connect_io",
            (Stage::Init, Error::ServerRejected) => "api2_init_rejected",
            (Stage::Init, Error::LimitReached) => "api2_init_limit",
            (Stage::Init, Error::Protocol) => "api2_init_protocol",
            (Stage::Init, Error::TimedOut) => "api2_init_timeout",
            (Stage::Init, Error::Cancelled) => "api2_init_cancelled",
            (Stage::Init, _) => "api2_init_io",
            (Stage::Credential, Error::InvalidToken) => "api2_credential_invalid",
            (Stage::Credential, _) => "api2_credential_unavailable",
            (Stage::LoginRequest, Error::InvalidToken) => "api2_login_request_token",
            (Stage::LoginRequest, _) => "api2_login_request_protocol",
            (Stage::LoginFrame, Error::ServerRejected) => "api2_login_frame_rejected",
            (Stage::LoginFrame, Error::LimitReached) => "api2_login_frame_limit",
            (Stage::LoginFrame, Error::Protocol) => "api2_login_frame_protocol",
            (Stage::LoginFrame, Error::TimedOut) => "api2_login_frame_timeout",
            (Stage::LoginFrame, Error::Cancelled) => "api2_login_frame_cancelled",
            (Stage::LoginFrame, _) => "api2_login_frame_io",
            (Stage::LoginDecompress, Error::LimitReached) => "api2_login_decompress_limit",
            (Stage::LoginDecompress, _) => "api2_login_decompress_protocol",
            (Stage::LoginPreflight, Error::LimitReached) => "api2_login_preflight_limit",
            (Stage::LoginPreflight, _) => "api2_login_preflight_protocol",
            (Stage::LoginMessagepack, _) => "api2_login_msgpack_protocol",
            (Stage::LoginTrailing, _) => "api2_login_trailing_protocol",
            (Stage::LoginProfile, _) => "api2_login_profile_missing",
            (Stage::LoginContact, _) => "api2_login_contact_missing",
            (Stage::LoginId, _) => "api2_login_id_invalid",
            (Stage::LoginBind, Error::AccountMismatch) => "api2_login_bind_account",
            (Stage::LoginBind, _) => "api2_login_bind_protocol",
            (Stage::History, Error::ServerRejected) => "api2_history_rejected",
            (Stage::History, Error::HistoryScope) => "api2_history_scope",
            (Stage::History, Error::Clock) => "api2_history_clock",
            (Stage::History, Error::LimitReached) => "api2_history_limit",
            (Stage::History, Error::Protocol) => "api2_history_protocol",
            (Stage::History, Error::TimedOut) => "api2_history_timeout",
            (Stage::History, Error::Cancelled) => "api2_history_cancelled",
            (Stage::History, _) => "api2_history_io",
        }
    }
}
pub(crate) type Result<T> = std::result::Result<T, TransportError>;
type PinnedStream = StreamOwned<ClientConnection, TcpStream>;

/// Cloneable cancellation shared by a reader and its caller. It has no account
/// or chat identity and can be set from another thread during a blocking read.
#[derive(Clone, Default)]
pub(crate) struct HistoryCancel(Arc<AtomicBool>);
impl HistoryCancel {
    pub(crate) fn new() -> Self {
        Self::default()
    }

    pub(crate) fn cancel(&self) {
        self.0.store(true, Ordering::Release);
    }

    pub(crate) fn is_cancelled(&self) -> bool {
        self.0.load(Ordering::Acquire)
    }

    fn flag(&self) -> &AtomicBool {
        self.0.as_ref()
    }
}

/// Owned, wire-typed identity. A text ID is wiped when its page is dropped.
/// No Debug or Serialize implementation may expose message identifiers.
pub(crate) enum HistoryMessageId {
    Text(Zeroizing<String>),
    Unsigned(u64),
    Negative(i64),
}
impl Clone for HistoryMessageId {
    fn clone(&self) -> Self {
        match self {
            Self::Text(value) => Self::Text(Zeroizing::new(value.as_str().to_owned())),
            Self::Unsigned(value) => Self::Unsigned(*value),
            Self::Negative(value) => Self::Negative(*value),
        }
    }
}
impl PartialEq for HistoryMessageId {
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (Self::Text(left), Self::Text(right)) => left.as_str() == right.as_str(),
            (Self::Unsigned(left), Self::Unsigned(right)) => left == right,
            (Self::Negative(left), Self::Negative(right)) => left == right,
            _ => false,
        }
    }
}
impl Eq for HistoryMessageId {}
impl Hash for HistoryMessageId {
    fn hash<H: Hasher>(&self, state: &mut H) {
        match self {
            Self::Text(value) => {
                0_u8.hash(state);
                value.as_str().hash(state);
            }
            Self::Unsigned(value) => {
                1_u8.hash(state);
                value.hash(state);
            }
            Self::Negative(value) => {
                2_u8.hash(state);
                value.hash(state);
            }
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum HistoryMessageKind {
    User,
    Channel,
    Service,
    Other,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum HistoryAttachmentKind {
    Photo,
    Video,
    Audio,
    File,
    Sticker,
    Share,
    Call,
    Location,
    Control,
    InlineKeyboard,
    Other,
}

/// Protected retrieval data is held only by the selected reader/coordinator.
/// No Debug or Serialize implementation may expose it through diagnostics.
#[derive(Clone)]
pub(crate) struct HistoryVideoRef {
    pub(crate) message_id: HistoryMessageId,
    pub(crate) video_id: u64,
    pub(crate) token: Zeroizing<String>,
}

#[derive(Clone)]
pub(crate) enum HistoryMediaSource {
    AudioUrl(Zeroizing<String>),
    PhotoUrl(Zeroizing<String>),
    VideoRef(HistoryVideoRef),
}

/// A page owns only fields needed for a later export adapter. Protected source
/// values remain internal and are never serialized into a public DTO.
pub(crate) struct HistoryMessage {
    pub(crate) id: HistoryMessageId,
    pub(crate) time_ms: i64,
    pub(crate) sender_id: i64,
    pub(crate) kind: HistoryMessageKind,
    pub(crate) text: Zeroizing<String>,
    pub(crate) attachments: Vec<HistoryAttachmentKind>,
    pub(crate) media_sources: Vec<Option<HistoryMediaSource>>,
    pub(crate) rich_content_present: bool,
    pub(crate) unsupported_visible_content: bool,
}

/// `evidence` can suggest a next inclusive timestamp, never full completion.
pub(crate) struct HistoryPage {
    pub(crate) messages: Vec<HistoryMessage>,
    pub(crate) evidence: PageEvidence,
}

impl From<ContractError> for TransportError {
    fn from(value: ContractError) -> Self {
        match value {
            ContractError::ServerError => Self::ServerRejected,
            ContractError::AccountMismatch => Self::AccountMismatch,
            ContractError::ScopeMismatch => Self::HistoryScope,
            ContractError::FrameTooLarge => Self::LimitReached,
            _ => Self::Protocol,
        }
    }
}

/// Only structural diagnostics; these fields contain no account, chat,
/// message ID, timestamp, text, token, or raw network body.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct PreviewEvidence {
    pub pages: u8,
    pub first_count: usize,
    pub second_count: usize,
    pub boundary_overlap_count: usize,
    pub first_advanced_in_time: bool,
    pub second_advanced_in_time: bool,
    pub empty_followup_window: bool,
    pub cursor_field_seen: bool,
    pub has_more_field_seen: bool,
    pub total_field_seen: bool,
}

fn pinned_config() -> Result<Arc<ClientConfig>> {
    if Sha256::digest(ROOT_CA_DER).as_slice() != ROOT_CA_SHA256 {
        return Err(TransportError::TrustPin);
    }
    let mut roots = RootCertStore::empty();
    roots
        .add(CertificateDer::from(ROOT_CA_DER.to_vec()))
        .map_err(|_| TransportError::TrustPin)?;
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let config = ClientConfig::builder_with_provider(provider)
        .with_protocol_versions(&[&rustls::version::TLS13, &rustls::version::TLS12])
        .map_err(|_| TransportError::Tls)?
        .with_root_certificates(roots)
        .with_no_client_auth();
    Ok(Arc::new(config))
}

/// The only network destination. TLS handshake and name validation finish
/// before a caller can pass a token to LOGIN.
fn remaining_budget(cancelled: &AtomicBool, deadline: Instant) -> io::Result<Duration> {
    if cancelled.load(Ordering::Acquire) {
        return Err(io::Error::new(
            io::ErrorKind::Interrupted,
            "preview cancelled",
        ));
    }
    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        return Err(io::Error::new(io::ErrorKind::TimedOut, "preview deadline"));
    }
    Ok(remaining.min(SOCKET_TIMEOUT))
}

fn connect_pinned(cancelled: &AtomicBool, deadline: Instant) -> Result<PinnedStream> {
    if cancelled.load(Ordering::Acquire) {
        return Err(TransportError::Cancelled);
    }
    let config = pinned_config()?;
    let mut socket = None;
    let mut connect_error = TransportError::Io;
    let addresses = (API2_HOST, 443)
        .to_socket_addrs()
        .map_err(|_| TransportError::Io)?;
    for address in addresses.take(3) {
        let timeout = remaining_budget(cancelled, deadline).map_err(classify_stream_io)?;
        match TcpStream::connect_timeout(&address, timeout) {
            Ok(candidate) => {
                socket = Some(candidate);
                break;
            }
            Err(error) => connect_error = classify_stream_io(error),
        }
    }
    let socket = socket.ok_or(connect_error)?;
    remaining_budget(cancelled, deadline).map_err(classify_stream_io)?;
    let server_name =
        ServerName::try_from(API2_HOST.to_owned()).map_err(|_| TransportError::Tls)?;
    let client = ClientConnection::new(config, server_name).map_err(|_| TransportError::Tls)?;
    let mut stream = StreamOwned::new(client, socket);
    for _ in 0..8 {
        if !stream.conn.is_handshaking() {
            return Ok(stream);
        }
        remaining_budget(cancelled, deadline).map_err(classify_stream_io)?;
        let mut socket = DeadlineIo {
            inner: &mut stream.sock,
            cancelled,
            deadline,
        };
        let (read, written) = stream
            .conn
            .complete_io(&mut socket)
            .map_err(classify_stream_io)?;
        if read == 0 && written == 0 {
            return Err(TransportError::Tls);
        }
    }
    if stream.conn.is_handshaking() {
        Err(TransportError::Tls)
    } else {
        Ok(stream)
    }
}

trait CancellationFlag {
    fn flag(&self) -> &AtomicBool;
}

#[derive(Debug)]
struct CancelledIo;
impl fmt::Display for CancelledIo {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        out.write_str("transport cancelled")
    }
}
impl std::error::Error for CancelledIo {}
impl CancellationFlag for &AtomicBool {
    fn flag(&self) -> &AtomicBool {
        self
    }
}
impl CancellationFlag for HistoryCancel {
    fn flag(&self) -> &AtomicBool {
        HistoryCancel::flag(self)
    }
}

trait TimeoutSocket: Read + Write {
    fn set_read_budget(&mut self, timeout: Duration) -> io::Result<()>;
    fn set_write_budget(&mut self, timeout: Duration) -> io::Result<()>;
}
impl TimeoutSocket for TcpStream {
    fn set_read_budget(&mut self, timeout: Duration) -> io::Result<()> {
        self.set_read_timeout(Some(timeout))
    }

    fn set_write_budget(&mut self, timeout: Duration) -> io::Result<()> {
        self.set_write_timeout(Some(timeout))
    }
}

/// rustls can loop over several socket reads inside one Stream::read. Guard
/// each raw operation so a stream of tiny TLS records cannot renew the budget.
struct DeadlineIo<'a, S: TimeoutSocket> {
    inner: &'a mut S,
    cancelled: &'a AtomicBool,
    deadline: Instant,
}
impl<S: TimeoutSocket> DeadlineIo<'_, S> {
    fn budget(&self) -> io::Result<Duration> {
        remaining_budget(self.cancelled, self.deadline).map_err(|error| {
            if error.kind() == io::ErrorKind::Interrupted {
                // rustls retries Interrupted indefinitely inside complete_io.
                io::Error::new(io::ErrorKind::ConnectionAborted, CancelledIo)
            } else {
                error
            }
        })
    }
}
impl<S: TimeoutSocket> Read for DeadlineIo<'_, S> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let budget = self.budget()?;
        self.inner.set_read_budget(budget)?;
        self.inner.read(buf)
    }
}
impl<S: TimeoutSocket> Write for DeadlineIo<'_, S> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let budget = self.budget()?;
        self.inner.set_write_budget(budget)?;
        self.inner.write(buf)
    }

    fn flush(&mut self) -> io::Result<()> {
        let budget = self.budget()?;
        self.inner.set_write_budget(budget)?;
        self.inner.flush()
    }
}

struct TimedPinnedStream<C: CancellationFlag> {
    inner: PinnedStream,
    cancelled: C,
    deadline: Instant,
}
impl<C: CancellationFlag> Read for TimedPinnedStream<C> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let mut socket = DeadlineIo {
            inner: &mut self.inner.sock,
            cancelled: self.cancelled.flag(),
            deadline: self.deadline,
        };
        Stream::new(&mut self.inner.conn, &mut socket).read(buf)
    }
}
impl<C: CancellationFlag> Write for TimedPinnedStream<C> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let mut socket = DeadlineIo {
            inner: &mut self.inner.sock,
            cancelled: self.cancelled.flag(),
            deadline: self.deadline,
        };
        Stream::new(&mut self.inner.conn, &mut socket).write(buf)
    }
    fn flush(&mut self) -> io::Result<()> {
        let mut socket = DeadlineIo {
            inner: &mut self.inner.sock,
            cancelled: self.cancelled.flag(),
            deadline: self.deadline,
        };
        Stream::new(&mut self.inner.conn, &mut socket).flush()
    }
}

struct RawFrame {
    header: FrameHeader,
    body: Zeroizing<Vec<u8>>,
}
fn classify_stream_io(error: io::Error) -> TransportError {
    if error.kind() == io::ErrorKind::Interrupted
        || error
            .get_ref()
            .and_then(|source| source.downcast_ref::<CancelledIo>())
            .is_some()
    {
        TransportError::Cancelled
    } else if matches!(
        error.kind(),
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
    ) {
        TransportError::TimedOut
    } else if error.kind() == io::ErrorKind::InvalidData
        && error
            .get_ref()
            .and_then(|source| source.downcast_ref::<rustls::Error>())
            .is_some()
    {
        TransportError::Tls
    } else {
        TransportError::Io
    }
}

fn read_raw_frame(stream: &mut impl Read) -> Result<RawFrame> {
    let mut header_bytes = [0_u8; 10];
    stream
        .read_exact(&mut header_bytes)
        .map_err(classify_stream_io)?;
    let header = FrameHeader::parse(&header_bytes)?;
    let mut body = Zeroizing::new(vec![0_u8; header.body_bytes]);
    stream.read_exact(&mut body).map_err(classify_stream_io)?;
    Ok(RawFrame { header, body })
}

struct DecodedBody {
    bytes: Zeroizing<Vec<u8>>,
    length: usize,
}
impl DecodedBody {
    fn as_slice(&self) -> &[u8] {
        &self.bytes[..self.length]
    }
}
fn decode_body(raw: RawFrame) -> Result<DecodedBody> {
    if raw.header.compression_coefficient == 0 {
        let length = raw.body.len();
        return Ok(DecodedBody {
            bytes: raw.body,
            length,
        });
    }
    if raw.body.is_empty() {
        return Err(TransportError::Protocol);
    }
    let mut output = Zeroizing::new(vec![0_u8; MAX_DECOMPRESSED_BODY]);
    let length = decompress_into(&raw.body, &mut output).map_err(|error| match error {
        DecompressError::OutputTooSmall { .. } => TransportError::LimitReached,
        _ => TransportError::Protocol,
    })?;
    Ok(DecodedBody {
        bytes: output,
        length,
    })
}

struct Response {
    header: FrameHeader,
    body: DecodedBody,
}
fn invoke_with_progress(
    stream: &mut (impl Read + Write),
    sequence: u16,
    opcode: u16,
    payload: &Zeroizing<Vec<u8>>,
    mut before_decode: impl FnMut(),
) -> Result<Response> {
    let request = envelope_request(sequence, opcode, payload)?;
    stream.write_all(&request).map_err(classify_stream_io)?;
    stream.flush().map_err(classify_stream_io)?;
    let mut received_bytes = 0_usize;
    for _ in 0..MAX_UNRELATED_FRAMES {
        let frame = read_raw_frame(stream)?;
        received_bytes = received_bytes
            .checked_add(frame.body.len() + 10)
            .ok_or(TransportError::Protocol)?;
        if received_bytes > MAX_WIRE_BYTES_PER_REQUEST {
            return Err(TransportError::Protocol);
        }
        match frame.header.matches_response(sequence, opcode)? {
            ResponseMatch::Unrelated => continue,
            ResponseMatch::ServerError => return Err(TransportError::ServerRejected),
            ResponseMatch::Success => {
                let header = frame.header;
                before_decode();
                return Ok(Response {
                    header,
                    body: decode_body(frame)?,
                });
            }
        }
    }
    Err(TransportError::Protocol)
}
fn invoke(
    stream: &mut (impl Read + Write),
    sequence: u16,
    opcode: u16,
    payload: &Zeroizing<Vec<u8>>,
) -> Result<Response> {
    invoke_with_progress(stream, sequence, opcode, payload, || {})
}

fn take_bytes<'a>(input: &'a [u8], position: &mut usize, count: usize) -> Result<&'a [u8]> {
    let end = position
        .checked_add(count)
        .ok_or(TransportError::Protocol)?;
    let value = input.get(*position..end).ok_or(TransportError::Protocol)?;
    *position = end;
    Ok(value)
}
fn read_length(input: &[u8], position: &mut usize, width: usize) -> Result<usize> {
    let mut value = 0_usize;
    for byte in take_bytes(input, position, width)? {
        value = value
            .checked_mul(256)
            .and_then(|part| part.checked_add(usize::from(*byte)))
            .ok_or(TransportError::Protocol)?;
    }
    Ok(value)
}
fn scan_children(
    input: &[u8],
    position: &mut usize,
    depth: u8,
    nodes: &mut usize,
    children: usize,
) -> Result<()> {
    if children > MAX_VALUE_NODES - *nodes || children > input.len() - *position {
        return Err(TransportError::Protocol);
    }
    for _ in 0..children {
        scan_value(input, position, depth + 1, nodes)?;
    }
    Ok(())
}
fn scan_value(input: &[u8], position: &mut usize, depth: u8, nodes: &mut usize) -> Result<()> {
    if depth >= MAX_VALUE_DEPTH || *nodes >= MAX_VALUE_NODES {
        return Err(TransportError::Protocol);
    }
    *nodes += 1;
    let marker = take_bytes(input, position, 1)?[0];
    match marker {
        0x00..=0x7f | 0xc0 | 0xc2 | 0xc3 | 0xe0..=0xff => Ok(()),
        0x80..=0x8f => scan_children(
            input,
            position,
            depth,
            nodes,
            usize::from(marker & 0x0f) * 2,
        ),
        0x90..=0x9f => scan_children(input, position, depth, nodes, usize::from(marker & 0x0f)),
        0xa0..=0xbf => {
            take_bytes(input, position, usize::from(marker & 0x1f))?;
            Ok(())
        }
        0xc4 | 0xd9 => {
            let n = read_length(input, position, 1)?;
            take_bytes(input, position, n)?;
            Ok(())
        }
        0xc5 | 0xda => {
            let n = read_length(input, position, 2)?;
            take_bytes(input, position, n)?;
            Ok(())
        }
        0xc6 | 0xdb => {
            let n = read_length(input, position, 4)?;
            take_bytes(input, position, n)?;
            Ok(())
        }
        0xc7 | 0xc8 | 0xc9 => {
            let width = match marker {
                0xc7 => 1,
                0xc8 => 2,
                _ => 4,
            };
            let n = read_length(input, position, width)?
                .checked_add(1)
                .ok_or(TransportError::Protocol)?;
            take_bytes(input, position, n)?;
            Ok(())
        }
        0xca | 0xce | 0xd2 => {
            take_bytes(input, position, 4)?;
            Ok(())
        }
        0xcb | 0xcf | 0xd3 => {
            take_bytes(input, position, 8)?;
            Ok(())
        }
        0xcc | 0xd0 => {
            take_bytes(input, position, 1)?;
            Ok(())
        }
        0xcd | 0xd1 => {
            take_bytes(input, position, 2)?;
            Ok(())
        }
        0xd4..=0xd8 => {
            let n = (1_usize << (marker - 0xd4)) + 1;
            take_bytes(input, position, n)?;
            Ok(())
        }
        0xdc | 0xdd => {
            let width = if marker == 0xdc { 2 } else { 4 };
            let n = read_length(input, position, width)?;
            scan_children(input, position, depth, nodes, n)
        }
        0xde | 0xdf => {
            let width = if marker == 0xde { 2 } else { 4 };
            let n = read_length(input, position, width)?
                .checked_mul(2)
                .ok_or(TransportError::Protocol)?;
            scan_children(input, position, depth, nodes, n)
        }
        _ => Err(TransportError::Protocol),
    }
}
fn preflight_messagepack(input: &[u8]) -> Result<()> {
    if input.is_empty() || input.len() > MAX_DECOMPRESSED_BODY {
        return Err(TransportError::Protocol);
    }
    let mut position = 0;
    let mut nodes = 0;
    scan_value(input, &mut position, 0, &mut nodes)?;
    if position != input.len() {
        return Err(TransportError::Protocol);
    }
    Ok(())
}

#[derive(Clone, Copy)]
enum DecodePhase {
    Messagepack,
    Trailing,
}
fn read_value_with_progress(
    body: &DecodedBody,
    mut on_phase: impl FnMut(DecodePhase),
) -> Result<ValueRef<'_>> {
    preflight_messagepack(body.as_slice())?;
    on_phase(DecodePhase::Messagepack);
    let mut reader = body.as_slice();
    let value = read_value_ref_with_max_depth(&mut reader, RMPV_DEPTH_BUDGET)
        .map_err(|_| TransportError::Protocol)?;
    on_phase(DecodePhase::Trailing);
    if !reader.is_empty() {
        return Err(TransportError::Protocol);
    }
    Ok(value)
}
fn read_value(body: &DecodedBody) -> Result<ValueRef<'_>> {
    read_value_with_progress(body, |_| {})
}

fn unique_field<'b, 'a>(value: &'b ValueRef<'a>, key: &str) -> Result<Option<&'b ValueRef<'a>>> {
    let ValueRef::Map(entries) = value else {
        return Err(TransportError::Protocol);
    };
    let mut result = None;
    for (name, field) in entries {
        if matches!(name, ValueRef::String(text) if text.as_str() == Some(key)) {
            if result.is_some() {
                return Err(TransportError::Protocol);
            }
            result = Some(field);
        }
    }
    Ok(result)
}
fn required_field<'b, 'a>(value: &'b ValueRef<'a>, key: &str) -> Result<&'b ValueRef<'a>> {
    unique_field(value, key)?.ok_or(TransportError::Protocol)
}
fn signed_i64(value: &ValueRef<'_>) -> Result<i64> {
    match value {
        ValueRef::Integer(number) => number
            .as_i64()
            .or_else(|| number.as_u64().and_then(|id| i64::try_from(id).ok()))
            .ok_or(TransportError::Protocol),
        _ => Err(TransportError::Protocol),
    }
}

fn write_map_header(out: &mut Zeroizing<Vec<u8>>, count: u8) -> Result<()> {
    if count > 15 {
        return Err(TransportError::Protocol);
    }
    out.push(0x80 | count);
    Ok(())
}
fn write_str(out: &mut Zeroizing<Vec<u8>>, value: &str) -> Result<()> {
    let bytes = value.as_bytes();
    match bytes.len() {
        0..=31 => out.push(0xa0 | bytes.len() as u8),
        32..=255 => out.extend_from_slice(&[0xd9, bytes.len() as u8]),
        256..=65_535 => {
            out.push(0xda);
            out.extend_from_slice(&(bytes.len() as u16).to_be_bytes());
        }
        _ => return Err(TransportError::Protocol),
    }
    out.extend_from_slice(bytes);
    Ok(())
}
fn write_bool(out: &mut Zeroizing<Vec<u8>>, value: bool) {
    out.push(if value { 0xc3 } else { 0xc2 });
}
fn write_integer(out: &mut Zeroizing<Vec<u8>>, value: i64) {
    if (0..=127).contains(&value) {
        out.push(value as u8);
    } else if (-32..=-1).contains(&value) {
        out.push(value as i8 as u8);
    } else if (0..=u8::MAX as i64).contains(&value) {
        out.extend_from_slice(&[0xcc, value as u8]);
    } else if (0..=u16::MAX as i64).contains(&value) {
        out.push(0xcd);
        out.extend_from_slice(&(value as u16).to_be_bytes());
    } else if (0..=u32::MAX as i64).contains(&value) {
        out.push(0xce);
        out.extend_from_slice(&(value as u32).to_be_bytes());
    } else if value >= 0 {
        out.push(0xcf);
        out.extend_from_slice(&(value as u64).to_be_bytes());
    } else {
        out.push(0xd3);
        out.extend_from_slice(&value.to_be_bytes());
    }
}
fn write_unsigned(out: &mut Zeroizing<Vec<u8>>, value: u64) {
    if let Ok(signed) = i64::try_from(value) {
        write_integer(out, signed);
    } else {
        out.push(0xcf);
        out.extend_from_slice(&value.to_be_bytes());
    }
}
fn field_str(out: &mut Zeroizing<Vec<u8>>, key: &str, value: &str) -> Result<()> {
    write_str(out, key)?;
    write_str(out, value)
}
fn field_bool(out: &mut Zeroizing<Vec<u8>>, key: &str, value: bool) -> Result<()> {
    write_str(out, key)?;
    write_bool(out, value);
    Ok(())
}
fn field_int(out: &mut Zeroizing<Vec<u8>>, key: &str, value: i64) -> Result<()> {
    write_str(out, key)?;
    write_integer(out, value);
    Ok(())
}
fn field_uint(out: &mut Zeroizing<Vec<u8>>, key: &str, value: u64) -> Result<()> {
    write_str(out, key)?;
    write_unsigned(out, value);
    Ok(())
}

fn new_device_id() -> Result<String> {
    let mut random = [0_u8; 16];
    getrandom::getrandom(&mut random).map_err(|_| TransportError::Io)?;
    random[6] = (random[6] & 0x0f) | 0x40;
    random[8] = (random[8] & 0x3f) | 0x80;
    let hex = b"0123456789abcdef";
    let mut id = String::with_capacity(36);
    for (index, byte) in random.into_iter().enumerate() {
        if matches!(index, 4 | 6 | 8 | 10) {
            id.push('-');
        }
        id.push(hex[(byte >> 4) as usize] as char);
        id.push(hex[(byte & 0x0f) as usize] as char);
    }
    Ok(id)
}

fn pack_init() -> Result<Zeroizing<Vec<u8>>> {
    let mut out = Zeroizing::new(Vec::with_capacity(256));
    write_map_header(&mut out, 2)?;
    write_str(&mut out, "userAgent")?;
    write_map_header(&mut out, 7)?;
    field_str(&mut out, "deviceType", "DESKTOP")?;
    field_str(&mut out, "locale", "ru")?;
    field_str(&mut out, "deviceLocale", "ru")?;
    field_str(&mut out, "osVersion", "Windows 10")?;
    field_str(&mut out, "deviceName", "ValMAX diagnostics")?;
    field_str(&mut out, "appVersion", "26.35.1")?;
    field_str(&mut out, "timezone", "Europe/Moscow")?;
    field_str(&mut out, "deviceId", &new_device_id()?)?;
    Ok(out)
}
fn pack_login(token: &[u8]) -> Result<Zeroizing<Vec<u8>>> {
    if token.is_empty() || token.len() > MAX_TOKEN_BYTES {
        return Err(TransportError::InvalidToken);
    }
    let token = std::str::from_utf8(token).map_err(|_| TransportError::InvalidToken)?;
    let mut out = Zeroizing::new(Vec::with_capacity(token.len() + 128));
    write_map_header(&mut out, 7)?;
    field_bool(&mut out, "interactive", false)?;
    field_int(&mut out, "chatsCount", 0)?;
    field_int(&mut out, "chatsSync", 0)?;
    field_int(&mut out, "contactsSync", 0)?;
    field_int(&mut out, "presenceSync", 0)?;
    field_int(&mut out, "draftsSync", 0)?;
    // Put the secret last: any allocation growth before this point can only
    // leave nonsecret fields in a retired Vec allocation.
    field_str(&mut out, "token", token)?;
    Ok(out)
}
fn pack_history(window: &HistoryWindow) -> Result<Zeroizing<Vec<u8>>> {
    let mut out = Zeroizing::new(Vec::with_capacity(180));
    write_map_header(&mut out, 9)?;
    field_int(&mut out, "chatId", window.chat_id())?;
    field_int(&mut out, "forward", 0)?;
    field_int(&mut out, "backward", window.backward() as i64)?;
    field_int(&mut out, "backwardTime", 0)?;
    field_int(&mut out, "forwardTime", 0)?;
    field_bool(&mut out, "getChat", false)?;
    field_int(&mut out, "from", window.from_ms())?;
    field_bool(&mut out, "getMessages", true)?;
    field_bool(&mut out, "interactive", false)?;
    Ok(out)
}

fn pack_video_play(chat_id: i64, source: &HistoryVideoRef) -> Result<Zeroizing<Vec<u8>>> {
    if source.video_id == 0 || source.token.len() > MAX_VIDEO_TOKEN_BYTES {
        return Err(TransportError::Protocol);
    }
    let mut out = Zeroizing::new(Vec::with_capacity(source.token.len() + 160));
    write_map_header(&mut out, 4)?;
    field_uint(&mut out, "videoId", source.video_id)?;
    field_str(&mut out, "token", &source.token)?;
    field_int(&mut out, "chatId", chat_id)?;
    write_str(&mut out, "messageId")?;
    match &source.message_id {
        HistoryMessageId::Text(value) => write_str(&mut out, value)?,
        HistoryMessageId::Unsigned(value) => write_unsigned(&mut out, *value),
        HistoryMessageId::Negative(value) => write_integer(&mut out, *value),
    }
    Ok(out)
}

fn init(stream: &mut (impl Read + Write)) -> Result<()> {
    let response = invoke(stream, 1, INIT_OPCODE, &pack_init()?)?;
    if response.header.command != 1 {
        return Err(TransportError::Protocol);
    }
    Ok(())
}

struct Session<S> {
    stream: S,
    scope: BoundScope,
}
fn bind_session_with_stage<S: Read + Write, C: LoginCredentials>(
    mut stream: S,
    selected: SelectedScope,
    credentials: &C,
    mut on_stage: impl FnMut(PreviewStage),
) -> Result<Session<S>> {
    on_stage(PreviewStage::Init);
    init(&mut stream)?;
    on_stage(PreviewStage::Credential);
    let target = selected.credential_target();
    let token = credentials
        .read(&target)
        .map_err(|_| TransportError::Credential)?
        .ok_or(TransportError::Credential)?;
    on_stage(PreviewStage::LoginRequest);
    let login = pack_login(&token)?;
    on_stage(PreviewStage::LoginFrame);
    let response = invoke_with_progress(&mut stream, 2, LOGIN_OPCODE, &login, || {
        on_stage(PreviewStage::LoginDecompress)
    })?;
    on_stage(PreviewStage::LoginPreflight);
    let value = read_value_with_progress(&response.body, |phase| {
        on_stage(match phase {
            DecodePhase::Messagepack => PreviewStage::LoginMessagepack,
            DecodePhase::Trailing => PreviewStage::LoginTrailing,
        })
    })?;
    on_stage(PreviewStage::LoginProfile);
    let profile = required_field(&value, "profile")?;
    on_stage(PreviewStage::LoginContact);
    let contact = required_field(profile, "contact")?;
    on_stage(PreviewStage::LoginId);
    let profile_id = signed_i64(required_field(contact, "id")?)?;
    on_stage(PreviewStage::LoginBind);
    let scope = selected.bind_login(response.header, 2, profile_id)?;
    Ok(Session { stream, scope })
}
fn bind_session<S: Read + Write, C: LoginCredentials>(
    stream: S,
    selected: SelectedScope,
    credentials: &C,
) -> Result<Session<S>> {
    bind_session_with_stage(stream, selected, credentials, |_| {})
}

struct PageEvidenceInternal {
    count: usize,
    oldest_time: Option<i64>,
    ids: Zeroizing<Vec<[u8; 32]>>,
    cursor_field: bool,
    has_more_field: bool,
    total_field: bool,
}
fn message_id<'a>(value: &'a ValueRef<'a>) -> Result<MessageIdentity<'a>> {
    match value {
        ValueRef::String(text) => text
            .as_str()
            .map(MessageIdentity::Text)
            .ok_or(TransportError::Protocol),
        ValueRef::Integer(id) => id
            .as_u64()
            .map(MessageIdentity::Unsigned)
            .or_else(|| id.as_i64().map(MessageIdentity::Negative))
            .ok_or(TransportError::Protocol),
        _ => Err(TransportError::Protocol),
    }
}
fn fingerprint(salt: &[u8; 32], id: MessageIdentity<'_>) -> [u8; 32] {
    let mut hash = Sha256::new();
    hash.update(salt);
    match id {
        MessageIdentity::Text(text) => {
            hash.update(b"T");
            hash.update(text.as_bytes());
        }
        MessageIdentity::Unsigned(value) => {
            hash.update(b"U");
            hash.update(value.to_be_bytes());
        }
        MessageIdentity::Negative(value) => {
            hash.update(b"N");
            hash.update(value.to_be_bytes());
        }
    }
    hash.finalize().into()
}
fn parse_history_page(
    window: &HistoryWindow,
    response: &Response,
    salt: &[u8; 32],
) -> Result<PageEvidenceInternal> {
    let value = read_value(&response.body)?;
    let chat_id = unique_field(&value, "chatId")?
        .map(signed_i64)
        .transpose()?;
    let messages = required_field(&value, "messages")?
        .as_array()
        .ok_or(TransportError::Protocol)?;
    if messages.len() > window.backward() {
        return Err(TransportError::Protocol);
    }
    let mut stamps = Vec::with_capacity(messages.len());
    for message in messages {
        if let Some(row_chat_id) = unique_field(message, "chatId")? {
            if signed_i64(row_chat_id)? != window.chat_id() {
                return Err(TransportError::HistoryScope);
            }
        }
        stamps.push(MessageStamp {
            id: message_id(required_field(message, "id")?)?,
            time_ms: signed_i64(required_field(message, "time")?)?,
        });
    }
    let validated = window.validate_page(response.header, chat_id, true, &stamps)?;
    let oldest_time = match validated {
        PageEvidence::AdvanceCandidate { next_from_ms, .. } => Some(next_from_ms),
        PageEvidence::NoTimeProgress { .. } | PageEvidence::EmptyWindow => None,
    };
    let ids = Zeroizing::new(
        stamps
            .iter()
            .map(|item| fingerprint(salt, item.id))
            .collect(),
    );
    Ok(PageEvidenceInternal {
        count: stamps.len(),
        oldest_time,
        ids,
        cursor_field: unique_field(&value, "cursor")?.is_some()
            || unique_field(&value, "next")?.is_some(),
        has_more_field: unique_field(&value, "hasMore")?.is_some(),
        total_field: unique_field(&value, "total")?.is_some(),
    })
}

fn owned_message_id(id: MessageIdentity<'_>) -> HistoryMessageId {
    match id {
        MessageIdentity::Text(text) => HistoryMessageId::Text(Zeroizing::new(text.to_owned())),
        MessageIdentity::Unsigned(value) => HistoryMessageId::Unsigned(value),
        MessageIdentity::Negative(value) => HistoryMessageId::Negative(value),
    }
}

fn message_kind(value: &ValueRef<'_>) -> Result<HistoryMessageKind> {
    let ValueRef::String(text) = value else {
        return Err(TransportError::Protocol);
    };
    let text = text.as_str().ok_or(TransportError::Protocol)?;
    Ok(match text {
        "USER" => HistoryMessageKind::User,
        "CHANNEL" => HistoryMessageKind::Channel,
        "SERVICE" => HistoryMessageKind::Service,
        _ => HistoryMessageKind::Other,
    })
}

fn attachment_kind(value: &ValueRef<'_>) -> Result<HistoryAttachmentKind> {
    let primary = unique_field(value, "_type")?;
    let fallback = unique_field(value, "type")?;
    let selected = match (primary, fallback) {
        (Some(_), Some(_)) => return Err(TransportError::Protocol),
        (Some(value), None) | (None, Some(value)) => value,
        (None, None) => return Ok(HistoryAttachmentKind::Other),
    };
    let ValueRef::String(text) = selected else {
        return Ok(HistoryAttachmentKind::Other);
    };
    Ok(match text.as_str() {
        Some("PHOTO") => HistoryAttachmentKind::Photo,
        Some("VIDEO") => HistoryAttachmentKind::Video,
        Some("AUDIO") => HistoryAttachmentKind::Audio,
        Some("FILE") => HistoryAttachmentKind::File,
        Some("STICKER") => HistoryAttachmentKind::Sticker,
        Some("SHARE") => HistoryAttachmentKind::Share,
        Some("CALL") => HistoryAttachmentKind::Call,
        Some("LOCATION") => HistoryAttachmentKind::Location,
        Some("CONTROL") => HistoryAttachmentKind::Control,
        Some("INLINE_KEYBOARD") => HistoryAttachmentKind::InlineKeyboard,
        _ => HistoryAttachmentKind::Other,
    })
}

fn attachment_media_source(
    value: &ValueRef<'_>,
    kind: HistoryAttachmentKind,
    message_id: &HistoryMessageId,
) -> Option<HistoryMediaSource> {
    if kind == HistoryAttachmentKind::Video {
        let ValueRef::Integer(number) = unique_field(value, "videoId").ok()?? else {
            return None;
        };
        let video_id = number.as_u64().filter(|id| *id > 0)?;
        let token = match unique_field(value, "token").ok()? {
            Some(ValueRef::String(value)) => value.as_str()?,
            None => "",
            _ => return None,
        };
        if token.len() > MAX_VIDEO_TOKEN_BYTES
            || !token.bytes().all(|byte| (0x21..=0x7e).contains(&byte))
            || matches!(message_id, HistoryMessageId::Text(value)
                if value.len() > 128 || !value.bytes().all(|byte| (0x21..=0x7e).contains(&byte)))
        {
            return None;
        }
        return Some(HistoryMediaSource::VideoRef(HistoryVideoRef {
            message_id: message_id.clone(),
            video_id,
            token: Zeroizing::new(token.to_owned()),
        }));
    }
    let field = match kind {
        HistoryAttachmentKind::Audio => "url",
        HistoryAttachmentKind::Photo => "baseUrl",
        _ => return None,
    };
    let ValueRef::String(url) = unique_field(value, field).ok()?? else {
        return None;
    };
    let raw = url.as_str()?;
    match kind {
        HistoryAttachmentKind::Audio if crate::api2_media_download::eligible_audio_url(raw) => {
            Some(HistoryMediaSource::AudioUrl(Zeroizing::new(raw.to_owned())))
        }
        HistoryAttachmentKind::Photo if crate::api2_media_download::eligible_photo_url(raw) => {
            Some(HistoryMediaSource::PhotoUrl(Zeroizing::new(raw.to_owned())))
        }
        _ => None,
    }
}

fn structurally_nonempty(value: &ValueRef<'_>) -> bool {
    match value {
        ValueRef::Nil => false,
        ValueRef::Boolean(value) => *value,
        ValueRef::String(value) => value.as_str().is_none_or(|text| !text.is_empty()),
        ValueRef::Binary(value) => !value.is_empty(),
        ValueRef::Array(value) => !value.is_empty(),
        ValueRef::Map(value) => !value.is_empty(),
        _ => true,
    }
}

fn unknown_visible_fields(message: &ValueRef<'_>) -> Result<bool> {
    let ValueRef::Map(entries) = message else {
        return Err(TransportError::Protocol);
    };
    for (key, value) in entries {
        let ValueRef::String(key) = key else {
            return Ok(true);
        };
        let Some(key) = key.as_str() else {
            return Ok(true);
        };
        if !matches!(
            key,
            "id" | "time"
                | "sender"
                | "type"
                | "text"
                | "attaches"
                | "attachments"
                | "chatId"
                | "cid"
                | "status"
                | "updateTime"
                | "elements"
                | "link"
                | "options"
        ) && structurally_nonempty(value)
        {
            return Ok(true);
        }
    }
    Ok(false)
}

/// Parse one correlated page into owned, bounded DTOs. `cid` is deliberately
/// ignored: live evidence showed it differs from the selected DIALOG chat ID.
fn parse_history_data_page(window: &HistoryWindow, response: &Response) -> Result<HistoryPage> {
    let value = read_value(&response.body)?;
    let response_chat_id = unique_field(&value, "chatId")?
        .map(signed_i64)
        .transpose()?;
    let messages = required_field(&value, "messages")?
        .as_array()
        .ok_or(TransportError::Protocol)?;
    if messages.len() > HISTORY_WINDOW {
        return Err(TransportError::Protocol);
    }
    let mut stamps = Vec::with_capacity(messages.len());
    for message in messages {
        if let Some(row_chat_id) = unique_field(message, "chatId")? {
            if signed_i64(row_chat_id)? != window.chat_id() {
                return Err(TransportError::HistoryScope);
            }
        }
        stamps.push(MessageStamp {
            id: message_id(required_field(message, "id")?)?,
            time_ms: signed_i64(required_field(message, "time")?)?,
        });
    }
    let evidence = window.validate_page(response.header, response_chat_id, true, &stamps)?;
    let mut rows = Vec::with_capacity(messages.len());
    let mut attachment_count = 0_usize;
    for (message, stamp) in messages.iter().zip(stamps) {
        let owned_id = owned_message_id(stamp.id);
        let sender_id = signed_i64(required_field(message, "sender")?)?;
        let kind = message_kind(required_field(message, "type")?)?;
        let ValueRef::String(text) = required_field(message, "text")? else {
            return Err(TransportError::Protocol);
        };
        let text = text.as_str().ok_or(TransportError::Protocol)?;
        if text.len() > MAX_HISTORY_TEXT_BYTES {
            return Err(TransportError::Protocol);
        }
        let primary_attaches = unique_field(message, "attaches")?;
        let fallback_attaches = unique_field(message, "attachments")?;
        let attached = match (primary_attaches, fallback_attaches) {
            (Some(_), Some(_)) => return Err(TransportError::Protocol),
            (Some(value), None) | (None, Some(value)) => match value {
                ValueRef::Nil => None,
                ValueRef::Array(items) => Some(items.as_slice()),
                _ => return Err(TransportError::Protocol),
            },
            (None, None) => None,
        };
        let mut attachments = Vec::new();
        let mut media_sources = Vec::new();
        if let Some(attached) = attached {
            attachment_count = attachment_count
                .checked_add(attached.len())
                .ok_or(TransportError::Protocol)?;
            if attachment_count > MAX_HISTORY_ATTACHMENTS_PER_PAGE {
                return Err(TransportError::Protocol);
            }
            attachments.reserve(attached.len());
            media_sources.reserve(attached.len());
            for attachment in attached {
                let kind = attachment_kind(attachment)?;
                media_sources.push(attachment_media_source(attachment, kind, &owned_id));
                attachments.push(kind);
            }
        }
        let mut rich_content_present = false;
        for name in ["elements", "link", "options"] {
            rich_content_present |= unique_field(message, name)?.is_some_and(structurally_nonempty);
        }
        let unsupported_visible_content = rich_content_present
            || kind == HistoryMessageKind::Other
            || attachments.contains(&HistoryAttachmentKind::Other)
            || unknown_visible_fields(message)?;
        rows.push(HistoryMessage {
            id: owned_id,
            time_ms: stamp.time_ms,
            sender_id,
            kind,
            text: Zeroizing::new(text.to_owned()),
            attachments,
            media_sources,
            rich_content_present,
            unsupported_visible_content,
        });
    }
    Ok(HistoryPage {
        messages: rows,
        evidence,
    })
}

fn response_message_id_matches(value: &ValueRef<'_>, expected: &HistoryMessageId) -> bool {
    match (value, expected) {
        (ValueRef::String(value), HistoryMessageId::Text(expected)) => {
            value.as_str() == Some(expected.as_str())
        }
        (ValueRef::Integer(value), HistoryMessageId::Unsigned(expected)) => {
            value.as_u64() == Some(*expected)
        }
        (ValueRef::Integer(value), HistoryMessageId::Negative(expected)) => {
            value.as_i64() == Some(*expected)
        }
        _ => false,
    }
}

/// VIDEO_PLAY(83) has no per-asset echo in the observed own-account response.
/// The single in-flight request is tied to its GET_HISTORY attachment through
/// the same LOGIN-bound session and the exact sequence/opcode reply envelope.
fn parse_video_sources(
    scope: &BoundScope,
    source: &HistoryVideoRef,
    response: &Response,
) -> Result<Vec<Zeroizing<String>>> {
    let value = read_value(&response.body)?;
    let ValueRef::Map(fields) = &value else {
        return Err(TransportError::Protocol);
    };
    if unique_field(&value, "chatId")?
        .is_some_and(|echo| signed_i64(echo).ok() != Some(scope.chat_id()))
        || unique_field(&value, "messageId")?
            .is_some_and(|echo| !response_message_id_matches(echo, &source.message_id))
        || unique_field(&value, "videoId")?.is_some_and(
            |echo| !matches!(echo, ValueRef::Integer(id) if id.as_u64() == Some(source.video_id)),
        )
    {
        return Err(TransportError::HistoryScope);
    }
    let mut sources = Vec::<(u32, Zeroizing<String>)>::new();
    for (key, item) in fields {
        let ValueRef::String(key) = key else {
            return Err(TransportError::Protocol);
        };
        let Some(key) = key.as_str() else {
            return Err(TransportError::Protocol);
        };
        let Some(height) = key.strip_prefix("MP4_") else {
            continue;
        };
        if !(2..=5).contains(&height.len())
            || !height.bytes().all(|byte| byte.is_ascii_digit())
            || sources.len() >= 16
        {
            return Err(TransportError::Protocol);
        }
        let height = height
            .parse::<u32>()
            .map_err(|_| TransportError::Protocol)?;
        if height == 0 || sources.iter().any(|(seen, _)| *seen == height) {
            return Err(TransportError::Protocol);
        }
        let ValueRef::String(url) = item else {
            return Err(TransportError::Protocol);
        };
        let url = url.as_str().ok_or(TransportError::Protocol)?;
        if url.len() > MAX_VIDEO_SOURCE_URL_BYTES
            || !crate::api2_media_download::eligible_video_url(url)
        {
            return Err(TransportError::Protocol);
        }
        sources.push((height, Zeroizing::new(url.to_owned())));
    }
    if sources.is_empty() {
        return Err(TransportError::ServerRejected);
    }
    sources.sort_unstable_by(|left, right| right.0.cmp(&left.0));
    Ok(sources.into_iter().map(|(_, url)| url).collect())
}

impl<S: Read + Write> Session<S> {
    fn video_sources(
        &mut self,
        sequence: u16,
        source: &HistoryVideoRef,
    ) -> Result<Vec<Zeroizing<String>>> {
        let response = invoke(
            &mut self.stream,
            sequence,
            VIDEO_PLAY_OPCODE,
            &pack_video_play(self.scope.chat_id(), source)?,
        )?;
        parse_video_sources(&self.scope, source, &response)
    }

    fn data_window(&mut self, sequence: u16, from_ms: i64) -> Result<HistoryPage> {
        let window = self
            .scope
            .history_window(sequence, from_ms, HISTORY_WINDOW)?;
        let response = invoke(
            &mut self.stream,
            sequence,
            HISTORY_OPCODE,
            &pack_history(&window)?,
        )?;
        parse_history_data_page(&window, &response)
    }

    fn history_window(
        &mut self,
        sequence: u16,
        from_ms: i64,
        salt: &[u8; 32],
    ) -> Result<PageEvidenceInternal> {
        let window = self
            .scope
            .history_window(sequence, from_ms, PREVIEW_WINDOW)?;
        let response = invoke(
            &mut self.stream,
            sequence,
            HISTORY_OPCODE,
            &pack_history(&window)?,
        )?;
        parse_history_page(&window, &response, salt)
    }

    fn preview_two_windows(&mut self) -> Result<PreviewEvidence> {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| TransportError::Clock)?;
        let from_ms = i64::try_from(now.as_millis())
            .map_err(|_| TransportError::Clock)?
            .checked_add(PREVIEW_CLOCK_SKEW_MS)
            .ok_or(TransportError::Clock)?;
        let mut salt = Zeroizing::new([0_u8; 32]);
        getrandom::getrandom(&mut salt[..]).map_err(|_| TransportError::Io)?;
        let first = self.history_window(3, from_ms, &salt)?;
        let mut report = PreviewEvidence {
            pages: 1,
            first_count: first.count,
            second_count: 0,
            boundary_overlap_count: 0,
            first_advanced_in_time: first.oldest_time.is_some(),
            second_advanced_in_time: false,
            empty_followup_window: false,
            cursor_field_seen: first.cursor_field,
            has_more_field_seen: first.has_more_field,
            total_field_seen: first.total_field,
        };
        if let Some(cursor) = first.oldest_time {
            thread::sleep(Duration::from_millis(250));
            let second = self.history_window(4, cursor, &salt)?;
            report.pages = 2;
            report.second_count = second.count;
            report.boundary_overlap_count = second
                .ids
                .iter()
                .filter(|id| first.ids.contains(id))
                .count();
            report.second_advanced_in_time = second.oldest_time.is_some();
            report.empty_followup_window = second.count == 0;
            report.cursor_field_seen |= second.cursor_field;
            report.has_more_field_seen |= second.has_more_field;
            report.total_field_seen |= second.total_field;
        }
        Ok(report)
    }
}

/// A reusable, LOGIN-bound session for one selected account and DIALOG. It
/// owns a clone of the cancellation handle, so a future job can hold both in
/// separate fields without self-references. Any failed page poisons the reader.
pub(crate) struct HistoryReader {
    session: Session<TimedPinnedStream<HistoryCancel>>,
    cancel: HistoryCancel,
    next_sequence: u16,
    windows_read: u16,
    last_window_completed: Option<Instant>,
    deadline: Instant,
    poisoned: bool,
}

impl HistoryReader {
    /// Resolve one VIDEO source through the same selected, LOGIN-bound api2
    /// connection. No numeric identifier passes through QML or JSON.
    pub(crate) fn resolve_video_sources(
        &mut self,
        source: &HistoryVideoRef,
    ) -> Result<Vec<Zeroizing<String>>> {
        if self.poisoned || self.cancel.is_cancelled() {
            return Err(if self.cancel.is_cancelled() {
                TransportError::Cancelled
            } else {
                TransportError::Protocol
            });
        }
        let sequence = self.next_sequence;
        let next_sequence = sequence
            .checked_add(1)
            .ok_or(TransportError::LimitReached)?;
        let deadline = Instant::now() + VIDEO_PLAY_DEADLINE;
        if let Err(error) =
            wait_for_history_spacing(self.last_window_completed, &self.cancel, deadline)
        {
            self.poisoned = true;
            return Err(error);
        }
        self.session.stream.deadline = deadline;
        let result = finish_bounded_reader_operation(
            self.session.video_sources(sequence, source),
            &self.cancel,
            deadline,
        );
        match result {
            Ok(url) => {
                self.next_sequence = next_sequence;
                self.last_window_completed = Some(Instant::now());
                Ok(url)
            }
            Err(error) => {
                if error == TransportError::ServerRejected {
                    // The correlated response was consumed. A missing/denied
                    // source belongs to this asset; reuse the bound session
                    // for the next asset instead of issuing another LOGIN.
                    self.next_sequence = next_sequence;
                    self.last_window_completed = Some(Instant::now());
                } else {
                    self.poisoned = true;
                }
                Err(error)
            }
        }
    }

    /// The next request starts slightly after the local clock to tolerate a
    /// small client/server skew. This is not a completeness or EOF marker.
    pub(crate) fn initial_from_ms() -> Result<i64> {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| TransportError::Clock)?;
        i64::try_from(now.as_millis())
            .map_err(|_| TransportError::Clock)?
            .checked_add(PREVIEW_CLOCK_SKEW_MS)
            .ok_or(TransportError::Clock)
    }

    /// One bounded GET_HISTORY(49) window. The returned timestamp candidate
    /// is inclusive; callers must deduplicate IDs and prove any terminal edge.
    pub(crate) fn read_backward(&mut self, from_ms: i64) -> Result<HistoryPage> {
        if self.poisoned {
            return Err(TransportError::Protocol);
        }
        if self.cancel.is_cancelled() {
            self.poisoned = true;
            return Err(TransportError::Cancelled);
        }
        let now = Instant::now();
        if now >= self.deadline {
            self.poisoned = true;
            return Err(TransportError::TimedOut);
        }
        if self.windows_read >= HISTORY_MAX_WINDOWS {
            self.poisoned = true;
            return Err(TransportError::LimitReached);
        }
        let sequence = self.next_sequence;
        let Some(next_sequence) = sequence.checked_add(1) else {
            self.poisoned = true;
            return Err(TransportError::LimitReached);
        };
        let page_deadline = history_operation_deadline(now, HISTORY_PAGE_DEADLINE, self.deadline);
        if let Err(error) =
            wait_for_history_spacing(self.last_window_completed, &self.cancel, page_deadline)
        {
            self.poisoned = true;
            return Err(error);
        }
        self.session.stream.deadline = page_deadline;
        let result = read_bounded_window(
            &mut self.session,
            sequence,
            from_ms,
            &self.cancel,
            page_deadline,
        );
        match result {
            Ok(page) => {
                self.next_sequence = next_sequence;
                self.windows_read += 1;
                self.last_window_completed = Some(Instant::now());
                Ok(page)
            }
            Err(error) => {
                self.poisoned = true;
                Err(error)
            }
        }
    }
}

fn classify_reader_io(
    error: TransportError,
    cancel: &HistoryCancel,
    deadline: Instant,
) -> TransportError {
    if matches!(error, TransportError::Io | TransportError::Tls) {
        if cancel.is_cancelled() {
            return TransportError::Cancelled;
        }
        if Instant::now() >= deadline {
            return TransportError::TimedOut;
        }
    }
    error
}

fn history_operation_deadline(now: Instant, limit: Duration, session_deadline: Instant) -> Instant {
    (now + limit).min(session_deadline)
}

/// Wait after a successful response before sending the next GET_HISTORY. The
/// first window has no pause; every later wait shares that call's deadline.
fn wait_for_history_spacing(
    last_completed: Option<Instant>,
    cancel: &HistoryCancel,
    deadline: Instant,
) -> Result<()> {
    wait_for_history_spacing_with(last_completed, cancel, deadline, thread::sleep)
}

fn wait_for_history_spacing_with<F: FnMut(Duration)>(
    last_completed: Option<Instant>,
    cancel: &HistoryCancel,
    deadline: Instant,
    mut sleep: F,
) -> Result<()> {
    let not_before = last_completed.map(|completed| completed + HISTORY_WINDOW_SPACING);
    loop {
        if cancel.is_cancelled() {
            return Err(TransportError::Cancelled);
        }
        let now = Instant::now();
        if now >= deadline {
            return Err(TransportError::TimedOut);
        }
        let Some(not_before) = not_before else {
            return Ok(());
        };
        if now >= not_before {
            return Ok(());
        }
        let slice = not_before
            .saturating_duration_since(now)
            .min(deadline.saturating_duration_since(now))
            .min(HISTORY_WAIT_SLICE);
        sleep(slice);
    }
}

/// Check the wall clock even when a network call or decoding succeeded after
/// the deadline. Dropping a late page also drops its zeroizing text/IDs.
fn finish_bounded_reader_operation<T>(
    result: Result<T>,
    cancel: &HistoryCancel,
    deadline: Instant,
) -> Result<T> {
    if cancel.is_cancelled() {
        return Err(TransportError::Cancelled);
    }
    if Instant::now() >= deadline {
        return Err(TransportError::TimedOut);
    }
    result.map_err(|error| classify_reader_io(error, cancel, deadline))
}

fn read_bounded_window<S: Read + Write>(
    session: &mut Session<S>,
    sequence: u16,
    from_ms: i64,
    cancel: &HistoryCancel,
    deadline: Instant,
) -> Result<HistoryPage> {
    if cancel.is_cancelled() {
        return Err(TransportError::Cancelled);
    }
    if Instant::now() >= deadline {
        return Err(TransportError::TimedOut);
    }
    finish_bounded_reader_operation(session.data_window(sequence, from_ms), cancel, deadline)
}

/// The only production constructor for the reader. The host, root certificate
/// pin, page size and session limits are fixed here, not supplied by callers.
pub(crate) fn open_selected_reader<C: LoginCredentials>(
    selected: SelectedScope,
    credentials: &C,
    cancel: HistoryCancel,
) -> Result<HistoryReader> {
    if cancel.is_cancelled() {
        return Err(TransportError::Cancelled);
    }
    let now = Instant::now();
    let deadline = now + HISTORY_DEADLINE;
    let open_deadline = history_operation_deadline(now, HISTORY_OPEN_DEADLINE, deadline);
    let stream = finish_bounded_reader_operation(
        connect_pinned(cancel.flag(), open_deadline),
        &cancel,
        open_deadline,
    )?;
    let stream = TimedPinnedStream {
        inner: stream,
        cancelled: cancel.clone(),
        deadline: open_deadline,
    };
    let session = finish_bounded_reader_operation(
        bind_session(stream, selected, credentials),
        &cancel,
        open_deadline,
    )?;
    Ok(HistoryReader {
        session,
        cancel,
        next_sequence: 3,
        windows_read: 0,
        last_window_completed: None,
        deadline,
        poisoned: false,
    })
}

/// No token, account, chat ID, or history is sent. Intended for an explicit
/// one-off native transport smoke test; never runs on application startup.
#[allow(dead_code)]
pub(crate) fn synthetic_init_probe() -> Result<()> {
    let cancelled = AtomicBool::new(false);
    let deadline = Instant::now() + PREVIEW_DEADLINE;
    let stream = connect_pinned(&cancelled, deadline)?;
    let mut stream = TimedPinnedStream {
        inner: stream,
        cancelled: &cancelled,
        deadline,
    };
    init(&mut stream)
}

/// Called only by the selected-chat preview route, never by export.start.
/// One call sends at most two small read-only pages; an empty second page is
/// not an EOF proof or a full export signal.
pub(crate) fn preview_selected<C: LoginCredentials>(
    selected: SelectedScope,
    credentials: &C,
    cancelled: &AtomicBool,
) -> std::result::Result<PreviewEvidence, PreviewFailure> {
    let stage = Cell::new(PreviewStage::Connect);
    let deadline = Instant::now() + PREVIEW_DEADLINE;
    let stream = connect_pinned(cancelled, deadline).map_err(|error| PreviewFailure {
        stage: stage.get(),
        error,
    })?;
    let stream = TimedPinnedStream {
        inner: stream,
        cancelled,
        deadline,
    };
    let mut session =
        bind_session_with_stage(stream, selected, credentials, |next| stage.set(next)).map_err(
            |error| PreviewFailure {
                stage: stage.get(),
                error,
            },
        )?;
    stage.set(PreviewStage::History);
    session
        .preview_two_windows()
        .map_err(|error| PreviewFailure {
            stage: stage.get(),
            error,
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::login_credentials::{CredentialError, LoginCredentials};
    use std::io::{self, Cursor};

    struct SyntheticDuplex {
        incoming: Cursor<Vec<u8>>,
        outgoing: Vec<u8>,
        delay_next_read: Option<Duration>,
    }
    impl SyntheticDuplex {
        fn new(incoming: Vec<u8>) -> Self {
            Self {
                incoming: Cursor::new(incoming),
                outgoing: Vec::new(),
                delay_next_read: None,
            }
        }
    }
    impl Read for SyntheticDuplex {
        fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
            if let Some(delay) = self.delay_next_read.take() {
                thread::sleep(delay);
            }
            self.incoming.read(buf)
        }
    }
    impl Write for SyntheticDuplex {
        fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
            self.outgoing.extend_from_slice(buf);
            Ok(buf.len())
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }
    #[derive(Default)]
    struct MeteredSocket {
        raw_reads: usize,
        read_budgets: Vec<Duration>,
        write_budgets: Vec<Duration>,
    }
    impl Read for MeteredSocket {
        fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
            self.raw_reads += 1;
            if let Some(first) = buf.first_mut() {
                *first = 0;
                Ok(1)
            } else {
                Ok(0)
            }
        }
    }
    impl Write for MeteredSocket {
        fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
            Ok(buf.len())
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }
    impl TimeoutSocket for MeteredSocket {
        fn set_read_budget(&mut self, timeout: Duration) -> io::Result<()> {
            self.read_budgets.push(timeout);
            Ok(())
        }
        fn set_write_budget(&mut self, timeout: Duration) -> io::Result<()> {
            self.write_budgets.push(timeout);
            Ok(())
        }
    }
    struct SyntheticCredentials;
    impl LoginCredentials for SyntheticCredentials {
        fn owned_token_names(&self) -> crate::login_credentials::Result<Vec<String>> {
            Ok(Vec::new())
        }
        fn read(&self, name: &str) -> crate::login_credentials::Result<Option<Zeroizing<Vec<u8>>>> {
            assert_eq!(name, "access_token_42@MXMOD");
            Ok(Some(Zeroizing::new(b"synthetic-token".to_vec())))
        }
        fn set_owned(
            &self,
            _: &str,
            _: &[u8],
            _: Option<&[u8]>,
        ) -> crate::login_credentials::Result<()> {
            Err(CredentialError::InvalidPayload)
        }
        fn delete_owned(&self, _: &str, _: &[u8]) -> crate::login_credentials::Result<()> {
            Err(CredentialError::InvalidPayload)
        }
    }
    fn reply(sequence: u16, opcode: u16, body: &[u8], compressed: bool) -> Vec<u8> {
        let payload = if compressed {
            lz4_flex::block::compress(body)
        } else {
            body.to_vec()
        };
        let len = payload.len();
        let mut frame = vec![
            11,
            1,
            sequence.to_be_bytes()[0],
            sequence.to_be_bytes()[1],
            opcode.to_be_bytes()[0],
            opcode.to_be_bytes()[1],
            if compressed { 2 } else { 0 },
            (len >> 16) as u8,
            (len >> 8) as u8,
            len as u8,
        ];
        frame.extend_from_slice(&payload);
        frame
    }
    fn login_reply(profile_id: i64) -> Vec<u8> {
        let mut body = Zeroizing::new(Vec::new());
        write_map_header(&mut body, 1).unwrap();
        write_str(&mut body, "profile").unwrap();
        write_map_header(&mut body, 1).unwrap();
        write_str(&mut body, "contact").unwrap();
        write_map_header(&mut body, 1).unwrap();
        field_int(&mut body, "id", profile_id).unwrap();
        reply(2, LOGIN_OPCODE, &body, true)
    }
    fn history_reply(sequence: u16, rows: &[(i64, i64)], compressed: bool) -> Vec<u8> {
        let mut body = Zeroizing::new(Vec::new());
        write_map_header(&mut body, 3).unwrap();
        write_str(&mut body, "messages").unwrap();
        body.push(0x90 | rows.len() as u8);
        for (id, time) in rows {
            write_map_header(&mut body, 3).unwrap();
            field_int(&mut body, "id", *id).unwrap();
            field_int(&mut body, "time", *time).unwrap();
            field_str(&mut body, "text", "synthetic message never surfaced").unwrap();
        }
        field_str(&mut body, "cursor", "synthetic-cursor").unwrap();
        field_bool(&mut body, "hasMore", true).unwrap();
        reply(sequence, HISTORY_OPCODE, &body, compressed)
    }

    fn typed_history_reply(
        sequence: u16,
        rows: &[(i64, i64, &str, bool)],
        row_chat_id: i64,
        compressed: bool,
    ) -> Vec<u8> {
        let mut body = Zeroizing::new(Vec::new());
        write_map_header(&mut body, 2).unwrap();
        field_int(&mut body, "chatId", -99).unwrap();
        write_str(&mut body, "messages").unwrap();
        assert!(rows.len() <= 15);
        body.push(0x90 | rows.len() as u8);
        for (id, time, attachment_type, rich) in rows {
            write_map_header(&mut body, if *rich { 11 } else { 8 }).unwrap();
            field_int(&mut body, "id", *id).unwrap();
            field_int(&mut body, "time", *time).unwrap();
            field_int(&mut body, "sender", 55).unwrap();
            field_str(&mut body, "type", "USER").unwrap();
            field_str(&mut body, "text", "synthetic text").unwrap();
            field_int(&mut body, "cid", 777).unwrap(); // Deliberately not chatId.
            field_int(&mut body, "chatId", row_chat_id).unwrap();
            write_str(&mut body, "attaches").unwrap();
            body.push(0x91);
            write_map_header(&mut body, 3).unwrap();
            field_str(&mut body, "_type", attachment_type).unwrap();
            field_str(
                &mut body,
                "url",
                "https://media.invalid/private?signature=synthetic",
            )
            .unwrap();
            field_str(&mut body, "token", "synthetic-token").unwrap();
            if *rich {
                write_str(&mut body, "elements").unwrap();
                body.push(0x91);
                write_str(&mut body, "synthetic rich payload").unwrap();
                write_str(&mut body, "link").unwrap();
                body.push(0xc0);
                write_str(&mut body, "options").unwrap();
                body.push(0x90);
            }
        }
        reply(sequence, HISTORY_OPCODE, &body, compressed)
    }

    #[test]
    fn official_root_is_pinned_and_tls_builder_has_only_that_root() {
        assert_eq!(Sha256::digest(ROOT_CA_DER).as_slice(), ROOT_CA_SHA256);
        assert!(pinned_config().is_ok());
    }

    fn synthetic_bound_scope() -> BoundScope {
        SelectedScope::parse("42", "-99")
            .unwrap()
            .bind_login(
                FrameHeader {
                    version: 11,
                    command: 1,
                    sequence: 2,
                    opcode: LOGIN_OPCODE,
                    compression_coefficient: 0,
                    body_bytes: 0,
                },
                2,
                42,
            )
            .unwrap()
    }

    #[test]
    fn video_play_preserves_int64_and_orders_pinned_mp4_variants() {
        let source = HistoryVideoRef {
            message_id: HistoryMessageId::Unsigned(9_007_199_254_740_993),
            video_id: 9_007_199_254_740_995,
            token: Zeroizing::new("synthetic-token".into()),
        };
        let packed = pack_video_play(-99, &source).unwrap();
        let decoded = DecodedBody {
            length: packed.len(),
            bytes: packed,
        };
        let value = read_value(&decoded).unwrap();
        assert_eq!(
            signed_i64(required_field(&value, "chatId").unwrap()).unwrap(),
            -99
        );
        assert_eq!(
            required_field(&value, "messageId").unwrap().as_u64(),
            Some(9_007_199_254_740_993)
        );
        assert_eq!(
            required_field(&value, "videoId").unwrap().as_u64(),
            Some(9_007_199_254_740_995)
        );
        assert!(unique_field(&value, "postId").unwrap().is_none());

        let mut body = Zeroizing::new(Vec::new());
        write_map_header(&mut body, 2).unwrap();
        field_str(
            &mut body,
            "MP4_144",
            "https://maxvd291.okcdn.ru/synthetic-144.mp4?sig=fake",
        )
        .unwrap();
        field_str(
            &mut body,
            "MP4_720",
            "https://maxvd291.okcdn.ru/synthetic-720.mp4?sig=fake",
        )
        .unwrap();
        let mut session = Session {
            stream: SyntheticDuplex::new(reply(3, VIDEO_PLAY_OPCODE, &body, false)),
            scope: synthetic_bound_scope(),
        };
        let urls = session.video_sources(3, &source).unwrap();
        assert_eq!(
            urls.iter().map(|url| url.as_str()).collect::<Vec<_>>(),
            [
                "https://maxvd291.okcdn.ru/synthetic-720.mp4?sig=fake",
                "https://maxvd291.okcdn.ru/synthetic-144.mp4?sig=fake",
            ]
        );
        assert_eq!(
            &session.stream.outgoing[4..6],
            &VIDEO_PLAY_OPCODE.to_be_bytes()
        );
    }

    #[test]
    fn video_play_rejects_mismatched_echo_or_foreign_source() {
        let source = HistoryVideoRef {
            message_id: HistoryMessageId::Text(Zeroizing::new("9007199254740993".into())),
            video_id: 42,
            token: Zeroizing::new(String::new()),
        };
        let mut wrong_scope = Zeroizing::new(Vec::new());
        write_map_header(&mut wrong_scope, 2).unwrap();
        field_int(&mut wrong_scope, "chatId", -100).unwrap();
        field_str(
            &mut wrong_scope,
            "MP4_144",
            "https://maxvd291.okcdn.ru/synthetic.mp4",
        )
        .unwrap();
        let frame = read_raw_frame(&mut Cursor::new(reply(
            3,
            VIDEO_PLAY_OPCODE,
            &wrong_scope,
            false,
        )))
        .unwrap();
        let response = Response {
            header: frame.header,
            body: decode_body(frame).unwrap(),
        };
        assert!(matches!(
            parse_video_sources(&synthetic_bound_scope(), &source, &response),
            Err(TransportError::HistoryScope)
        ));

        let mut foreign = Zeroizing::new(Vec::new());
        write_map_header(&mut foreign, 1).unwrap();
        field_str(&mut foreign, "MP4_144", "https://example.invalid/video.mp4").unwrap();
        let frame = read_raw_frame(&mut Cursor::new(reply(
            3,
            VIDEO_PLAY_OPCODE,
            &foreign,
            false,
        )))
        .unwrap();
        let response = Response {
            header: frame.header,
            body: decode_body(frame).unwrap(),
        };
        assert!(matches!(
            parse_video_sources(&synthetic_bound_scope(), &source, &response),
            Err(TransportError::Protocol)
        ));
    }

    #[test]
    fn tls_record_failure_is_distinct_from_socket_io_after_handshake() {
        fn tls_error() -> io::Error {
            io::Error::new(io::ErrorKind::InvalidData, rustls::Error::DecryptError)
        }

        struct FailedTlsRead;
        impl Read for FailedTlsRead {
            fn read(&mut self, _buffer: &mut [u8]) -> io::Result<usize> {
                Err(tls_error())
            }
        }

        assert_eq!(classify_stream_io(tls_error()), TransportError::Tls);
        assert!(matches!(
            read_raw_frame(&mut FailedTlsRead),
            Err(TransportError::Tls)
        ));
        assert_eq!(
            classify_stream_io(io::Error::new(io::ErrorKind::InvalidData, "non-TLS data")),
            TransportError::Io
        );
        for (kind, expected) in [
            (io::ErrorKind::TimedOut, TransportError::TimedOut),
            (io::ErrorKind::WouldBlock, TransportError::TimedOut),
            (io::ErrorKind::Interrupted, TransportError::Cancelled),
            (io::ErrorKind::ConnectionReset, TransportError::Io),
        ] {
            assert_eq!(classify_stream_io(io::Error::from(kind)), expected);
        }
    }

    #[test]
    fn synthetic_requests_preserve_int64_and_bound_token_encoding() {
        let selected = SelectedScope::parse("9007199254740993", "-9007199254740995").unwrap();
        let bound = selected
            .bind_login(
                FrameHeader::parse(&[11, 1, 0, 2, 0, 19, 0, 0, 0, 0]).unwrap(),
                2,
                9_007_199_254_740_993,
            )
            .unwrap();
        let request = pack_history(&bound.history_window(3, 2_000, 3).unwrap()).unwrap();
        let mut reader = request.as_slice();
        let parsed = read_value_ref_with_max_depth(&mut reader, 24).unwrap();
        assert_eq!(
            signed_i64(required_field(&parsed, "chatId").unwrap()).unwrap(),
            -9_007_199_254_740_995
        );
        assert_eq!(
            signed_i64(required_field(&parsed, "from").unwrap()).unwrap(),
            2_000
        );
        assert!(reader.is_empty());
        let token = b"synthetic-only-token";
        let packed_login = pack_login(token).unwrap();
        assert!(packed_login.len() < 512);
        assert!(pack_login(&[]).is_err());
        assert!(pack_login(&[0xff]).is_err());
    }

    #[test]
    fn frame_caps_and_lz4_block_decode_are_bounded() {
        let payload = pack_init().unwrap();
        let compressed = lz4_flex::block::compress(&payload);
        let raw = RawFrame {
            header: FrameHeader::parse(&[
                11,
                1,
                0,
                1,
                0,
                6,
                2,
                0,
                (compressed.len() >> 8) as u8,
                compressed.len() as u8,
            ])
            .unwrap(),
            body: Zeroizing::new(compressed),
        };
        let decoded = decode_body(raw).unwrap();
        assert_eq!(decoded.as_slice(), payload.as_slice());
        let oversized = FrameHeader::parse(&[11, 1, 0, 1, 0, 6, 0, 0x20, 0, 1]);
        assert!(oversized.is_err());
    }

    #[test]
    fn messagepack_preflight_caps_declared_containers_actual_nodes_and_depth() {
        assert!(preflight_messagepack(&pack_init().unwrap()).is_ok());
        assert!(preflight_messagepack(&[0xdd, 0xff, 0xff, 0xff, 0xff]).is_err());
        assert!(preflight_messagepack(&[0xdf, 0xff, 0xff, 0xff, 0xff]).is_err());
        let mut many_nil = vec![0xdd, 0x00, 0x01, 0x86, 0xa1]; // array32 of 100_001
        many_nil.extend(std::iter::repeat_n(0xc0, 100_001));
        assert!(many_nil.len() < MAX_DECOMPRESSED_BODY);
        assert!(preflight_messagepack(&many_nil).is_err());
        assert!(preflight_messagepack(&[0x80, 0x00]).is_err());
        let mut nested = vec![0x91; MAX_VALUE_DEPTH as usize];
        nested.push(0xc0);
        assert!(preflight_messagepack(&nested).is_err());
    }

    #[test]
    fn cancellation_and_deadline_reject_io_before_reading() {
        let cancelled = AtomicBool::new(true);
        assert_eq!(
            remaining_budget(&cancelled, Instant::now() + Duration::from_secs(1))
                .unwrap_err()
                .kind(),
            io::ErrorKind::Interrupted
        );
        cancelled.store(false, Ordering::Release);
        assert_eq!(
            remaining_budget(&cancelled, Instant::now() - Duration::from_millis(1))
                .unwrap_err()
                .kind(),
            io::ErrorKind::TimedOut
        );
    }

    #[test]
    fn deadline_io_checks_each_raw_operation_and_cancel_cannot_be_retried_by_rustls() {
        let cancel = AtomicBool::new(false);
        let mut socket = MeteredSocket::default();
        let mut byte = [0_u8; 1];
        {
            let mut io = DeadlineIo {
                inner: &mut socket,
                cancelled: &cancel,
                deadline: Instant::now() + Duration::from_millis(120),
            };
            assert_eq!(io.read(&mut byte).unwrap(), 1);
            assert_eq!(io.write(&[1]).unwrap(), 1);
            io.flush().unwrap();
            assert_eq!(io.read(&mut byte).unwrap(), 1);
            thread::sleep(Duration::from_millis(140));
            assert_eq!(
                io.read(&mut byte).unwrap_err().kind(),
                io::ErrorKind::TimedOut
            );
        }
        assert_eq!(socket.raw_reads, 2);
        assert_eq!(socket.read_budgets.len(), 2);
        assert!(socket.read_budgets[1] <= socket.read_budgets[0]);
        assert_eq!(socket.write_budgets.len(), 2);

        cancel.store(true, Ordering::Release);
        let mut io = DeadlineIo {
            inner: &mut socket,
            cancelled: &cancel,
            deadline: Instant::now() + Duration::from_secs(1),
        };
        assert_eq!(
            io.read(&mut byte).unwrap_err().kind(),
            io::ErrorKind::ConnectionAborted
        );
        assert_eq!(
            io.write(&[1]).unwrap_err().kind(),
            io::ErrorKind::ConnectionAborted
        );
        assert_eq!(socket.raw_reads, 2);
    }

    #[test]
    fn history_open_and_page_budgets_fit_first_ui_watchdog_and_session_cap() {
        assert!(HISTORY_OPEN_DEADLINE + HISTORY_PAGE_DEADLINE < Duration::from_secs(60));
        let now = Instant::now();
        let session_deadline = now + HISTORY_DEADLINE;
        assert_eq!(
            history_operation_deadline(now, HISTORY_OPEN_DEADLINE, session_deadline),
            now + HISTORY_OPEN_DEADLINE
        );
        assert_eq!(
            history_operation_deadline(now, HISTORY_PAGE_DEADLINE, session_deadline),
            now + HISTORY_PAGE_DEADLINE
        );
        assert_eq!(
            history_operation_deadline(
                session_deadline - Duration::from_secs(3),
                HISTORY_PAGE_DEADLINE,
                session_deadline
            ),
            session_deadline
        );
        let cancel = HistoryCancel::new();
        assert!(matches!(
            finish_bounded_reader_operation(Ok(()), &cancel, now - Duration::from_millis(1)),
            Err(TransportError::TimedOut)
        ));
    }

    #[test]
    fn history_spacing_skips_first_window_and_waits_after_a_completed_window() {
        let cancel = HistoryCancel::new();
        let deadline = Instant::now() + Duration::from_secs(3);
        let mut first_sleeps = 0;
        wait_for_history_spacing_with(None, &cancel, deadline, |_| first_sleeps += 1).unwrap();
        assert_eq!(first_sleeps, 0);

        let completed = Instant::now();
        let mut slices = 0;
        wait_for_history_spacing_with(Some(completed), &cancel, deadline, |slice| {
            assert!(slice > Duration::ZERO);
            assert!(slice <= HISTORY_WAIT_SLICE);
            slices += 1;
            thread::sleep(slice);
        })
        .unwrap();
        assert!(slices >= 1);
        assert!(Instant::now() >= completed + HISTORY_WINDOW_SPACING);
    }

    #[test]
    fn history_spacing_honours_cancel_and_page_deadline() {
        let cancel = HistoryCancel::new();
        let signal = cancel.clone();
        let mut sleeps = 0;
        assert!(matches!(
            wait_for_history_spacing_with(
                Some(Instant::now()),
                &cancel,
                Instant::now() + Duration::from_secs(1),
                |_| {
                    sleeps += 1;
                    signal.cancel();
                }
            ),
            Err(TransportError::Cancelled)
        ));
        assert_eq!(sleeps, 1);

        let cancel = HistoryCancel::new();
        assert!(matches!(
            wait_for_history_spacing_with(
                Some(Instant::now()),
                &cancel,
                Instant::now() + Duration::from_millis(40),
                thread::sleep
            ),
            Err(TransportError::TimedOut)
        ));
    }

    #[test]
    fn delayed_synthetic_history_page_is_discarded_at_wall_clock_deadline() {
        let mut incoming = reply(1, INIT_OPCODE, &[0x80], false);
        incoming.extend(login_reply(42));
        incoming.extend(typed_history_reply(
            3,
            &[(101, 1_000, "PHOTO", false)],
            -99,
            false,
        ));
        let selected = SelectedScope::parse("42", "-99").unwrap();
        let mut session = bind_session(
            SyntheticDuplex::new(incoming),
            selected,
            &SyntheticCredentials,
        )
        .unwrap();
        session.stream.delay_next_read = Some(Duration::from_millis(80));
        let cancel = HistoryCancel::new();
        assert!(matches!(
            read_bounded_window(
                &mut session,
                3,
                2_000,
                &cancel,
                Instant::now() + Duration::from_millis(40)
            ),
            Err(TransportError::TimedOut)
        ));
        assert!(session.stream.delay_next_read.is_none());
    }

    #[test]
    fn message_chat_id_when_present_must_match_the_selected_scope() {
        let selected = SelectedScope::parse("42", "-99").unwrap();
        let bound = selected
            .bind_login(
                FrameHeader::parse(&[11, 1, 0, 2, 0, 19, 0, 0, 0, 0]).unwrap(),
                2,
                42,
            )
            .unwrap();
        let window = bound.history_window(3, 2_000, 3).unwrap();
        let mut body = Zeroizing::new(Vec::new());
        write_map_header(&mut body, 1).unwrap();
        write_str(&mut body, "messages").unwrap();
        body.push(0x91);
        write_map_header(&mut body, 3).unwrap();
        field_int(&mut body, "id", 101).unwrap();
        field_int(&mut body, "time", 1_000).unwrap();
        field_int(&mut body, "chatId", -100).unwrap();
        let length = body.len();
        let response = Response {
            header: FrameHeader::parse(&[
                11,
                1,
                0,
                3,
                0,
                49,
                0,
                0,
                (length >> 8) as u8,
                length as u8,
            ])
            .unwrap(),
            body: DecodedBody {
                bytes: body,
                length,
            },
        };
        assert!(matches!(
            parse_history_page(&window, &response, &[0; 32]),
            Err(TransportError::HistoryScope)
        ));
    }

    #[test]
    fn synthetic_login_and_two_history_windows_keep_only_boundary_counts() {
        let mut incoming = Vec::new();
        incoming.extend(reply(1, INIT_OPCODE, &[0x80], false));
        let mut unrelated = reply(0, 128, &[0x80], false);
        unrelated[1] = 2;
        incoming.extend(unrelated);
        incoming.extend(login_reply(42));
        incoming.extend(history_reply(
            3,
            &[(101, 1_000), (102, 900), (103, 899)],
            false,
        ));
        incoming.extend(history_reply(
            4,
            &[(103, 899), (104, 898), (105, 897)],
            true,
        ));
        let selected = SelectedScope::parse("42", "-99").unwrap();
        let mut session = bind_session(
            SyntheticDuplex::new(incoming),
            selected,
            &SyntheticCredentials,
        )
        .unwrap();
        let evidence = session.preview_two_windows().unwrap();
        assert_eq!(
            evidence,
            PreviewEvidence {
                pages: 2,
                first_count: 3,
                second_count: 3,
                boundary_overlap_count: 1,
                first_advanced_in_time: true,
                second_advanced_in_time: true,
                empty_followup_window: false,
                cursor_field_seen: true,
                has_more_field_seen: true,
                total_field_seen: false,
            }
        );
    }

    #[test]
    fn reusable_bound_session_returns_typed_content_without_media_url_or_token() {
        let mut incoming = reply(1, INIT_OPCODE, &[0x80], false);
        incoming.extend(login_reply(42));
        incoming.extend(typed_history_reply(
            3,
            &[(101, 1_000, "PHOTO", true)],
            -99,
            false,
        ));
        incoming.extend(typed_history_reply(
            4,
            &[(101, 1_000, "PHOTO", true), (102, 900, "VIDEO", false)],
            -99,
            true,
        ));
        let selected = SelectedScope::parse("42", "-99").unwrap();
        let mut session = bind_session(
            SyntheticDuplex::new(incoming),
            selected,
            &SyntheticCredentials,
        )
        .unwrap();
        let first = session.data_window(3, 2_000).unwrap();
        let second = session.data_window(4, 1_000).unwrap();
        assert_eq!(first.messages.len(), 1);
        assert_eq!(second.messages.len(), 2);
        assert!(first.messages[0].id == second.messages[0].id);
        assert_eq!(second.messages[1].time_ms, 900);
        assert_eq!(second.messages[1].sender_id, 55);
        assert!(second.messages[1].kind == HistoryMessageKind::User);
        assert_eq!(second.messages[1].text.as_str(), "synthetic text");
        assert!(first.messages[0].attachments == [HistoryAttachmentKind::Photo]);
        assert!(second.messages[1].attachments == [HistoryAttachmentKind::Video]);
        assert!(first.messages[0].rich_content_present);
        assert!(first.messages[0].unsupported_visible_content);
        assert!(!second.messages[1].rich_content_present);
        assert!(!second.messages[1].unsupported_visible_content);
        assert!(matches!(
            second.evidence,
            PageEvidence::AdvanceCandidate {
                next_from_ms: 900,
                count: 2
            }
        ));
        let outgoing = &session.stream.outgoing;
        let mut position = 0;
        for (sequence, opcode) in [
            (1, INIT_OPCODE),
            (2, LOGIN_OPCODE),
            (3, HISTORY_OPCODE),
            (4, HISTORY_OPCODE),
        ] {
            let header = FrameHeader::parse(&outgoing[position..position + 10]).unwrap();
            assert_eq!((header.sequence, header.opcode), (sequence, opcode));
            position += 10 + header.body_bytes;
        }
        assert_eq!(position, outgoing.len());
    }

    #[test]
    fn typed_reader_rejects_wrong_row_chat_id_but_ignores_unrelated_cid() {
        let selected = SelectedScope::parse("42", "-99").unwrap();
        let mut good = reply(1, INIT_OPCODE, &[0x80], false);
        good.extend(login_reply(42));
        good.extend(typed_history_reply(
            3,
            &[(101, 1_000, "FILE", false)],
            -99,
            false,
        ));
        let mut session =
            bind_session(SyntheticDuplex::new(good), selected, &SyntheticCredentials).unwrap();
        assert!(session.data_window(3, 2_000).is_ok());

        let selected = SelectedScope::parse("42", "-99").unwrap();
        let mut bad = reply(1, INIT_OPCODE, &[0x80], false);
        bad.extend(login_reply(42));
        bad.extend(typed_history_reply(
            3,
            &[(101, 1_000, "FILE", false)],
            -100,
            false,
        ));
        let mut session =
            bind_session(SyntheticDuplex::new(bad), selected, &SyntheticCredentials).unwrap();
        assert!(matches!(
            session.data_window(3, 2_000),
            Err(TransportError::HistoryScope)
        ));
    }

    #[test]
    fn cloneable_cancel_and_reader_send_contract_are_static() {
        fn require_send<T: Send>() {}
        require_send::<HistoryReader>();
        let cancel = HistoryCancel::new();
        let other_thread = cancel.clone();
        other_thread.cancel();
        assert!(cancel.is_cancelled());
        assert!(matches!(
            classify_reader_io(
                TransportError::Io,
                &cancel,
                Instant::now() + Duration::from_secs(1)
            ),
            TransportError::Cancelled
        ));
        assert!(matches!(
            classify_reader_io(
                TransportError::HistoryScope,
                &cancel,
                Instant::now() + Duration::from_secs(1)
            ),
            TransportError::HistoryScope
        ));
    }

    #[test]
    fn synthetic_login_profile_mismatch_stops_before_any_history_request() {
        let mut incoming = reply(1, INIT_OPCODE, &[0x80], false);
        incoming.extend(login_reply(43));
        let selected = SelectedScope::parse("42", "-99").unwrap();
        assert!(matches!(
            bind_session(
                SyntheticDuplex::new(incoming),
                selected,
                &SyntheticCredentials
            ),
            Err(TransportError::AccountMismatch)
        ));
    }

    #[test]
    fn empty_followup_window_is_reported_without_a_completion_claim() {
        let mut incoming = reply(1, INIT_OPCODE, &[0x80], false);
        incoming.extend(login_reply(42));
        incoming.extend(history_reply(3, &[(101, 1_000), (102, 900)], false));
        incoming.extend(history_reply(4, &[], false));
        let selected = SelectedScope::parse("42", "-99").unwrap();
        let mut session = bind_session(
            SyntheticDuplex::new(incoming),
            selected,
            &SyntheticCredentials,
        )
        .unwrap();
        let evidence = session.preview_two_windows().unwrap();
        assert_eq!(evidence.pages, 2);
        assert_eq!(evidence.first_count, 2);
        assert_eq!(evidence.second_count, 0);
        assert!(evidence.empty_followup_window);
    }

    #[test]
    fn preview_failure_codes_identify_only_fixed_stage_and_category() {
        let cases = [
            (
                PreviewStage::Connect,
                TransportError::Tls,
                "api2_connect_tls",
            ),
            (
                PreviewStage::Init,
                TransportError::Protocol,
                "api2_init_protocol",
            ),
            (
                PreviewStage::Credential,
                TransportError::Credential,
                "api2_credential_unavailable",
            ),
            (
                PreviewStage::LoginBind,
                TransportError::AccountMismatch,
                "api2_login_bind_account",
            ),
            (
                PreviewStage::LoginFrame,
                TransportError::LimitReached,
                "api2_login_frame_limit",
            ),
            (
                PreviewStage::LoginPreflight,
                TransportError::Protocol,
                "api2_login_preflight_protocol",
            ),
            (
                PreviewStage::History,
                TransportError::HistoryScope,
                "api2_history_scope",
            ),
        ];
        for (stage, error, expected) in cases {
            let code = PreviewFailure { stage, error }.code();
            assert_eq!(code, expected);
            assert!(
                code.len() <= 32
                    && code.bytes().all(|byte| byte.is_ascii_lowercase()
                        || byte.is_ascii_digit()
                        || byte == b'_')
            );
        }
        assert_eq!(
            TransportError::from(ContractError::FrameTooLarge),
            TransportError::LimitReached
        );
    }

    #[test]
    fn preview_io_and_decode_limits_keep_distinct_safe_categories() {
        assert_eq!(
            classify_stream_io(io::Error::from(io::ErrorKind::TimedOut)),
            TransportError::TimedOut
        );
        assert_eq!(
            classify_stream_io(io::Error::from(io::ErrorKind::WouldBlock)),
            TransportError::TimedOut
        );
        assert_eq!(
            classify_stream_io(io::Error::new(
                io::ErrorKind::ConnectionAborted,
                CancelledIo
            )),
            TransportError::Cancelled
        );
        assert_eq!(
            classify_stream_io(io::Error::from(io::ErrorKind::ConnectionAborted)),
            TransportError::Io
        );
        let expanded = vec![0_u8; MAX_DECOMPRESSED_BODY + 1];
        let frame = reply(2, LOGIN_OPCODE, &expanded, true);
        let raw = read_raw_frame(&mut SyntheticDuplex::new(frame)).unwrap();
        assert!(matches!(
            decode_body(raw),
            Err(TransportError::LimitReached)
        ));
    }

    #[test]
    fn messagepack_reader_budget_matches_bounded_preflight_depth() {
        // Nine nested one-item arrays are legal under the independent
        // structural preflight, but rmpv charges 21 depth units for them and
        // the final string. Its former budget of 20 rejected valid data.
        let mut encoded = Zeroizing::new(vec![0x91; 9]);
        encoded.extend_from_slice(&[0xa1, b'x']);
        assert!(preflight_messagepack(&encoded).is_ok());
        let mut former = encoded.as_slice();
        assert!(read_value_ref_with_max_depth(&mut former, MAX_VALUE_DEPTH as usize).is_err());
        let length = encoded.len();
        let body = DecodedBody {
            bytes: encoded,
            length,
        };
        let mut phases = Vec::new();
        assert!(matches!(
            read_value_with_progress(&body, |phase| phases.push(phase)),
            Ok(ValueRef::Array(_))
        ));
        assert!(matches!(
            phases.as_slice(),
            [DecodePhase::Messagepack, DecodePhase::Trailing]
        ));

        let mut boundary = Zeroizing::new(vec![0x91; MAX_VALUE_DEPTH as usize - 1]);
        boundary.extend_from_slice(&[0xa1, b'x']);
        assert!(preflight_messagepack(&boundary).is_ok());
        let mut insufficient = boundary.as_slice();
        assert!(read_value_ref_with_max_depth(&mut insufficient, RMPV_DEPTH_BUDGET - 1).is_err());
        let length = boundary.len();
        let body = DecodedBody {
            bytes: boundary,
            length,
        };
        assert!(matches!(read_value(&body), Ok(ValueRef::Array(_))));

        let mut excessive = vec![0x91; MAX_VALUE_DEPTH as usize];
        excessive.extend_from_slice(&[0xa1, b'x']);
        assert!(matches!(
            preflight_messagepack(&excessive),
            Err(TransportError::Protocol)
        ));
    }

    #[test]
    fn preview_session_marks_init_credential_and_login_before_history() {
        let mut incoming = reply(1, INIT_OPCODE, &[0x80], false);
        incoming.extend(login_reply(42));
        let selected = SelectedScope::parse("42", "-99").unwrap();
        let mut observed = Vec::new();
        let result = bind_session_with_stage(
            SyntheticDuplex::new(incoming),
            selected,
            &SyntheticCredentials,
            |stage| observed.push(stage),
        );
        assert!(result.is_ok());
        assert_eq!(observed.first(), Some(&PreviewStage::Init));
        assert_eq!(observed.get(1), Some(&PreviewStage::Credential));
        assert_eq!(observed.last(), Some(&PreviewStage::LoginBind));

        let mut rejected = reply(1, INIT_OPCODE, &[0x80], false);
        rejected.extend(login_reply(43));
        let mut last = PreviewStage::Connect;
        let result = bind_session_with_stage(
            SyntheticDuplex::new(rejected),
            SelectedScope::parse("42", "-99").unwrap(),
            &SyntheticCredentials,
            |stage| last = stage,
        );
        assert!(matches!(result, Err(TransportError::AccountMismatch)));
        assert_eq!(last, PreviewStage::LoginBind);
    }

    #[test]
    #[ignore = "explicit one-off native LOGIN; exact account ID supplied in env, no history request"]
    fn live_login_only_reports_sanitized_stage() {
        let own_id = std::env::var("VALMAX_API2_TEST_OWN_ID").unwrap_or_else(|_| {
            panic!("VALMAX_API2_TEST_OWN_ID must be set to a canonical account ID")
        });
        // This chat value remains local. The test stops after LOGIN and never
        // sends GET_HISTORY for it.
        let selected = SelectedScope::parse(&own_id, "1")
            .unwrap_or_else(|_| panic!("invalid canonical account ID in test environment"));
        let cancelled = AtomicBool::new(false);
        let deadline = Instant::now() + PREVIEW_DEADLINE;
        let stage = Cell::new(PreviewStage::Connect);
        let result: std::result::Result<(), PreviewFailure> = (|| {
            let stream = connect_pinned(&cancelled, deadline).map_err(|error| PreviewFailure {
                stage: stage.get(),
                error,
            })?;
            let stream = TimedPinnedStream {
                inner: stream,
                cancelled: &cancelled,
                deadline,
            };
            let _session = bind_session_with_stage(
                stream,
                selected,
                &crate::login_credentials::WindowsCredentials::new(),
                |next| stage.set(next),
            )
            .map_err(|error| PreviewFailure {
                stage: stage.get(),
                error,
            })?;
            Ok(())
        })();
        match result {
            Ok(()) => println!("api2_native_login=ok"),
            Err(failure) => panic!("api2_native_login={}", failure.code()),
        }
    }

    #[test]
    #[ignore = "explicit selected-chat native preview; IDs supplied in env and never printed"]
    fn live_selected_preview_reports_sanitized_stage() {
        let own_id = std::env::var("VALMAX_API2_TEST_OWN_ID")
            .unwrap_or_else(|_| panic!("VALMAX_API2_TEST_OWN_ID must be set"));
        let chat_id = std::env::var("VALMAX_API2_TEST_CHAT_ID")
            .unwrap_or_else(|_| panic!("VALMAX_API2_TEST_CHAT_ID must be set"));
        let selected = SelectedScope::parse(&own_id, &chat_id)
            .unwrap_or_else(|_| panic!("invalid canonical selected IDs in test environment"));
        let cancelled = AtomicBool::new(false);
        match preview_selected(
            selected,
            &crate::login_credentials::WindowsCredentials::new(),
            &cancelled,
        ) {
            Ok(evidence) => println!("api2_native_preview=ok pages={}", evidence.pages),
            Err(failure) => panic!("api2_native_preview={}", failure.code()),
        }
    }

    #[test]
    #[ignore = "one-off live synthetic INIT over pinned api2 TLS"]
    fn live_synthetic_init_has_verified_tls_and_correlated_response() {
        assert_eq!(synthetic_init_probe(), Ok(()));
    }
}
