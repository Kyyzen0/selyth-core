//! selyth-test — run the Selyth Core test suites.
//!
//!   selyth-test list [--root DIR]
//!   selyth-test run [SUITE...] [--root DIR] [--jobs N] [--only SUBSTR] [--strict]
//!                   [--python PATH] [--dafny PATH] [--node PATH]
//!
//! Suites: python, dafny, eval, ts, all (default: all). Steps are discovered from the
//! Makefile targets test-python / test-dafny / test-eval / test-ts / test-properties.
//! A step whose tool is missing (Dafny, Node) is reported as skipped; --strict makes a
//! skip fail the run. Exit code is 1 if any step failed.

use selyth_test::{detect_tools, exit_code, makefile, render, run_steps, steps_for};
use std::path::{Path, PathBuf};
use std::process::exit;

struct Args {
    command: String,
    suites: Vec<String>,
    root: PathBuf,
    jobs: usize,
    only: Option<String>,
    strict: bool,
    python: Option<String>,
    dafny: Option<PathBuf>,
    node: Option<PathBuf>,
}

fn usage() -> ! {
    eprintln!(
        "usage: selyth-test list [--root DIR]\n\
         \x20      selyth-test run [SUITE...] [--root DIR] [--jobs N] [--only SUBSTR] [--strict]\n\
         \x20                   [--python PATH] [--dafny PATH] [--node PATH]\n\
         suites: python, dafny, eval, ts, all (default: all)"
    );
    exit(2);
}

fn parse_args() -> Args {
    let mut args = std::env::args().skip(1);
    let Some(command) = args.next() else { usage() };
    if command != "list" && command != "run" {
        usage();
    }
    let mut parsed = Args {
        command,
        suites: vec![],
        root: PathBuf::from("."),
        jobs: 4,
        only: None,
        strict: false,
        python: None,
        dafny: None,
        node: None,
    };
    let mut positional: Vec<String> = vec![];
    while let Some(arg) = args.next() {
        let mut take_value = |flag: &str| -> String {
            args.next().unwrap_or_else(|| {
                eprintln!("missing value for {flag}");
                usage();
            })
        };
        match arg.as_str() {
            "--root" => parsed.root = PathBuf::from(take_value("--root")),
            "--jobs" => {
                parsed.jobs = take_value("--jobs").parse().unwrap_or_else(|_| {
                    eprintln!("--jobs expects a positive integer");
                    usage();
                })
            }
            "--only" => parsed.only = Some(take_value("--only")),
            "--strict" => parsed.strict = true,
            "--python" => parsed.python = Some(take_value("--python")),
            "--dafny" => parsed.dafny = Some(PathBuf::from(take_value("--dafny"))),
            "--node" => parsed.node = Some(PathBuf::from(take_value("--node"))),
            "-h" | "--help" => usage(),
            other if other.starts_with('-') => {
                eprintln!("unknown option {other}");
                usage();
            }
            other => positional.push(other.to_string()),
        }
    }
    if positional.is_empty() {
        parsed.suites.push("all".to_string());
    } else {
        parsed.suites = positional;
    }
    parsed
}

fn main() {
    let args = parse_args();
    let makefile_path = makefile::default_makefile(&args.root);
    let text = std::fs::read_to_string(&makefile_path).unwrap_or_else(|err| {
        eprintln!("cannot read {}: {err}", makefile_path.display());
        exit(2);
    });
    let python = args
        .python
        .clone()
        .unwrap_or_else(|| args.root.join(".venv/bin/python").to_string_lossy().into_owned());
    let mut steps = steps_for(&text, &args.suites, &args.root, &python);
    if let Some(needle) = &args.only {
        steps.retain(|s| s.name.contains(needle.as_str()));
    }
    if args.command == "list" {
        println!("suites discovered from {}", makefile_path.display());
        for step in &steps {
            let req = match (step.needs_dafny, step.needs_node) {
                (true, true) => "dafny+node",
                (true, false) => "dafny",
                (false, true) => "node",
                (false, false) => "-",
            };
            println!("  {:>7}  {:<40} {}", step.suite, step.name, req);
        }
        println!("{} steps", steps.len());
        return;
    }
    if steps.is_empty() {
        eprintln!("no steps matched (suites: {:?}, --only: {:?})", args.suites, args.only);
        exit(2);
    }
    // The Python suites need the venv; build it if missing (make is idempotent here).
    if args.suites.iter().any(|s| s == "python" || s == "all")
        && !Path::new(&python).is_file()
        && args.python.is_none()
    {
        eprintln!("building the venv first (make venv)...");
        let status = std::process::Command::new("make")
            .arg("venv")
            .current_dir(&args.root)
            .status();
        if !status.map(|s| s.success()).unwrap_or(false) {
            eprintln!("make venv failed; run it yourself and retry");
            exit(2);
        }
    }
    let tools = detect_tools(args.dafny.clone(), args.node.clone());
    eprintln!(
        "tools: dafny={} node={}",
        tools.dafny.as_ref().map(|p| p.display().to_string()).unwrap_or_else(|| "none".into()),
        tools.node.as_ref().map(|p| p.display().to_string()).unwrap_or_else(|| "none".into()),
    );
    let outcomes = run_steps(&steps, args.jobs, args.strict, &tools, &args.root);
    println!("{}", render(&outcomes));
    exit(exit_code(&outcomes, args.strict));
}
