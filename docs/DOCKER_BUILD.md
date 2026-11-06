# Docker Build

Build from a clean export of a commit, not from a working folder. The repository has no `.dockerignore`, so a working-folder build copies whatever is present into the image.

## Verified build

Image `voicequote-backend:44550ea`, id `sha256:51af1a4ae2da…`, 470 MB, from commit `44550ea3a3b4dc210ffe378568a9745dcdfc3809`, built 2026-11-07. Base image `python:3.13-slim@sha256:09a17cad0266…`.

```bash
# From SE_Project. The -c flags keep Linux line endings even on a Windows machine.
mkdir -p /tmp/vq-build
git -c core.autocrlf=false -c core.eol=lf archive 44550ea backend | tar -x -C /tmp/vq-build
cd /tmp/vq-build/backend
docker build --progress=plain \
  --label org.opencontainers.image.revision=44550ea3a3b4dc210ffe378568a9745dcdfc3809 \
  -t voicequote-backend:44550ea .
```

Build time was about 2 minutes on this machine. The only build warning is cosmetic (`FromAsCasing` on `as builder`).

## Verified contents

| Check | Result |
| --- | --- |
| `.env`, `.venv`, `.git`, credentials, keys, secrets | None found in `/app` or elsewhere outside system paths |
| Secrets in image layers or config | None (`docker history`, image environment) |
| Customer files | None (`generated_pdfs`, `uploads` hold only `.gitkeep`) |
| Compilers and tools | No gcc, make, git, ssh or wget. `curl` is present for the health check |
| Runtime user | `appuser` (uid 1000), not root |
| `start.sh` line endings | LF, 0 carriage returns |
| Python dependencies | 64 packages, `pip check` clean. `pip-audit`: no known vulnerabilities on 2026-11-07. OS packages not scanned |
| Exact versions | `backend/scripts/verify/image-freeze-44550ea.txt` |

## Known build problems

| Problem | Effect | Fix |
| --- | --- | --- |
| No `.dockerignore` | A working-folder build copies `.venv` (264 MB), `.saas-test-certs` (test private keys) and any local files. `backend-backend:latest` contains a `.venv` this way | Add a `.dockerignore` |
| No `.gitattributes`, `core.autocrlf=true` here | A Windows checkout or plain `git archive` gives `start.sh` CRLF endings, confirmed on this machine. The effect on `sh` was not run, but CRLF normally breaks `set -e` | Add `*.sh text eol=lf` |
| `start.sh` is mode 100644 in git | Harmless today: the image runs `sh scripts/start.sh`. It showed as executable in this image only because of the Windows build context | Set mode 100755 |
| Unpinned dependencies (`>=`) and base tag | A later build can differ, so builds are not reproducible | Commit a lock file and pin the base digest |
| Tests, `pytest` and `docker-compose.yml` (with a dev password) are in the image | Extra attack surface, and the password is a local development value | Exclude with `.dockerignore`, separate dev requirements |
| `/app` and `/opt/venv` writable by the runtime user | A compromised process could alter code | Make them root-owned, read-only |
| No home directory for `appuser` | Tools expecting `$HOME` can fail. The app itself is unaffected | Create one, or set `HOME` |
