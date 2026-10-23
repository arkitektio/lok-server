"""A hub's TLS certificate: the one certificate apps trust for that hub's services.

A hub that terminates TLS itself (konstruktor's gateway) serves ONE self-signed certificate for
every host it is reached at, and sends it (it is public) in its manifest as ``tls_certificate``.
It is stored on ``Hub.tls_certificate`` when the hub is authorized — a re-authorized hub sends a
new one, or none (it is a plain-HTTP hub again): that is the rotation — and handed to apps on
each of the hub's instances as ``tls.certificates``.

An app trusts exactly that certificate for the hub's aliases, and nothing else: there is no CA
and no chain. Because lok itself is reached over a publicly valid certificate, the app can
believe the certificate it is handed here, which is what makes a self-signed hub verifiable.
"""

from __future__ import annotations

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

MAX_PEM_LENGTH = 16_384


def validate_tls_certificate(value: object) -> str:
    """``value`` (re-encoded as one canonical PEM) if it is a single server certificate, else ``ValueError``.

    A server certificate here: one PEM block, X.509 v3, with a subjectAltName (clients match
    hosts against it, never the CN) and not a CA certificate — rustls, which the LiveKit SDK
    uses, refuses a CA certificate presented as a server's.
    """
    if not isinstance(value, str) or len(value) > MAX_PEM_LENGTH:
        raise ValueError("tls_certificate must be a PEM string of at most 16 KiB")
    if value.count("-----BEGIN CERTIFICATE-----") != 1:
        raise ValueError("tls_certificate must hold exactly one PEM certificate")
    try:
        certificate = x509.load_pem_x509_certificate(value.encode())
    except ValueError as error:
        raise ValueError("tls_certificate is not a readable PEM certificate") from error
    if certificate.version != x509.Version.v3:
        raise ValueError("tls_certificate must be an X.509 v3 certificate")
    try:
        _ = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound as error:
        raise ValueError("tls_certificate must name its hosts in a subjectAltName") from error
    try:
        constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
        if constraints.ca:
            raise ValueError("tls_certificate must be a server certificate, not a CA")
    except x509.ExtensionNotFound:
        pass
    return certificate.public_bytes(Encoding.PEM).decode()
