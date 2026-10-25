//! Host-only, short-lived acknowledgement of passive retention infrastructure.
//! A saved preference cannot create this status or make pending RAM durable.
use serde_json::{Value, json};
use std::{sync::Mutex, time::{Duration, Instant}};

const MAX_COUNT: u64 = 1_000_000_000;
const STATUS_TTL: Duration = Duration::from_secs(10);

/// ABI1, 184 bytes on Windows x64. All booleans are u32 to reject bad values.
/// C++ copies only count-only native hook/worker state and the preference
/// revision acknowledged by the host; no account, chat, ID, text, or path.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct NativeStatus {
    pub size: u32,
    pub version: u32,
    pub error: u32,
    pub installed: u32,
    pub images_exact: u32,
    pub listeners: u32,
    pub gates: u32,
    pub owner_join: u32,
    pub store_ready: u32,
    pub worker_available: u32,
    pub worker_ready: u32,
    pub preference_enabled: u32,
    pub applied: u32,
    pub reserved: u32,
    pub generation: u64,
    pub revision: [u8; 64],
    pub captured: u64,
    pub marked: u64,
    pub missing: u64,
    pub missed: u64,
    pub failures: u64,
    pub queued: u32,
    pub in_flight: u32,
    pub pending: u32,
    pub reserved_tail: u32,
}

impl NativeStatus {
    fn valid(&self) -> bool {
        let flags = [self.installed, self.images_exact, self.owner_join,
            self.store_ready, self.worker_available, self.worker_ready,
            self.preference_enabled, self.applied];
        let hex = self.revision.iter().all(|byte| byte.is_ascii_digit()
            || (b'a'..=b'f').contains(byte));
        let empty_revision = self.revision == [0; 64];
        self.size as usize == std::mem::size_of::<Self>() && self.version == 1
            && self.error <= 7 && self.listeners <= 3 && self.gates <= 3
            && self.reserved == 0 && self.reserved_tail == 0
            && flags.iter().all(|flag| *flag <= 1)
            && [self.captured, self.marked, self.missing, self.missed, self.failures]
                .iter().all(|count| *count <= MAX_COUNT)
            && [self.queued, self.in_flight, self.pending]
                .iter().all(|count| u64::from(*count) <= MAX_COUNT)
            && (hex || self.applied == 0 && empty_revision)
            && (self.applied == 0 || self.generation > 0 && hex)
            && (self.worker_ready == 0 || self.worker_available == 1
                && self.preference_enabled == 1 && self.applied == 1 && self.error == 0)
    }

    fn infrastructure_ready(&self) -> bool {
        self.installed == 1 && self.images_exact == 1 && self.listeners == 3
            && self.gates == 0 && self.owner_join == 1 && self.store_ready == 1
            && self.worker_available == 1 && self.error == 0
    }

    fn error_code(&self) -> Option<&'static str> {
        match self.error {
            0 => None,
            1 => Some("preflight"),
            2 => Some("route"),
            3 => Some("queue_full"),
            4 => Some("buffer_full"),
            5 => Some("storage"),
            6 => Some("stalled"),
            7 => Some("shutdown"),
            _ => Some("retention_unavailable"),
        }
    }
}

#[derive(Default)]
struct Inner {
    observed: Option<(Instant, NativeStatus)>,
    ever_seen: bool,
    last_generation: u64,
    must_exceed_generation: Option<u64>,
}

#[derive(Default)]
pub struct RetentionAck { inner: Mutex<Inner> }

impl RetentionAck {
    pub fn update(&self, status: NativeStatus) -> bool {
        let Ok(mut inner) = self.inner.lock() else { return false; };
        inner.ever_seen = true;
        // A delayed healthy poll must not undo a terminal worker failure for
        // the same applied generation. The native worker revokes permanently;
        // a later generation is required to demonstrate renewed availability.
        if inner.observed.as_ref().is_some_and(|(_, old)| old.generation > 0
            && old.generation == status.generation && old.applied == 1
            && old.error != 0 && status.error == 0) {
            return false;
        }
        if !status.valid() || status.generation < inner.last_generation
            || inner.observed.as_ref().is_some_and(|(_, old)| old.generation == status.generation
                && old.applied == 1 && status.applied == 1 && old.revision != status.revision) {
            inner.observed = None;
            return false;
        }
        inner.last_generation = status.generation;
        inner.observed = Some((Instant::now(), status));
        true
    }

    pub fn invalidate(&self) {
        if let Ok(mut inner) = self.inner.lock() { inner.observed = None; }
    }

    /// A successful saved-setting CAS requires a later native generation.
    /// This prevents an old true ACK from confirming a true→false→true cycle.
    pub fn preference_saved(&self) {
        if let Ok(mut inner) = self.inner.lock() {
            inner.must_exceed_generation = Some(inner.last_generation);
        }
    }

    pub fn infrastructure_ready(&self) -> bool {
        self.inner.lock().ok().and_then(|inner| Self::fresh(&inner, Instant::now()))
            .is_some_and(|status| status.infrastructure_ready())
    }

    fn fresh(inner: &Inner, now: Instant) -> Option<NativeStatus> {
        inner.observed.as_ref().and_then(|(seen, status)|
            now.checked_duration_since(*seen).filter(|age| *age <= STATUS_TTL).map(|_| *status))
    }

    pub fn status(&self, mut desired: Value) -> Value {
        self.status_at(&mut desired, Instant::now());
        desired
    }

    fn status_at(&self, desired: &mut Value, now: Instant) {
        let (selected, ever_seen, must_exceed) = self.inner.lock().ok()
            .map(|inner| (Self::fresh(&inner, now), inner.ever_seen, inner.must_exceed_generation))
            .unwrap_or((None, true, None));
        let ready = selected.is_some_and(|status| status.infrastructure_ready());
        let saved = desired["enabled"] == true;
        let revision = desired["revision"].as_str().map(str::as_bytes);
        let applied = selected.is_some_and(|status| status.applied == 1
            && status.generation > must_exceed.unwrap_or(0)
            && revision == Some(&status.revision[..]));
        let enabled = ready && applied && saved && selected.is_some_and(|status|
            status.worker_ready == 1 && status.preference_enabled == 1);
        let error = match selected {
            Some(status) => status.error_code().or_else(||
                (!ready).then_some("retention_not_ready")),
            None if ever_seen => Some("host_status_stale"),
            None => Some("retention_not_integrated"),
        };
        let mut runtime = json!({"ready":ready,"applied":applied,"enabled":enabled,
            "historySyncVerified":false,"error":error,
            "retainedEvents":null,"retainedMessages":null});
        if let Some(status) = selected {
            runtime["queue"] = json!({"queued":status.queued,"inFlight":status.in_flight,
                "pending":status.pending,"captured":status.captured,"marked":status.marked,
                "missing":status.missing,"missed":status.missed,"failures":status.failures});
        }
        desired["runtime"] = runtime;
        desired["capabilities"] = json!({"capture":selected.is_some() || ever_seen,
            "lookup":true,"enable":ready && !enabled,"disable":true});
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn status(revision: &str, enabled: bool, generation: u64) -> NativeStatus {
        let mut bytes = [0_u8; 64];
        bytes.copy_from_slice(revision.as_bytes());
        NativeStatus { size:184, version:1, error:0, installed:1, images_exact:1,
            listeners:3, gates:0, owner_join:1, store_ready:1, worker_available:1,
            worker_ready:enabled.into(), preference_enabled:enabled.into(), applied:1,
            reserved:0, generation, revision:bytes, captured:0, marked:0, missing:0,
            missed:0, failures:0, queued:0, in_flight:0, pending:0, reserved_tail:0 }
    }
    fn desired(enabled: bool) -> Value {
        json!({"schema":2,"enabled":enabled,
            "revision":crate::retention_preferences::revision(enabled)})
    }

    #[test]
    fn disabled_ready_active_revoke_and_expiry_are_distinct() {
        assert_eq!(std::mem::size_of::<NativeStatus>(), 184);
        let ack = RetentionAck::default();
        assert!(!ack.infrastructure_ready());
        let initially = ack.status(desired(false));
        assert_eq!(initially["runtime"]["ready"], false);
        assert_eq!(initially["capabilities"]["enable"], false);
        let off = status(&crate::retention_preferences::revision(false), false, 1);
        assert!(ack.update(off));
        assert!(ack.infrastructure_ready());
        let ready = ack.status(desired(false));
        assert_eq!(ready["runtime"]["ready"], true);
        assert_eq!(ready["runtime"]["applied"], true);
        assert_eq!(ready["runtime"]["enabled"], false);
        assert_eq!(ready["capabilities"]["enable"], true);
        ack.preference_saved();
        assert_eq!(ack.status(desired(true))["runtime"]["applied"], false);
        let on = status(&crate::retention_preferences::revision(true), true, 2);
        assert!(ack.update(on));
        assert_eq!(ack.status(desired(true))["runtime"]["enabled"], true);
        let mut revoked = on;
        revoked.error = 5;
        revoked.worker_ready = 0;
        revoked.pending = 0;
        assert!(ack.update(revoked));
        let failed = ack.status(desired(true));
        assert_eq!(failed["runtime"]["ready"], false);
        assert_eq!(failed["runtime"]["applied"], true);
        assert_eq!(failed["runtime"]["enabled"], false);
        assert_eq!(failed["runtime"]["error"], "storage");
        assert!(!ack.update(on));
        assert_eq!(ack.status(desired(true))["runtime"]["error"], "storage");
        let mut later = desired(true);
        ack.status_at(&mut later, Instant::now() + Duration::from_secs(11));
        assert_eq!(later["runtime"]["error"], "host_status_stale");
        assert_eq!(later["capabilities"]["capture"], true);
    }

    #[test]
    fn generation_and_invalid_status_cannot_reconfirm_old_preference() {
        let ack = RetentionAck::default();
        let first = status(&crate::retention_preferences::revision(true), true, 1);
        assert!(ack.update(first));
        ack.preference_saved();
        assert_eq!(ack.status(desired(true))["runtime"]["applied"], false);
        assert!(ack.update(first));
        assert_eq!(ack.status(desired(true))["runtime"]["applied"], false);
        let second = status(&crate::retention_preferences::revision(true), true, 2);
        assert!(ack.update(second));
        assert_eq!(ack.status(desired(true))["runtime"]["enabled"], true);
        let mut bad = second;
        bad.pending = u32::MAX;
        assert!(!ack.update(bad));
        assert_eq!(ack.status(desired(true))["runtime"]["enabled"], false);
        assert!(!ack.update(first));
        assert_eq!(ack.status(desired(true))["runtime"]["enabled"], false);
    }
}
