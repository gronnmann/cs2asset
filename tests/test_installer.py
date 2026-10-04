import pytest
from filelock import FileLock

import cs2asset.installer as module
from cs2asset.config import atomic_json
from cs2asset.discovery import Project
from cs2asset.errors import CS2AssetError
from cs2asset.installer import Installer, contained_path, file_hash, safe_relative


@pytest.fixture
def setup(tmp_path):
    project = Project("de_test", tmp_path / "content", tmp_path / "game")
    staged = tmp_path / "staged"
    staged.mkdir()
    source = staged / "test.vmat"
    compiled = staged / "test.vmat_c"
    source.write_bytes(b"first source")
    compiled.write_bytes(b"first compiled")
    installer = Installer(project)
    return installer, source, compiled


REL = "materials/cs2asset/polyhaven/test/2k/test.vmat"


def install(setup, identifier="polyhaven:test:2k", **kwargs):
    installer, source, compiled = setup
    return installer.install(
        identifier, {REL: source}, {REL + "_c": compiled}, {"asset": "test"}, **kwargs
    )


def test_installs_paired_roots_and_hash_manifest(setup):
    installer, source, compiled = setup
    record = install(setup)
    assert (installer.project.content_dir / REL).read_bytes() == source.read_bytes()
    assert (installer.project.game_dir / (REL + "_c")).read_bytes() == compiled.read_bytes()
    assert installer.imports()[record["id"]] == record
    assert len(record["outputs"]) == 2
    assert record["outputs"][0]["sha256"] == file_hash(source)
    assert not installer.journal_path.exists()


def test_repeated_import_reuses_unchanged_outputs(setup, monkeypatch):
    install(setup)

    def no_copy(*args):
        raise AssertionError("Unchanged files should not be copied")

    monkeypatch.setattr(module, "_copy_atomic", no_copy)
    install(setup)


def test_unowned_file_is_never_overwritten_even_with_flag(setup):
    installer, source, _ = setup
    target = installer.project.content_dir / REL
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    with pytest.raises(CS2AssetError, match="not owned"):
        install(setup, overwrite=True)


def test_user_edit_requires_explicit_overwrite(setup):
    installer, source, _ = setup
    install(setup)
    target = installer.project.content_dir / REL
    target.write_bytes(b"user editing")
    with pytest.raises(CS2AssetError, match="User-edited"):
        install(setup)
    assert target.read_bytes() == b"user editing"
    install(setup, overwrite=True)
    assert target.read_bytes() == source.read_bytes()


def test_shared_identical_dependency_and_conflicting_replacement(setup):
    installer, source, _ = setup
    install(setup, "asset-one")
    install(setup, "asset-two")
    source.write_bytes(b"changed source")
    with pytest.raises(CS2AssetError, match="shared with another"):
        install(setup, "asset-one", overwrite=True)
    assert len(installer.imports()) == 2


def test_obsolete_outputs_removed_unless_shared(setup):
    installer, source, compiled = setup
    install(setup, "asset-one")
    install(setup, "asset-two")
    second = REL.replace("test.vmat", "second.vmat")
    installer.install("asset-one", {second: source}, {second + "_c": compiled}, {})
    assert (installer.project.content_dir / REL).exists()
    installer.install("asset-two", {second: source}, {second + "_c": compiled}, {})
    assert not (installer.project.content_dir / REL).exists()
    assert not (installer.project.game_dir / (REL + "_c")).exists()


def test_failure_after_first_publication_rolls_back_both_files_and_manifest(setup, monkeypatch):
    installer, source, compiled = setup
    old_record = install(setup)
    source.write_bytes(b"new source")
    compiled.write_bytes(b"new compiled")
    real_copy = module._copy_atomic
    failed = False

    def fail_once(src, dst):
        nonlocal failed
        if dst == installer.project.game_dir / (REL + "_c") and not failed:
            failed = True
            raise OSError("simulated disk failure")
        real_copy(src, dst)

    monkeypatch.setattr(module, "_copy_atomic", fail_once)
    with pytest.raises(OSError, match="simulated"):
        install(setup)
    assert (installer.project.content_dir / REL).read_bytes() == b"first source"
    assert (installer.project.game_dir / (REL + "_c")).read_bytes() == b"first compiled"
    assert installer.imports()[old_record["id"]] == old_record
    assert not installer.journal_path.exists()


def test_failed_first_install_leaves_no_owned_files(setup, monkeypatch):
    installer, _, _ = setup
    real_copy = module._copy_atomic

    def fail_compiled(src, dst):
        if dst.suffix == ".vmat_c":
            raise OSError("disk full")
        real_copy(src, dst)

    monkeypatch.setattr(module, "_copy_atomic", fail_compiled)
    with pytest.raises(OSError, match="disk full"):
        install(setup)
    assert not (installer.project.content_dir / REL).exists()
    assert installer.imports() == {}


def test_interrupted_job_recovered_from_disk(setup, monkeypatch):
    installer, source, compiled = setup
    record = install(setup)
    source.write_bytes(b"new source")
    compiled.write_bytes(b"new compiled")
    real_copy = module._copy_atomic
    recover = installer._recover_locked
    call_count = 0

    def simulated_crash(src, dst):
        if dst == installer.project.game_dir / (REL + "_c"):
            raise KeyboardInterrupt()
        real_copy(src, dst)

    def skip_immediate_rollback():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return recover()
        return False

    monkeypatch.setattr(module, "_copy_atomic", simulated_crash)
    monkeypatch.setattr(installer, "_recover_locked", skip_immediate_rollback)
    with pytest.raises(KeyboardInterrupt):
        install(setup)
    assert installer.journal_path.exists()
    assert (installer.project.content_dir / REL).read_bytes() == b"new source"
    monkeypatch.setattr(module, "_copy_atomic", real_copy)
    fresh = Installer(installer.project)
    assert fresh.recover()
    assert (installer.project.content_dir / REL).read_bytes() == b"first source"
    assert fresh.imports()[record["id"]] == record
    assert not fresh.recover()


def test_lock_contention_is_actionable(setup):
    installer, _, _ = setup
    installer.meta.mkdir(parents=True)
    installer.lock_timeout = 0
    with (
        FileLock(str(installer.meta / "project.lock")),
        pytest.raises(CS2AssetError, match="Another import"),
    ):
        install(setup)


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "/absolute",
        "C:/path",
        "a\\b",
        "a//b",
        "a/./b",
        "a/../b",
        "con/file",
        "a/nul.png",
        "a/name.",
        "a/name:stream",
        ".",
        "",
    ],
)
def test_unsafe_paths_rejected(path):
    with pytest.raises(CS2AssetError):
        safe_relative(path)


def test_symlink_escape_rejected(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires Windows developer mode or elevation")
    with pytest.raises(CS2AssetError, match="escapes"):
        contained_path(root, "link/file")


def test_case_collisions_rejected(setup):
    installer, source, _ = setup
    with pytest.raises(CS2AssetError, match="case-insensitive"):
        installer.install("test", {REL: source, REL.upper(): source}, {}, {})


def test_journal_cannot_delete_arbitrary_directory(setup):
    installer, _, _ = setup
    atomic_json(installer.journal_path, {"id": "../../outside", "state": "committed"})
    with pytest.raises(CS2AssetError, match="identifier"):
        installer.recover()
