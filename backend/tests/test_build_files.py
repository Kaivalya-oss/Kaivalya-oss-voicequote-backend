"""Reproducible builds and a clean build context. Skipped inside images, which do not contain these files."""
import os
import re
import subprocess

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO = os.path.dirname(BACKEND)


def read(*parts: str) -> str:
    path = os.path.join(*parts)
    if not os.path.exists(path):
        pytest.skip(f"{path} is not available here")
    return open(path, encoding="utf-8").read()


def test_every_base_image_is_pinned_by_digest():
    froms = re.findall(r"^FROM\s+(\S+)", read(BACKEND, "Dockerfile"), re.MULTILINE)
    external = [image for image in froms if image not in ("runtime",)]
    assert external
    for image in external:
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), image


def test_dependencies_install_only_by_hash():
    dockerfile = read(BACKEND, "Dockerfile")
    for line in re.findall(r"^RUN pip install.*$", dockerfile, re.MULTILINE):
        assert "--require-hashes" in line, line


@pytest.mark.parametrize("name", ["requirements.txt", "requirements-dev.txt"])
def test_locks_pin_exact_versions_with_hashes(name):
    packages = re.split(r"\n(?=[A-Za-z0-9])", "\n".join(l for l in read(BACKEND, name).splitlines() if not l.startswith("#")))
    assert packages
    for package in packages:
        assert re.match(r"^[A-Za-z0-9._-]+==[^\s]+ \\", package), package[:60]
        assert "--hash=sha256:" in package, package[:60]


def test_test_tools_are_not_runtime_dependencies():
    lock = read(BACKEND, "requirements.txt")
    for name in ("pytest", "pytest-asyncio", "pluggy", "iniconfig"):
        assert not re.search(rf"^{name}==", lock, re.MULTILINE), name


def test_production_image_is_the_default_target_and_excludes_tests():
    dockerfile = read(BACKEND, "Dockerfile")
    stages = re.split(r"^FROM ", dockerfile, flags=re.MULTILINE)
    assert stages[-1].strip() == "runtime AS production"
    runtime = next(stage for stage in stages if " AS runtime" in stage)
    copied = " ".join(re.findall(r"^COPY (.*)$", runtime, re.MULTILINE))
    for path in ("tests", "seed.py", "COPY . ", "docker-compose", "pytest"):
        assert path not in copied, path
    assert "COPY . ." not in runtime


def test_application_code_is_not_writable_by_the_runtime_user():
    runtime = read(BACKEND, "Dockerfile").split("AS runtime")[1].split("AS dev")[0]
    assert "--chown" not in runtime
    assert "chown appuser:appuser uploads generated_pdfs" in runtime
    assert "chown -R" not in runtime


@pytest.mark.parametrize(
    "pattern",
    [".env", ".venv", ".saas-test-certs", ".secrets", "credentials.json", "generated_pdfs", "uploads", "**/*.pem", "**/*.key", "**/*.enc", ".git"],
)
def test_dockerignore_excludes_sensitive_paths(pattern):
    assert pattern in read(BACKEND, ".dockerignore").splitlines()


def test_text_files_keep_lf_line_endings():
    rules = read(REPO, ".gitattributes").splitlines()
    assert "* text=auto eol=lf" in rules
    assert "*.sh text eol=lf" in rules


def test_git_applies_lf_rule_even_with_autocrlf():
    read(REPO, ".gitattributes")
    try:
        out = subprocess.run(
            ["git", "-c", "core.autocrlf=true", "check-attr", "eol", "backend/scripts/example.sh"],
            cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("git is not available here")
    assert out.strip().endswith("eol: lf")
