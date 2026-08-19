"""Behaviour of dependent discovery, driven through a stub `gh` on PATH.

The decisions that matter: the shape of the output (a JSON array a matrix can consume), that
the publisher never bumps itself, and that a truncated search is an error rather than a
quietly short list.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "discover.sh"


def make_gh(tmp_path: pathlib.Path, stdout: str, exit_code: int = 0) -> pathlib.Path:
    """Stub `gh` that replays fixed output and logs the arguments it was called with.

    The output goes through a file rather than an inlined string so that newlines survive
    intact — the script's whole contract is one repository per line.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    fixture = tmp_path / "gh_stdout"
    fixture.write_text(stdout)
    gh = bindir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{tmp_path}/args"\n'
        f'cat "{fixture}"\n'
        f"exit {exit_code}\n"
    )
    gh.chmod(0o755)
    return bindir


def run(bindir: pathlib.Path, *args: str):
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", GH_TOKEN="stub")
    return subprocess.run(
        ["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True
    )


def test_returns_a_json_array_of_bare_names(tmp_path):
    bindir = make_gh(tmp_path, "acme/consumer-a\nacme/consumer-b\n")
    result = run(bindir, "acme", "my-package", "publisher")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["consumer-a", "consumer-b"]


def test_excludes_the_publisher(tmp_path):
    bindir = make_gh(tmp_path, "acme/consumer-a\nacme/publisher\n")
    result = run(bindir, "acme", "my-package", "publisher")
    assert json.loads(result.stdout) == ["consumer-a"]


def test_deduplicates(tmp_path):
    """One repository can match on several pyproject.toml files."""
    bindir = make_gh(tmp_path, "acme/consumer-a\nacme/consumer-a\nacme/consumer-b\n")
    result = run(bindir, "acme", "my-package", "publisher")
    assert json.loads(result.stdout) == ["consumer-a", "consumer-b"]


def test_no_matches_is_an_empty_array(tmp_path):
    """The caller decides whether empty is an error; this just reports it."""
    bindir = make_gh(tmp_path, "")
    result = run(bindir, "acme", "my-package", "publisher")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_override_skips_the_search(tmp_path):
    bindir = make_gh(tmp_path, "acme/should-not-be-used\n")
    result = run(bindir, "acme", "my-package", "publisher", "consumer-a acme/consumer-b")
    assert json.loads(result.stdout) == ["consumer-a", "consumer-b"]
    # An owner/ prefix is tolerated on the manual input and stripped, and no search ran.
    assert not (tmp_path / "args").exists()


def test_truncated_search_is_an_error(tmp_path):
    """1000 is the Search API's ceiling; a silently short list would look like success."""
    bindir = make_gh(tmp_path, "".join(f"acme/consumer-{i}\n" for i in range(1000)))
    result = run(bindir, "acme", "my-package", "publisher")
    assert result.returncode == 1
    assert "1000-result maximum" in result.stderr


def test_searches_the_right_thing(tmp_path):
    bindir = make_gh(tmp_path, "acme/consumer-a\n")
    run(bindir, "acme", "my-package", "publisher")
    args = (tmp_path / "args").read_text()
    assert "--owner acme" in args
    assert "my-package" in args
    assert "--filename pyproject.toml" in args
