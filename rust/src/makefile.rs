//! Parse the repository Makefile into runnable test steps.
//!
//! The Makefile is the single source of truth for what "the tests" are: a new
//! `$(VPY) tools/test-*.py` line under `test-python` (or a new suite target) is picked
//! up automatically, no Rust change needed. Only the suite targets are expanded:
//! prerequisite targets such as `venv` are never run as steps.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// A suite target name in the Makefile, mapped to the short name the CLI uses.
pub const SUITES: &[(&str, &str)] = &[
    ("python", "test-python"),
    ("dafny", "test-dafny"),
    ("eval", "test-eval"),
    ("ts", "test-ts"),
    ("ts", "test-properties"),
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Step {
    pub suite: String,
    pub name: String,
    /// argv[0] is the program; a shell step uses ["sh", "-c", line].
    pub argv: Vec<String>,
    pub needs_dafny: bool,
    pub needs_node: bool,
}

fn is_target_name(text: &str) -> bool {
    !text.is_empty()
        && text
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-' || c == '.')
}

/// Split the Makefile text into target name -> recipe lines (tab stripped).
pub fn parse_targets(text: &str) -> BTreeMap<String, Vec<String>> {
    let mut targets: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut current: Option<String> = None;
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix('\t') {
            if let Some(name) = &current {
                targets.entry(name.clone()).or_default().push(rest.to_string());
            }
        } else if let Some(colon) = line.find(':') {
            let name = &line[..colon];
            current = if is_target_name(name) {
                Some(name.to_string())
            } else {
                None
            };
        } else {
            current = None;
        }
    }
    targets
}

fn step_name_from_python_tool(arg: &str) -> String {
    let path = arg.trim();
    Path::new(path)
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| path.to_string())
}

/// Build the step list for the given suite names ("python", "dafny", "eval", "ts", "all").
/// `python` is the interpreter used for `$(VPY)` lines; `root` is the repo root (steps run there).
pub fn steps_for(makefile_text: &str, suites: &[String], root: &Path, python: &str) -> Vec<Step> {
    let targets = parse_targets(makefile_text);
    let wanted: Vec<&str> = if suites.iter().any(|s| s == "all") {
        vec!["python", "dafny", "eval", "ts"]
    } else {
        suites.iter().map(|s| s.as_str()).collect()
    };
    let mut steps = Vec::new();
    for (short, target) in SUITES {
        if !wanted.contains(short) {
            continue;
        }
        let needs_dafny = *target == "test-dafny" || *target == "test-eval";
        let needs_node = needs_dafny || *target == "test-ts" || *target == "test-properties";
        let Some(recipe) = targets.get(*target) else {
            continue;
        };
        for line in recipe {
            let line = line.trim();
            if line.is_empty() || line.starts_with('@') {
                continue; // echoes and guards are presentation, not steps
            }
            if let Some(rest) = line.strip_prefix("$(VPY)") {
                let rest = rest.trim();
                steps.push(Step {
                    suite: short.to_string(),
                    name: step_name_from_python_tool(rest),
                    argv: vec![python.to_string(), rest.to_string()],
                    needs_dafny,
                    needs_node,
                });
            } else {
                let label = line
                    .split_whitespace()
                    .take(3)
                    .collect::<Vec<_>>()
                    .join(" ");
                steps.push(Step {
                    suite: short.to_string(),
                    name: format!("{target}: {label}"),
                    argv: vec!["sh".to_string(), "-c".to_string(), line.to_string()],
                    needs_dafny,
                    needs_node,
                });
            }
        }
    }
    let _ = root;
    steps
}

/// Default Makefile path for a repo root.
pub fn default_makefile(root: &Path) -> PathBuf {
    root.join("Makefile")
}

#[cfg(test)]
mod tests {
    use super::*;

    const FIXTURE: &str = "\
# comment
PYTHON ?= python3
VENV := .venv
VPY := $(VENV)/bin/python

.PHONY: test test-python

test: test-python

test-python: venv
\t$(VPY) tools/test-alpha.py
\t@echo
\t@echo \"== heading\"
\t$(VPY) tools/test-beta.py

test-dafny: venv
\t$(VPY) tools/test-gamma.py

test-ts:
\t@command -v node >/dev/null || exit 1
\tcd ts && npm ci && npm test
";

    #[test]
    fn targets_are_parsed_with_recipes() {
        let targets = parse_targets(FIXTURE);
        assert_eq!(targets["test-python"].len(), 4);
        assert_eq!(targets["test-ts"].len(), 2);
        assert!(!targets.contains_key("PYTHON"));
    }

    #[test]
    fn python_lines_become_python_steps_echoes_are_skipped() {
        let steps = steps_for(FIXTURE, &["python".to_string()], Path::new("."), "/venv/bin/python");
        assert_eq!(steps.len(), 2);
        assert_eq!(steps[0].name, "test-alpha");
        assert_eq!(steps[0].argv, vec!["/venv/bin/python", "tools/test-alpha.py"]);
        assert!(!steps[0].needs_dafny && !steps[0].needs_node);
        assert_eq!(steps[1].name, "test-beta");
    }

    #[test]
    fn shell_lines_become_sh_steps_and_flags_follow_the_target() {
        let steps = steps_for(FIXTURE, &["ts".to_string(), "dafny".to_string()], Path::new("."), "py");
        let npm = steps.iter().find(|s| s.name.contains("npm")).unwrap();
        assert_eq!(npm.argv[0], "sh");
        assert!(npm.needs_node && !npm.needs_dafny);
        let gamma = steps.iter().find(|s| s.name == "test-gamma").unwrap();
        assert!(gamma.needs_dafny && gamma.needs_node);
    }

    #[test]
    fn all_expands_to_the_four_suites() {
        let steps = steps_for(FIXTURE, &["all".to_string()], Path::new("."), "py");
        let suites: Vec<&str> = steps.iter().map(|s| s.suite.as_str()).collect();
        assert!(suites.contains(&"python") && suites.contains(&"dafny") && suites.contains(&"ts"));
        assert!(!suites.contains(&"eval")); // the fixture has no test-eval target
    }
}
