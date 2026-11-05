"""Generate a throwaway CA and server certificates for docker-compose.saas-test.yml. Test use only."""
import datetime
import ipaddress  # noqa: F401
import os
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

OUT = sys.argv[1] if len(sys.argv) > 1 else ".saas-test-certs"
HOSTS = ["saas-tenant-a", "saas-tenant-b"]
now = datetime.datetime.now(datetime.timezone.utc)


def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def write_key(path, k):
    with open(path, "wb") as fh:
        fh.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                 serialization.NoEncryption()))


def write_cert(path, c):
    with open(path, "wb") as fh:
        fh.write(c.public_bytes(serialization.Encoding.PEM))


def name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def self_signed_ca(cn):
    k = key()
    cert = (
        x509.CertificateBuilder().subject_name(name(cn)).issuer_name(name(cn)).public_key(k.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                     data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(k.public_key()), critical=False)
        .sign(k, hashes.SHA256())
    )
    return k, cert


os.makedirs(OUT, exist_ok=True)
ca_key, ca_cert = self_signed_ca("VoiceQuote SaaS Test CA")
write_cert(os.path.join(OUT, "ca.crt"), ca_cert)
# A second CA nothing is signed by, for testing that an untrusted chain is rejected.
_, other_cert = self_signed_ca("Unrelated Test CA")
write_cert(os.path.join(OUT, "other-ca.crt"), other_cert)

for host in HOSTS:
    k = key()
    cert = (
        x509.CertificateBuilder().subject_name(name(host)).issuer_name(ca_cert.subject).public_key(k.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    write_key(os.path.join(OUT, f"{host}.key"), k)
    write_cert(os.path.join(OUT, f"{host}.crt"), cert)
print(f"Wrote test certificates to {OUT}")
