"""Run Valve's compiler in an isolated addon and retain complete diagnostics."""

import os
import queue
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .errors import CS2AssetError


def run_process(
    args: list[str],
    log: Path,
    *,
    timeout: float = 600,
    progress: Callable[[str], None] | None = None,
    cwd: Path | None = None,
) -> str:
    log.parent.mkdir(parents=True, exist_ok=True)
    output: queue.Queue = queue.Queue()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        raise CS2AssetError(f"Cannot start {args[0]}: {exc}") from exc

    def reader():
        try:
            for line in process.stdout:
                output.put(line)
        finally:
            output.put(None)

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    lines = []
    deadline = time.monotonic() + timeout
    try:
        with log.open("w", encoding="utf-8") as handle:
            handle.write("Command: " + subprocess.list2cmdline(args) + "\n")
            while True:
                if time.monotonic() > deadline:
                    raise CS2AssetError(f"Process timed out after {timeout:g}s. Log: {log}")
                try:
                    line = output.get(timeout=0.1)
                except queue.Empty:
                    continue
                if line is None:
                    break
                lines.append(line)
                handle.write(line)
                handle.flush()
                if progress:
                    progress(line.rstrip())
        try:
            code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise CS2AssetError(f"Process timed out after {timeout:g}s. Log: {log}") from exc
    finally:
        if process.poll() is None:
            if os.name == "nt":
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        creationflags=flags,
                        check=False,
                        timeout=10,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
                if process.poll() is None:
                    process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        reader_thread.join(timeout=2)
        process.stdout.close()
    text = "".join(lines)
    if code:
        tail = "\n".join(text.splitlines()[-12:])
        raise CS2AssetError(f"{Path(args[0]).name} failed (exit {code}). Log: {log}\n{tail}")
    return text


def compile_resources(
    installation,
    content: Path,
    game: Path,
    sources: list[Path],
    logs: Path,
    *,
    force: bool = False,
    timeout: float = 600,
    progress=None,
) -> dict[str, Path]:
    """Compile source roots and collect every child resource generated in staging."""
    if not sources:
        raise CS2AssetError("No source resources were provided for compilation.")
    content = content.resolve()
    game = game.resolve()
    game.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(sources):
        source = source.resolve()
        try:
            relative = source.relative_to(content)
        except ValueError as exc:
            raise CS2AssetError(
                f"Compilation source is outside its staging addon: {source}"
            ) from exc
        if not source.is_file():
            raise CS2AssetError(f"Compilation source is missing: {source}")
        expected = game / (relative.as_posix() + "_c")
        if not expected.resolve().is_relative_to(game):
            raise CS2AssetError(f"Compiled output redirects outside its staging addon: {expected}")
        previous = expected.stat() if expected.is_file() else None
        args = [
            str(installation.compiler),
            "-nop4",
            "-game",
            str(installation.root / "game/csgo"),
            "-i",
            str(source),
        ]
        if force:
            args.append("-f")
        text = run_process(
            args,
            logs / f"compile-{index:03d}.log",
            timeout=timeout,
            progress=progress,
            cwd=installation.compiler.parent,
        )
        summary = re.search(r"\b(\d+) compiled,\s*(\d+) failed,\s*(\d+) skipped", text)
        if re.search(r"(?:\[FAIL\]|ERROR:\s*\d+ compiled|FATAL ERROR)", text) or (
            summary and int(summary[2]) > 0
        ):
            raise CS2AssetError(f"Valve reported compilation failure. See {logs}")
        if not expected.is_file() or expected.stat().st_size == 0:
            raise CS2AssetError(f"Compiler produced no expected resource: {expected}. See {logs}")
        current = expected.stat()
        changed = previous is None or (
            current.st_mtime_ns,
            current.st_ctime_ns,
            current.st_size,
        ) != (previous.st_mtime_ns, previous.st_ctime_ns, previous.st_size)
        # Valve's explicit skipped summary is evidence of its dependency check.
        # An old output plus an empty/unsuccessful invocation is not success.
        validated_skip = summary is not None and int(summary[2]) == 0 and int(summary[3]) > 0
        if not changed and not validated_skip:
            raise CS2AssetError(
                f"Compiler left a stale output without validating it: {expected}. See {logs}"
            )
    return {
        p.relative_to(game).as_posix(): p
        for p in game.rglob("*")
        if p.is_file() and p.suffix.endswith("_c")
    }
