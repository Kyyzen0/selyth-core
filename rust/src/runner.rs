//! Run steps in parallel, capture their output, classify each as passed / failed / skipped.

use crate::makefile::Step;
use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::mpsc;
use std::sync::Mutex;
use std::time::Instant;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Status {
    Passed,
    Failed,
    Skipped(String),
}

#[derive(Debug, Clone)]
pub struct Outcome {
    pub step: Step,
    pub status: Status,
    /// Last lines of combined stdout+stderr, kept for failures.
    pub tail: String,
    pub secs: f64,
}

#[derive(Debug, Clone, Default)]
pub struct Tools {
    pub dafny: Option<PathBuf>,
    pub node: Option<PathBuf>,
}

fn which(program: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let candidate = dir.join(program);
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

/// Detect the external tools: CLI flag, then the environment variables the backend itself
/// reads (SELYTH_DAFNY, NODE / SELYTH_NODE), then PATH, then the default install location.
pub fn detect_tools(dafny_arg: Option<PathBuf>, node_arg: Option<PathBuf>) -> Tools {
    let dafny = dafny_arg
        .or_else(|| std::env::var_os("SELYTH_DAFNY").map(PathBuf::from))
        .or_else(|| which("dafny"))
        .or_else(|| {
            std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/opt/dafny/dafny"))
        })
        .filter(|p| p.is_file());
    let node = node_arg
        .or_else(|| std::env::var_os("NODE").map(PathBuf::from))
        .or_else(|| std::env::var_os("SELYTH_NODE").map(PathBuf::from))
        .or_else(|| which("node"))
        .filter(|p| p.is_file());
    Tools { dafny, node }
}

fn tail_of(text: &str, lines: usize) -> String {
    let all: Vec<&str> = text.lines().collect();
    let start = all.len().saturating_sub(lines);
    all[start..].join("\n")
}

fn run_one(step: &Step, cwd: &Path) -> (Status, String, f64) {
    let started = Instant::now();
    let output = Command::new(&step.argv[0])
        .args(&step.argv[1..])
        .current_dir(cwd)
        .output();
    let secs = started.elapsed().as_secs_f64();
    match output {
        Ok(out) => {
            let text = format!(
                "{}{}",
                String::from_utf8_lossy(&out.stdout),
                String::from_utf8_lossy(&out.stderr)
            );
            if out.status.success() {
                (Status::Passed, text, secs)
            } else {
                (Status::Failed, text, secs)
            }
        }
        Err(err) => (Status::Failed, format!("cannot start {}: {err}", step.argv[0]), secs),
    }
}

/// Run the steps with at most `jobs` in parallel. A step whose required tool is missing is
/// skipped (a skip fails the run only under `strict`). Results come back in step order.
pub fn run_steps(steps: &[Step], jobs: usize, strict: bool, tools: &Tools, cwd: &Path) -> Vec<Outcome> {
    let queue: Mutex<VecDeque<usize>> = Mutex::new((0..steps.len()).collect());
    let (tx, rx) = mpsc::channel::<(usize, Outcome)>();
    let jobs = jobs.max(1).min(steps.len().max(1));
    std::thread::scope(|scope| {
        for _ in 0..jobs {
            let queue = &queue;
            let tx = tx.clone();
            let tools = tools;
            scope.spawn(move || loop {
                let next = { queue.lock().unwrap().pop_front() };
                let Some(i) = next else { break };
                let step = &steps[i];
                let skipped = if step.needs_dafny && tools.dafny.is_none() {
                    Some("no Dafny (set SELYTH_DAFNY or pass --dafny)".to_string())
                } else if step.needs_node && tools.node.is_none() {
                    Some("no Node (set NODE or pass --node)".to_string())
                } else {
                    None
                };
                let (status, tail, secs) = match skipped {
                    Some(reason) => (Status::Skipped(reason), String::new(), 0.0),
                    None => run_one(step, cwd),
                };
                let _ = strict;
                let _ = tx.send((
                    i,
                    Outcome {
                        step: step.clone(),
                        status,
                        tail: tail_of(&tail, 15),
                        secs,
                    },
                ));
            });
        }
    });
    drop(tx); // every worker sender is gone; the receiver loop below must terminate
    let mut by_index: Vec<Option<Outcome>> = (0..steps.len()).map(|_| None).collect();
    for (i, outcome) in rx {
        by_index[i] = Some(outcome);
    }
    by_index.into_iter().map(|o| o.expect("every step produced an outcome")).collect()
}

/// Exit code of the whole run: 1 if any step failed (or a skip under --strict), else 0.
pub fn exit_code(outcomes: &[Outcome], strict: bool) -> i32 {
    let failed = outcomes
        .iter()
        .any(|o| o.status == Status::Failed || (strict && matches!(o.status, Status::Skipped(_))));
    if failed {
        1
    } else {
        0
    }
}

/// One line per step plus the summary line.
pub fn render(outcomes: &[Outcome]) -> String {
    let mut lines = Vec::new();
    let mut passed = 0;
    let mut failed = 0;
    let mut skipped = 0;
    let total_secs: f64 = outcomes.iter().map(|o| o.secs).sum();
    for o in outcomes {
        match &o.status {
            Status::Passed => {
                passed += 1;
                lines.push(format!("[pass] {} [{}] {:.1}s", o.step.name, o.step.suite, o.secs));
            }
            Status::Failed => {
                failed += 1;
                lines.push(format!("[FAIL] {} [{}] {:.1}s", o.step.name, o.step.suite, o.secs));
                for line in o.tail.lines() {
                    lines.push(format!("    | {line}"));
                }
            }
            Status::Skipped(reason) => {
                skipped += 1;
                lines.push(format!("[skip] {} [{}] ({reason})", o.step.name, o.step.suite));
            }
        }
    }
    lines.push(format!(
        "summary: {passed} passed, {failed} failed, {skipped} skipped, {total_secs:.1}s total"
    ));
    lines.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn step(name: &str, argv: &[&str]) -> Step {
        Step {
            suite: "python".to_string(),
            name: name.to_string(),
            argv: argv.iter().map(|s| s.to_string()).collect(),
            needs_dafny: false,
            needs_node: false,
        }
    }

    #[test]
    fn true_and_false_pass_fail_and_report() {
        let steps = vec![step("ok", &["true"]), step("bad", &["false"])];
        let outcomes = run_steps(&steps, 2, false, &Tools::default(), Path::new("/tmp"));
        assert_eq!(outcomes[0].status, Status::Passed);
        assert_eq!(outcomes[1].status, Status::Failed);
        assert_eq!(exit_code(&outcomes, false), 1);
        let report = render(&outcomes);
        assert!(report.contains("[pass] ok [python]"));
        assert!(report.contains("[FAIL] bad [python]"));
        assert!(report.contains("summary: 1 passed, 1 failed, 0 skipped"));
    }

    #[test]
    fn missing_tool_skips_and_strict_turns_it_into_a_failure() {
        let mut s = step("needs-dafny", &["true"]);
        s.needs_dafny = true;
        let outcomes = run_steps(&[s], 1, false, &Tools::default(), Path::new("/tmp"));
        assert!(matches!(outcomes[0].status, Status::Skipped(_)));
        assert_eq!(exit_code(&outcomes, false), 0);
        assert_eq!(exit_code(&outcomes, true), 1);
    }

    #[test]
    fn tail_keeps_the_last_lines_only() {
        let text = (1..=30).map(|i| format!("line{i}")).collect::<Vec<_>>().join("\n");
        assert_eq!(tail_of(&text, 3), "line28\nline29\nline30");
    }
}
