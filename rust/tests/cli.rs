//! Integration tests: run the compiled binary against a fixture repo.

use std::path::PathBuf;
use std::process::Command;

fn fixture_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/fake")
}

fn selyth_test() -> Command {
    Command::new(env!("CARGO_BIN_EXE_selyth-test"))
}

#[test]
fn list_shows_the_discovered_steps_and_requirements() {
    let out = selyth_test()
        .args(["list", "--root"])
        .arg(fixture_root())
        .output()
        .unwrap();
    assert!(out.status.success());
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("test-ok"), "{stdout}");
    assert!(stdout.contains("test-needs-dafny"), "{stdout}");
    assert!(stdout.contains("dafny"), "{stdout}");
    assert!(!stdout.contains("heading"), "echo lines are not steps: {stdout}");
}

#[test]
fn run_reports_pass_fail_and_exit_code() {
    let out = selyth_test()
        .args(["run", "python", "--root"])
        .arg(fixture_root())
        .args(["--python", "/bin/echo"]) // $(VPY) steps become `/bin/echo tools/...`: always pass
        .args(["--dafny", "/nonexistent"])
        .output()
        .unwrap();
    // the fixture's raw `exit 1` recipe line fails, the python step passes
    assert_eq!(out.status.code(), Some(1));
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("[pass] test-ok"), "{stdout}");
    assert!(stdout.contains("[FAIL] test-python: exit 1"), "{stdout}");
    assert!(stdout.contains("summary: 1 passed, 1 failed"), "{stdout}");
}

#[test]
fn run_skips_steps_whose_tool_is_missing_and_strict_fails_on_them() {
    let out = selyth_test()
        .args(["run", "dafny", "--root"])
        .arg(fixture_root())
        .args(["--python", "/bin/echo"])
        .args(["--dafny", "/nonexistent"])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(0));
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("[skip] test-needs-dafny"), "{stdout}");
    assert!(stdout.contains("0 passed, 0 failed, 1 skipped"), "{stdout}");

    let out = selyth_test()
        .args(["run", "dafny", "--strict", "--root"])
        .arg(fixture_root())
        .args(["--python", "/bin/echo"])
        .args(["--dafny", "/nonexistent"])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(1));
}

#[test]
fn only_filters_steps_by_name() {
    let out = selyth_test()
        .args(["run", "python", "--only", "test-ok", "--root"])
        .arg(fixture_root())
        .args(["--python", "/bin/echo"])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(0));
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("[pass] test-ok"), "{stdout}");
    assert!(stdout.contains("summary: 1 passed, 0 failed"), "{stdout}");
}
