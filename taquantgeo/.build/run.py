#!/usr/bin/env python3
"""Autonomous-build harness driver for TaQuantGeo.

Iterates .build/phases/*.md in numeric order, hydrates a per-phase prompt
from CLAUDE.md excerpts + recent handoffs + this phase's contract, invokes
`claude --dangerously-skip-permissions --print` as a subprocess, parses
the handoff written by the phase agent, and advances. State lives in
.build/build_state.json so the harness is resumable.

Design rules:
- stdlib only (Python 3.11+)
- Never destructive. State writes are atomic via os.replace.
- Handoff Status field is the source of truth for per-phase outcome.
- Blocked phases get re-attempted on next startup before new phases run.
- Dynamic phase discovery after phase 90: any new 91-98 that appears on
  disk is executed in numeric order before phase 99.
- SIGINT writes partial state and exits 3.
- No cost cap — telemetry only, written to cost_ledger.json when we can
  find structured usage in the subprocess output.

Flags:
  --dry-run          List phases in order, do not invoke claude
  --from-phase NN    Start at NN (skipping any prior completed)
  --only NN          Run exactly one phase, then exit
  --skip NN[,MM,...] Skip these phases for this run only
  --resume           Default; implicitly continues from state

Exit codes:
  0   All phases completed (or ran to blocked-halt cleanly)
  1   A phase reported status=failed; see reports/NN_failure.md
  2   Preflight error (no claude on PATH, not on main, dirty tree, etc.)
  3   SIGINT; state saved
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

HERE: Final = Path(__file__).resolve().parent
REPO: Final = HERE.parent
PHASES_DIR: Final = HERE / "phases"
HANDOFFS_DIR: Final = HERE / "handoffs"
REPORTS_DIR: Final = HERE / "reports"
TEMPLATES_DIR: Final = HERE / "templates"

STATE_PATH: Final = HERE / "build_state.json"
COST_PATH: Final = HERE / "cost_ledger.json"
MANUAL_SETUP_PATH: Final = HERE / "manual_setup_required.md"

CLAUDE_CLI: Final = os.environ.get("CLAUDE_CLI", "claude")

DEFAULT_PHASE_TIMEOUT_MIN: Final = 180  # fallback if phase metadata omits

# ANSI helpers for status lines on stderr only — agent output goes to reports/
RESET = "\033[0m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
GREY = "\033[90m"


def stderr(color: str, msg: str) -> None:
    sys.stderr.write(f"{color}{msg}{RESET}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# State I/O
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_state() -> dict[str, object]:
    if not STATE_PATH.exists():
        return {
            "version": 1,
            "created_at": _now_iso(),
            "commit_baseline": "",
            "test_count_baseline": 0,
            "current_phase": None,
            "completed_phases": [],
            "blocked_phases": [],
            "failed_phases": [],
            "phase_runs": [],
        }
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict[str, object]) -> None:
    _atomic_write(STATE_PATH, json.dumps(state, indent=2, sort_keys=True))


def load_cost_ledger() -> dict[str, object]:
    if not COST_PATH.exists():
        return {"phases": {}, "cumulative_usd_est": 0.0}
    return json.loads(COST_PATH.read_text(encoding="utf-8"))


def save_cost_ledger(ledger: dict[str, object]) -> None:
    _atomic_write(COST_PATH, json.dumps(ledger, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# Phase discovery + metadata parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseFile:
    path: Path
    num: str  # "00", "01", ..., "23b", "23c", "90", "99"
    sort_key: tuple[int, int, int]
    slug: str
    contents: str


def _parse_phase_num(name: str) -> tuple[str, tuple[int, int, int]]:
    # Accept "NN_slug.md" or "NNx_slug.md" (letter suffix for sub-phases 23b/c)
    m = re.match(r"^(\d+)([a-z]*)_(.+)\.md$", name)
    if not m:
        raise ValueError(f"unparseable phase filename: {name}")
    major = int(m.group(1))
    suffix = m.group(2)
    # Letter-suffix ordering: "" < "a" < "b" < ... -> 0, 1, 2
    suffix_ord = 0 if suffix == "" else ord(suffix) - ord("a") + 1
    return f"{major:02d}{suffix}", (major, suffix_ord, 0)


def discover_phases() -> list[PhaseFile]:
    if not PHASES_DIR.is_dir():
        raise RuntimeError(f"phases dir missing: {PHASES_DIR}")
    out: list[PhaseFile] = []
    for p in sorted(PHASES_DIR.glob("*.md")):
        try:
            num, sort_key = _parse_phase_num(p.name)
        except ValueError:
            stderr(YELLOW, f"skipping unparseable phase file: {p.name}")
            continue
        slug = p.stem.split("_", 1)[1] if "_" in p.stem else p.stem
        contents = p.read_text(encoding="utf-8")
        out.append(PhaseFile(path=p, num=num, sort_key=sort_key, slug=slug, contents=contents))
    out.sort(key=lambda x: x.sort_key)
    return out


def parse_metadata(contents: str) -> dict[str, str]:
    """Return a dict of the '## Metadata' bullet lines as key -> value.

    Keys normalized to lower_snake_case. Tolerates bullets like '- Effort:
    `max`' or '- Max phase runtime (minutes): 60' or '- Depends on phases:
    04, 06'."""
    meta: dict[str, str] = {}
    lines = contents.splitlines()
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## Metadata"):
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped.startswith("- "):
            body = stripped[2:]
            if ":" in body:
                k, _, v = body.partition(":")
                key = k.strip().lower()
                key = re.sub(r"\s*\(.*?\)", "", key)  # drop parentheticals
                key = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
                val = v.strip().strip("`")
                meta[key] = val
    return meta


def phase_timeout_seconds(contents: str) -> int:
    m = parse_metadata(contents)
    raw = m.get("max_phase_runtime", "")
    try:
        minutes = int(raw)
    except ValueError:
        minutes = DEFAULT_PHASE_TIMEOUT_MIN
    return max(60, minutes * 60)


# ---------------------------------------------------------------------------
# Handoff parsing
# ---------------------------------------------------------------------------


HANDOFF_STATUS_RE = re.compile(r"^##\s*Status\s*$", re.MULTILINE)


def parse_handoff_status(handoff_path: Path) -> str | None:
    if not handoff_path.exists():
        return None
    text = handoff_path.read_text(encoding="utf-8")
    m = HANDOFF_STATUS_RE.search(text)
    if not m:
        return None
    tail = text[m.end() :].strip().splitlines()
    if not tail:
        return None
    first_line = tail[0].strip().strip("`").lower()
    # Line sometimes reads "One of: `completed` | ..." in the template; the
    # filled-in handoff replaces it with one word.
    for word in ("completed", "partially_completed", "blocked", "failed"):
        if first_line == word or first_line.startswith(word):
            return word
        if word in first_line and first_line.count("|") == 0:
            return word
    return None


# ---------------------------------------------------------------------------
# Preflight + drift check
# ---------------------------------------------------------------------------


def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        cmd,
        check=False,
        capture_output=True,
        text=True,
        cwd=str(REPO),
        **kw,  # type: ignore[arg-type]
    )


def preflight() -> int:
    if shutil.which(CLAUDE_CLI) is None:
        stderr(RED, f"preflight: '{CLAUDE_CLI}' not on PATH. Install Claude Code CLI.")
        return 2
    status = run(["git", "status", "--porcelain"])
    if status.returncode != 0:
        stderr(RED, f"preflight: git status failed: {status.stderr}")
        return 2
    if status.stdout.strip():
        stderr(RED, "preflight: working tree is not clean. Commit or stash before running.")
        stderr(GREY, status.stdout)
        return 2
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if branch.returncode != 0 or branch.stdout.strip() != "main":
        stderr(RED, f"preflight: not on main (got {branch.stdout.strip()!r}). Checkout main.")
        return 2
    # Pull main to catch any external merges (e.g., dependabot auto-merge)
    pull = run(["git", "pull", "--ff-only"])
    if pull.returncode != 0:
        stderr(YELLOW, f"preflight: 'git pull --ff-only' non-zero: {pull.stderr.strip()}")
        # Non-fatal; user may be offline — continue
    return 0


def drift_note(state: dict[str, object]) -> None:
    """Note drift of main since the last run's commit_baseline and bump the baseline.

    NOT a full reconciliation: this function logs the commits that advanced main
    (useful for the operator to eyeball what merged during an interrupted run)
    and updates state["commit_baseline"] to the new HEAD. It does NOT re-derive
    `completed_phases` from merged-PR titles — that would require parsing each PR
    for a `feat/phase-NN-slug` pattern, matching to phase files, and updating the
    completed set. Future upgrade path; for v1 the assumption is that the
    harness is the primary merge source on main, so drift is usually just
    dependabot auto-merges of non-phase changes.
    """
    baseline = str(state.get("commit_baseline") or "")
    if not baseline:
        return
    head = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    if head == baseline:
        return
    merge_base = run(["git", "merge-base", baseline, head]).stdout.strip()
    if merge_base == head:
        return
    log = run(["git", "log", "--oneline", f"{baseline}..{head}"]).stdout.strip()
    if log:
        stderr(YELLOW, "drift: main advanced since last run; commits since baseline:")
        stderr(GREY, log)
    state["commit_baseline"] = head


# ---------------------------------------------------------------------------
# Test count baseline
# ---------------------------------------------------------------------------


def current_test_count() -> int:
    res = run(["uv", "run", "pytest", "-m", "not integration and not live", "--collect-only", "-q"])
    if res.returncode not in (0, 5):  # 5 = no tests collected; shouldn't happen here
        stderr(YELLOW, f"could not collect tests: rc={res.returncode}")
        return 0
    m = re.search(r"^(\d+)\s+tests?\s+collected", res.stdout, re.MULTILINE)
    if not m:
        return 0
    return int(m.group(1))


# ---------------------------------------------------------------------------
# Context hydration for a phase prompt
# ---------------------------------------------------------------------------

TOP_CLAUDE_MD = REPO / "CLAUDE.md"
USER_CLAUDE_MD = Path.home() / ".claude" / "CLAUDE.md"


def hydrate_prompt(phase: PhaseFile, recent_handoffs: list[Path]) -> str:
    parts: list[str] = [
        "# AUTONOMOUS BUILD HARNESS — PHASE INVOCATION",
        "",
        "You are being invoked as a phase agent. Work on EXACTLY the contract ",
        "in this phase file and nothing outside it. When you finish, write a ",
        "handoff file at .build/handoffs/NN_handoff.md conforming to the ",
        "handoff template. The driver parses your handoff's Status field to ",
        "advance. Do not ask clarifying questions — the contract is literal.",
        "",
        f"Working directory: {REPO}",
        "",
        "## Repo CLAUDE.md (conventions, storage tiers, data layout)",
        "",
    ]
    if TOP_CLAUDE_MD.exists():
        parts.append(TOP_CLAUDE_MD.read_text(encoding="utf-8"))
    parts += [
        "",
        "## User CLAUDE.md (cursor_apply routing, pre-commit review, git discipline)",
        "",
    ]
    if USER_CLAUDE_MD.exists():
        parts.append(USER_CLAUDE_MD.read_text(encoding="utf-8"))
    parts += [
        "",
        "## Templates",
        "",
        "### phase_template.md",
        "",
        (TEMPLATES_DIR / "phase_template.md").read_text(encoding="utf-8"),
        "",
        "### handoff_template.md",
        "",
        (TEMPLATES_DIR / "handoff_template.md").read_text(encoding="utf-8"),
        "",
        "### candidate_phase_template.md",
        "",
        (TEMPLATES_DIR / "candidate_phase_template.md").read_text(encoding="utf-8"),
        "",
        "## Recent handoffs (most recent ≤2)",
        "",
    ]
    for hp in recent_handoffs[-2:]:
        parts.append(f"### {hp.name}")
        parts.append("")
        parts.append(hp.read_text(encoding="utf-8"))
        parts.append("")
    parts += [
        f"## THIS PHASE ({phase.num} — {phase.slug})",
        "",
        phase.contents,
        "",
        "## BEGIN",
        "",
        "Read the phase contract above. Orient. Execute. Write the handoff. "
        "Merge the PR on green CI. Exit only after the handoff is written.",
    ]
    return "\n".join(parts)


def recent_handoff_paths() -> list[Path]:
    if not HANDOFFS_DIR.is_dir():
        return []
    return sorted(HANDOFFS_DIR.glob("*_handoff.md"))


# ---------------------------------------------------------------------------
# Phase invocation
# ---------------------------------------------------------------------------


def invoke_phase(phase: PhaseFile, dry_run: bool) -> tuple[str, Path]:
    """Invoke Claude CLI with the hydrated prompt. Returns (status, log_path)."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    HANDOFFS_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    log_path = REPORTS_DIR / f"{phase.num}_{phase.slug}_{ts}.log"

    if dry_run:
        stderr(GREY, f"[dry-run] {phase.num} {phase.slug} would be invoked")
        return "dry_run", log_path

    handoffs = recent_handoff_paths()
    prompt = hydrate_prompt(phase, handoffs)
    timeout = phase_timeout_seconds(phase.contents)

    stderr(BLUE, f"▶ phase {phase.num} {phase.slug} (timeout {timeout // 60} min)")
    started = time.monotonic()
    try:
        with log_path.open("wb") as log_fh:
            proc = subprocess.Popen(  # noqa: S603
                [CLAUDE_CLI, "--dangerously-skip-permissions", "--print"],
                stdin=subprocess.PIPE,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=str(REPO),
            )
            if proc.stdin is None:
                msg = "subprocess.Popen did not return a stdin pipe"
                raise RuntimeError(msg)
            try:
                proc.stdin.write(prompt.encode("utf-8"))
            finally:
                proc.stdin.close()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                stderr(RED, f"phase {phase.num} TIMED OUT after {timeout // 60} min")
                return "failed", log_path
    except FileNotFoundError:
        stderr(RED, f"claude CLI not found: {CLAUDE_CLI}")
        return "failed", log_path

    elapsed = time.monotonic() - started
    stderr(GREY, f"  phase subprocess exited in {elapsed:.1f}s")

    handoff_path = HANDOFFS_DIR / f"{phase.num}_handoff.md"
    status = parse_handoff_status(handoff_path)
    if status is None:
        stderr(RED, f"phase {phase.num}: no handoff at {handoff_path} (or missing Status)")
        return "failed", log_path
    return status, log_path


# ---------------------------------------------------------------------------
# Blocked-phase re-attempt flow
# ---------------------------------------------------------------------------


def reattempt_blocked_phases(state: dict[str, object], all_phases: list[PhaseFile]) -> None:
    blocked = list(state.get("blocked_phases") or [])
    if not blocked:
        return
    stderr(YELLOW, f"re-attempting {len(blocked)} blocked phase(s): {', '.join(blocked)}")
    idx_by_num = {p.num: p for p in all_phases}
    still_blocked: list[str] = []
    for num in blocked:
        p = idx_by_num.get(num)
        if p is None:
            stderr(YELLOW, f"  blocked phase {num} no longer on disk; dropping")
            continue
        # Archive prior handoff so the new run can write fresh
        ho = HANDOFFS_DIR / f"{num}_handoff.md"
        if ho.exists():
            n = 1
            while (HANDOFFS_DIR / f"{num}_handoff_attempt_{n}.md").exists():
                n += 1
            ho.rename(HANDOFFS_DIR / f"{num}_handoff_attempt_{n}.md")
        status, _ = invoke_phase(p, dry_run=False)
        if status == "completed":
            completed = list(state.get("completed_phases") or [])
            if num not in completed:
                completed.append(num)
            state["completed_phases"] = completed
            # Attempt to remove the phase's entry from manual_setup_required.md
            try_strip_manual_setup_entry(num)
            stderr(GREEN, f"  {num} re-attempted → completed")
        elif status == "blocked":
            still_blocked.append(num)
            stderr(YELLOW, f"  {num} still blocked")
        else:
            still_blocked.append(num)
            stderr(RED, f"  {num} unexpected status: {status}")
    state["blocked_phases"] = still_blocked
    save_state(state)


def try_strip_manual_setup_entry(phase_num: str) -> None:
    """Best-effort: remove lines in manual_setup_required.md that reference
    this phase number. Leaves the file if phase_num isn't mentioned."""
    if not MANUAL_SETUP_PATH.exists():
        return
    lines = MANUAL_SETUP_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    keep: list[str] = []
    removed = False
    in_block = False
    for line in lines:
        if re.match(rf"^\s*-\s*(Phase|phase)\s*{re.escape(phase_num)}\b", line):
            in_block = True
            removed = True
            continue
        if in_block and line.startswith(("- ", "## ")):
            in_block = False
        if in_block:
            continue
        keep.append(line)
    if removed:
        _atomic_write(MANUAL_SETUP_PATH, "".join(keep))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def ensure_manual_setup_file() -> None:
    if MANUAL_SETUP_PATH.exists():
        return
    MANUAL_SETUP_PATH.write_text(
        "# Manual setup required\n\n"
        "Populated by phases that hit missing required-severity external "
        "services. Each entry lists phase number, service, env var, where "
        "to obtain, estimated setup time.\n\n"
        "(empty at start)\n",
        encoding="utf-8",
    )


def record_run(state: dict[str, object], phase: PhaseFile, status: str, log_path: Path) -> None:
    runs = list(state.get("phase_runs") or [])
    runs.append(
        {
            "phase_num": phase.num,
            "slug": phase.slug,
            "status": status,
            "log": str(log_path.relative_to(REPO)),
            "finished_at": _now_iso(),
        }
    )
    state["phase_runs"] = runs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="build-harness", description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-phase", metavar="NN")
    ap.add_argument("--only", metavar="NN")
    ap.add_argument("--skip", metavar="NN[,MM,...]", default="")
    ap.add_argument("--resume", action="store_true")
    return ap.parse_args(argv)


def _install_sigint_handler(state: dict[str, object]) -> None:
    def _handler(signum: int, frame: object) -> None:
        _ = signum, frame
        stderr(YELLOW, "SIGINT received; saving state and exiting (3)")
        save_state(state)
        sys.exit(3)

    signal.signal(signal.SIGINT, _handler)


@dataclass
class RunContext:
    args: argparse.Namespace
    state: dict[str, object]
    phases: list[PhaseFile]
    completed: set[str]
    skip: set[str]


def _sort_key_of(num: str, phases: list[PhaseFile]) -> tuple[int, int, int]:
    for p in phases:
        if p.num == num:
            return p.sort_key
    return (0, 0, 0)


def _should_run(ctx: RunContext, p: PhaseFile) -> bool:
    if ctx.args.only and p.num != ctx.args.only:
        return False
    if ctx.args.from_phase and p.sort_key < _sort_key_of(ctx.args.from_phase, ctx.phases):
        return False
    if p.num in ctx.skip:
        return False
    return not (p.num in ctx.completed and not ctx.args.only)


def _handle_completed(ctx: RunContext, p: PhaseFile) -> None:
    ctx.completed.add(p.num)
    ctx.state["completed_phases"] = sorted(ctx.completed)
    # A phase that was previously blocked and is now re-run via --only or
    # --from-phase (bypassing reattempt_blocked_phases) must be cleared from
    # blocked_phases — otherwise the next startup's reattempt path will re-run it.
    blocked = list(ctx.state.get("blocked_phases") or [])
    if p.num in blocked:
        blocked.remove(p.num)
        ctx.state["blocked_phases"] = blocked
    try_strip_manual_setup_entry(p.num)
    stderr(GREEN, f"  ✓ {p.num} completed")


def _handle_blocked(ctx: RunContext, p: PhaseFile) -> int:
    blocked = list(ctx.state.get("blocked_phases") or [])
    if p.num not in blocked:
        blocked.append(p.num)
    ctx.state["blocked_phases"] = blocked
    stderr(YELLOW, f"  ⏸ {p.num} blocked — see manual_setup_required.md")
    save_state(ctx.state)
    stderr(YELLOW, "--- manual_setup_required.md ---")
    if MANUAL_SETUP_PATH.exists():
        sys.stderr.write(MANUAL_SETUP_PATH.read_text(encoding="utf-8"))
        sys.stderr.flush()
    return 0


def _handle_failed(ctx: RunContext, p: PhaseFile, log_path: Path) -> int:
    failed = list(ctx.state.get("failed_phases") or [])
    failed.append({"phase_num": p.num, "log": str(log_path.relative_to(REPO))})
    ctx.state["failed_phases"] = failed
    failure_report = REPORTS_DIR / f"{p.num}_failure.md"
    if not failure_report.exists():
        failure_report.write_text(
            f"# Phase {p.num} failure\n\n"
            f"Status recorded: `failed`\n\n"
            f"Log: `{log_path.relative_to(REPO)}`\n\n"
            "The phase agent reported `failed` in its handoff OR did "
            "not produce a handoff. Inspect the log for the cause, "
            "correct manually, then resume with `python3 .build/run.py`.\n",
            encoding="utf-8",
        )
    stderr(RED, f"  ✗ {p.num} failed — see {failure_report}")
    save_state(ctx.state)
    return 1


def _handle_partial(ctx: RunContext, p: PhaseFile) -> int:
    stderr(YELLOW, f"  ! {p.num} partially_completed — treating as blocked for safety")
    blocked = list(ctx.state.get("blocked_phases") or [])
    if p.num not in blocked:
        blocked.append(p.num)
    ctx.state["blocked_phases"] = blocked
    save_state(ctx.state)
    return 0


def _dispatch_status(ctx: RunContext, p: PhaseFile, status: str, log_path: Path) -> int | None:
    if status == "completed":
        _handle_completed(ctx, p)
    elif status == "blocked":
        return _handle_blocked(ctx, p)
    elif status == "failed":
        return _handle_failed(ctx, p, log_path)
    elif status == "partially_completed":
        return _handle_partial(ctx, p)
    elif status == "dry_run":
        pass
    else:
        stderr(RED, f"  ? {p.num} unknown status: {status} — treating as failed")
        return 1
    save_state(ctx.state)
    return None


def _run_one(ctx: RunContext, p: PhaseFile) -> int | None:
    stderr(BOLD + BLUE, f"\n=== phase {p.num}: {p.slug} ===")
    ctx.state["current_phase"] = p.num
    save_state(ctx.state)
    status, log_path = invoke_phase(p, ctx.args.dry_run)
    if ctx.args.dry_run:
        stderr(GREY, f"  (dry-run) prompt would be {log_path.name}")
        return None
    record_run(ctx.state, p, status, log_path)
    return _dispatch_status(ctx, p, status, log_path)


def _run_dynamic_promoted(ctx: RunContext) -> int | None:
    new_phases = discover_phases()
    known = {pp.num for pp in ctx.phases}
    fresh = [np for np in new_phases if np.num not in known and 91 <= int(np.num[:2]) <= 98]
    if not fresh:
        return None
    stderr(BLUE, f"dynamic discovery: running {len(fresh)} promoted phase(s) before 99")
    for fp in fresh:
        if _should_run(ctx, fp):
            rc = _run_one(ctx, fp)
            if rc is not None:
                return rc
    return None


def _primary_loop(ctx: RunContext) -> int:
    executed_any = False
    for p in ctx.phases:
        if not _should_run(ctx, p):
            continue
        if p.num == "99":
            rc = _run_dynamic_promoted(ctx)
            if rc is not None:
                return rc
        rc = _run_one(ctx, p)
        executed_any = True
        if rc is not None:
            return rc
        if ctx.args.only:
            break
    if not executed_any:
        stderr(GREY, "no phases to run under current flags / state")
    return 0


def _bootstrap_state(state: dict[str, object]) -> None:
    if not state.get("commit_baseline"):
        state["commit_baseline"] = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    if not state.get("test_count_baseline"):
        state["test_count_baseline"] = current_test_count()
    save_state(state)
    drift_note(state)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ensure_manual_setup_file()
    state = load_state()
    _install_sigint_handler(state)
    if not args.dry_run:
        code = preflight()
        if code != 0:
            return code
    _bootstrap_state(state)

    phases = discover_phases()
    if not phases:
        stderr(RED, "no phase files under .build/phases/")
        return 2

    ctx = RunContext(
        args=args,
        state=state,
        phases=phases,
        completed=set(state.get("completed_phases") or []),  # type: ignore[arg-type]
        skip={s.strip() for s in args.skip.split(",") if s.strip()},
    )

    if not args.only and not args.from_phase and not args.dry_run:
        reattempt_blocked_phases(state, phases)
        # Re-sync completed set after re-attempts
        ctx.completed = set(state.get("completed_phases") or [])  # type: ignore[arg-type]

    return _primary_loop(ctx)


if __name__ == "__main__":
    sys.exit(main())
