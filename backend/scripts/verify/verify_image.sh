#!/bin/bash
# Full verification of a built image, against the isolated stack in docker-compose.verify.yml only.
#
#   IMAGE=voicequote-backend:<tag> DEV_IMAGE=voicequote-backend:<tag>-dev ROLLBACK_IMAGE=voicequote-backend:30d80ca \
#   CERT_DIR=<certs> scripts/verify/verify_image.sh <phase>...
#
# Phases: contents safety single pytest multitenant showroommigration outage migration backup hashes platformadmin all
# Never reads a real .env, and every database and container here is disposable.
export MSYS_NO_PATHCONV=1
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PG=vqv-postgres-1
NET=vqv_net
TESTPW=vqv-test-only
URL=postgresql+asyncpg://postgres:$TESTPW@postgres:5432
JWT="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
ADMIN_EMAIL=verify.admin@example.com
ADMIN_PASSWORD="Verify-$(python -c 'import secrets; print(secrets.token_hex(16))')"
FAILS=0
PASSES=0
WORK="$(mktemp -d)"
DOCKER_WORK="$(cygpath -m "$WORK" 2>/dev/null || echo "$WORK")"  # the path as Docker on Windows needs it
trap 'rm -rf "$WORK"' EXIT

ok() { PASSES=$((PASSES + 1)); echo "PASS  $1"; }
bad() { FAILS=$((FAILS + 1)); echo "FAIL  $1${2:+  -- $2}"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected '$3', got '$2'"; fi; }
sql() { docker exec -i "$PG" psql -U postgres -d "$1" -Atq -c "$2" 2>&1 | tr -d '\r'; }
newdb() { sql vqv_single "DROP DATABASE IF EXISTS $1 WITH (FORCE)" >/dev/null; sql vqv_single "CREATE DATABASE $1" >/dev/null; }
rmc() { docker rm -f "$@" >/dev/null 2>&1; true; }
envs() { echo "-e APP_ENV=production -e DEBUG=False -e JWT_SECRET_KEY=$JWT -e FRONTEND_URL=https://staging.invalid -e DATABASE_URL=$URL/$1"; }

wait_healthy() {
    local i status
    for i in $(seq 1 60); do
        status="$(docker inspect "$1" --format '{{.State.Status}}' 2>/dev/null)"
        [ "$status" = "exited" ] && return 1
        docker run --rm --network $NET --entrypoint curl "$IMAGE" -fsS -m 3 "http://$1:8000/health" >/dev/null 2>&1 && return 0
        sleep 1
    done
    return 1
}
login_status() {
    docker run --rm -i --network $NET --entrypoint python "$IMAGE" -c "
import httpx,sys
print(httpx.post('http://$1:8000/api/v1/auth/login',json={'email':'$2','password':'$3'},timeout=20).status_code)" 2>&1 | tail -1 | tr -d '\r'
}
DEV_ACCOUNTS="admin@dealership.com:AdminPass123! owner@dealership.com:OwnerPass123! sales@dealership.com:SalesPass123! finance@dealership.com:FinancePass123! inventory@dealership.com:InventoryPass123!"

phase_contents() {
    echo "=== Image contents: $IMAGE"
    docker run --rm --network none --entrypoint sh "$IMAGE" -c '
      cd /app
      echo "user=$(id -un) uid=$(id -u)"
      echo "top: $(ls -A | tr "\n" " ")"
      echo "scripts: $(ls scripts | tr "\n" " ")"
      for p in .env .venv .git tests tests_saas scripts/seed.py scripts/start.sh scripts/verify docker-compose.yml .saas-test-certs credentials.json Dockerfile pytest.ini; do [ -e "$p" ] && echo "PRESENT $p"; done
      echo "sensitive=$(find / -xdev \( -name "*.pem" -o -name "*.key" -o -name "*.enc" -o -name ".env*" -o -name "credentials*.json" \) -not -path "/proc/*" -not -path "/sys/*" -not -path "/opt/venv/*" -not -path "/usr/*" -not -path "/etc/ssl/*" 2>/dev/null | wc -l)"
      echo "pytest=$(python -c "import importlib.util as u; print(u.find_spec(\"pytest\") is not None)")"
      echo "writable=$(for d in /app /app/app /app/alembic /app/scripts /opt/venv; do [ -w "$d" ] && echo "$d"; done | tr "\n" " ")"
      echo "storage=$(for d in /app/uploads /app/generated_pdfs; do [ -w "$d" ] && echo "$d"; done | tr "\n" " ")"
      echo "tools=$(for t in gcc make git ssh wget nc; do command -v "$t"; done | tr "\n" " ")"
      echo "cr=$(find /app -name "*.py" -o -name "*.ini" | xargs cat | tr -cd "\r" | wc -c)"
      echo "home=$HOME"
      echo "pipcheck=$(pip check 2>&1 | tail -1)"
    ' | tr -d '\r' | tee /tmp/vq_contents.txt
    grep -q "^PRESENT" /tmp/vq_contents.txt && bad "no excluded file present" || ok "no excluded file present"
    check "non-root user" "$(grep -c '^user=appuser uid=1000$' /tmp/vq_contents.txt)" 1
    check "no sensitive files" "$(grep -c '^sensitive=0$' /tmp/vq_contents.txt)" 1
    check "pytest not installed" "$(grep -c '^pytest=False$' /tmp/vq_contents.txt)" 1
    check "code not writable by runtime user" "$(grep -c '^writable=$' /tmp/vq_contents.txt)" 1
    check "storage writable" "$(grep -c '^storage=/app/uploads /app/generated_pdfs $' /tmp/vq_contents.txt)" 1
    check "no compilers or network tools" "$(grep -c '^tools=$' /tmp/vq_contents.txt)" 1
    check "no carriage returns in code" "$(grep -c '^cr=0$' /tmp/vq_contents.txt)" 1
    check "dependencies consistent" "$(grep -c 'No broken' /tmp/vq_contents.txt)" 1
    check "default command only starts the server" "$(docker inspect "$IMAGE" --format '{{json .Config.Cmd}}' | grep -cE 'uvicorn' )" 1
    check "default command has no migration or seed" "$(docker inspect "$IMAGE" --format '{{json .Config.Cmd}}{{json .Config.Entrypoint}}' | grep -cE 'alembic|seed|start.sh')" 0
    check "no secret in image environment" "$(docker inspect "$IMAGE" --format '{{json .Config.Env}}' | grep -ciE 'password|secret|token|api_key')" 0
    check "no secret in image history" "$(docker history --no-trunc "$IMAGE" | grep -ciE 'password=|secret=|api_key=|token=')" 0
    echo "--- build context from a dirty working folder (nothing saved as an image)"
    local tar="$WORK/ctx.tar"
    (cd "$HERE/../.." && printf 'FROM scratch\nCOPY . /\n' | docker build -q -f- -o "type=tar,dest=$DOCKER_WORK/ctx.tar" . >/dev/null 2>&1)
    if [ -f "$tar" ]; then
        echo "INFO  context holds $(tar -tf "$tar" | wc -l) entries"
        tar -tf "$tar" | grep -E '(^|/)(\.env|\.venv|\.saas-test-certs|\.secrets|credentials\.json|__pycache__)(/|$)|\.pem$|\.key$|\.enc$|generated_pdfs|uploads/' > /tmp/vq_ctx_bad.txt
        check "dockerignore keeps local files out of a working-folder build" "$(wc -l < /tmp/vq_ctx_bad.txt | tr -d ' ')" 0
        rm -f "$tar"
    else
        bad "dockerignore context test" "could not export context"
    fi
}

phase_safety() {
    echo "=== Production startup safety"
    local c=vqv-safety
    newdb vqs_empty
    rmc $c
    docker run -d --name $c --network $NET $(envs vqs_empty) "$IMAGE" >/dev/null
    docker wait $c >/dev/null
    check "refuses to start on an unmigrated database (exit non-zero)" "$([ "$(docker inspect $c --format '{{.State.ExitCode}}')" != 0 ] && echo yes)" yes
    check "message names the migration command" "$(docker logs $c 2>&1 | grep -q 'alembic upgrade head' && echo yes)" yes
    check "startup created no tables" "$(sql vqs_empty "select count(*) from information_schema.tables where table_schema='public'")" 0
    rmc $c

    docker run --rm --network $NET $(envs vqs_empty) --entrypoint alembic "$IMAGE" upgrade head >/dev/null 2>&1
    check "explicit migration succeeds" "$?" 0
    check "migration created no accounts" "$(sql vqs_empty 'select count(*) from users')" 0

    docker run --rm --network $NET $(envs vqs_empty) -e SEED_DEV_DATA=true --entrypoint python "$IMAGE" -c "import app.core.config" >/dev/null 2>&1
    check "SEED_DEV_DATA=true refused in production" "$?" 1
    docker run --rm --network $NET $(envs vqs_empty) -e SEED_DEV_DATA=true "$IMAGE" >/dev/null 2>&1
    check "server refuses to start with SEED_DEV_DATA=true" "$([ $? != 0 ] && echo yes)" yes
    docker run --rm --network $NET $(envs vqs_empty) --entrypoint test "$IMAGE" -e scripts/seed.py
    check "seed script absent from production image" "$?" 1
    for env_name in production staging prod; do
        out="$(docker run --rm --network $NET $(envs vqs_empty) -e APP_ENV=$env_name --entrypoint python "$DEV_IMAGE" scripts/seed.py 2>&1)"
        check "dev image seed refused without opt-in (APP_ENV=$env_name)" "$?" 1
        check "  refusal is explicit" "$(echo "$out" | grep -c 'Refusing to seed')" 1
    done
    docker run --rm --network $NET $(envs vqs_empty) -e APP_ENV=staging -e SEED_DEV_DATA=true --entrypoint python "$DEV_IMAGE" scripts/seed.py >/dev/null 2>&1
    check "dev image seed refused with opt-in on staging" "$([ $? != 0 ] && echo yes)" yes
    check "no accounts after all seed attempts" "$(sql vqs_empty 'select count(*) from users')" 0

    docker run -d --name $c --network $NET $(envs vqs_empty) "$IMAGE" >/dev/null
    wait_healthy $c; check "starts on a migrated database" "$?" 0
    check "startup created no accounts" "$(sql vqs_empty 'select count(*) from users')" 0
    check "startup created no roles" "$(sql vqs_empty 'select count(*) from roles')" 0
    for a in $DEV_ACCOUNTS; do
        check "default account refused, empty database: ${a%%:*}" "$(login_status $c "${a%%:*}" "${a#*:}")" 401
    done

    for pair in "admin@dealership.com:$ADMIN_PASSWORD" "$ADMIN_EMAIL:AdminPass123!" "$ADMIN_EMAIL:short"; do
        docker run --rm --network $NET $(envs vqs_empty) -e ADMIN_EMAIL="${pair%%:*}" -e ADMIN_FULL_NAME=Verify -e ADMIN_PASSWORD="${pair#*:}" --entrypoint python "$IMAGE" scripts/create_admin.py >/dev/null 2>&1
        check "create_admin refuses published or weak value (${pair%%:*})" "$?" 1
    done
    docker run --rm --network $NET $(envs vqs_empty) -e ADMIN_EMAIL=$ADMIN_EMAIL -e ADMIN_FULL_NAME=Verify -e ADMIN_PASSWORD="$ADMIN_PASSWORD" --entrypoint python "$IMAGE" scripts/create_admin.py >/dev/null 2>&1
    check "create_admin creates the first administrator" "$?" 0
    check "real administrator can sign in" "$(login_status $c $ADMIN_EMAIL "$ADMIN_PASSWORD")" 200
    check "password never reaches the logs" "$(docker logs $c 2>&1 | grep -c "$ADMIN_PASSWORD")" 0
    docker restart $c >/dev/null; wait_healthy $c
    check "restart adds no accounts" "$(sql vqs_empty 'select count(*) from users')" 1
    rmc $c

    echo "--- a database that already holds the default accounts"
    newdb vqs_seeded
    docker run --rm --network $NET -e APP_ENV=test -e SEED_DEV_DATA=true -e DATABASE_URL=$URL/vqs_seeded --entrypoint sh "$DEV_IMAGE" -c "alembic upgrade head && python scripts/seed.py" >/dev/null 2>&1
    check "test database seeded on purpose" "$(sql vqs_seeded 'select count(*) from users')" 5
    docker run -d --name $c --network $NET $(envs vqs_seeded) "$IMAGE" >/dev/null
    wait_healthy $c
    for a in $DEV_ACCOUNTS; do
        check "default account refused, seeded database: ${a%%:*}" "$(login_status $c "${a%%:*}" "${a#*:}")" 401
    done
    local minted
    minted="$(docker run --rm --network none -e APP_ENV=production -e JWT_SECRET_KEY=$JWT --entrypoint python "$IMAGE" -c "
from app.core.security import create_access_token
print(create_access_token(subject=1, extra_claims={'email':'admin@dealership.com','role':'ADMIN','name':'x'}))" 2>/dev/null | tail -1 | tr -d '\r')"
    local status
    status="$(docker run --rm --network $NET --entrypoint curl "$IMAGE" -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $minted" http://$c:8000/api/v1/customers)"
    check "valid token for a default account refused" "$status" 401
    rmc $c
}

phase_single() {
    echo "=== Single-showroom API, APP_ENV=production"
    local c=vqv-single
    newdb vqv_s1
    rmc $c
    docker run --rm --network $NET $(envs vqv_s1) --entrypoint alembic "$IMAGE" upgrade head >/dev/null 2>&1
    docker run --rm --network $NET $(envs vqv_s1) -e ADMIN_EMAIL=$ADMIN_EMAIL -e ADMIN_FULL_NAME=Verify -e ADMIN_PASSWORD="$ADMIN_PASSWORD" --entrypoint python "$IMAGE" scripts/create_admin.py >/dev/null
    docker run -d --name $c --network $NET $(envs vqv_s1) -e MULTI_TENANT_ENABLED=false "$IMAGE" >/dev/null
    wait_healthy $c; check "healthy" "$?" 0
    docker run --rm -i --network $NET -e BASE=http://$c:8000 -e ADMIN_EMAIL=$ADMIN_EMAIL -e ADMIN_PASSWORD="$ADMIN_PASSWORD" --entrypoint python "$IMAGE" - < "$HERE/verify_single.py" | tr -d '\r' | tee /tmp/vq_single.txt | grep -vE '^PASS'
    check "API checks" "${PIPESTATUS[0]}" 0
    check "no errors in logs" "$(docker logs $c 2>&1 | grep -cE '\[ERROR\]|Traceback')" 0
    local t0 t1
    t0=$(date +%s); docker stop $c >/dev/null; t1=$(date +%s)
    check "graceful stop exit code" "$(docker inspect $c --format '{{.State.ExitCode}}')" 0
    echo "INFO  stopped in $((t1 - t0))s"
    rmc $c
}

phase_pytest() {
    echo "=== Test suites in the test image"
    newdb vqv_pt; newdb vqv_pta
    docker run --rm --network $NET -e APP_ENV=test -e SEED_DEV_DATA=true -e DATABASE_URL=$URL/vqv_pt -e TEST_DATABASE_URL=$URL/vqv_pt --entrypoint sh "$DEV_IMAGE" -c "alembic upgrade head && python scripts/seed.py" >/dev/null 2>&1
    docker run --rm --network $NET -e APP_ENV=test -e DEBUG=False -e DATABASE_URL=$URL/vqv_pta -e TEST_DATABASE_URL=$URL/vqv_pt -e PDF_STORAGE_DIR=/tmp/p -e UPLOAD_DIR=/tmp/u -e HOME=/tmp --entrypoint python "$DEV_IMAGE" -m pytest -q -p no:cacheprovider --tb=short | tail -3 | tr -d '\r'
    check "single-showroom pytest" "${PIPESTATUS[0]}" 0
    docker run --rm --network $NET -v "$CERT_DIR":/certs:ro -e DEBUG=False -e DATABASE_URL=$URL/vqv_single -e SAAS_TEST_CERT_DIR=/certs -e HOME=/tmp --entrypoint python "$DEV_IMAGE" -m pytest -c pytest_saas.ini -q -p no:cacheprovider --tb=short | tail -3 | tr -d '\r'
    check "multi-tenant pytest" "${PIPESTATUS[0]}" 0
}

mt_env() {
    echo "-e APP_ENV=staging -e DEBUG=False -e JWT_SECRET_KEY=$JWT -e FRONTEND_URL=https://staging.invalid -e MULTI_TENANT_ENABLED=true \
      -e CONTROL_PLANE_DATABASE_URL=$URL/vqv_control -e TENANT_DB_ALLOW_PRIVATE_HOSTS=true -e SECRETS_LOCAL_DIR=/tmp/vq-secrets -e SECRETS_MASTER_KEY=$MT_KEY"
}
MT_KEY="$(python -c 'import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
vm() { # phase, extra env...
    local p=$1; shift
    docker run --rm -i --network $NET -v "$CERT_DIR":/certs:ro -e BASE=http://vqv-mt:8000 -e CA_FILE=/certs/ca.crt -e APP_PASSWORD=app-user-test-password-9x7 -e "STATE=${MT_STATE:-{\}}" -e ADMIN_BASE=http://vqv-padmin:8000 -e SINK_BASE=http://vqv-smtp:8025 -e ADMIN_EMAIL=platform.admin@example.com -e ADMIN_PASSWORD="$ADMIN_LOGIN_PW" "$@" --entrypoint python "$DEV_IMAGE" - "$p" < "$HERE/verify_multitenant.py" 2>&1 | tr -d '\r'
    return "${PIPESTATUS[0]}"
}

start_sink() {
    rmc vqv-smtp
    docker run -d --name vqv-smtp --pull=never --network $NET -i --entrypoint python "$DEV_IMAGE" -c "$(cat "$HERE/smtp_sink.py")" >/dev/null
    sleep 1
}

ADMIN_ROLE_PW="$(python -c 'import secrets; print(secrets.token_hex(24))')"
ADMIN_SIGNING="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
ADMIN_LOGIN_PW="Verify-Admin-Passphrase-1"
start_admin_for() {  # $1 = control database, already migrated
    rmc vqv-padmin
    sql vqv_single "DROP ROLE IF EXISTS vqa_mt" >/dev/null
    sql vqv_single "CREATE ROLE vqa_mt LOGIN PASSWORD '$ADMIN_ROLE_PW' IN ROLE voicequote_platform_admin" >/dev/null
    docker run --rm --network $NET -e ADMIN_BOOTSTRAP_DATABASE_URL=$URL/$1 -e ADMIN_EMAIL=platform.admin@example.com -e ADMIN_FULL_NAME=Verify -e ADMIN_PASSWORD="$ADMIN_LOGIN_PW" --entrypoint python "$IMAGE" scripts/bootstrap_platform_admin.py >/dev/null 2>&1
    docker run -d --name vqv-padmin --network $NET -e ADMIN_ENV=staging -e ADMIN_DATABASE_URL="postgresql+asyncpg://vqa_mt:$ADMIN_ROLE_PW@postgres:5432/$1" -e ADMIN_JWT_SECRET_KEY=$ADMIN_SIGNING -e ADMIN_MFA_ENCRYPTION_KEY=$MT_KEY \
        -e ADMIN_ALLOWED_ORIGINS=https://admin.staging.invalid -e ADMIN_SMTP_HOST=vqv-smtp -e ADMIN_SMTP_PORT=2525 -e ADMIN_SMTP_STARTTLS=false -e ADMIN_SMTP_FROM=invitations@staging.invalid -e ADMIN_OWNER_INVITE_URL=https://app.staging.invalid/showroom/invite \
        --entrypoint uvicorn "$IMAGE" app.platform_admin.main:app --host 0.0.0.0 --port 8000 >/dev/null
    wait_healthy vqv-padmin; check "admin process healthy" "$?" 0
}

mt_prepare_servers() {
    for s in saas-tenant-a saas-tenant-b; do
        local name=showroom_${s##*-}
        docker exec -i vqv-$s-1 psql -U saas_admin -d showroom_test -q -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='vq_app') THEN CREATE ROLE vq_app LOGIN PASSWORD 'app-user-test-password-9x7' NOSUPERUSER NOCREATEDB NOCREATEROLE; END IF; END \$\$;" >/dev/null
        docker exec -i vqv-$s-1 psql -U saas_admin -d showroom_test -q -c "DROP DATABASE IF EXISTS $name WITH (FORCE)" -c "CREATE DATABASE $name OWNER vq_app" >/dev/null
    done
}

phase_multitenant() {
    echo "=== Multi-tenant"
    rmc vqv-mt
    newdb vqv_control
    docker run --rm --network $NET -e APP_ENV=production -e JWT_SECRET_KEY=$JWT -e MULTI_TENANT_ENABLED=true -e CONTROL_PLANE_DATABASE_URL=$URL/vqv_control "$IMAGE" >/dev/null 2>&1
    check "production refuses local secrets" "$([ $? != 0 ] && echo yes)" yes
    docker run --rm --network $NET $(mt_env) "$IMAGE" >/dev/null 2>&1
    check "refuses to start before control-plane migration" "$([ $? != 0 ] && echo yes)" yes
    check "startup created no control tables" "$(sql vqv_control "select count(*) from information_schema.tables where table_schema='public'")" 0
    out="$(docker run --rm --network $NET $(mt_env) --entrypoint alembic "$IMAGE" -c alembic_control.ini upgrade head 2>&1)"
    check "explicit control-plane migration" "$?" 0
    check "control-plane migration logs its progress" "$(echo "$out" | grep -c 'Running upgrade')" 4
    check "  initial revision logged" "$(echo "$out" | grep -c 'Running upgrade  -> c0a1_initial')" 1
    check "  Platform Admin revision logged" "$(echo "$out" | grep -c 'Running upgrade c0a1_initial -> c1a1_platform_admin')" 1
    check "  onboarding revision logged" "$(echo "$out" | grep -c 'Running upgrade c1a1_platform_admin -> c2a1_showroom_onboarding')" 1
    check "  sessions revision logged" "$(echo "$out" | grep -c 'Running upgrade c2a1_showroom_onboarding -> c3a1_showroom_sessions')" 1
    check "no business tables in the central database" "$(sql vqv_control "select count(*) from information_schema.tables where table_schema='public' and table_name in ('customers','quotations','quotation_items','leads','vehicle_variants','inventory_units')")" 0
    docker run -d --name vqv-mt --network $NET -v "$CERT_DIR":/certs:ro $(mt_env) "$IMAGE" >/dev/null
    wait_healthy vqv-mt; check "healthy" "$?" 0
    check "startup created no platform accounts" "$(sql vqv_control 'select count(*) from users')" 0
    check "startup created no showrooms" "$(sql vqv_control 'select count(*) from tenants')" 0
    for a in $DEV_ACCOUNTS; do
        check "default account refused on platform login: ${a%%:*}" "$(docker run --rm --network $NET --entrypoint python "$IMAGE" -c "
import httpx
print(httpx.post('http://vqv-mt:8000/api/v1/platform/auth/login',json={'email':'${a%%:*}','password':'${a#*:}'},timeout=20).status_code)" 2>&1 | tail -1 | tr -d '\r')" 401
    done
    mt_prepare_servers
    start_sink
    start_admin_for vqv_control
    vm setup >/tmp/vq_mt_setup.txt 2>&1
    check "black-box setup ran to the end" "$?" 0
    MT_STATE="$(grep -E '^\{' /tmp/vq_mt_setup.txt | tail -1)"
    grep -E '^FAIL|checks passed' /tmp/vq_mt_setup.txt
    check "black-box setup" "$(grep -c '^FAIL' /tmp/vq_mt_setup.txt)" 0
    echo "$MT_STATE" > /tmp/vq_mt_state.json
    vm access > /tmp/vq_mt_access.txt 2>&1
    check "black-box access rules ran to the end" "$?" 0
    grep -E '^FAIL|checks passed' /tmp/vq_mt_access.txt
    check "black-box access rules" "$(grep -c '^FAIL' /tmp/vq_mt_access.txt)" 0
    check "suspension and reactivation audited" "$(sql vqv_control "select count(*) from audit_events where action in ('SHOWROOM_SUSPENDED','SHOWROOM_REACTIVATED') and reason is not null")" 2
    check "onboarding audited" "$(sql vqv_control "select count(*) from audit_events where action='SHOWROOM_CREATED' and actor_type='PLATFORM_ADMIN'")" 2
    check "deactivation audited" "$(sql vqv_control "select count(*) from audit_events where action='MEMBER_DEACTIVATED'")" 1
    check "no invitation token or password in audit events" "$(docker exec -i $PG pg_dump -U postgres --data-only -t audit_events vqv_control | grep -cE 'VerifyOwner123|VerifyStaff123|token=')" 0
    rmc vqv-padmin
    for s in a b; do
        check "showroom $s: one TLS app connection only" "$(docker exec -i vqv-saas-tenant-$s-1 psql -U saas_admin -d showroom_test -Atq -c "select count(*) from pg_stat_ssl s join pg_stat_activity a using (pid) where a.usename='vq_app' and not s.ssl" | tr -d '\r')" 0
    done
    check "central database holds no customer strings" "$(docker exec -i $PG pg_dump -U postgres vqv_control | grep -cE 'Only In A|Only In B|app-user-test-password')" 0
    check "app logs hold no customer strings or passwords" "$(docker logs vqv-mt 2>&1 | grep -cE 'Only In A|Only In B|app-user-test-password')" 0
}

phase_outage() {
    echo "=== Real outage tests"
    local since; since="$(date -u +%FT%TZ)"
    MT_STATE="$(cat /tmp/vq_mt_state.json)"
    run() { local out; out="$(vm "$@")"; local rc=$?; echo "$out" | grep -E '^(INFO|FAIL)'; check "$LABEL" "$rc" 0; }

    echo "--- showroom B disconnected: name no longer resolves, data kept"
    docker network disconnect $NET vqv-saas-tenant-b-1
    LABEL="unresolvable: every request 503 within 8s" run probe -e WHICH=b -e EXPECT=503 -e LIMIT=8
    LABEL="unresolvable: health is recorded as unhealthy" run health -e WHICH=b -e HEALTHY=false -e CODE=TENANT_DB_HOST_UNRESOLVED
    check "unresolvable: error stored in central database" "$(sql vqv_control "select count(*) from tenant_connections where last_error_code='TENANT_DB_HOST_UNRESOLVED'")" 1
    LABEL="unresolvable: other showroom fast and unaffected" run probe -e WHICH=a -e EXPECT=200 -e LIMIT=2
    docker network connect --alias saas-tenant-b $NET vqv-saas-tenant-b-1
    sleep 2
    LABEL="recovery: requests served again" run probe -e WHICH=b -e EXPECT=200 -e LIMIT=8
    LABEL="recovery: health clears the error" run health -e WHICH=b -e HEALTHY=true
    check "recovery: stored error cleared" "$(sql vqv_control "select count(*) from tenant_connections where last_error_code is not null")" 0

    echo "--- showroom B paused: connections hang"
    docker pause vqv-saas-tenant-b-1 >/dev/null
    LABEL="hung: every request 503 within 8s" run probe -e WHICH=b -e EXPECT=503 -e LIMIT=8
    LABEL="hung: health bounded and unhealthy" run health -e WHICH=b -e HEALTHY=false
    LABEL="hung: other showroom fast and unaffected" run probe -e WHICH=a -e EXPECT=200 -e LIMIT=2
    docker unpause vqv-saas-tenant-b-1 >/dev/null
    sleep 2
    LABEL="recovery after hang" run probe -e WHICH=b -e EXPECT=200 -e LIMIT=8
    LABEL="recovery after hang: healthy" run health -e WHICH=b -e HEALTHY=true

    echo "--- showroom A stopped: server gone"
    docker stop vqv-saas-tenant-a-1 >/dev/null
    out="$(vm down)"; rc=$?; echo "$out" | grep -E '^(INFO|FAIL)|checks passed'; check "down phase" "$rc" 0
    docker start vqv-saas-tenant-a-1 >/dev/null
    out="$(vm up)"; rc=$?; echo "$out" | grep -E '^FAIL|checks passed'; check "up phase" "$rc" 0
    check "outages caused no unhandled errors" "$(docker logs --since "$since" vqv-mt 2>&1 | grep -c 'Traceback')" 0
    local t0 t1; t0=$(date +%s); docker stop vqv-mt >/dev/null; t1=$(date +%s)
    check "graceful stop exit code" "$(docker inspect vqv-mt --format '{{.State.ExitCode}}')" 0
    echo "INFO  stopped in $((t1 - t0))s"
    rmc vqv-mt
}

showroom_version() { docker exec -i "vqv-saas-tenant-$1-1" psql -U saas_admin -d "showroom_$1" -Atq -c 'select version_num from alembic_version' 2>&1 | tr -d '\r'; }
showroom_url() { echo "postgresql+asyncpg://vq_app:app-user-test-password-9x7@saas-tenant-$1:5432/showroom_$1"; }

phase_showroommigration() {
    echo "=== Showroom database migrations, one at a time (run straight after multitenant)"
    MT_STATE="$(cat /tmp/vq_mt_state.json)"
    local head=b2d5f8a1c604 old=a1c4e7d2f903 ctl
    ctl="$(sql vqv_control 'select version_num from control_plane_alembic_version')"
    check "control plane and showroom versions are tracked apart: no showroom table centrally" "$(sql vqv_control "select count(*) from information_schema.tables where table_name='alembic_version'")" 0
    check "no control version table in a showroom" "$(docker exec -i vqv-saas-tenant-a-1 psql -U saas_admin -d showroom_a -Atq -c "select count(*) from information_schema.tables where table_name='control_plane_alembic_version'" | tr -d '\r')" 0
    check "new showroom A migrated on connect" "$(showroom_version a)" $head
    check "new showroom B migrated on connect" "$(showroom_version b)" $head
    # Connection failures in this phase would leave the showroom "older" for the wrong reason.
    docker run --rm --network $NET -e APP_ENV=staging -e DATABASE_URL="$(showroom_url a)" --entrypoint alembic "$IMAGE" downgrade $old >/dev/null 2>&1
    check "showroom A put back on the older schema" "$(showroom_version a)" $old
    check "showroom B not affected" "$(showroom_version b)" $head
    docker restart vqv-mt >/dev/null; wait_healthy vqv-mt
    check "app restart does not migrate a showroom" "$(showroom_version a)" $old
    since="$(date -u +%FT%TZ)"
    out="$(vm probe -e WHICH=a -e EXPECT=503 -e LIMIT=8 -e CODE=TENANT_DB_SCHEMA_OUTDATED)"; rc=$?; echo "$out" | grep -E '^(INFO|FAIL)'; check "older showroom: staff get a clear 503, not a 500" "$rc" 0
    check "older showroom: no unhandled errors logged" "$(docker logs --since "$since" vqv-mt 2>&1 | grep -cE 'Traceback|Unhandled|UndefinedColumn')" 0
    LABEL="older showroom: health reports it"
    out="$(vm health -e WHICH=a -e HEALTHY=false -e CODE=TENANT_DB_SCHEMA_OUTDATED)"; rc=$?; echo "$out" | grep -E '^(INFO|FAIL)'; check "$LABEL" "$rc" 0
    out="$(vm probe -e WHICH=b -e EXPECT=200 -e LIMIT=2)"; rc=$?; echo "$out" | grep -E '^FAIL'; check "other showroom keeps working" "$rc" 0
    out="$(vm migrate -e WHICH=a)"; rc=$?; echo "$out" | grep -E '^(INFO|FAIL)'; check "Owner migrates the existing showroom" "$rc" 0
    check "showroom A back at head" "$(showroom_version a)" $head
    check "showroom A data kept" "$(docker exec -i vqv-saas-tenant-a-1 psql -U saas_admin -d showroom_a -Atq -c "select count(*) from customers where full_name='Only In A'" | tr -d '\r')" 1
    out="$(vm health -e WHICH=a -e HEALTHY=true)"; rc=$?; check "showroom A healthy after migration" "$rc" 0
    out="$(vm probe -e WHICH=a -e EXPECT=200 -e LIMIT=2)"; rc=$?; check "showroom A serves requests after migration" "$rc" 0
    check "migration recorded in the central log" "$(sql vqv_control "select count(*) from tenant_migration_runs where from_version='$old' and to_version='$head' and status='SUCCEEDED'")" 1
    check "control-plane version unchanged by showroom migration" "$(sql vqv_control 'select version_num from control_plane_alembic_version')" "$ctl"
    check "showroom B version still unchanged" "$(showroom_version b)" $head

    echo "--- the server-side command"
    docker run --rm --network $NET -e APP_ENV=staging -e DATABASE_URL="$(showroom_url b)" --entrypoint alembic "$IMAGE" downgrade $old >/dev/null 2>&1
    vm health -e WHICH=b -e HEALTHY=false -e CODE=TENANT_DB_SCHEMA_OUTDATED >/dev/null 2>&1
    out="$(docker exec vqv-mt python scripts/migrate_showrooms.py --plan 2>&1 | tr -d '\r')"
    check "plan names the showroom that needs migrating" "$(echo "$out" | grep -c 'REQUIRED')" 1
    check "plan changes nothing" "$(showroom_version b)" $old
    docker exec vqv-mt python scripts/migrate_showrooms.py --all >/dev/null 2>&1
    check "without --execute nothing runs" "$(showroom_version b)" $old
    out="$(docker exec vqv-mt python scripts/migrate_showrooms.py --all --execute 2>&1 | tr -d '\r')"; rc=$?
    check "command migrates it" "$rc" 0
    check "showroom B back at head" "$(showroom_version b)" $head
    check "showroom B data kept" "$(docker exec -i vqv-saas-tenant-b-1 psql -U saas_admin -d showroom_b -Atq -c "select count(*) from customers where full_name='Only In B'" | tr -d '\r')" 1
    out="$(vm probe -e WHICH=b -e EXPECT=200 -e LIMIT=2)"; rc=$?; check "showroom B serves requests after the command" "$rc" 0
    check "command is audited" "$(sql vqv_control "select count(*) from audit_events where action='SHOWROOM_MIGRATION_SUCCEEDED' and actor_type='SYSTEM'")" 1
}

phase_hashes() {
    echo "=== Dependency hashes and base image digest"
    local rev src="$WORK/src" digest
    rev="$(docker inspect "$IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
    echo "INFO  source revision $rev"
    mkdir -p "$src"
    git -C "$(cygpath -m "$HERE/../.." 2>/dev/null || echo "$HERE/../..")" archive "$rev" . | tar -x -C "$src"
    if [ ! -s "$src/Dockerfile" ] || [ ! -s "$src/requirements.txt" ]; then
        bad "source exported for hash checks"
        return
    fi
    ok "source exported for hash checks"
    digest="$(grep -m1 -oE 'python:3.13-slim@sha256:[0-9a-f]{64}' "$src/Dockerfile")"
    check "all FROM lines use one digest" "$(grep -E '^FROM python' "$src/Dockerfile" | grep -c "$digest")" 2
    check "no unpinned FROM" "$(grep -E '^FROM ' "$src/Dockerfile" | grep -vE '@sha256:| runtime ' | wc -l | tr -d ' ')" 0

    echo "--- installed packages against the lock"
    docker run --rm --network none --entrypoint pip "$IMAGE" freeze 2>/dev/null | tr -d '\r' | grep -E '==' | tr 'A-Z_' 'a-z-' | sort > "$WORK/installed.txt"
    tr -d '\r' < "$src/requirements.txt" | grep -E '^[A-Za-z0-9._-]+==' | sed 's/ .*//' | tr 'A-Z_' 'a-z-' | sort > "$WORK/locked.txt"
    check "image holds exactly the locked packages ($(wc -l < "$WORK/locked.txt" | tr -d ' '))" "$(diff "$WORK/installed.txt" "$WORK/locked.txt" | grep -c '^[<>]')" 0

    echo "--- pip refuses anything that does not match"
    local good; good="$(tr -d '\r' < "$src/requirements.txt" | awk '/^idna==/{f=1;print;next} f&&/^ /{print;next} f{exit}')"
    printf '%s\n' "$good" > "$WORK/good.txt"
    printf 'idna==%s --hash=sha256:%064d\n' "$(grep -m1 '^idna==' "$WORK/locked.txt" | cut -d= -f3)" 0 > "$WORK/badhash.txt"
    printf 'idna==%s\n' "$(grep -m1 '^idna==' "$WORK/locked.txt" | cut -d= -f3)" > "$WORK/nohash.txt"
    for f in good badhash nohash; do
        docker run --rm -i --entrypoint sh "$IMAGE" -c "cat > /tmp/r.txt; pip install -q --no-cache-dir --disable-pip-version-check --require-hashes --no-deps --target /tmp/x -r /tmp/r.txt" < "$WORK/$f.txt" > "$WORK/$f.out" 2>&1
        eval "${f}_rc=$?"
    done
    check "correct hash installs (shows the network is fine)" "$good_rc" 0
    check "wrong hash refused" "$([ "$badhash_rc" != 0 ] && grep -q 'DO NOT MATCH THE HASHES' "$WORK/badhash.out" && echo yes)" yes
    check "missing hash refused" "$([ "$nohash_rc" != 0 ] && grep -qi 'hash' "$WORK/nohash.out" && echo yes)" yes

    echo "--- docker build refuses a tampered lock"
    cp -r "$src" "$WORK/tampered"
    python - "$DOCKER_WORK/tampered/requirements.txt" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p, newline="").read()
block = re.search(r"^idna==.*?(?=^[a-z0-9])", s, re.M | re.S).group(0)
s = s.replace(block, re.sub(r"sha256:[0-9a-f]{64}", "sha256:" + "0" * 64, block))
open(p, "w", newline="").write(s)
PY
    check "lock really was tampered with" "$(grep -c "sha256:$(printf '%064d' 0)" "$WORK/tampered/requirements.txt" | grep -qv '^0$' && echo yes)" yes
    docker build --progress=plain --target builder -t vqv-should-not-exist "$DOCKER_WORK/tampered" > "$WORK/tampered.out" 2>&1
    check "build fails on a tampered hash" "$([ $? != 0 ] && grep -q 'DO NOT MATCH THE HASHES' "$WORK/tampered.out" && echo yes)" yes
    check "no image produced" "$(docker images -q vqv-should-not-exist | wc -l | tr -d ' ')" 0

    echo "--- docker build refuses a wrong base digest"
    cp -r "$src" "$WORK/badbase"
    sed -i -E "s/@sha256:[0-9a-f]{64}/@sha256:$(printf '%064d' 1)/" "$WORK/badbase/Dockerfile"
    docker build --progress=plain --target builder -t vqv-should-not-exist "$DOCKER_WORK/badbase" > "$WORK/badbase.out" 2>&1
    check "build fails on a wrong base digest" "$([ $? != 0 ] && grep -qE 'sha256:0{63}1' "$WORK/badbase.out" && echo yes)" yes
    check "no image produced" "$(docker images -q vqv-should-not-exist | wc -l | tr -d ' ')" 0

    echo "--- image really is built on the pinned base"
    docker pull -q "$digest" >/dev/null 2>&1
    local base_layers image_layers
    base_layers="$(docker inspect "$digest" --format '{{json .RootFS.Layers}}' | tr -d '[]"')"
    image_layers="$(docker inspect "$IMAGE" --format '{{json .RootFS.Layers}}' | tr -d '[]"')"
    case "$image_layers" in "$base_layers"*) ok "image layers start with the pinned base layers";; *) bad "image layers start with the pinned base layers";; esac

    echo "--- rebuild from nothing"
    docker build -q --no-cache --label org.opencontainers.image.revision="$rev" -t "$IMAGE-repro" "$DOCKER_WORK/src" > "$WORK/repro.out" 2>&1
    check "no-cache rebuild succeeds" "$?" 0
    tree='cd / && find opt/venv app -type f -not -path "*/__pycache__/*" -print0 | sort -z | xargs -0 sha256sum | sha256sum'
    local one two
    one="$(docker run --rm --network none --entrypoint sh "$IMAGE" -c "$tree" | tr -d '\r')"
    two="$(docker run --rm --network none --entrypoint sh "$IMAGE-repro" -c "$tree" | tr -d '\r')"
    echo "INFO  app and dependency tree: $one"
    check "application and dependency files identical after rebuild" "$two" "$one"
    local deb1 deb2
    deb1="$(docker run --rm --network none --entrypoint dpkg-query "$IMAGE" -W | tr -d '\r' | sha256sum)"
    deb2="$(docker run --rm --network none --entrypoint dpkg-query "$IMAGE-repro" -W | tr -d '\r' | sha256sum)"
    if [ "$deb1" = "$deb2" ]; then echo "INFO  OS packages match today, but apt versions are not pinned"; else echo "INFO  OS packages differ between builds"; fi
    echo "INFO  image ids: $(docker images -q "$IMAGE") and $(docker images -q "$IMAGE-repro")"
}

sum_all() { # table checksums, order-independent
    sql "$1" "select md5(string_agg(x, ',' order by x)) from (
      select 'users'||md5(coalesce(string_agg(t::text,'|' order by t.id),'')) x from (select id,email,password_hash,is_active from users) t
      union all select 'customers'||md5(coalesce(string_agg(t::text,'|' order by t.id),'')) from customers t
      union all select 'quotations'||md5(coalesce(string_agg(t::text,'|' order by t.id),'')) from (select id,quotation_number,customer_id,final_price,payment_mode,status from quotations) t
      union all select 'leads'||md5(coalesce(string_agg(t::text,'|' order by t.id),'')) from (select id,customer_id from leads) t
    ) y"
}

phase_migration() {
    echo "=== Migrations against a copy of the older schema"
    local c=vqv-mig
    rmc $c
    newdb vqm_old
    docker run --rm --network $NET -e APP_ENV=development -e DATABASE_URL=$URL/vqm_old --entrypoint sh "$ROLLBACK_IMAGE" -c "alembic upgrade head && python scripts/seed.py" >/dev/null 2>&1
    check "older database prepared at a1c4" "$(sql vqm_old 'select version_num from alembic_version')" a1c4e7d2f903
    local before; before="$(sum_all vqm_old)"
    docker run --rm --network $NET $(envs vqm_old) "$IMAGE" >/dev/null 2>&1
    check "new image refuses the older schema" "$([ $? != 0 ] && echo yes)" yes
    check "starting the new image changed nothing" "$(sql vqm_old 'select version_num from alembic_version')" a1c4e7d2f903
    check "starting the new image kept the data" "$(sum_all vqm_old)" "$before"

    sql vqm_old 'ALTER TABLE quotation_items ADD COLUMN total numeric' >/dev/null
    docker run --rm --network $NET $(envs vqm_old) --entrypoint alembic "$IMAGE" upgrade head >/dev/null 2>&1
    check "conflicting upgrade fails" "$([ $? != 0 ] && echo yes)" yes
    check "failed upgrade stays at the old version" "$(sql vqm_old 'select version_num from alembic_version')" a1c4e7d2f903
    check "failed upgrade leaves no partial column" "$(sql vqm_old "select count(*) from information_schema.columns where table_name='users' and column_name='central_user_id'")" 0
    sql vqm_old 'ALTER TABLE quotation_items DROP COLUMN total' >/dev/null

    docker run --rm --network $NET $(envs vqm_old) --entrypoint alembic "$IMAGE" upgrade head >/dev/null 2>&1
    check "explicit upgrade succeeds" "$?" 0
    check "upgraded to head" "$(sql vqm_old 'select version_num from alembic_version')" b2d5f8a1c604
    check "upgrade kept the data" "$(sum_all vqm_old)" "$before"
    docker run -d --name $c --network $NET $(envs vqm_old) "$IMAGE" >/dev/null
    wait_healthy $c; check "new image starts after explicit upgrade" "$?" 0
    for a in $DEV_ACCOUNTS; do
        check "existing default account refused: ${a%%:*}" "$(login_status $c "${a%%:*}" "${a#*:}")" 401
    done
    docker run --rm --network $NET $(envs vqm_old) -e ADMIN_EMAIL=$ADMIN_EMAIL -e ADMIN_FULL_NAME=Verify -e ADMIN_PASSWORD="$ADMIN_PASSWORD" --entrypoint python "$IMAGE" scripts/create_admin.py >/dev/null 2>&1
    check "operator can take over with a real administrator" "$(login_status $c $ADMIN_EMAIL "$ADMIN_PASSWORD")" 200
    sql vqm_old "delete from users where email='$ADMIN_EMAIL'" >/dev/null
    check "data still identical" "$(sum_all vqm_old)" "$before"
    rmc $c

    docker run --rm --network $NET $(envs vqm_old) --entrypoint alembic "$IMAGE" downgrade a1c4e7d2f903 >/dev/null 2>&1
    check "downgrade succeeds" "$?" 0
    check "downgrade kept the data" "$(sum_all vqm_old)" "$before"
    docker run -d --name $c --network $NET -e APP_ENV=production -e DEBUG=False -e JWT_SECRET_KEY=$JWT -e DATABASE_URL=$URL/vqm_old "$ROLLBACK_IMAGE" >/dev/null
    wait_healthy $c; check "rollback image starts after downgrade" "$?" 0
    check "rollback image: login works on its schema" "$(login_status $c admin@dealership.com AdminPass123!)" 200
    rmc $c
    docker run --rm --network $NET $(envs vqm_old) "$IMAGE" >/dev/null 2>&1
    check "new image still refuses the downgraded schema" "$([ $? != 0 ] && echo yes)" yes
}

phase_backup() {
    echo "=== Backup and restore"
    local c=vqv-bak dump="$WORK/backup.dump" ddump="$DOCKER_WORK/backup.dump"
    rmc $c
    newdb vqb_src
    docker run --rm --network $NET -e APP_ENV=test -e SEED_DEV_DATA=true -e DATABASE_URL=$URL/vqb_src --entrypoint sh "$DEV_IMAGE" -c "alembic upgrade head && python scripts/seed.py" >/dev/null 2>&1
    local before; before="$(sum_all vqb_src)"
    local tables; tables="$(sql vqb_src "select count(*) from information_schema.tables where table_schema='public'")"
    docker exec "$PG" sh -c 'pg_dump -U postgres -Fc vqb_src -f /tmp/backup.dump'
    check "pg_dump succeeds" "$?" 0
    docker cp "$PG:/tmp/backup.dump" "$ddump" >/dev/null 2>&1
    check "backup copied off the database container" "$([ -s "$dump" ] && echo yes)" yes
    echo "INFO  backup size: $(wc -c < "$dump") bytes"
    check "backup is readable" "$([ "$(docker exec "$PG" pg_restore --list /tmp/backup.dump | grep -c 'TABLE DATA')" -gt 0 ] && echo yes)" yes

    echo "--- loss of the database, restored from the off-container copy to the same server"
    sql vqv_single 'DROP DATABASE vqb_src WITH (FORCE)' >/dev/null
    check "database is gone" "$(sql vqv_single "select count(*) from pg_database where datname='vqb_src'")" 0
    newdb vqb_restored
    docker cp "$ddump" "$PG:/tmp/restore.dump" >/dev/null 2>&1
    docker exec "$PG" pg_restore -U postgres -d vqb_restored --no-owner --no-privileges --exit-on-error /tmp/restore.dump
    check "pg_restore succeeds" "$?" 0
    check "all tables restored" "$(sql vqb_restored "select count(*) from information_schema.tables where table_schema='public'")" "$tables"
    check "schema version restored" "$(sql vqb_restored 'select version_num from alembic_version')" b2d5f8a1c604
    check "restored data is identical" "$(sum_all vqb_restored)" "$before"

    echo "--- restored to a different server"
    local other=vqv-saas-tenant-b-1
    docker exec -i $other psql -U saas_admin -d showroom_test -q -c 'DROP DATABASE IF EXISTS vqb_other WITH (FORCE)' -c 'CREATE DATABASE vqb_other' >/dev/null
    docker cp "$ddump" "$other:/tmp/restore.dump" >/dev/null 2>&1
    docker exec "$other" pg_restore -U saas_admin -d vqb_other --no-owner --no-privileges --exit-on-error /tmp/restore.dump
    check "restore on another server" "$?" 0
    check "data identical on another server" "$(docker exec -i $other psql -U saas_admin -d vqb_other -Atq -c "select count(*) from customers" | tr -d '\r')" "$(sql vqb_restored 'select count(*) from customers')"

    echo "--- application on the restored database"
    docker run --rm --network $NET $(envs vqb_restored) -e ADMIN_EMAIL=$ADMIN_EMAIL -e ADMIN_FULL_NAME=Verify -e ADMIN_PASSWORD="$ADMIN_PASSWORD" --entrypoint python "$IMAGE" scripts/create_admin.py >/dev/null 2>&1
    docker run -d --name $c --network $NET $(envs vqb_restored) "$IMAGE" >/dev/null
    wait_healthy $c; check "application starts on the restored database" "$?" 0
    check "application signs in on the restored database" "$(login_status $c $ADMIN_EMAIL "$ADMIN_PASSWORD")" 200
    rmc $c
    docker exec "$PG" rm -f /tmp/backup.dump /tmp/restore.dump
    docker exec $other rm -f /tmp/restore.dump
}

phase_platformadmin() {
    echo "=== Platform Admin as its own process, on its least-privilege role"
    local adm=vqv-admin main=vqv-admin-main role=vqa_login rolepw adminjwt mfakey showjwt
    start_sink
    local control=vqa_control owner_url admin_url pw=Verify-Admin-Passphrase-1 email=platform.admin@example.com
    rolepw="$(python -c 'import secrets; print(secrets.token_hex(24))')"
    adminjwt="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
    showjwt="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
    mfakey="$MT_KEY"
    owner_url="$URL/$control"
    admin_url="postgresql+asyncpg://$role:$rolepw@postgres:5432/$control"
    rmc $adm $main
    newdb $control
    sql vqv_single "DROP ROLE IF EXISTS $role" >/dev/null
    main_env="-e APP_ENV=staging -e DEBUG=False -e JWT_SECRET_KEY=$showjwt -e FRONTEND_URL=https://staging.invalid -e MULTI_TENANT_ENABLED=true -e CONTROL_PLANE_DATABASE_URL=$owner_url -e TENANT_DB_ALLOW_PRIVATE_HOSTS=true -e SECRETS_LOCAL_DIR=/tmp/vq-secrets -e SECRETS_MASTER_KEY=$MT_KEY"
    admin_env="-e ADMIN_ENV=staging -e ADMIN_DATABASE_URL=$admin_url -e ADMIN_JWT_SECRET_KEY=$adminjwt -e ADMIN_MFA_ENCRYPTION_KEY=$mfakey -e ADMIN_ALLOWED_ORIGINS=https://admin.staging.invalid -e ADMIN_SMTP_HOST=vqv-smtp -e ADMIN_SMTP_PORT=2525 -e ADMIN_SMTP_STARTTLS=false -e ADMIN_SMTP_FROM=invitations@staging.invalid -e ADMIN_OWNER_INVITE_URL=https://app.staging.invalid/showroom/invite"
    uvcmd="--entrypoint uvicorn"
    uvargs="app.platform_admin.main:app --host 0.0.0.0 --port 8000"

    out="$(docker run --rm --network $NET $main_env --entrypoint alembic "$IMAGE" -c alembic_control.ini upgrade head 2>&1)"
    check "explicit control-plane migration" "$?" 0
    check "all control-plane revisions applied" "$(echo "$out" | grep -c 'Running upgrade')" 3
    check "schema at the latest revision" "$(sql $control 'select version_num from control_plane_alembic_version')" c3a1_showroom_sessions
    check "migration created no admin account" "$(sql $control 'select count(*) from platform_admins')" 0
    check "group role can't log in" "$(sql $control "select rolcanlogin from pg_roles where rolname='voicequote_platform_admin'")" f
    sql vqv_single "CREATE ROLE $role LOGIN PASSWORD '$rolepw' IN ROLE voicequote_platform_admin" >/dev/null

    echo "--- the process refuses unsafe configuration"
    docker run --rm --network $NET -e ADMIN_ENV=staging $uvcmd "$IMAGE" $uvargs >/dev/null 2>&1
    check "refuses to start with no admin secrets" "$([ $? != 0 ] && echo yes)" yes
    for bad in "JWT_SECRET_KEY=$showjwt" "SECRETS_MASTER_KEY=$MT_KEY" "CONTROL_PLANE_DATABASE_URL=$owner_url" "DATABASE_URL=$URL/vqv_single" "SECRETS_BACKEND=vqsentinel-backend-value"; do
        o="$(docker run --rm --network $NET $admin_env -e "$bad" $uvcmd "$IMAGE" $uvargs 2>&1)"; rc=$?
        check "refuses to start holding ${bad%%=*}" "$([ $rc != 0 ] && echo "$o" | grep -q 'must not hold' && echo yes)" yes
        check "  refusal doesn't print the value" "$(echo "$o" | grep -cF "${bad#*=}")" 0
    done
    o="$(docker run --rm --network $NET -e ADMIN_ENV=staging -e ADMIN_DATABASE_URL=$owner_url -e ADMIN_JWT_SECRET_KEY=$adminjwt -e ADMIN_MFA_ENCRYPTION_KEY=$mfakey $uvcmd "$IMAGE" $uvargs 2>&1)"; rc=$?
    check "refuses to run as a powerful database role" "$([ $rc != 0 ] && echo "$o" | grep -q 'privileges it must not have' && echo yes)" yes
    docker run --rm --network $NET $admin_env -e ADMIN_COOKIE_SECURE=false $uvcmd "$IMAGE" $uvargs >/dev/null 2>&1
    check "refuses insecure cookies outside development" "$([ $? != 0 ] && echo yes)" yes

    echo "--- bootstrap"
    boot() { docker run --rm --network $NET -e ADMIN_BOOTSTRAP_DATABASE_URL=$owner_url -e ADMIN_EMAIL="$1" -e ADMIN_FULL_NAME=Verify -e ADMIN_PASSWORD="$2" --entrypoint python "$IMAGE" scripts/bootstrap_platform_admin.py >/dev/null 2>&1; }
    boot "$email" AdminPass123!;     check "bootstrap refuses a published password" "$?" 1
    boot "$email" short;             check "bootstrap refuses a short password" "$?" 1
    boot "$email" "$pw";             check "bootstrap creates the admin" "$?" 0
    boot second@example.com "$pw";   check "second bootstrap refused" "$?" 1
    check "exactly one admin exists" "$(sql $control 'select count(*) from platform_admins')" 1
    check "bootstrap is audited" "$(sql $control "select count(*) from audit_events where action='PLATFORM_ADMIN_BOOTSTRAPPED'")" 1
    check "password stored hashed" "$(sql $control "select count(*) from platform_admins where password_hash like '\$2b\$%'")" 1

    docker run -d --name $adm --network $NET $admin_env $uvcmd "$IMAGE" $uvargs >/dev/null
    wait_healthy $adm; check "admin process healthy" "$?" 0
    docker run -d --name $main --network $NET $main_env --entrypoint uvicorn "$IMAGE" app.main:app --host 0.0.0.0 --port 8000 >/dev/null
    wait_healthy $main; check "showroom process healthy" "$?" 0

    echo "--- isolation of the running process"
    envs_seen="$(docker inspect $adm --format '{{range .Config.Env}}{{println .}}{{end}}' | sed 's/=.*//' | tr -d '\r')"
    for name in JWT_SECRET_KEY DATABASE_URL CONTROL_PLANE_DATABASE_URL SECRETS_MASTER_KEY SMTP_PASSWORD; do
        check "process environment lacks $name" "$(echo "$envs_seen" | grep -cx "$name")" 0
    done
    loaded="$(docker exec $adm python -c "
import sys, app.platform_admin.main as m
bad=[n for n in sys.modules if n.startswith('app.') and n not in ('app.control_plane','app.control_plane.models','app.core','app.core.exceptions','app.schemas','app.schemas.common') and not n.startswith('app.platform_admin')]
print(len(bad))" 2>&1 | tail -1 | tr -d '\r')"
    check "process loads no showroom, tenant or secrets code" "$loaded" 0
    conn_user="$(sql $control "select string_agg(distinct usename, ',') from pg_stat_activity where datname='$control' and application_name not like 'psql%' and usename='$role'")"
    check "process connects as the least-privilege role" "$conn_user" "$role"

    echo "--- the role, connecting exactly as the process does"
    role_sql() { docker run --rm --network $NET -e PGPASSWORD="$rolepw" --entrypoint psql "$PG_IMG" -h postgres -U $role -d $control -Atq -c "$1" 2>&1 | tr -d '\r'; }
    PG_IMG="$(docker inspect $PG --format '{{.Image}}')"
    for stmt in "SELECT secret_reference FROM tenant_connections" "SELECT * FROM tenant_connections" "SELECT password_hash FROM users" "SELECT invitation_token_hash FROM tenant_memberships" \
                "INSERT INTO platform_admins (id,email,full_name,password_hash,status,created_at,updated_at) VALUES (gen_random_uuid(),'x@example.com','X','x','ACTIVE',now(),now())" \
                "DELETE FROM platform_admins" "UPDATE platform_admins SET email='x@example.com'" "UPDATE audit_events SET action='x'" "DELETE FROM audit_events" "TRUNCATE audit_events" "DELETE FROM tenants" "CREATE TABLE vqa_probe (id int)" \
                "UPDATE users SET password_hash='x'" "UPDATE tenant_memberships SET role='OWNER'" "UPDATE tenant_connections SET secret_reference='x'"; do
        check "role refused: ${stmt:0:45}" "$(role_sql "$stmt" | grep -c 'permission denied')" 1
    done
    check "role can read connection metadata" "$(role_sql 'SELECT count(*) FROM tenant_connections')" "$(sql $control 'select count(*) from tenant_connections')"
    check "no business tables in the central database" "$(role_sql 'SELECT count(*) FROM information_schema.tables WHERE table_name IN ($$customers$$,$$quotations$$,$$leads$$)')" 0
    check "owner can't alter audit history either" "$(sql $control "UPDATE audit_events SET action='x'" | grep -c 'append-only')" 1

    echo "--- black-box API checks"
    docker run --rm -i --network $NET -e ADMIN_BASE=http://$adm:8000 -e SHOWROOM_BASE=http://$main:8000 -e ORIGIN=https://admin.staging.invalid -e EMAIL=$email -e PASSWORD="$pw" -e SHOWROOM_JWT_KEY="$showjwt" -e SINK_BASE=http://vqv-smtp:8025 \
        --entrypoint python "$DEV_IMAGE" - < "$HERE/verify_platform_admin.py" 2>&1 | tr -d '\r' | tee /tmp/vq_padmin.txt | grep -vE '^PASS'
    check "black-box API checks" "${PIPESTATUS[0]}" 0

    echo "--- afterwards"
    check "audit trail recorded sign-ins" "$([ "$(sql $control "select count(*) from audit_events where action='ADMIN_LOGIN_SUCCEEDED'")" -ge 3 ] && echo yes)" yes
    check "audit trail recorded failures" "$([ "$(sql $control "select count(*) from audit_events where action='ADMIN_LOGIN_FAILED'")" -ge 2 ] && echo yes)" yes
    check "audit trail recorded refresh replay" "$(sql $control "select count(*) from audit_events where action='ADMIN_REFRESH_REUSE_DETECTED'")" 1
    check "audit trail holds no password" "$(docker exec -i $PG pg_dump -U postgres --data-only -t audit_events $control | grep -c "$pw")" 0
    check "database holds no password or TOTP secret in the clear" "$(docker exec -i $PG pg_dump -U postgres --data-only -t platform_admins $control | grep -c "$pw")" 0
    check "admin logs hold no password" "$(docker logs $adm 2>&1 | grep -c "$pw")" 0
    check "admin logs hold no tracebacks" "$(docker logs $adm 2>&1 | grep -c 'Traceback')" 0
    t0=$(date +%s); docker stop $adm >/dev/null; t1=$(date +%s)
    check "graceful stop" "$(docker inspect $adm --format '{{.State.ExitCode}}')" 0
    echo "INFO  stopped in $((t1 - t0))s"
    rmc $adm $main vqv-smtp

    echo "--- downgrade"
    docker run --rm --network $NET $main_env --entrypoint alembic "$IMAGE" -c alembic_control.ini downgrade c0a1_initial >/dev/null 2>&1
    check "migration reverses" "$?" 0
    check "Platform Admin tables gone" "$(sql $control "select count(*) from information_schema.tables where table_name in ('platform_admins','audit_events','platform_admin_sessions')")" 0
    docker run --rm --network $NET $main_env --entrypoint alembic "$IMAGE" -c alembic_control.ini upgrade head >/dev/null 2>&1
    check "migration reapplies" "$?" 0
    sql vqv_single "DROP DATABASE $control WITH (FORCE)" >/dev/null
    sql vqv_single "DROP ROLE IF EXISTS $role" >/dev/null
}

[ "$#" -gt 0 ] || { echo "usage: $0 contents|safety|single|pytest|multitenant|showroommigration|outage|migration|backup|hashes|platformadmin|all ..."; exit 2; }
[ "$1" = all ] && set -- contents safety single pytest multitenant showroommigration outage migration backup hashes platformadmin
for p in "$@"; do "phase_$p"; done
echo
echo "RESULT: $PASSES passed, $FAILS failed"
[ "$FAILS" = 0 ]
