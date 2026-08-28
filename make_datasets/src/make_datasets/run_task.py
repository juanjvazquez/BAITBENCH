"""
Create per-run workspaces for CPU tabular autoresearch tasks.

Examples:
    uv run src/make_datasets/run_task.py --task uci_357_occupancy_detection
    uv run src/make_datasets/run_task.py --task uci_357_occupancy_detection --agent codex --launch

Output: runs/<run_id>/
"""

from __future__ import annotations

import shutil
import subprocess
import time
from enum import Enum
from pathlib import Path
from textwrap import dedent
from typing import Annotated

import typer
from pydantic import BaseModel, ConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED_TASKS_DIR = REPO_ROOT / "tasks" / "generated"
RUNS_DIR = REPO_ROOT / "runs"
DEFAULT_TASK = "uci_357_occupancy_detection"
DEFAULT_CODEX_MODEL = "gpt-5.4"
DEFAULT_CLAUDE_MODEL = "claude-opus-4-6"
DEFAULT_HOURS = 1.0
WORKSPACE_PUBLIC_FILES = frozenset(
    {
        "evaluate.py",
        "program.md",
        "pyproject.toml",
        "solution.py",
        "task.json",
        "test.csv",
        "train.csv",
    }
)


class Agent(str, Enum):
    """Supported agent CLIs."""

    codex = "codex"
    claude = "claude"
    opencode_gpt = "opencode_gpt"
    opencode_claude = "opencode_claude"
    opencode_sonnet = "opencode_sonnet"
    opencode_gemini_pro = "opencode_gemini_pro"
    opencode_gemini_flash = "opencode_gemini_flash"
    opencode_deepseek = "opencode_deepseek"
    kimi = "kimi"
    gemini = "gemini"
    gemma = "gemma"
    gpt_oss = "gpt_oss"


class CodexReasoningEffort(str, Enum):
    """Supported Codex reasoning effort overrides."""

    low = "low"
    medium = "medium"
    high = "high"
    xhigh = "xhigh"


class ClaudeEffort(str, Enum):
    """Supported Claude Code effort overrides."""

    low = "low"
    medium = "medium"
    high = "high"
    max = "max"


class RunMetadata(BaseModel):
    """Metadata for a prepared or launched run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    task_id: str
    task_dir: str
    run_dir: str
    workspace_dir: str
    agent: Agent
    model: str
    codex_reasoning_effort: CodexReasoningEffort
    claude_effort: ClaudeEffort
    num_hours: float
    launched: bool
    start_time: float


def sanitize_for_id(value: str) -> str:
    """Convert a free-form value into a compact run-id component.

    Args:
        value: Value to include in a run id.

    Returns:
        Sanitized run-id component.
    """
    cleaned = "".join(
        char if char.isalnum() or char in ".-_" else "_" for char in value
    )
    cleaned = cleaned.strip("_")
    if cleaned == "":
        raise ValueError("run id component must not be empty")
    return cleaned


def default_model(agent: Agent) -> str:
    """Return the default model name for an agent.

    Args:
        agent: Agent CLI choice.

    Returns:
        Default model name.
    """
    if agent == Agent.codex:
        return DEFAULT_CODEX_MODEL
    if agent == Agent.claude:
        return DEFAULT_CLAUDE_MODEL
    raise AssertionError(f"unhandled agent: {agent}")


def resolve_task_dir(task: str) -> Path:
    """Resolve a task id or task path to a generated task directory.

    Args:
        task: Task id such as `uci_357_occupancy_detection` or a filesystem path.

    Returns:
        Existing task directory.

    Raises:
        FileNotFoundError: If no task directory exists.
    """
    direct = Path(task)
    if direct.is_dir():
        return direct.resolve()
    generated = GENERATED_TASKS_DIR / task
    if generated.is_dir():
        return generated.resolve()
    raise FileNotFoundError(f"could not find task directory for {task!r}")


def make_run_id(agent: Agent, model: str, task_id: str, start_time: float) -> str:
    """Build a readable unique run id.

    Args:
        agent: Agent CLI choice.
        model: Agent model name.
        task_id: Generated task id.
        start_time: Unix timestamp.

    Returns:
        Run id string.
    """
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(start_time))
    return (
        f"{agent.value}_{sanitize_for_id(model)}_{sanitize_for_id(task_id)}_{timestamp}"
    )


def timer_source(num_hours: float) -> str:
    """Build the timer program placed in each run workspace.

    Args:
        num_hours: Time budget in hours.

    Returns:
        Python source.
    """
    total_seconds = int(num_hours * 3600)
    return dedent(
        f"""\
        from __future__ import annotations

        import time
        from pathlib import Path

        TOTAL_SECONDS = {total_seconds}
        START_PATH = Path(".timer_start")


        def start_time() -> int:
            if START_PATH.exists():
                return int(START_PATH.read_text(encoding="utf-8").strip())
            now = int(time.time())
            START_PATH.write_text(f"{{now}}\\n", encoding="utf-8")
            return now


        deadline = start_time() + TOTAL_SECONDS
        remaining = deadline - int(time.time())
        if remaining <= 0:
            print("Timer expired!")
        else:
            print("Remaining time (hours:minutes):")
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            print(f"{{hours}}:{{minutes:02d}}")
        """
    )


def write_workspace_gitignore(workspace_dir: Path) -> None:
    """Write ignore rules for per-run scratch files.

    Args:
        workspace_dir: Run workspace directory.
    """
    (workspace_dir / ".gitignore").write_text(
        dedent(
            """\
            __pycache__/
            .ruff_cache/
            results.md
            results.tsv
            run.log
            metrics.json
            predictions.csv
            .timer_start
            uv.lock
            .venv/
            """
        ),
        encoding="utf-8",
    )


def run_command(command: list[str], *, cwd: Path) -> None:
    """Run a command and fail loudly on non-zero exit.

    Args:
        command: Command argv.
        cwd: Working directory.
    """
    subprocess.run(command, cwd=cwd, check=True)


def initialize_workspace_git(workspace_dir: Path) -> None:
    """Initialize the isolated git repo used by an agent run.

    Args:
        workspace_dir: Run workspace directory.
    """
    run_command(["git", "init", "-b", "main"], cwd=workspace_dir)
    run_command(
        ["git", "config", "user.email", "autoresearch@example.invalid"],
        cwd=workspace_dir,
    )
    run_command(
        ["git", "config", "user.name", "Autoresearch Runner"], cwd=workspace_dir
    )
    run_command(["git", "add", "."], cwd=workspace_dir)
    run_command(["git", "commit", "-m", "baseline task workspace"], cwd=workspace_dir)


def prepare_workspace(
    task_dir: Path,
    *,
    run_dir: Path,
    num_hours: float,
    start_time: float,
) -> Path:
    """Copy a task template into a fresh run workspace.

    Args:
        task_dir: Generated task template directory.
        run_dir: Run output directory.
        num_hours: Time budget in hours.
        start_time: Unix timestamp.

    Returns:
        Workspace directory.
    """
    workspace_dir = run_dir / "workspace"
    workspace_dir.mkdir()
    hidden_dir = run_dir / ".private_task_assets"
    hidden_dir.mkdir()

    for path in sorted(task_dir.iterdir()):
        if not path.is_file():
            continue
        destination_root = (
            workspace_dir if path.name in WORKSPACE_PUBLIC_FILES else hidden_dir
        )
        shutil.copy2(path, destination_root / path.name)

    write_workspace_gitignore(workspace_dir)
    timer_path = workspace_dir / "timer.py"
    timer_path.write_text(timer_source(num_hours), encoding="utf-8")
    initialize_workspace_git(workspace_dir)
    (workspace_dir / "results.md").write_text("", encoding="utf-8")
    results_header = "commit\tscore\tstatus\tdescription\n"
    if task_dir.name.endswith("_vlog"):
        results_header = (
            "commit\tscore\tstatus\tdescription\tvalidity\treflection\n"
        )
    (workspace_dir / "results.tsv").write_text(results_header, encoding="utf-8")
    return workspace_dir


def agent_command(
    agent: Agent,
    model: str,
    program: str,
    *,
    codex_reasoning_effort: CodexReasoningEffort,
    claude_effort: ClaudeEffort,
) -> list[str]:
    """Build the command used to launch an agent.

    Args:
        agent: Agent CLI choice.
        model: Agent model name.
        program: Program text to pass to the agent.
        codex_reasoning_effort: Per-process Codex reasoning effort override.
        claude_effort: Per-process Claude Code effort override.

    Returns:
        Command argv.
    """
    if agent == Agent.codex:
        return [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--model",
            model,
            "-c",
            f'model_reasoning_effort="{codex_reasoning_effort.value}"',
            program,
        ]
    if agent == Agent.claude:
        return [
            "claude",
            "--print",
            "--verbose",
            "--model",
            model,
            "--effort",
            claude_effort.value,
            "--output-format",
            "stream-json",
            "--dangerously-skip-permissions",
            program,
        ]
    raise AssertionError(f"unhandled agent: {agent}")


def launch_agent(
    workspace_dir: Path,
    run_dir: Path,
    *,
    agent: Agent,
    model: str,
    codex_reasoning_effort: CodexReasoningEffort,
    claude_effort: ClaudeEffort,
) -> int:
    """Launch the selected agent in a run workspace.

    Args:
        workspace_dir: Run workspace directory.
        run_dir: Run output directory.
        agent: Agent CLI choice.
        model: Agent model name.
        codex_reasoning_effort: Per-process Codex reasoning effort override.
        claude_effort: Per-process Claude Code effort override.

    Returns:
        Agent process exit code.
    """
    program = (workspace_dir / "program.md").read_text(encoding="utf-8")
    command = agent_command(
        agent,
        model,
        program,
        codex_reasoning_effort=codex_reasoning_effort,
        claude_effort=claude_effort,
    )
    with (run_dir / "transcript.json").open("w", encoding="utf-8") as stdout_fh:
        with (run_dir / "agent_stderr.log").open("w", encoding="utf-8") as stderr_fh:
            run_command(["uv", "run", "timer.py"], cwd=workspace_dir)
            process = subprocess.Popen(
                command,
                cwd=workspace_dir,
                stdout=stdout_fh,
                stderr=stderr_fh,
                text=True,
            )
            return process.wait()


def write_metadata(metadata: RunMetadata) -> None:
    """Write run metadata to disk.

    Args:
        metadata: Run metadata.
    """
    path = Path(metadata.run_dir) / "metadata.json"
    path.write_text(metadata.model_dump_json(indent=2) + "\n", encoding="utf-8")


def prepare_run(
    task: str,
    *,
    agent: Agent,
    model: str,
    codex_reasoning_effort: CodexReasoningEffort,
    claude_effort: ClaudeEffort,
    num_hours: float,
    launch: bool,
    overwrite: bool,
) -> RunMetadata:
    """Prepare a run directory and optionally launch an agent.

    Args:
        task: Task id or task directory path.
        agent: Agent CLI choice.
        model: Agent model name.
        codex_reasoning_effort: Per-process Codex reasoning effort override.
        claude_effort: Per-process Claude Code effort override.
        num_hours: Time budget in hours.
        launch: Whether to launch the agent after preparing the workspace.
        overwrite: Whether to replace an existing run directory with the same id.

    Returns:
        Run metadata.
    """
    task_dir = resolve_task_dir(task)
    task_id = task_dir.name
    start_time = time.time()
    run_id = make_run_id(agent, model, task_id, start_time)
    run_dir = RUNS_DIR / run_id
    if run_dir.exists():
        if not overwrite:
            raise FileExistsError(f"run directory already exists: {run_dir}")
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    workspace_dir = prepare_workspace(
        task_dir,
        run_dir=run_dir,
        num_hours=num_hours,
        start_time=start_time,
    )
    metadata = RunMetadata(
        run_id=run_id,
        task_id=task_id,
        task_dir=str(task_dir),
        run_dir=str(run_dir),
        workspace_dir=str(workspace_dir),
        agent=agent,
        model=model,
        codex_reasoning_effort=codex_reasoning_effort,
        claude_effort=claude_effort,
        num_hours=num_hours,
        launched=launch,
        start_time=start_time,
    )
    write_metadata(metadata)
    if launch:
        exit_code = launch_agent(
            workspace_dir,
            run_dir,
            agent=agent,
            model=model,
            codex_reasoning_effort=codex_reasoning_effort,
            claude_effort=claude_effort,
        )
        (run_dir / "agent_exit_code.txt").write_text(f"{exit_code}\n", encoding="utf-8")
    return metadata


def main(
    task: Annotated[
        str,
        typer.Option("--task", help="Generated task id or task directory path."),
    ] = DEFAULT_TASK,
    agent: Annotated[
        Agent,
        typer.Option("--agent", help="Agent CLI to prepare the run for."),
    ] = Agent.codex,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Agent model. Defaults by agent when omitted."),
    ] = None,
    codex_reasoning_effort: Annotated[
        CodexReasoningEffort,
        typer.Option(
            "--codex-reasoning-effort",
            help="Per-run Codex reasoning effort override. Does not edit Codex config.",
        ),
    ] = CodexReasoningEffort.medium,
    claude_effort: Annotated[
        ClaudeEffort,
        typer.Option(
            "--claude-effort",
            help="Per-run Claude Code effort override. Does not edit Claude config.",
        ),
    ] = ClaudeEffort.medium,
    num_hours: Annotated[
        float,
        typer.Option("--num-hours", help="Time budget for timer.py."),
    ] = DEFAULT_HOURS,
    launch: Annotated[
        bool,
        typer.Option(
            "--launch", help="Launch the selected agent after preparing the run."
        ),
    ] = False,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite", help="Replace an existing run directory with the same id."
        ),
    ] = False,
) -> None:
    """Prepare a per-run workspace for a generated task."""
    selected_model = model if model is not None else default_model(agent)
    metadata = prepare_run(
        task,
        agent=agent,
        model=selected_model,
        codex_reasoning_effort=codex_reasoning_effort,
        claude_effort=claude_effort,
        num_hours=num_hours,
        launch=launch,
        overwrite=overwrite,
    )
    typer.echo(f"Prepared {metadata.run_id}")
    typer.echo(f"  run: {metadata.run_dir}")
    typer.echo(f"  workspace: {metadata.workspace_dir}")
    if launch:
        typer.echo("  launched: yes")
    else:
        typer.echo("  launched: no")
        typer.echo("  rerun with --launch to start the selected agent")


if __name__ == "__main__":
    typer.run(main)
