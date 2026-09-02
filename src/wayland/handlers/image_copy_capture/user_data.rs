// SPDX-License-Identifier: GPL-3.0-only

use std::{cell::RefCell, sync::Mutex};

use smithay::{
    backend::renderer::{
        ContextId,
        damage::OutputDamageTracker,
        gles::{GlesRenderbuffer, GlesTexture},
    },
    output::Output,
    wayland::image_copy_capture::{
        CursorSession, CursorSessionRef, Frame, FrameRef, Session, SessionRef,
    },
};

use smithay::utils::user_data::UserDataMap;

use crate::shell::{CosmicSurface, Workspace};

type ImageCopySessionsData = RefCell<ImageCopySessions>;
type PendingImageCopyBuffers = Mutex<Vec<(SessionRef, Frame)>>;

pub type SessionData = Mutex<SessionUserData>;

pub struct SessionUserData {
    pub dt: OutputDamageTracker,
    pub offscreen: Option<(ContextId<GlesTexture>, GlesRenderbuffer)>,
}

impl SessionUserData {
    pub fn new(tracker: OutputDamageTracker) -> SessionUserData {
        SessionUserData {
            dt: tracker,
            offscreen: None,
        }
    }
}

#[derive(Debug, Default)]
pub struct ImageCopySessions {
    sessions: Vec<Session>,
    cursor_sessions: Vec<CursorSession>,
}

impl Drop for ImageCopySessions {
    fn drop(&mut self) {
        for session in self.sessions.drain(..) {
            if let Some(sd) = session.user_data().get::<SessionData>() {
                sd.lock().unwrap().offscreen = None;
            }
        }
        for session in self.cursor_sessions.drain(..) {
            if let Some(sd) = session.user_data().get::<SessionData>() {
                sd.lock().unwrap().offscreen = None;
            }
        }
    }
}

/// Drop the capture sessions stored in the given `UserDataMap`, releasing their
/// offscreen renderbuffers immediately.
///
/// Used on output removal: an output capture session's [`SessionUserData`] holds
/// an `OutputDamageTracker` that references the `Output`, which in turn owns the
/// session — a cycle that stops the output (and its sessions) from being dropped
/// on their own. Draining the sessions here breaks that cycle.
///
/// It also clears each session's offscreen renderbuffer directly: dropping a
/// `Session` does not free it, because smithay's `ImageCopyCaptureState` keeps a
/// `SessionRef` clone sharing the same `Arc<UserDataMap>` until its own
/// `cleanup()` runs after a client roundtrip. The shared map makes the clear
/// visible to that lingering ref.
pub fn stop_all_capture_sessions(user_data: &UserDataMap) {
    if let Some(data) = user_data.get::<ImageCopySessionsData>() {
        let mut data = data.borrow_mut();
        for session in data.sessions.drain(..) {
            if let Some(sd) = session.user_data().get::<SessionData>() {
                sd.lock().unwrap().offscreen = None;
            }
            // Session::drop() sends `stopped` and fails active frames.
        }
        for session in data.cursor_sessions.drain(..) {
            if let Some(sd) = session.user_data().get::<SessionData>() {
                sd.lock().unwrap().offscreen = None;
            }
        }
    }
}

pub trait SessionHolder {
    fn add_session(&mut self, session: Session);
    fn remove_session(&mut self, session: &SessionRef);
    fn sessions(&self) -> Vec<SessionRef>;

    fn add_cursor_session(&mut self, session: CursorSession);
    fn remove_cursor_session(&mut self, session: &CursorSessionRef);
    fn cursor_sessions(&self) -> Vec<CursorSessionRef>;
}

pub trait FrameHolder {
    fn add_frame(&mut self, session: SessionRef, frame: Frame);
    fn remove_frame(&mut self, frame: &FrameRef);
    fn take_pending_frames(&self) -> Vec<(SessionRef, Frame)>;
}

impl SessionHolder for Output {
    fn add_session(&mut self, session: Session) {
        self.user_data()
            .insert_if_missing(ImageCopySessionsData::default);
        self.user_data()
            .get::<ImageCopySessionsData>()
            .unwrap()
            .borrow_mut()
            .sessions
            .push(session);
    }

    fn remove_session(&mut self, session: &SessionRef) {
        if let Some(sessions) = self.user_data().get::<ImageCopySessionsData>() {
            sessions.borrow_mut().sessions.retain(|s| s != session);
        }
    }

    fn sessions(&self) -> Vec<SessionRef> {
        self.user_data()
            .get::<ImageCopySessionsData>()
            .map_or(Vec::new(), |sessions| {
                sessions
                    .borrow()
                    .sessions
                    .iter()
                    .map(|s| (*s).clone())
                    .collect()
            })
    }

    fn add_cursor_session(&mut self, session: CursorSession) {
        self.user_data()
            .insert_if_missing(ImageCopySessionsData::default);
        self.user_data()
            .get::<ImageCopySessionsData>()
            .unwrap()
            .borrow_mut()
            .cursor_sessions
            .push(session);
    }

    fn remove_cursor_session(&mut self, session: &CursorSessionRef) {
        if let Some(sessions) = self.user_data().get::<ImageCopySessionsData>() {
            sessions
                .borrow_mut()
                .cursor_sessions
                .retain(|s| s != session);
        }
    }

    fn cursor_sessions(&self) -> Vec<CursorSessionRef> {
        self.user_data()
            .get::<ImageCopySessionsData>()
            .map_or(Vec::new(), |sessions| {
                sessions
                    .borrow()
                    .cursor_sessions
                    .iter()
                    .map(|s| (*s).clone())
                    .collect()
            })
    }
}

impl FrameHolder for Output {
    fn add_frame(&mut self, session: SessionRef, frame: Frame) {
        self.user_data()
            .insert_if_missing_threadsafe(PendingImageCopyBuffers::default);
        self.user_data()
            .get::<PendingImageCopyBuffers>()
            .unwrap()
            .lock()
            .unwrap()
            .push((session, frame));
    }
    fn remove_frame(&mut self, frame: &FrameRef) {
        if let Some(pending) = self.user_data().get::<PendingImageCopyBuffers>() {
            pending.lock().unwrap().retain(|(_, f)| f != frame);
        }
    }
    fn take_pending_frames(&self) -> Vec<(SessionRef, Frame)> {
        self.user_data()
            .get::<PendingImageCopyBuffers>()
            .map(|pending| std::mem::take(&mut *pending.lock().unwrap()))
            .unwrap_or_default()
    }
}

impl SessionHolder for Workspace {
    fn add_session(&mut self, session: Session) {
        self.image_copy.sessions.push(session);
    }

    fn remove_session(&mut self, session: &SessionRef) {
        self.image_copy.sessions.retain(|s| s != session);
    }
    fn sessions(&self) -> Vec<SessionRef> {
        self.image_copy
            .sessions
            .iter()
            .map(|s| (*s).clone())
            .collect()
    }

    fn add_cursor_session(&mut self, session: CursorSession) {
        self.image_copy.cursor_sessions.push(session);
    }

    fn remove_cursor_session(&mut self, session: &CursorSessionRef) {
        self.image_copy.cursor_sessions.retain(|s| s != session);
    }
    fn cursor_sessions(&self) -> Vec<CursorSessionRef> {
        self.image_copy
            .cursor_sessions
            .iter()
            .map(|s| (*s).clone())
            .collect()
    }
}

impl SessionHolder for CosmicSurface {
    fn add_session(&mut self, session: Session) {
        self.user_data()
            .insert_if_missing(ImageCopySessionsData::default);
        self.user_data()
            .get::<ImageCopySessionsData>()
            .unwrap()
            .borrow_mut()
            .sessions
            .push(session);
    }

    fn remove_session(&mut self, session: &SessionRef) {
        if let Some(sessions) = self.user_data().get::<ImageCopySessionsData>() {
            sessions.borrow_mut().sessions.retain(|s| s != session);
        }
    }
    fn sessions(&self) -> Vec<SessionRef> {
        self.user_data()
            .get::<ImageCopySessionsData>()
            .map_or(Vec::new(), |sessions| {
                sessions
                    .borrow()
                    .sessions
                    .iter()
                    .map(|s| (*s).clone())
                    .collect()
            })
    }

    fn add_cursor_session(&mut self, session: CursorSession) {
        self.user_data()
            .insert_if_missing(ImageCopySessionsData::default);
        self.user_data()
            .get::<ImageCopySessionsData>()
            .unwrap()
            .borrow_mut()
            .cursor_sessions
            .push(session);
    }

    fn remove_cursor_session(&mut self, session: &CursorSessionRef) {
        if let Some(sessions) = self.user_data().get::<ImageCopySessionsData>() {
            sessions
                .borrow_mut()
                .cursor_sessions
                .retain(|s| s != session);
        }
    }

    fn cursor_sessions(&self) -> Vec<CursorSessionRef> {
        self.user_data()
            .get::<ImageCopySessionsData>()
            .map_or(Vec::new(), |sessions| {
                sessions
                    .borrow()
                    .cursor_sessions
                    .iter()
                    .map(|s| (*s).clone())
                    .collect()
            })
    }
}
