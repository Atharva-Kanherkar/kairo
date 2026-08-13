//! Replay harness: feed a recorded provider transcript through a tool under
//! test and diff what comes out against what a lossless pipe must emit.
//!
//! A transcript is the verbatim wire exchange captured from a real provider:
//! the request body and the ordered SSE frames of the response. Tests never
//! hit the network, the recorded frames are the ground truth.

pub mod checks;

use serde::{Deserialize, Serialize};

/// One captured SSE frame, byte-faithful. `event` is None for data-only
/// streams (Chat Completions style); Anthropic/Responses streams carry it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SseFrame {
    pub event: Option<String>,
    pub data: String,
}

/// A recorded exchange with one endpoint: what was sent, what came back.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Transcript {
    /// Wire dialect of this exchange, e.g. "openai-chat", "anthropic",
    /// "openai-responses", "gemini".
    pub dialect: String,
    /// Verbatim JSON request body.
    pub request: serde_json::Value,
    /// Ordered response frames (a non-streaming response is one frame).
    pub frames: Vec<SseFrame>,
    /// Where and when this was captured; provider, model, tool versions.
    pub provenance: Provenance,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Provenance {
    pub captured_at: String,
    pub endpoint: String,
    pub model: String,
    /// Upstream issue this transcript reproduces, e.g. "litellm#35663".
    pub reproduces: Option<String>,
}

impl Transcript {
    pub fn from_json(s: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(s)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn transcript_round_trips_through_json() {
        let t = Transcript {
            dialect: "openai-chat".into(),
            request: serde_json::json!({"model": "qwen3", "stream": true}),
            frames: vec![SseFrame { event: None, data: "{\"choices\":[]}".into() }],
            provenance: Provenance {
                captured_at: "2026-08-12".into(),
                endpoint: "http://localhost:11434/v1/chat/completions".into(),
                model: "qwen3".into(),
                reproduces: Some("litellm#35663".into()),
            },
        };
        let s = serde_json::to_string(&t).unwrap();
        let back = Transcript::from_json(&s).unwrap();
        assert_eq!(back.frames, t.frames);
        assert_eq!(back.dialect, t.dialect);
    }
}
