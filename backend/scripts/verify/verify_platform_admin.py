"""Black-box checks of a running Platform Admin process, from outside, over HTTP.

Imports nothing from the application, and has its own TOTP, so it can't share a bug with the code it tests.
Usage: ADMIN_BASE=... SHOWROOM_BASE=... ORIGIN=... EMAIL=... PASSWORD=... python verify_platform_admin.py
"""
import base64
import hashlib
import hmac
import os
import re
import struct
import sys
import time

import httpx
import jwt

ADMIN = os.environ["ADMIN_BASE"]
SHOWROOM = os.environ["SHOWROOM_BASE"]
ORIGIN = os.environ["ORIGIN"]
EMAIL = os.environ["EMAIL"]
PASSWORD = os.environ["PASSWORD"]
A = f"{ADMIN}/api/v1/admin"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  -- ' + str(detail)[:200] if detail and not ok else ''}")


def totp(secret, step):
    digest = hmac.new(base64.b32decode(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return str((struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)


def H(token):
    return {"Authorization": f"Bearer {token}"}


def cookie_from(response):
    match = re.search(r"vq_admin_refresh=([^;]+)", response.headers.get("set-cookie", ""))
    return match[1] if match else None


c = httpx.Client(timeout=30)
step = int(time.time() // 30)

check("health", c.get(f"{ADMIN}/health").json() == {"status": "healthy"})
check("API documentation isn't served", c.get(f"{ADMIN}/docs").status_code == 404 and c.get(f"{ADMIN}/openapi.json").status_code == 404)
r = c.get(f"{A}/me")
check("no token: 401", r.status_code == 401)
check("responses aren't cached", r.headers.get("cache-control") == "no-store")
for path in ("/api/v1/admin/register", "/api/v1/admin/auth/register", "/api/v1/admin/admins", "/api/v1/admin/signup", "/api/v1/admin/users"):
    check(f"no registration route: {path}", c.post(f"{ADMIN}{path}", json={"email": "x@example.com", "password": "Long-Enough-Password-1", "full_name": "X"}).status_code == 404)
for path in ("/api/v1/customers", "/api/v1/quotations", "/api/v1/leads", "/api/v1/platform/me", "/api/v1/platform/showrooms/register"):
    check(f"showroom route absent from admin API: {path}", c.get(f"{ADMIN}{path}").status_code == 404)

r = c.post(f"{A}/auth/login", json={"email": EMAIL, "password": "wrong-password-value"})
check("wrong password: 401, generic", r.status_code == 401 and r.json()["error"]["code"] == "ADMIN_AUTH_FAILED")
r = c.post(f"{A}/auth/login", json={"email": "other@example.com", "password": PASSWORD})
check("unknown email: identical answer", r.status_code == 401 and r.json()["error"]["message"] == "Authentication failed.")

r = c.post(f"{A}/auth/login", json={"email": EMAIL, "password": PASSWORD})
check("password accepted", r.status_code == 200, r.text)
login = r.json()["data"]
check("first login requires MFA enrolment", login["mfa_enrollment_required"] is True)
check("password step sets no session cookie", "set-cookie" not in r.headers)
check("password step reaches no admin route", c.get(f"{A}/me", headers=H(login["mfa_token"])).status_code == 401)
claims = jwt.decode(login["mfa_token"], options={"verify_signature": False})
check("token audience", claims["aud"] == "voicequote-admin" and claims["iss"] == "voicequote-admin-api")

start = c.post(f"{A}/auth/mfa/enroll/start", json={"mfa_token": login["mfa_token"]}).json()["data"]
secret = start["secret"]
check("authenticator URI names the service", start["otpauth_uri"].startswith("otpauth://totp/"))
check("wrong enrolment code refused", c.post(f"{A}/auth/mfa/enroll/confirm", json={"mfa_token": login["mfa_token"], "code": "000000"}).status_code == 401)
r = c.post(f"{A}/auth/mfa/enroll/confirm", json={"mfa_token": login["mfa_token"], "code": totp(secret, step)})
check("MFA enrolment completes", r.status_code == 200, r.text)
data = r.json()["data"]
access, codes = data["access_token"], data["recovery_codes"]
cookie = r.headers.get("set-cookie", "").lower()
check("10 recovery codes shown once", len(codes) == 10)
for attribute in ("httponly", "samesite=strict", "path=/api/v1/admin/auth"):
    check(f"refresh cookie: {attribute}", attribute in cookie)
check("refresh token not in the response body", "refresh" not in r.text)
refresh1 = cookie_from(r)

me = c.get(f"{A}/me", headers=H(access))
check("admin route works with full sign-in", me.status_code == 200 and me.json()["data"]["email"] == EMAIL, me.text)
check("profile holds no secrets", not re.search("password|mfa_secret|hash", me.text))

# Boundary with the showroom API, using a real showroom account on a real showroom process.
owner_email = f"owner-{int(time.time())}@example.com"
created = c.post(f"{A}/showrooms", headers=H(access), json={"showroom_name": f"Boundary {int(time.time())}", "legal_business_name": "Boundary Pvt Ltd", "owner_email": owner_email, "owner_full_name": "Boundary Owner"})
check("admin creates a showroom", created.status_code == 201 and created.json()["data"]["invitation_delivery"] == "SENT", created.text)
mail = c.get(f"{os.environ['SINK_BASE']}/message", params={"to": owner_email}).text
invite_token = re.search(r"#token=([\w\-]+)", mail)[1]
check("invitation token isn't in the admin's response", invite_token not in created.text)
owner = c.post(f"{SHOWROOM}/api/v1/platform/invitations/accept", json={"token": invite_token, "full_name": "Boundary Owner", "password": "Boundary-Owner-Pass-1"})
check("Owner accepts the invitation", owner.status_code == 200, owner.text)
sdata = owner.json()["data"]
check("public showroom registration is gone", c.post(f"{SHOWROOM}/api/v1/platform/showrooms/register", json={"showroom_name": "Claimed", "email": "claimer@example.com", "password": "Claimer-Password-1", "full_name": "Claimer"}).status_code == 404)
check("showroom token refused on admin showroom routes", c.get(f"{A}/showrooms/{created.json()['data']['id']}", headers=H(sdata["access_token"])).status_code == 401)
check("showroom token names its audience", jwt.decode(sdata["access_token"], options={"verify_signature": False})["aud"] == "voicequote-showroom")
for label, token in (("access", sdata["access_token"]), ("refresh", sdata["refresh_token"])):
    check(f"showroom {label} token refused by admin API", c.get(f"{A}/me", headers=H(token)).status_code == 401)
for path in ("/api/v1/platform/me", "/api/v1/platform/memberships", "/api/v1/platform/tenants/current", "/api/v1/customers", "/api/v1/quotations", "/api/v1/leads", "/api/v1/dashboard/summary"):
    check(f"admin token refused by showroom API: {path}", c.get(f"{SHOWROOM}{path}", headers=H(access)).status_code == 401)
check("admin token can't create a showroom through the showroom API", c.post(f"{SHOWROOM}/api/v1/platform/showrooms", headers=H(access), json={"showroom_name": "Nope"}).status_code == 401)
check("showroom API serves no admin routes", c.post(f"{SHOWROOM}/api/v1/admin/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 404)

# Forged tokens, each using what a showroom process could sign: its key is passed in only here.
key = os.environ["SHOWROOM_JWT_KEY"]
body = jwt.decode(access, options={"verify_signature": False})
check("admin claims signed with the showroom key: refused", c.get(f"{A}/me", headers=H(jwt.encode(body, key, algorithm="HS256"))).status_code == 401)
none_token = jwt.encode(body, "", algorithm="none")
check("unsigned token: refused", c.get(f"{A}/me", headers=H(none_token)).status_code == 401)

# Refresh and rotation
check("refresh from another origin: refused", c.post(f"{A}/auth/refresh", headers={"Origin": "https://evil.example.com", "Cookie": f"vq_admin_refresh={refresh1}"}).status_code == 403)
check("refresh with no origin: refused", c.post(f"{A}/auth/refresh", headers={"Cookie": f"vq_admin_refresh={refresh1}"}).status_code == 403)
r = c.post(f"{A}/auth/refresh", headers={"Origin": ORIGIN, "Cookie": f"vq_admin_refresh={refresh1}"})
check("refresh from the admin origin works", r.status_code == 200, r.text)
access2, refresh2 = r.json()["data"]["access_token"], cookie_from(r)
check("refresh token rotates", refresh2 and refresh2 != refresh1)
c.cookies.clear()
r = c.post(f"{A}/auth/refresh", headers={"Origin": ORIGIN, "Cookie": f"vq_admin_refresh={refresh1}"})
check("replayed refresh token: refused", r.status_code == 401)
check("replay ends the whole session", c.get(f"{A}/me", headers=H(access2)).status_code == 401)
c.cookies.clear()

# Second sign-in, with a real code, and replay of that code
wait = time.time() % 30
if wait > 25:
    time.sleep(31 - wait)
step2 = int(time.time() // 30) + 1
login2 = c.post(f"{A}/auth/login", json={"email": EMAIL, "password": PASSWORD}).json()["data"]
check("second sign-in needs a code, not enrolment", login2["mfa_enrollment_required"] is False)
r = c.post(f"{A}/auth/mfa/verify", json={"mfa_token": login2["mfa_token"], "code": totp(secret, step2)})
check("correct code signs in", r.status_code == 200, r.text)
access3 = r.json()["data"]["access_token"]
c.cookies.clear()
login3 = c.post(f"{A}/auth/login", json={"email": EMAIL, "password": PASSWORD}).json()["data"]
check("same code can't be used twice", c.post(f"{A}/auth/mfa/verify", json={"mfa_token": login3["mfa_token"], "code": totp(secret, step2)}).status_code == 401)
r = c.post(f"{A}/auth/mfa/verify", json={"mfa_token": login3["mfa_token"], "recovery_code": codes[0]})
check("recovery code works", r.status_code == 200, r.text)
access4 = r.json()["data"]["access_token"]
c.cookies.clear()
login4 = c.post(f"{A}/auth/login", json={"email": EMAIL, "password": PASSWORD}).json()["data"]
check("recovery code can't be used twice", c.post(f"{A}/auth/mfa/verify", json={"mfa_token": login4["mfa_token"], "recovery_code": codes[0]}).status_code == 401)
check("enrolment can't restart once complete", c.post(f"{A}/auth/mfa/enroll/start", json={"mfa_token": login4["mfa_token"]}).status_code == 401)

# Sessions
listed = c.get(f"{A}/auth/sessions", headers=H(access4)).json()["data"]
check("sessions listed, without token hashes", len(listed) >= 2 and "refresh_token_hash" not in str(listed))
other = next(s for s in listed if not s["current"])
check("another session can be ended", c.post(f"{A}/auth/sessions/{other['id']}/revoke", headers=H(access4)).status_code == 200)
check("ended session stops working immediately", c.get(f"{A}/me", headers=H(access3)).status_code == 401)
check("sign out", c.post(f"{A}/auth/logout", headers=H(access4)).status_code == 200)
check("signed-out token stops working immediately", c.get(f"{A}/me", headers=H(access4)).status_code == 401)
c.cookies.clear()

# Lockout runs last, because it blocks this source.
for _ in range(10):
    last = c.post(f"{A}/auth/login", json={"email": EMAIL, "password": "wrong-password-value"})
    if last.status_code == 429:
        break
check("repeated failures lock the source", last.status_code == 429 and last.json()["error"]["code"] == "ADMIN_TEMPORARILY_LOCKED", last.status_code)
check("correct password refused while locked", c.post(f"{A}/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 429)
spoofed = c.post(f"{A}/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers={"X-Forwarded-For": "203.0.113.9"})
check("forged forwarded header doesn't evade the lock", spoofed.status_code == 429)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
