# Docker Build

## Verified build

Image `voicequote-backend:3f4fb8e`, id `5ecc7cdce036`, 451 MB, from commit `3f4fb8e7b0328ba4730f243b919f3f8697906c09`, built 2026-11-07. Test image `voicequote-backend:3f4fb8e-dev`, id `cc06746bd1b2`.

```bash
# From SE_Project. A plain export is safe: .gitattributes keeps LF on every platform.
mkdir -p ../vq-build
git archive 3f4fb8e backend | tar -x -C ../vq-build
cd ../vq-build/backend
docker build --label org.opencontainers.image.revision=$(git -C ../../SE_Project rev-parse 3f4fb8e) -t voicequote-backend:3f4fb8e .
docker build --target dev -t voicequote-backend:3f4fb8e-dev .
```

The default target is the production image. The `dev` target adds tests, `pytest` and the seed script, and is never deployed.

## Verified contents

| Check | Result |
| --- | --- |
| `/app` holds only | `alembic`, `alembic_control`, their `.ini` files, `app`, `scripts/create_admin.py`, `generated_pdfs`, `uploads` |
| Absent | `.env`, `.venv`, `.git`, tests, `seed.py`, compose files, Dockerfile, `pytest` |
| Real credential values | None in any of 12,098 files across all layers, or in the image configuration (checked by value, against the real `.env` and the running container) |
| Secret formats, private keys | None in `/app` |
| Runtime user | `appuser` (uid 1000), with a home directory |
| Writable by runtime user | Only `generated_pdfs` and `uploads`. Code and dependencies are root-owned |
| Tools | No gcc, make, git, ssh, wget or nc. `curl` remains for the health check |
| Line endings | 0 carriage returns in code |
| Working-folder build context | `.dockerignore` keeps out `.env`, `.venv`, test keys, caches and customer files (tested from a real working folder) |

The image does contain the published development passwords, as strings in `app/core/dev_accounts.py`, because the app needs them to refuse them. They are public already, and grant nothing outside development.

## Reproducibility

| Check | Result | Status |
| --- | --- | --- |
| Base image pinned by digest (`python:3.13-slim@sha256:09a17cad0266…`) | All `FROM` lines | PASS |
| Image actually built on that base | Its layers begin with the pinned base's layers | PASS |
| Wrong base digest | Build fails, no image produced | PASS |
| Dependencies locked with hashes (58 runtime, 6 test) | `requirements.txt`, `requirements-dev.txt` | PASS |
| Image holds exactly the locked packages | `pip freeze` matches the lock, 58 of 58 | PASS |
| Wrong hash | `pip` refuses, while the correct hash installs on the same network | PASS |
| Missing hash | `pip` refuses | PASS |
| Tampered lock in a real `docker build` | Build fails with `DO NOT MATCH THE HASHES`, no image produced | PASS |
| `--no-cache` rebuild | Every file in `/app` and `/opt/venv` identical, excluding `__pycache__` | PASS |
| Bit-for-bit identical image | **No.** Image ids differ, because layers carry timestamps | PARTIAL |
| OS packages | `apt` versions are not pinned. They matched today, but can change with Debian updates | PARTIAL |

OS package scanning has still not been done. `pip-audit` last ran on the same locked versions (no known vulnerabilities on 2026-11-07).

## Updating dependencies

Edit `requirements.in` or `requirements-dev.in`, then on Python 3.13:

```bash
pip-compile --generate-hashes --allow-unsafe --strip-extras requirements.in
```

```bash
pip-compile --generate-hashes --allow-unsafe --strip-extras requirements-dev.in
```

Rebuild, then rerun `scripts/verify/verify_image.sh all`. To change the base image, update the digest on both `FROM python` lines.
