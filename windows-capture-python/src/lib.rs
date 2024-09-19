
#![warn(clippy::nursery)]
#![warn(clippy::cargo)]
#![allow(clippy::multiple_crate_versions)] // Should update as soon as possible

/// Contains the main capture functionality, including the `WindowsCaptureHandler` trait and related types.
pub mod capture;

pub mod encoder;