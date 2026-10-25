//! Explicit same-parent root moves for a detached, authenticated recovery stage.
//! No credentials, marker changes, recursive cleanup or automatic rollback.
//! Directory handles identify the moved roots; this does not freeze descendant
//! contents. The caller owns the transfer mutex and closed-client checks and
//! must retain the recovery marker until its complete transaction commits.
use super::{win, Result, StageError, State};
use std::{cell::RefCell, rc::Rc};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RootSwapState {
    Ready,
    SwapFailed,
    Published,
    RolledBack,
    RollbackFailed,
    Committed,
}

/// Process-local progress, not a durable journal. Drop only releases pins and
/// preserves every tree, including a published new MXMOD and the prior root.
/// A process crash between moves therefore requires the separate recovery latch.
pub struct RootSwap {
    state: Rc<RefCell<State>>,
    stage_name: String,
    old: Option<win::Directory>,
    old_name: String,
    old_moved: bool,
    published: bool,
    phase: RootSwapState,
}
impl RootSwap {
    /// Only the consuming PreparedRecovery handoff may create this machine.
    /// It already disarmed cleanup and closed all descendant handles. Do not
    /// require Rc count one here: the consuming wrapper lives until return.
    pub(super) fn new(state: Rc<RefCell<State>>) -> Result<Self> {
        let (stage_name, old) = {
            let state = state.try_borrow().map_err(|_| StageError::InvalidRequest)?;
            if !state.closed
                || !state.sealed
                || !state.recovery_prepared
                || state.cleanup_failed
                || state.failed.get()
                || state.open_sinks.get() != 0
                || state.directories.len() != 1
                || !state.files.is_empty()
            {
                return Err(StageError::InvalidRequest);
            }
            let root = &state.directories[0];
            let Some(suffix) = root.name.strip_prefix(".MXMOD-MData-stage-") else {
                return Err(StageError::InvalidRequest);
            };
            if !crate::startup_status::valid_operation_id(suffix)
                || root
                    .directory
                    .path()
                    .file_name()
                    .and_then(|name| name.to_str())
                    != Some(root.name.as_str())
            {
                return Err(StageError::InvalidRequest);
            }
            state._parent.verify()?;
            root.directory.verify()?;
            let old = state._parent.open_move_child("MXMOD")?;
            (root.name.clone(), old)
        };
        let old_name = format!(
            "MXMOD-MData-before-{}",
            crate::startup_status::new_operation_id().map_err(|_| StageError::IoError)?
        );
        let result = Self {
            state,
            stage_name,
            old,
            old_name,
            old_moved: false,
            published: false,
            phase: RootSwapState::Ready,
        };
        result.revalidate()?;
        Ok(result)
    }

    pub fn state(&self) -> RootSwapState {
        self.phase
    }
    pub fn old_moved(&self) -> bool {
        self.old_moved
    }
    pub fn published(&self) -> bool {
        self.published
    }

    /// Checks retained parent/root identities and the names implied by our
    /// actual move flags. This makes no descendant-content or atomicity claim.
    /// An absent original MXMOD stays an observation; noReplace at publish is
    /// what prevents overwriting a later foreign creation at that destination.
    pub fn revalidate(&self) -> Result<()> {
        let state = self
            .state
            .try_borrow()
            .map_err(|_| StageError::InvalidRequest)?;
        if !state.closed || state.directories.len() != 1 || !state.files.is_empty() {
            return Err(StageError::InvalidRequest);
        }
        state._parent.verify()?;
        let root = &state.directories[0].directory;
        root.verify()?;
        let expected_root = if self.published {
            "MXMOD"
        } else {
            &self.stage_name
        };
        if root.path().file_name().and_then(|name| name.to_str()) != Some(expected_root)
            || root.path().parent() != Some(state._parent.path())
        {
            return Err(StageError::ProfileChanged);
        }
        if let Some(old) = &self.old {
            old.verify()?;
            let expected_old = if self.old_moved {
                &self.old_name
            } else {
                "MXMOD"
            };
            if old.path().file_name().and_then(|name| name.to_str()) != Some(expected_old)
                || old.path().parent() != Some(state._parent.path())
            {
                return Err(StageError::ProfileChanged);
            }
        } else if self.old_moved {
            return Err(StageError::InvalidRequest);
        }
        state._parent.verify()
    }

    /// One attempt: old MXMOD -> unique before-name, then stage -> MXMOD.
    /// Both calls use the same pinned parent and kernel noReplace semantics.
    /// A failed post-rename check still updates our actual namespace flags.
    pub fn swap(&mut self) -> Result<()> {
        if self.phase != RootSwapState::Ready {
            return Err(StageError::InvalidRequest);
        }
        self.phase = RootSwapState::SwapFailed;
        self.revalidate()?;
        if let Some(old) = self.old.as_mut() {
            let result = {
                let state = self
                    .state
                    .try_borrow()
                    .map_err(|_| StageError::InvalidRequest)?;
                old.rename_sibling(&state._parent, &self.old_name)
            };
            match result {
                Ok(()) => self.old_moved = true,
                Err(error) => {
                    if error.moved {
                        self.old_moved = true;
                    }
                    return Err(error.error);
                }
            }
        }
        let result = {
            let mut state = self
                .state
                .try_borrow_mut()
                .map_err(|_| StageError::InvalidRequest)?;
            let State {
                directories,
                _parent: parent,
                ..
            } = &mut *state;
            directories[0].directory.rename_sibling(parent, "MXMOD")
        };
        match result {
            Ok(()) => self.published = true,
            Err(error) => {
                if error.moved {
                    self.published = true;
                }
                return Err(error.error);
            }
        }
        self.revalidate()?;
        self.phase = RootSwapState::Published;
        Ok(())
    }

    /// Explicit rollback, never Drop. Undo a published new root first. Only
    /// after it moved away may the original root return, without replacement.
    /// If a later check fails after a successful move, continue the remaining
    /// safe move but report rollback_failed. A retry only attempts flags still
    /// set; it never moves a root that has already been restored.
    pub fn rollback(&mut self) -> Result<()> {
        match self.phase {
            RootSwapState::Committed => return Err(StageError::InvalidRequest),
            RootSwapState::RolledBack => return Ok(()),
            _ => {}
        }
        let mut failed = false;
        if self.published {
            let result: Result<_> = (|| {
                let mut state = self
                    .state
                    .try_borrow_mut()
                    .map_err(|_| StageError::RollbackFailed)?;
                let State {
                    directories,
                    _parent: parent,
                    ..
                } = &mut *state;
                Ok(directories[0]
                    .directory
                    .rename_sibling(parent, &self.stage_name))
            })();
            match result {
                Ok(Ok(())) => self.published = false,
                Ok(Err(error)) => {
                    if error.moved {
                        self.published = false;
                    }
                    failed = true;
                }
                Err(_) => failed = true,
            }
        }
        if !self.published && self.old_moved {
            let result: Result<_> = (|| {
                let state = self
                    .state
                    .try_borrow()
                    .map_err(|_| StageError::RollbackFailed)?;
                let old = self.old.as_mut().ok_or(StageError::RollbackFailed)?;
                Ok(old.rename_sibling(&state._parent, "MXMOD"))
            })();
            match result {
                Ok(Ok(())) => self.old_moved = false,
                Ok(Err(error)) => {
                    if error.moved {
                        self.old_moved = false;
                    }
                    failed = true;
                }
                Err(_) => failed = true,
            }
        }
        if self.revalidate().is_err() || self.published || self.old_moved {
            failed = true;
        }
        if failed {
            self.phase = RootSwapState::RollbackFailed;
            Err(StageError::RollbackFailed)
        } else {
            self.phase = RootSwapState::RolledBack;
            Ok(())
        }
    }

    /// Marks the successful publication terminal only after final identity
    /// checks. A verification failure leaves rollback available. The previous
    /// tree is deliberately preserved; credential and marker commits are the
    /// caller's separate steps, not atomic with these directory moves.
    pub fn commit(&mut self) -> Result<()> {
        if self.phase != RootSwapState::Published {
            return Err(StageError::InvalidRequest);
        }
        self.revalidate()?;
        self.phase = RootSwapState::Committed;
        Ok(())
    }
}

#[cfg(test)]
#[path = "recovery_swap_tests.rs"]
mod tests;
