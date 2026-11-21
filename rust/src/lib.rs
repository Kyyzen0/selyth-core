//! selyth-test: run the Selyth Core test suites, discovered from the Makefile.
//!
//! The Makefile stays the single source of truth for what the tests are; this crate turns
//! its suite targets into individually reported, optionally parallel steps, and skips
//! cleanly the steps whose external tool (Dafny, Node) is not installed.

pub mod makefile;
pub mod runner;

pub use makefile::{steps_for, Step, SUITES};
pub use runner::{detect_tools, exit_code, render, run_steps, Outcome, Status, Tools};
