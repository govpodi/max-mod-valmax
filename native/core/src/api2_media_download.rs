//! Narrow, account-bound API2 media transfer. Only a URL retained inside a
//! validated selected-chat history page can reach this module. The URL never
//! enters a JSON receipt, QML, a log, or the published archive.

use crate::{
    api2_history_transport::HistoryCancel, export_render, export_storage::ExternalMediaWriter,
};
use rustls::{
    ClientConfig, ClientConnection, RootCertStore, Stream,
    pki_types::{CertificateDer, ServerName},
};
use sha2::{Digest, Sha256};
use std::{
    io::{self, BufReader, Read, Write},
    net::{IpAddr, Ipv4Addr, Ipv6Addr, TcpStream, ToSocketAddrs},
    sync::Arc,
    time::{Duration, Instant},
};
use zeroize::Zeroizing;

const AUDIO_HOST: &str = "a.oneme.ru";
const PHOTO_HOST: &str = "i.oneme.ru";
const ROOT_CA_DER: &[u8] = include_bytes!("../certs/isrg_root_x1.der");
const ROOT_CA_SHA256: [u8; 32] = [
    0x96, 0xbc, 0xec, 0x06, 0x26, 0x49, 0x76, 0xf3, 0x74, 0x60, 0x77, 0x9a, 0xcf, 0x28, 0xc5, 0xa7,
    0xcf, 0xe8, 0xa3, 0xc0, 0xaa, 0xe1, 0x1a, 0x8f, 0xfc, 0xee, 0x05, 0xc0, 0xbd, 0xdf, 0x08, 0xc6,
];
const VIDEO_ROOT_CA_DER: &[u8] = include_bytes!("../certs/harica_ecc_root_ca_2015.der");
const VIDEO_ROOT_CA_SHA256: [u8; 32] = [
    0x44, 0xb5, 0x45, 0xaa, 0x8a, 0x25, 0xe6, 0x5a, 0x73, 0xca, 0x15, 0xdc, 0x27, 0xfc, 0x36, 0xd2,
    0x4c, 0x1c, 0xb9, 0x95, 0x3a, 0x06, 0x65, 0x39, 0xb1, 0x15, 0x82, 0xdc, 0x48, 0x7b, 0x48, 0x33,
];
const MAX_URL_BYTES: usize = 8192;
const MAX_HEADER_BYTES: usize = 16 * 1024;
// Explicit local per-asset bound for supported media. The archive accepts up
// to 16 GiB; no separate oneme CDN limit is proven. Never trust an unverified
// Content-Length claim to set this transport bound.
const MAX_MEDIA_BYTES: u64 = 4 * 1024 * 1024 * 1024;
const SOCKET_TIMEOUT: Duration = Duration::from_secs(4);
const CONNECT_DEADLINE: Duration = Duration::from_secs(30);
const IDLE_DEADLINE: Duration = Duration::from_secs(30);
pub(crate) const MAX_MEDIA_TRANSFER_DURATION: Duration = Duration::from_secs(4 * 60 * 60);
const TRANSFER_CHUNK_BYTES: usize = 64 * 1024;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum MediaError {
    InvalidUrl,
    TrustPin,
    Tls,
    Network,
    Timeout,
    Cancelled,
    NotAvailable,
    PermissionDenied,
    TooLarge,
    InvalidResponse,
    InvalidMedia,
    Storage(export_render::Error),
}
impl MediaError {
    pub(crate) fn reason(self) -> &'static str {
        match self {
            Self::NotAvailable => "not_available",
            Self::PermissionDenied => "permission_denied",
            Self::Timeout => "timeout",
            Self::Cancelled => "cancelled",
            Self::TooLarge => "asset_too_large",
            Self::InvalidUrl
            | Self::TrustPin
            | Self::Tls
            | Self::Network
            | Self::InvalidResponse
            | Self::InvalidMedia
            | Self::Storage(_) => "download_failed",
        }
    }
}
type Result<T> = std::result::Result<T, MediaError>;

#[derive(Clone, Copy)]
enum CdnKind {
    Audio,
    Photo,
    Video,
}
impl CdnKind {
    fn fixed_host(self) -> Option<&'static str> {
        match self {
            Self::Audio => Some(AUDIO_HOST),
            Self::Photo => Some(PHOTO_HOST),
            Self::Video => None,
        }
    }
    fn max_bytes(self) -> u64 {
        MAX_MEDIA_BYTES
    }
    fn max_transfer_duration(self) -> Duration {
        MAX_MEDIA_TRANSFER_DURATION
    }
    fn minimum_bytes(self) -> usize {
        match self {
            Self::Audio | Self::Video => 64,
            Self::Photo => 32,
        }
    }
    fn render_kind(self) -> &'static str {
        match self {
            Self::Audio => "voice",
            Self::Photo => "image",
            Self::Video => "video",
        }
    }
    fn media_type(self) -> &'static str {
        match self {
            Self::Audio => "audio",
            Self::Photo => "image",
            Self::Video => "video",
        }
    }
    fn root(self) -> (&'static [u8], [u8; 32]) {
        match self {
            Self::Audio | Self::Photo => (ROOT_CA_DER, ROOT_CA_SHA256),
            Self::Video => (VIDEO_ROOT_CA_DER, VIDEO_ROOT_CA_SHA256),
        }
    }
}

/// Exact origin check on the raw wire string, before any URL normalization.
/// Signed query bytes are left untouched. Host, scheme, port and redirects
/// cannot be selected by an attachment.
fn video_parts(raw: &str) -> Result<(&str, &str)> {
    const SCHEME: &str = "https://";
    const SUFFIX: &str = ".okcdn.ru";
    let bytes = raw.as_bytes();
    if bytes.len() > MAX_URL_BYTES
        || !raw.starts_with(SCHEME)
        || bytes
            .iter()
            .any(|byte| !(0x21..=0x7e).contains(byte) || *byte == b'\\' || *byte == b'#')
    {
        return Err(MediaError::InvalidUrl);
    }
    let rest = &raw[SCHEME.len()..];
    let slash = rest.find('/').ok_or(MediaError::InvalidUrl)?;
    let host = &rest[..slash];
    let target = &rest[slash..];
    let digits = host
        .strip_prefix("maxvd")
        .and_then(|value| value.strip_suffix(SUFFIX))
        .ok_or(MediaError::InvalidUrl)?;
    if !(1..=4).contains(&digits.len()) || !digits.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(MediaError::InvalidUrl);
    }
    validate_percent_escapes(target)?;
    Ok((host, target))
}

fn validate_percent_escapes(target: &str) -> Result<()> {
    let mut index = 0;
    while index < target.len() {
        if target.as_bytes()[index] == b'%' {
            if target.len() - index < 3
                || !target.as_bytes()[index + 1].is_ascii_hexdigit()
                || !target.as_bytes()[index + 2].is_ascii_hexdigit()
            {
                return Err(MediaError::InvalidUrl);
            }
            index += 3;
        } else {
            index += 1;
        }
    }
    Ok(())
}

fn exact_target(raw: &str, kind: CdnKind) -> Result<&str> {
    let prefix = match kind {
        CdnKind::Audio => "https://a.oneme.ru",
        CdnKind::Photo => "https://i.oneme.ru",
        CdnKind::Video => return video_parts(raw).map(|(_, target)| target),
    };
    let bytes = raw.as_bytes();
    if bytes.len() > MAX_URL_BYTES
        || !raw.starts_with(prefix)
        || bytes.len() <= prefix.len()
        || bytes[prefix.len()] != b'/'
        || bytes
            .iter()
            .any(|byte| !(0x21..=0x7e).contains(byte) || *byte == b'\\' || *byte == b'#')
    {
        return Err(MediaError::InvalidUrl);
    }
    let target = &raw[prefix.len()..];
    validate_percent_escapes(target)?;
    Ok(target)
}

pub(crate) fn eligible_audio_url(raw: &str) -> bool {
    exact_target(raw, CdnKind::Audio).is_ok()
}

pub(crate) fn eligible_photo_url(raw: &str) -> bool {
    exact_target(raw, CdnKind::Photo).is_ok()
}

pub(crate) fn eligible_video_url(raw: &str) -> bool {
    video_parts(raw).is_ok()
}

fn public_ipv4(value: Ipv4Addr) -> bool {
    let octets = value.octets();
    !(value.is_private()
        || value.is_loopback()
        || value.is_link_local()
        || value.is_multicast()
        || value.is_broadcast()
        || value.is_documentation()
        || value.is_unspecified()
        || octets[0] == 0
        || (octets[0] == 100 && (64..=127).contains(&octets[1]))
        || (octets[0] == 192 && octets[1] == 0 && octets[2] == 0)
        || (octets[0] == 198 && (18..=19).contains(&octets[1]))
        || octets[0] >= 224)
}

fn public_ipv6(value: Ipv6Addr) -> bool {
    if let Some(mapped) = value.to_ipv4_mapped() {
        return public_ipv4(mapped);
    }
    let segments = value.segments();
    (segments[0] & 0xe000) == 0x2000
        && !(segments[0] == 0x2001 && segments[1] == 0x0db8)
        && !value.is_multicast()
}

fn public_address(address: IpAddr) -> bool {
    match address {
        IpAddr::V4(value) => public_ipv4(value),
        IpAddr::V6(value) => public_ipv6(value),
    }
}

fn check_cancel(cancel: &HistoryCancel, deadline: Instant) -> Result<Duration> {
    if cancel.is_cancelled() {
        return Err(MediaError::Cancelled);
    }
    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        return Err(MediaError::Timeout);
    }
    Ok(remaining.min(SOCKET_TIMEOUT))
}

fn classify_io(error: io::Error, cancel: &HistoryCancel) -> MediaError {
    if cancel.is_cancelled() {
        MediaError::Cancelled
    } else if matches!(
        error.kind(),
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
    ) {
        MediaError::Timeout
    } else if error.kind() == io::ErrorKind::InvalidData
        && error
            .get_ref()
            .and_then(|source| source.downcast_ref::<rustls::Error>())
            .is_some()
    {
        MediaError::Tls
    } else {
        MediaError::Network
    }
}

fn classify_read(error: io::Error) -> MediaError {
    if matches!(
        error.kind(),
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
    ) {
        MediaError::Timeout
    } else if error.kind() == io::ErrorKind::InvalidData
        && error
            .get_ref()
            .and_then(|source| source.downcast_ref::<rustls::Error>())
            .is_some()
    {
        MediaError::Tls
    } else {
        MediaError::InvalidResponse
    }
}

fn pinned_cdn_config(kind: CdnKind) -> Result<Arc<ClientConfig>> {
    let (root, expected_sha) = kind.root();
    if Sha256::digest(root).as_slice() != expected_sha {
        return Err(MediaError::TrustPin);
    }
    let mut roots = RootCertStore::empty();
    roots
        .add(CertificateDer::from(root.to_vec()))
        .map_err(|_| MediaError::TrustPin)?;
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let config = ClientConfig::builder_with_provider(provider)
        .with_protocol_versions(&[&rustls::version::TLS13, &rustls::version::TLS12])
        .map_err(|_| MediaError::Tls)?
        .with_root_certificates(roots)
        .with_no_client_auth();
    Ok(Arc::new(config))
}

struct TimedTlsStream<'a> {
    conn: ClientConnection,
    socket: TcpStream,
    cancel: &'a HistoryCancel,
    budget: ActivityBudget,
}
#[derive(Clone, Copy)]
struct ActivityBudget {
    absolute_deadline: Instant,
    idle_deadline: Instant,
}
impl ActivityBudget {
    fn new(absolute_deadline: Instant, now: Instant) -> Self {
        Self {
            absolute_deadline,
            idle_deadline: now + IDLE_DEADLINE,
        }
    }
    fn remaining_at(&self, now: Instant) -> Option<Duration> {
        let absolute = self.absolute_deadline.saturating_duration_since(now);
        let idle = self.idle_deadline.saturating_duration_since(now);
        if absolute.is_zero() || idle.is_zero() {
            None
        } else {
            Some(absolute.min(idle).min(SOCKET_TIMEOUT))
        }
    }
    fn note_progress_at(&mut self, now: Instant) {
        self.idle_deadline = now + IDLE_DEADLINE;
    }
}
struct DeadlineSocket<'a> {
    socket: &'a mut TcpStream,
    cancel: &'a HistoryCancel,
    budget: &'a mut ActivityBudget,
}
impl DeadlineSocket<'_> {
    fn budget(&self) -> io::Result<Duration> {
        if self.cancel.is_cancelled() {
            return Err(io::Error::new(io::ErrorKind::ConnectionAborted, "media cancelled"));
        }
        self.budget.remaining_at(Instant::now()).ok_or_else(|| {
            io::Error::new(io::ErrorKind::TimedOut, "media transfer deadline or idle limit")
        })
    }
}
impl Read for DeadlineSocket<'_> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        loop {
            let budget = self.budget()?;
            self.socket.set_read_timeout(Some(budget))?;
            match self.socket.read(buf) {
                Ok(count) => {
                    if count > 0 {
                        self.budget.note_progress_at(Instant::now());
                    }
                    return Ok(count);
                }
                Err(error) if matches!(error.kind(), io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock) => {
                    if self.cancel.is_cancelled() {
                        return Err(io::Error::new(io::ErrorKind::ConnectionAborted, "media cancelled"));
                    }
                    if self.budget.remaining_at(Instant::now()).is_none() {
                        return Err(error);
                    }
                }
                Err(error) => return Err(error),
            }
        }
    }
}
impl Write for DeadlineSocket<'_> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let budget = self.budget()?;
        self.socket.set_write_timeout(Some(budget))?;
        let count = self.socket.write(buf)?;
        if count > 0 {
            self.budget.note_progress_at(Instant::now());
        }
        Ok(count)
    }
    fn flush(&mut self) -> io::Result<()> {
        let budget = self.budget()?;
        self.socket.set_write_timeout(Some(budget))?;
        self.socket.flush()
    }
}
impl Read for TimedTlsStream<'_> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let mut socket = DeadlineSocket {
            socket: &mut self.socket,
            cancel: self.cancel,
            budget: &mut self.budget,
        };
        Stream::new(&mut self.conn, &mut socket).read(buf)
    }
}
impl Write for TimedTlsStream<'_> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let mut socket = DeadlineSocket {
            socket: &mut self.socket,
            cancel: self.cancel,
            budget: &mut self.budget,
        };
        Stream::new(&mut self.conn, &mut socket).write(buf)
    }
    fn flush(&mut self) -> io::Result<()> {
        let mut socket = DeadlineSocket {
            socket: &mut self.socket,
            cancel: self.cancel,
            budget: &mut self.budget,
        };
        Stream::new(&mut self.conn, &mut socket).flush()
    }
}

fn connect_cdn<'a>(
    kind: CdnKind,
    host: &str,
    cancel: &'a HistoryCancel,
    connect_deadline: Instant,
    absolute_deadline: Instant,
) -> Result<TimedTlsStream<'a>> {
    let config = pinned_cdn_config(kind)?;
    let mut socket = None;
    let mut last_error = MediaError::Network;
    for address in (host, 443)
        .to_socket_addrs()
        .map_err(|_| MediaError::Network)?
        .take(8)
    {
        if !public_address(address.ip()) {
            continue;
        }
        let budget = check_cancel(cancel, connect_deadline.min(absolute_deadline))?;
        match TcpStream::connect_timeout(&address, budget) {
            Ok(candidate) => {
                socket = Some(candidate);
                break;
            }
            Err(error) => last_error = classify_io(error, cancel),
        }
    }
    let socket = socket.ok_or(last_error)?;
    let name = ServerName::try_from(host.to_owned()).map_err(|_| MediaError::Tls)?;
    let conn = ClientConnection::new(config, name).map_err(|_| MediaError::Tls)?;
    let mut stream = TimedTlsStream {
        conn,
        socket,
        cancel,
        budget: ActivityBudget::new(absolute_deadline, Instant::now()),
    };
    for _ in 0..8 {
        if !stream.conn.is_handshaking() {
            return Ok(stream);
        }
        let mut raw = DeadlineSocket {
            socket: &mut stream.socket,
            cancel,
            budget: &mut stream.budget,
        };
        let (read, written) = stream
            .conn
            .complete_io(&mut raw)
            .map_err(|error| classify_io(error, cancel))?;
        if read == 0 && written == 0 {
            return Err(MediaError::Tls);
        }
    }
    Err(MediaError::Tls)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum BodyFraming {
    Length(u64),
    Chunked,
}
struct ResponseHead {
    status: u16,
    framing: Option<BodyFraming>,
    content_type: Option<String>,
}

fn read_line<R: Read>(source: &mut R, budget: &mut usize, max_line: usize) -> Result<Vec<u8>> {
    let mut line = Vec::new();
    loop {
        if *budget == 0 || line.len() >= max_line {
            return Err(MediaError::InvalidResponse);
        }
        let mut byte = [0_u8; 1];
        source.read_exact(&mut byte).map_err(classify_read)?;
        *budget -= 1;
        line.push(byte[0]);
        if byte[0] == b'\n' {
            break;
        }
    }
    if line.len() < 2 || !line.ends_with(b"\r\n") {
        return Err(MediaError::InvalidResponse);
    }
    line.truncate(line.len() - 2);
    if line.iter().any(|byte| !(0x20..=0x7e).contains(byte)) {
        return Err(MediaError::InvalidResponse);
    }
    Ok(line)
}

fn parse_head<R: Read>(source: &mut R) -> Result<ResponseHead> {
    let mut budget = MAX_HEADER_BYTES;
    let status_line = read_line(source, &mut budget, 256)?;
    let status_line = std::str::from_utf8(&status_line).map_err(|_| MediaError::InvalidResponse)?;
    let mut parts = status_line.splitn(3, ' ');
    let protocol = parts.next().ok_or(MediaError::InvalidResponse)?;
    let status = parts
        .next()
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or(MediaError::InvalidResponse)?;
    if !matches!(protocol, "HTTP/1.1" | "HTTP/1.0") || !(100..=599).contains(&status) {
        return Err(MediaError::InvalidResponse);
    }
    let mut length = None;
    let mut transfer = None;
    let mut encoding = None;
    let mut content_type = None;
    loop {
        let line = read_line(source, &mut budget, MAX_HEADER_BYTES)?;
        if line.is_empty() {
            break;
        }
        if line[0] == b' ' || !line.contains(&b':') {
            return Err(MediaError::InvalidResponse);
        }
        let split = line.iter().position(|byte| *byte == b':').unwrap();
        let name = &line[..split];
        if name.is_empty()
            || !name
                .iter()
                .all(|byte| byte.is_ascii_alphanumeric() || *byte == b'-')
        {
            return Err(MediaError::InvalidResponse);
        }
        let value = std::str::from_utf8(&line[split + 1..])
            .map_err(|_| MediaError::InvalidResponse)?
            .trim();
        match name.to_ascii_lowercase().as_slice() {
            b"content-length" if length.is_none() => {
                if value.is_empty()
                    || value.len() > 20
                    || !value.bytes().all(|b| b.is_ascii_digit())
                {
                    return Err(MediaError::InvalidResponse);
                }
                length = Some(
                    value
                        .parse::<u64>()
                        .map_err(|_| MediaError::InvalidResponse)?,
                );
            }
            b"transfer-encoding" if transfer.is_none() => {
                transfer = Some(value.to_ascii_lowercase())
            }
            b"content-encoding" if encoding.is_none() => {
                encoding = Some(value.to_ascii_lowercase())
            }
            b"content-type" if content_type.is_none() => {
                content_type = Some(value.to_ascii_lowercase())
            }
            b"content-length" | b"transfer-encoding" | b"content-encoding" | b"content-type" => {
                return Err(MediaError::InvalidResponse);
            }
            _ => {}
        }
    }
    if encoding.as_deref().is_some_and(|value| value != "identity") {
        return Err(MediaError::InvalidResponse);
    }
    let framing = match (length, transfer.as_deref()) {
        (Some(size), None) => Some(BodyFraming::Length(size)),
        (None, Some("chunked")) => Some(BodyFraming::Chunked),
        (None, None) => None,
        _ => return Err(MediaError::InvalidResponse),
    };
    Ok(ResponseHead {
        status,
        framing,
        content_type,
    })
}

fn allowed_media_type(kind: CdnKind, content_type: Option<&str>) -> bool {
    let Some(value) = content_type else {
        return true;
    };
    let base = value.split(';').next().unwrap_or("").trim();
    (if matches!(kind, CdnKind::Video) {
        base == "video/mp4"
    } else {
        base.starts_with(&format!("{}/", kind.media_type()))
    }) || base == "application/octet-stream"
}

struct MediaSink<'a, F: FnMut(&[u8]) -> Result<()>> {
    write: &'a mut F,
    kind: CdnKind,
    prefix: Zeroizing<[u8; 64]>,
    prefix_len: usize,
    approved: bool,
    received: u64,
}
impl<F: FnMut(&[u8]) -> Result<()>> MediaSink<'_, F> {
    fn push(&mut self, mut data: &[u8]) -> Result<()> {
        self.received = self
            .received
            .checked_add(data.len() as u64)
            .filter(|total| *total <= self.kind.max_bytes())
            .ok_or(MediaError::TooLarge)?;
        if !self.approved {
            let to_copy = (self.kind.minimum_bytes() - self.prefix_len).min(data.len());
            self.prefix[self.prefix_len..self.prefix_len + to_copy]
                .copy_from_slice(&data[..to_copy]);
            self.prefix_len += to_copy;
            data = &data[to_copy..];
            if self.prefix_len == self.kind.minimum_bytes() {
                self.approve()?;
            }
        }
        if self.approved && !data.is_empty() {
            (self.write)(data)?;
        }
        Ok(())
    }
    fn approve(&mut self) -> Result<()> {
        let head = &self.prefix[..self.prefix_len];
        let detected = export_render::inline_kind(self.kind.render_kind(), head);
        if detected.0 != Some(self.kind.media_type())
            || matches!(self.kind, CdnKind::Video) && detected.1 != Some("video/mp4")
            || detected.1 == Some("audio/ogg") && !valid_initial_ogg(head)
            || matches!(detected.1, Some("audio/mp4" | "video/mp4")) && !valid_initial_mp4(head)
        {
            return Err(MediaError::InvalidMedia);
        }
        (self.write)(&self.prefix[..self.prefix_len])?;
        self.approved = true;
        Ok(())
    }
    fn finish(&mut self) -> Result<u64> {
        if !self.approved {
            // A four-byte magic such as OggS is not a complete asset. No
            // prefix reaches the selected archive until enough structure is
            // available for this media kind.
            return Err(MediaError::InvalidMedia);
        }
        Ok(self.received)
    }
}

fn valid_initial_ogg(head: &[u8]) -> bool {
    if head.len() < 64 || !head.starts_with(b"OggS") || head[4] != 0 || head[5] & 0x02 == 0 {
        return false;
    }
    let segment_count = head[26] as usize;
    let packet_start = 27 + segment_count;
    if segment_count == 0 || packet_start + 8 > head.len() || head[27] < 8 {
        return false;
    }
    let first_packet = &head[packet_start..];
    first_packet.starts_with(b"OpusHead")
        || first_packet.starts_with(b"\x01vorbis")
        || first_packet.starts_with(b"Speex   ")
}

fn valid_initial_mp4(head: &[u8]) -> bool {
    if head.len() < 64 || head.get(4..8) != Some(b"ftyp") {
        return false;
    }
    let box_bytes = u32::from_be_bytes(head[..4].try_into().unwrap());
    (16..=4096).contains(&box_bytes)
        && head[8..12].iter().any(|byte| *byte != 0)
        && head[8..12]
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || *byte == b' ')
}

fn copy_media_body_cancellable<R: Read, F: FnMut(&[u8]) -> Result<()>>(
    source: &mut R,
    framing: BodyFraming,
    kind: CdnKind,
    cancel: &HistoryCancel,
    write: &mut F,
) -> Result<u64> {
    let mut sink = MediaSink {
        write,
        kind,
        prefix: Zeroizing::new([0; 64]),
        prefix_len: 0,
        approved: false,
        received: 0,
    };
    let mut buffer = Zeroizing::new(vec![0_u8; TRANSFER_CHUNK_BYTES]);
    match framing {
        BodyFraming::Length(length) => {
            if length < kind.minimum_bytes() as u64 {
                return Err(MediaError::InvalidMedia);
            }
            if length > kind.max_bytes() {
                return Err(MediaError::TooLarge);
            }
            let mut remaining = length;
            while remaining > 0 {
                if cancel.is_cancelled() {
                    return Err(MediaError::Cancelled);
                }
                let count = remaining.min(buffer.len() as u64) as usize;
                source
                    .read_exact(&mut buffer[..count])
                    .map_err(classify_read)?;
                sink.push(&buffer[..count])?;
                remaining -= count as u64;
            }
        }
        BodyFraming::Chunked => {
            let mut trailer_budget = MAX_HEADER_BYTES;
            loop {
                if cancel.is_cancelled() {
                    return Err(MediaError::Cancelled);
                }
                let line = read_line(source, &mut trailer_budget, 128)?;
                let size = line.split(|byte| *byte == b';').next().unwrap_or(&[]);
                if size.is_empty() || size.len() > 16 || !size.iter().all(u8::is_ascii_hexdigit) {
                    return Err(MediaError::InvalidResponse);
                }
                let size = u64::from_str_radix(
                    std::str::from_utf8(size).map_err(|_| MediaError::InvalidResponse)?,
                    16,
                )
                .map_err(|_| MediaError::InvalidResponse)?;
                if size == 0 {
                    loop {
                        if read_line(source, &mut trailer_budget, MAX_HEADER_BYTES)?.is_empty() {
                            break;
                        }
                    }
                    break;
                }
                if size > kind.max_bytes() - sink.received {
                    return Err(MediaError::TooLarge);
                }
                let mut remaining = size;
                while remaining > 0 {
                    if cancel.is_cancelled() {
                        return Err(MediaError::Cancelled);
                    }
                    let count = remaining.min(buffer.len() as u64) as usize;
                    source
                        .read_exact(&mut buffer[..count])
                        .map_err(classify_read)?;
                    sink.push(&buffer[..count])?;
                    remaining -= count as u64;
                }
                let mut terminator = [0_u8; 2];
                source.read_exact(&mut terminator).map_err(classify_read)?;
                if terminator != *b"\r\n" {
                    return Err(MediaError::InvalidResponse);
                }
            }
        }
    }
    if cancel.is_cancelled() {
        return Err(MediaError::Cancelled);
    }
    sink.finish()
}

#[cfg(test)]
fn copy_media_body<R: Read, F: FnMut(&[u8]) -> Result<()>>(
    source: &mut R,
    framing: BodyFraming,
    kind: CdnKind,
    write: &mut F,
) -> Result<u64> {
    copy_media_body_cancellable(source, framing, kind, &HistoryCancel::new(), write)
}

/// A direct AUDIO URL or PHOTO baseUrl observed in the selected, login-bound
/// GET_HISTORY page. No other hostname or redirection is admitted.
/// The caller owns a one-asset writer lease and settles it after this returns.
fn download_media(
    raw_url: &str,
    kind: CdnKind,
    cancel: &HistoryCancel,
    writer: &mut ExternalMediaWriter,
    absolute_deadline: Instant,
) -> Result<u64> {
    let target = exact_target(raw_url, kind)?;
    let host = kind
        .fixed_host()
        .or_else(|| video_parts(raw_url).ok().map(|(host, _)| host))
        .ok_or(MediaError::InvalidUrl)?;
    let connect_deadline = Instant::now() + CONNECT_DEADLINE;
    let mut stream = connect_cdn(kind, host, cancel, connect_deadline, absolute_deadline)?;
    let accept = match kind {
        CdnKind::Audio => "audio/*, application/octet-stream",
        CdnKind::Photo => "image/*, application/octet-stream",
        CdnKind::Video => "video/mp4, application/octet-stream",
    };
    let request = Zeroizing::new(format!(
        "GET {target} HTTP/1.1\r\nHost: {host}\r\nAccept: {accept}\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n"
    ));
    stream
        .write_all(request.as_bytes())
        .map_err(|error| classify_io(error, cancel))?;
    stream.flush().map_err(|error| classify_io(error, cancel))?;
    let mut source = BufReader::with_capacity(TRANSFER_CHUNK_BYTES, stream);
    let head = parse_head(&mut source).map_err(|error| {
        if cancel.is_cancelled() {
            MediaError::Cancelled
        } else {
            error
        }
    })?;
    match head.status {
        200 => {}
        401 | 403 => return Err(MediaError::PermissionDenied),
        404 | 410 => return Err(MediaError::NotAvailable),
        _ => return Err(MediaError::InvalidResponse),
    }
    if !allowed_media_type(kind, head.content_type.as_deref()) {
        return Err(MediaError::InvalidMedia);
    }
    let framing = head.framing.ok_or(MediaError::InvalidResponse)?;
    copy_media_body_cancellable(&mut source, framing, kind, cancel, &mut |bytes| {
        writer.write(bytes).map_err(MediaError::Storage)
    })
    .map_err(|error| {
        if cancel.is_cancelled() {
            MediaError::Cancelled
        } else {
            error
        }
    })
}

pub(crate) fn download_audio(
    raw_url: &str,
    cancel: &HistoryCancel,
    writer: &mut ExternalMediaWriter,
) -> Result<u64> {
    download_media(raw_url, CdnKind::Audio, cancel, writer,
        Instant::now() + CdnKind::Audio.max_transfer_duration())
}

pub(crate) fn download_photo(
    raw_url: &str,
    cancel: &HistoryCancel,
    writer: &mut ExternalMediaWriter,
) -> Result<u64> {
    download_media(raw_url, CdnKind::Photo, cancel, writer,
        Instant::now() + CdnKind::Photo.max_transfer_duration())
}

pub(crate) fn download_video(
    raw_url: &str,
    cancel: &HistoryCancel,
    writer: &mut ExternalMediaWriter,
    absolute_deadline: Instant,
) -> Result<u64> {
    download_media(raw_url, CdnKind::Video, cancel, writer, absolute_deadline)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn active_transfer_can_progress_past_two_minutes_but_idle_and_absolute_limits_hold() {
        let start = Instant::now();
        let absolute = start + Duration::from_secs(600);
        let mut budget = ActivityBudget::new(absolute, start);
        for elapsed in (20..=200).step_by(20) {
            let now = start + Duration::from_secs(elapsed);
            assert!(budget.remaining_at(now).is_some());
            budget.note_progress_at(now);
        }
        assert!(budget.remaining_at(start + Duration::from_secs(229)).is_some());
        assert!(budget.remaining_at(start + Duration::from_secs(230)).is_none());
        budget.note_progress_at(start + Duration::from_secs(590));
        assert!(budget.remaining_at(start + Duration::from_secs(599)).is_some());
        assert!(budget.remaining_at(absolute).is_none());
    }

    #[test]
    fn tls_record_read_error_is_not_relabelled_as_recoverable_media() {
        let error = io::Error::new(io::ErrorKind::InvalidData, rustls::Error::DecryptError);
        assert_eq!(classify_read(error), MediaError::Tls);
    }

    #[test]
    fn video_stream_over_one_gib_is_counted_without_allocating_the_asset() {
        struct LogicalVideo {
            position: u64,
            remaining: u64,
        }
        impl Read for LogicalVideo {
            fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
                let count = self.remaining.min(output.len() as u64) as usize;
                if count == 0 {
                    return Ok(0);
                }
                output[..count].fill(0);
                let mut prefix = [0_u8; 64];
                prefix[..4].copy_from_slice(&24_u32.to_be_bytes());
                prefix[4..8].copy_from_slice(b"ftyp");
                prefix[8..12].copy_from_slice(b"isom");
                if self.position < prefix.len() as u64 {
                    let length = count.min(prefix.len() - self.position as usize);
                    output[..length].copy_from_slice(
                        &prefix[self.position as usize..self.position as usize + length],
                    );
                }
                self.position += count as u64;
                self.remaining -= count as u64;
                Ok(count)
            }
        }
        let size = 1024_u64 * 1024 * 1024 + 64;
        let mut source = LogicalVideo { position: 0, remaining: size };
        let mut counted = 0_u64;
        let copied = copy_media_body(
            &mut source,
            BodyFraming::Length(size),
            CdnKind::Video,
            &mut |part| {
                counted += part.len() as u64;
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(copied, size);
        assert_eq!(counted, size);
        assert_eq!(source.remaining, 0);
        for kind in [CdnKind::Audio, CdnKind::Photo, CdnKind::Video] {
            assert_eq!(kind.max_bytes(), 4 * 1024_u64 * 1024 * 1024);
            assert_eq!(kind.max_transfer_duration(), Duration::from_secs(4 * 60 * 60));
        }
    }

    #[test]
    fn cancelling_mid_body_stops_before_the_next_chunk() {
        struct CancelAfterFirstRead {
            inner: Cursor<Vec<u8>>,
            cancel: HistoryCancel,
            first: bool,
        }
        impl Read for CancelAfterFirstRead {
            fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
                let count = self.inner.read(output)?;
                if count > 0 && self.first {
                    self.first = false;
                    self.cancel.cancel();
                }
                Ok(count)
            }
        }
        let mut body = synthetic_ogg_prefix();
        body.resize(128 * 1024, 0);
        let cancel = HistoryCancel::new();
        let mut source = CancelAfterFirstRead {
            inner: Cursor::new(body),
            cancel: cancel.clone(),
            first: true,
        };
        let mut accepted = 0usize;
        let result = copy_media_body_cancellable(
            &mut source,
            BodyFraming::Length(128 * 1024),
            CdnKind::Audio,
            &cancel,
            &mut |chunk| {
                accepted += chunk.len();
                Ok(())
            },
        );
        assert_eq!(result, Err(MediaError::Cancelled));
        assert!(cancel.is_cancelled());
        assert_eq!(accepted, TRANSFER_CHUNK_BYTES);
    }

    fn synthetic_ogg_prefix() -> Vec<u8> {
        let mut bytes = vec![0_u8; 128];
        bytes[..4].copy_from_slice(b"OggS");
        bytes[5] = 0x02; // beginning of stream
        bytes[26] = 1; // one lacing entry
        bytes[27] = 19; // OpusHead packet length
        bytes[28..36].copy_from_slice(b"OpusHead");
        bytes[36] = 1; // OpusHead version
        bytes
    }

    #[test]
    fn raw_audio_origin_is_exact_and_query_is_opaque() {
        assert_eq!(
            exact_target("https://a.oneme.ru/file?sig=a%2Fb", CdnKind::Audio).unwrap(),
            "/file?sig=a%2Fb"
        );
        for value in [
            "http://a.oneme.ru/file",
            "https://A.oneme.ru/file",
            "https://a.oneme.ru:443/file",
            "https://a.oneme.ru.evil.test/file",
            "https://user@a.oneme.ru/file",
            "https://a.oneme.ru/file#fragment",
            "https://a.oneme.ru/file\\other",
            "https://a.oneme.ru/file%0",
            "https://a.oneme.ru/file\r\nHost: evil.test",
            "https://127.0.0.1/file",
        ] {
            assert_eq!(
                exact_target(value, CdnKind::Audio),
                Err(MediaError::InvalidUrl)
            );
        }
        assert_eq!(
            exact_target("https://i.oneme.ru/photo", CdnKind::Photo).unwrap(),
            "/photo"
        );
        assert_eq!(
            exact_target("https://a.oneme.ru/photo", CdnKind::Photo),
            Err(MediaError::InvalidUrl)
        );
    }

    #[test]
    fn video_source_allows_only_observed_okcdn_family_and_pinned_root() {
        assert_eq!(
            video_parts("https://maxvd291.okcdn.ru/synthetic.mp4?sig=a%2Fb"),
            Ok(("maxvd291.okcdn.ru", "/synthetic.mp4?sig=a%2Fb"))
        );
        assert_eq!(
            Sha256::digest(VIDEO_ROOT_CA_DER).as_slice(),
            VIDEO_ROOT_CA_SHA256
        );
        assert!(pinned_cdn_config(CdnKind::Video).is_ok());
        for value in [
            "http://maxvd291.okcdn.ru/video.mp4",
            "https://maxvd291.okcdn.ru:443/video.mp4",
            "https://user@maxvd291.okcdn.ru/video.mp4",
            "https://maxvd291.okcdn.ru.evil.test/video.mp4",
            "https://other.okcdn.ru/video.mp4",
            "https://MAXVD291.okcdn.ru/video.mp4",
            "https://maxvd291.okcdn.ru/video.mp4#part",
            "https://maxvd291.okcdn.ru/video.mp4\\other",
            "https://maxvd291.okcdn.ru/video.mp4?sig=%0",
            "https://127.0.0.1/video.mp4",
        ] {
            assert!(!eligible_video_url(value));
        }
    }

    #[test]
    fn bounded_mp4_body_requires_signature_and_respects_size_cap() {
        let mut bytes = vec![0_u8; 96];
        bytes[..4].copy_from_slice(&24_u32.to_be_bytes());
        bytes[4..8].copy_from_slice(b"ftyp");
        bytes[8..12].copy_from_slice(b"isom");
        bytes[16..20].copy_from_slice(b"isom");
        bytes[20..24].copy_from_slice(b"mp42");
        bytes[24..28].copy_from_slice(&72_u32.to_be_bytes());
        bytes[28..32].copy_from_slice(b"mdat");
        let mut output = Vec::new();
        let count = copy_media_body(
            &mut Cursor::new(bytes.as_slice()),
            BodyFraming::Length(bytes.len() as u64),
            CdnKind::Video,
            &mut |part| {
                output.extend_from_slice(part);
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(count, bytes.len() as u64);
        assert_eq!(output, bytes);
        assert_eq!(
            copy_media_body(
                &mut Cursor::new(bytes.as_slice()),
                BodyFraming::Length(MAX_MEDIA_BYTES + 1),
                CdnKind::Video,
                &mut |_| Ok(())
            ),
            Err(MediaError::TooLarge)
        );
        assert_eq!(
            copy_media_body(
                &mut Cursor::new(b"not an mp4".as_slice()),
                BodyFraming::Length(10),
                CdnKind::Video,
                &mut |_| Ok(())
            ),
            Err(MediaError::InvalidMedia)
        );
        assert_eq!(
            copy_media_body(
                &mut Cursor::new(b"\x00\x00\x00\x14ftyp".as_slice()),
                BodyFraming::Length(8),
                CdnKind::Video,
                &mut |_| Ok(())
            ),
            Err(MediaError::InvalidMedia)
        );
        assert!(allowed_media_type(CdnKind::Video, Some("video/mp4")));
        assert!(!allowed_media_type(CdnKind::Video, Some("text/html")));
    }

    #[test]
    fn cdn_address_policy_rejects_private_and_reserved_ranges() {
        for value in [
            "127.0.0.1",
            "10.1.2.3",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.1.1",
            "100.64.1.1",
            "198.19.1.1",
            "203.0.113.5",
            "::1",
            "fc00::1",
            "fe80::1",
            "2001:db8::1",
        ] {
            assert!(!public_address(value.parse().unwrap()));
        }
        assert!(public_address("1.1.1.1".parse().unwrap()));
        assert!(public_address("2606:4700:4700::1111".parse().unwrap()));
    }

    #[test]
    fn content_headers_reject_redirects_conflicting_lengths_and_encoding() {
        let mut source = Cursor::new(
            b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\nContent-Type: audio/ogg\r\n\r\n".as_slice(),
        );
        let head = parse_head(&mut source).unwrap();
        assert_eq!(head.framing, Some(BodyFraming::Length(12)));
        assert!(allowed_media_type(
            CdnKind::Audio,
            head.content_type.as_deref()
        ));
        for header in [
            "Content-Length: 12\r\nContent-Length: 12",
            "Content-Length: 12\r\nTransfer-Encoding: chunked",
            "Content-Encoding: gzip",
            "Transfer-Encoding: gzip",
        ] {
            let reply = format!("HTTP/1.1 200 OK\r\n{header}\r\n\r\n");
            assert!(parse_head(&mut Cursor::new(reply.as_bytes())).is_err());
        }
        assert!(!allowed_media_type(CdnKind::Audio, Some("text/html")));
        assert!(allowed_media_type(CdnKind::Photo, Some("image/webp")));
        assert!(!allowed_media_type(CdnKind::Photo, Some("audio/ogg")));
    }

    #[test]
    fn bounded_audio_body_requires_signature_and_complete_framing() {
        let payload = synthetic_ogg_prefix();
        let mut output = Vec::new();
        let length = copy_media_body(
            &mut Cursor::new(payload.as_slice()),
            BodyFraming::Length(payload.len() as u64),
            CdnKind::Audio,
            &mut |bytes| {
                output.extend_from_slice(bytes);
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(length, payload.len() as u64);
        assert_eq!(output, payload);
        for malformed in [b"OggS".as_slice(), &[b'O', b'g', b'g', b'S', 0, 0][..]] {
            let mut leaked = Vec::new();
            assert_eq!(
                copy_media_body(
                    &mut Cursor::new(malformed),
                    BodyFraming::Length(malformed.len() as u64),
                    CdnKind::Audio,
                    &mut |bytes| {
                        leaked.extend_from_slice(bytes);
                        Ok(())
                    }
                ),
                Err(MediaError::InvalidMedia)
            );
            assert!(leaked.is_empty());
        }
        let fake_ogg = vec![b'O', b'g', b'g', b'S']
            .into_iter()
            .chain(std::iter::repeat(0).take(60))
            .collect::<Vec<_>>();
        assert_eq!(
            copy_media_body(
                &mut Cursor::new(fake_ogg.as_slice()),
                BodyFraming::Length(fake_ogg.len() as u64),
                CdnKind::Audio,
                &mut |_| Ok(())
            ),
            Err(MediaError::InvalidMedia)
        );
        let mut output = Vec::new();
        assert_eq!(
            copy_media_body(
                &mut Cursor::new(payload.as_slice()),
                BodyFraming::Length(payload.len() as u64 + 1),
                CdnKind::Audio,
                &mut |bytes| {
                    output.extend_from_slice(bytes);
                    Ok(())
                },
            ),
            Err(MediaError::InvalidResponse)
        );
        let html = b"<html>not audio</html>";
        assert_eq!(
            copy_media_body(
                &mut Cursor::new(html.as_slice()),
                BodyFraming::Length(html.len() as u64),
                CdnKind::Audio,
                &mut |_| Ok(())
            ),
            Err(MediaError::InvalidMedia)
        );
    }

    #[test]
    fn chunked_audio_requires_terminal_chunk() {
        let bytes = synthetic_ogg_prefix();
        let mut payload = format!("{:x}\r\n", bytes.len()).into_bytes();
        payload.extend_from_slice(&bytes);
        payload.extend_from_slice(b"\r\n0\r\n\r\n");
        let mut output = Vec::new();
        let count = copy_media_body(
            &mut Cursor::new(payload.as_slice()),
            BodyFraming::Chunked,
            CdnKind::Audio,
            &mut |bytes| {
                output.extend_from_slice(bytes);
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(count, bytes.len() as u64);
        assert_eq!(output, bytes);
        let truncated = &payload[..payload.len() - 5];
        assert_eq!(
            copy_media_body(
                &mut Cursor::new(truncated),
                BodyFraming::Chunked,
                CdnKind::Audio,
                &mut |_| Ok(())
            ),
            Err(MediaError::InvalidResponse)
        );
    }

    #[test]
    fn photo_accepts_full_image_signatures_and_rejects_html() {
        let webp = b"RIFF\x18\x00\x00\x00WEBPVP8Xsynthetic-photo-data";
        let mut output = Vec::new();
        assert_eq!(
            copy_media_body(
                &mut Cursor::new(webp.as_slice()),
                BodyFraming::Length(webp.len() as u64),
                CdnKind::Photo,
                &mut |bytes| {
                    output.extend_from_slice(bytes);
                    Ok(())
                },
            ),
            Ok(webp.len() as u64)
        );
        assert_eq!(output, webp);
        assert_eq!(
            copy_media_body(
                &mut Cursor::new(b"<html>bad</html>".as_slice()),
                BodyFraming::Length(16),
                CdnKind::Photo,
                &mut |_| Ok(()),
            ),
            Err(MediaError::InvalidMedia)
        );
    }
}
