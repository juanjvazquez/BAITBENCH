#!/usr/bin/env python3
"""Rich transcript viewer for Codex and Claude run transcripts.

Usage:
    uv run scripts/view_transcript.py <transcript.json>
    uv run scripts/view_transcript.py <transcript.json> --no-output  # hide command output
    uv run scripts/view_transcript.py <transcript.json> --max-output 20  # truncate output lines

Can also read from VPS:
    ssh -i ~/.ssh/autoresearch team@87.99.129.5 'cat ~/make_datasets_runs/<run_id>/transcript.json' | uv run scripts/view_transcript.py -
"""
from __future__ import annotations

import json
import shlex
import sys
import textwrap
from pathlib import Path


BLUE = "\033[34m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"


def wrap(text: str, indent: str = "    ") -> str:
    lines = text.splitlines()
    return "\n".join(indent + l for l in lines)


def truncate_output(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    head = lines[: max_lines // 2]
    tail = lines[-(max_lines // 2) :]
    omitted = len(lines) - len(head) - len(tail)
    return "\n".join(head + [f"    ... ({omitted} lines omitted) ..."] + tail)


def unwrap_bash(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if len(parts) >= 3 and parts[1] == "-lc":
        return parts[2]
    return command


def render_codex_transcript(lines: list[dict], *, max_output: int, show_output: bool) -> None:
    step = 0
    for obj in lines:
        item = obj.get("item")
        if not isinstance(item, dict):
            # Handle turn.started, turn.completed, thread.started
            t = obj.get("type", "")
            if t == "turn.completed":
                usage = obj.get("usage", {})
                inp = usage.get("input_tokens", 0)
                out = usage.get("output_tokens", 0)
                cached = usage.get("cached_input_tokens", 0)
                print(f"\n{DIM}─── turn complete: {inp:,} input ({cached:,} cached), {out:,} output ───{RESET}")
            continue

        if obj.get("type") != "item.completed":
            continue

        item_type = item.get("type")

        if item_type == "agent_message":
            text = item.get("text", "")
            print(f"\n{BOLD}{BLUE}💭 Agent:{RESET}")
            print(wrap(text, indent="   "))

        elif item_type == "command_execution":
            step += 1
            command = unwrap_bash(item.get("command", ""))
            exit_code = item.get("exit_code")
            output = item.get("aggregated_output", "")

            color = GREEN if exit_code == 0 else RED
            print(f"\n{BOLD}{color}[{step}] ${RESET} {CYAN}{command}{RESET}")
            if exit_code != 0:
                print(f"   {RED}exit code: {exit_code}{RESET}")
            if show_output and output.strip():
                truncated = truncate_output(output.strip(), max_output)
                print(f"{DIM}{wrap(truncated)}{RESET}")

        elif item_type == "file_change":
            step += 1
            changes = item.get("changes", [])
            for change in changes:
                kind = change.get("kind", "?")
                path = change.get("path", "?")
                # Shorten path
                parts = path.split("/")
                short = "/".join(parts[-2:]) if len(parts) > 2 else path
                icon = {"add": "📄", "update": "✏️", "delete": "🗑️"}.get(kind, "📝")
                print(f"\n{YELLOW}[{step}] {icon} {kind}: {short}{RESET}")


def render_claude_transcript(lines: list[dict], *, max_output: int, show_output: bool) -> None:
    step = 0
    for obj in lines:
        t = obj.get("type", "")
        msg = obj.get("message", {})
        role = msg.get("role", "")
        content = msg.get("content", [])

        if t == "system":
            print(f"{DIM}─── session start: {obj.get('model', '?')} ───{RESET}")
            continue

        if not isinstance(content, list):
            continue

        for c in content:
            if not isinstance(c, dict):
                continue
            ct = c.get("type", "")

            if ct == "text" and role == "assistant":
                text = c.get("text", "")
                if text.strip():
                    print(f"\n{BOLD}{BLUE}💭 Agent:{RESET}")
                    print(wrap(text.strip(), indent="   "))

            elif ct == "tool_use" and role == "assistant":
                step += 1
                name = c.get("name", "?")
                inp = c.get("input", {})

                if name == "Bash":
                    command = inp.get("command", "")
                    print(f"\n{BOLD}{GREEN}[{step}] ${RESET} {CYAN}{command[:200]}{RESET}")
                elif name == "Read":
                    path = inp.get("file_path", "")
                    parts = path.split("/")
                    short = "/".join(parts[-2:]) if len(parts) > 2 else path
                    print(f"\n{BOLD}{MAGENTA}[{step}] 📖 Read:{RESET} {short}")
                elif name == "Edit":
                    path = inp.get("file_path", "")
                    parts = path.split("/")
                    short = "/".join(parts[-2:]) if len(parts) > 2 else path
                    old = (inp.get("old_string", "") or "")[:80]
                    new = (inp.get("new_string", "") or "")[:80]
                    print(f"\n{BOLD}{YELLOW}[{step}] ✏️ Edit:{RESET} {short}")
                    if old:
                        print(f"   {RED}- {old}{RESET}")
                    if new:
                        print(f"   {GREEN}+ {new}{RESET}")
                elif name == "Write":
                    path = inp.get("file_path", "")
                    parts = path.split("/")
                    short = "/".join(parts[-2:]) if len(parts) > 2 else path
                    print(f"\n{BOLD}{YELLOW}[{step}] 📄 Write:{RESET} {short}")
                else:
                    print(f"\n{BOLD}{GREEN}[{step}] 🔧 {name}:{RESET} {str(inp)[:150]}")

            elif ct == "tool_result" and role == "user":
                result = c.get("content", "")
                is_error = c.get("is_error", False)
                if is_error:
                    print(f"   {RED}ERROR: {str(result)[:200]}{RESET}")
                elif show_output and str(result).strip():
                    truncated = truncate_output(str(result).strip(), max_output)
                    print(f"{DIM}{wrap(truncated)}{RESET}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Rich transcript viewer")
    parser.add_argument("path", help="Path to transcript.json, or - for stdin")
    parser.add_argument("--no-output", action="store_true", help="Hide command/tool output")
    parser.add_argument("--max-output", type=int, default=30, help="Max output lines per command")
    args = parser.parse_args()

    if args.path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(args.path).read_text()

    lines = [json.loads(line) for line in raw.strip().splitlines() if line.strip()]

    show_output = not args.no_output

    # Detect format: Codex has "item" keys, Claude has "message" keys
    has_item = any("item" in obj for obj in lines[:20])
    has_message = any("message" in obj for obj in lines[:20])

    if has_item and not has_message:
        print(f"{BOLD}Format: Codex{RESET}\n")
        render_codex_transcript(lines, max_output=args.max_output, show_output=show_output)
    elif has_message:
        print(f"{BOLD}Format: Claude{RESET}\n")
        render_claude_transcript(lines, max_output=args.max_output, show_output=show_output)
    else:
        print("Unknown transcript format", file=sys.stderr)
        sys.exit(1)

    print(f"\n{DIM}─── end of transcript ───{RESET}")


if __name__ == "__main__":
    main()
