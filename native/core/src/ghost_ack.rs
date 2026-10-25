//! Host-only acknowledgement of the actual in-process C++ ghost hooks.
//! UI requests cannot supply this state. Saving preferences never sets it.
use std::{sync::Mutex,time::{Duration,Instant}};
use serde_json::{Value,json};

#[repr(C)]
#[derive(Clone,Copy)]
pub struct NativePolicy {
    pub size:u32, pub version:u32, pub revision:[u8;64],
    pub enabled:u8,pub hide_typing:u8,pub hide_read:u8,pub hide_online:u8,pub hide_stories:u8,
    pub reserved:[u8;3],
}
#[repr(C)]
#[derive(Clone,Copy)]
pub struct NativeStatus {
    pub size:u32,pub version:u32,pub error:u32,pub ready:u32,pub policy:NativePolicy,
    pub blocked_typing:u64,pub blocked_read:u64,pub blocked_presence:u64,pub blocked_stories:u64,
    pub unread_faults:u64,pub replacements:u32,pub listeners:u32,
}
impl NativeStatus {
    fn valid(&self)->bool {
        self.size as usize==std::mem::size_of::<Self>() && self.version==1 && self.error<=6 && self.ready<=1
            && self.policy.size as usize==std::mem::size_of::<NativePolicy>() && self.policy.version==1
            && self.policy.reserved==[0;3]
            && self.policy.revision.iter().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(b))
            && [self.policy.enabled,self.policy.hide_typing,self.policy.hide_read,self.policy.hide_online,self.policy.hide_stories].iter().all(|v|*v<=1)
            && [self.blocked_typing,self.blocked_read,self.blocked_presence,self.blocked_stories,self.unread_faults].iter().all(|v|*v<=1_000_000)
            && (self.ready==0 || self.error==0 && self.replacements==5 && self.listeners==7)
    }
}
#[derive(Default)]
pub struct GhostAck { observed:Mutex<Option<(Instant,NativeStatus)>> }
impl GhostAck {
    pub fn update(&self,status:NativeStatus)->bool {
        let Ok(mut current)=self.observed.lock() else {return false;};
        if !status.valid() {*current=None;return false;}
        *current=Some((Instant::now(),status));true
    }
    pub fn invalidate(&self) {if let Ok(mut value)=self.observed.lock(){*value=None;}}
    pub fn status(&self,desired:&Value)->Value {self.at(desired,Instant::now())}
    fn at(&self,desired:&Value,now:Instant)->Value {
        let selected=self.observed.lock().ok().and_then(|v|*v)
            .filter(|(seen,_)|now.checked_duration_since(*seen).is_some_and(|age|age<=Duration::from_secs(10))).map(|(_,v)|v);
        let ready=selected.is_some_and(|s|s.ready==1 && s.error==0);
        let flags=&desired["settings"];
        let applied=ready && selected.is_some_and(|s| {
            desired["revision"].as_str().map(str::as_bytes)==Some(&s.policy.revision[..])
                && [ ("enabled",s.policy.enabled),("hideTyping",s.policy.hide_typing),("hideRead",s.policy.hide_read),
                    ("hideOnline",s.policy.hide_online),("hideStories",s.policy.hide_stories) ].iter()
                    .all(|(key,flag)|flags[*key].as_bool()==Some(*flag==1))
        });
        let active=applied && flags["enabled"]==true && ["hideTyping","hideRead","hideOnline","hideStories"].iter().any(|k|flags[*k]==true);
        json!({"scope":"all_accounts","state":if active {"active"}else if applied {"off"}else if ready {"pending"}else {"unavailable"},
            "ready":ready,"features":{"hideTyping":ready,"hideRead":ready,"hideOnline":ready,"hideStories":ready},"applied":applied,
            "blocked":{"typing":selected.map_or(0,|s|s.blocked_typing),"read":selected.map_or(0,|s|s.blocked_read),
                "presence":selected.map_or(0,|s|s.blocked_presence),"stories":selected.map_or(0,|s|s.blocked_stories)}})
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn status(settings:crate::ghost_store::Settings)->NativeStatus {
        let mut revision=[0;64];revision.copy_from_slice(settings.revision().as_bytes());
        NativeStatus {size:144,version:1,error:0,ready:1,policy:NativePolicy {size:80,version:1,revision,
            enabled:settings.enabled.into(),hide_typing:settings.hide_typing.into(),hide_read:settings.hide_read.into(),
            hide_online:settings.hide_online.into(),hide_stories:settings.hide_stories.into(),reserved:[0;3]},
            blocked_typing:0,blocked_read:0,blocked_presence:0,blocked_stories:0,unread_faults:0,replacements:5,listeners:7}
    }
    #[test]
    fn desired_changes_do_not_reuse_ack_and_lease_expires() {
        assert_eq!(std::mem::size_of::<NativePolicy>(),80);assert_eq!(std::mem::size_of::<NativeStatus>(),144);
        let mut settings=crate::ghost_store::Settings::default();let ack=GhostAck::default();
        assert_eq!(ack.status(&settings.snapshot())["state"],"unavailable");
        assert!(ack.update(status(settings)));assert_eq!(ack.status(&settings.snapshot())["state"],"off");
        settings.enabled=true;assert_eq!(ack.status(&settings.snapshot())["state"],"pending");
        assert!(ack.update(status(settings)));assert_eq!(ack.status(&settings.snapshot())["state"],"active");
        assert_eq!(ack.at(&settings.snapshot(),Instant::now()+Duration::from_secs(11))["state"],"unavailable");
        ack.invalidate();assert_eq!(ack.status(&settings.snapshot())["state"],"unavailable");
    }
    #[test]
    fn malformed_native_status_revokes_previous_confirmation() {
        let settings=crate::ghost_store::Settings::default();let ack=GhostAck::default();
        for mutation in 0..7 {
            let mut data=status(settings);assert!(ack.update(data));
            match mutation {0=>data.ready=2,1=>data.policy.hide_read=2,2=>data.error=3,
                3=>data.blocked_read=1_000_001,4=>data.listeners=6,5=>data.policy.revision[0]=b'X',_=>data.policy.reserved[0]=1};
            assert!(!ack.update(data));assert_eq!(ack.status(&settings.snapshot())["applied"],false);
        }
    }
}
