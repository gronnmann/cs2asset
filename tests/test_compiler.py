import sys

import pytest

import cs2asset.compiler as module
from cs2asset.compiler import compile_resources, run_process
from cs2asset.discovery import Installation
from cs2asset.errors import CS2AssetError


@pytest.fixture
def staged(tmp_path):
    root = tmp_path / "CS2 with spaces"
    compiler = root / "game/bin/win64/resourcecompiler.exe"
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(b"compiler")
    installation = Installation(root, compiler, root / "game", root / "content")
    content = root / "content/csgo_addons/stage"
    game = root / "game/csgo_addons/stage"
    source = content / "materials/test.vmat"
    source.parent.mkdir(parents=True)
    source.write_text("material")
    expected = game / "materials/test.vmat_c"
    expected.parent.mkdir(parents=True)
    return installation, content, game, source, expected, tmp_path / "logs"


def invoke(staged, **kwargs):
    installation, content, game, source, _, logs = staged
    return compile_resources(installation, content, game, [source], logs, **kwargs)


def test_compile_captures_full_dependency_set_and_correct_arguments(staged, monkeypatch):
    installation, _, game, source, expected, _ = staged

    def fake(args, log, **kwargs):
        assert args == [
            str(installation.compiler),
            "-nop4",
            "-game",
            str(installation.game_context),
            "-i",
            str(source),
            "-f",
        ]
        assert kwargs["cwd"] == installation.compiler.parent
        expected.write_bytes(b"fresh material")
        (game / "materials/generated.vtex_c").write_bytes(b"dependency")
        (game / "temporary.log").write_text("not an asset")
        return "OK: 2 compiled, 0 failed, 0 skipped"

    monkeypatch.setattr(module, "run_process", fake)
    assert set(invoke(staged, force=True)) == {
        "materials/test.vmat_c",
        "materials/generated.vtex_c",
    }


@pytest.mark.parametrize(
    "output", ["", "OK: 0 compiled, 0 failed, 0 skipped", "OK: 1 compiled, 0 failed, 0 skipped"]
)
def test_stale_output_never_masks_unproductive_compiler(staged, monkeypatch, output):
    staged[4].write_bytes(b"stale")
    monkeypatch.setattr(module, "run_process", lambda *a, **k: output)
    with pytest.raises(CS2AssetError, match="stale output"):
        invoke(staged)


def test_explicit_valve_dependency_skip_reuses_output(staged, monkeypatch):
    staged[4].write_bytes(b"verified unchanged")
    monkeypatch.setattr(
        module, "run_process", lambda *a, **k: "OK: 0 compiled, 0 failed, 1 skipped"
    )
    assert "materials/test.vmat_c" in invoke(staged)


@pytest.mark.parametrize(
    "text",
    [
        "[FAIL] missing texture",
        "FATAL ERROR",
        "ERROR: 0 compiled",
        "1 compiled, 1 failed, 0 skipped",
    ],
)
def test_valve_failure_text_rejected_despite_stale_file(staged, monkeypatch, text):
    staged[4].write_bytes(b"stale")
    monkeypatch.setattr(module, "run_process", lambda *a, **k: text)
    with pytest.raises(CS2AssetError, match="Valve reported"):
        invoke(staged)


@pytest.mark.parametrize("exists", [False, True])
def test_missing_or_empty_output_rejected(staged, monkeypatch, exists):
    if exists:
        staged[4].touch()
    monkeypatch.setattr(
        module, "run_process", lambda *a, **k: "OK: 1 compiled, 0 failed, 0 skipped"
    )
    with pytest.raises(CS2AssetError, match="no expected resource"):
        invoke(staged)


def test_missing_and_external_sources_rejected_before_process(staged, monkeypatch, tmp_path):
    installation, content, game, source, _, logs = staged
    monkeypatch.setattr(module, "run_process", lambda *a, **k: pytest.fail("Should not launch"))
    with pytest.raises(CS2AssetError, match="outside"):
        compile_resources(installation, content, game, [tmp_path / "outside.vmat"], logs)
    source.unlink()
    with pytest.raises(CS2AssetError, match="missing"):
        invoke(staged)


def test_real_subprocess_preserves_arguments_logs_and_progress(tmp_path):
    seen = []
    log = tmp_path / "logs/run.log"
    text = run_process(
        [
            sys.executable,
            "-X",
            "utf8",
            "-u",
            "-c",
            "import sys; print(sys.argv[1]); print('second')",
            "a path with spaces and ł",
        ],
        log,
        progress=seen.append,
    )
    assert seen == ["a path with spaces and ł", "second"]
    assert "second" in text
    assert "Command:" in log.read_text(encoding="utf-8")


def test_subprocess_failure_is_actionable_and_retains_log(tmp_path):
    log = tmp_path / "failure.log"
    with pytest.raises(CS2AssetError, match="exit 7"):
        run_process(
            [sys.executable, "-c", "print('specific diagnostic'); raise SystemExit(7)"], log
        )
    assert "specific diagnostic" in log.read_text()


def test_timeout_kills_process_and_retains_log(tmp_path, monkeypatch):
    processes = []
    real_popen = module.subprocess.Popen

    def capture(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        if args[0][0] == sys.executable:
            processes.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", capture)
    with pytest.raises(CS2AssetError, match="timed out"):
        run_process(
            [sys.executable, "-u", "-c", "import time; print('started'); time.sleep(30)"],
            tmp_path / "timeout.log",
            timeout=0.3,
        )
    assert len(processes) == 1 and processes[0].poll() is not None


def test_cancellation_kills_process_and_retains_partial_log(tmp_path, monkeypatch):
    processes = []
    real_popen = module.subprocess.Popen

    def capture(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        if args[0][0] == sys.executable:
            processes.append(process)
        return process

    def cancel(line):
        raise KeyboardInterrupt()

    monkeypatch.setattr(module.subprocess, "Popen", capture)
    log = tmp_path / "cancelled.log"
    with pytest.raises(KeyboardInterrupt):
        run_process(
            [sys.executable, "-u", "-c", "import time; print('started'); time.sleep(30)"],
            log,
            progress=cancel,
        )
    assert len(processes) == 1 and processes[0].poll() is not None
    assert "started" in log.read_text()


def test_unknown_executable_has_actionable_error(tmp_path):
    with pytest.raises(CS2AssetError, match="Cannot start"):
        run_process([str(tmp_path / "missing.exe")], tmp_path / "missing.log")
