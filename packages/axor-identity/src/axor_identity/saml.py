"""Single sign-on over SAML 2.0, for providers a company runs SAML on (ADFS,
older Okta and Entra ID apps, Ping, OneLogin, Shibboleth).

Service-provider initiated, the profile every provider takes:

- the login form's "continue with SSO" sends the browser to the provider's
  single sign-on URL with an AuthnRequest (HTTP-Redirect binding: deflated,
  base64, in the query), its ID remembered with the sign-in's state — signed
  with the workspace's own key when the provider asks for that (ADFS);
- the provider posts a SAMLResponse back to our assertion consumer service
  (HTTP-POST binding);
- we check the XML signature against the provider's certificate — any of
  them, while it rotates — and read only what the signature covers, then the
  assertion's issuer, audience, recipient, time window and InResponseTo, and
  take the address from the NameID or an email attribute. An assertion the
  provider encrypted to our key is decrypted first (RSA-OAEP, AES-CBC/GCM).
  The account rules are OpenID Connect's (app.py ``_sso_account``).

Provider-initiated (a tile on the provider's dashboard) when the workspace
turns it on: no request to answer, so the assertion must carry no
InResponseTo and its ID is used once.

The workspace's admin gives the provider's metadata (or its SSO URL, entity id
and certificate) and gives the provider ours: the entity id, the ACS URL and
our certificate, all in our metadata.
"""
from __future__ import annotations

import base64
import datetime
import secrets
import zlib
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from xml.sax.saxutils import escape

PREFIX = "saml:"
SKEW = datetime.timedelta(minutes=3)
NS = {"samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
      "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
      "md": "urn:oasis:names:tc:SAML:2.0:metadata",
      "ds": "http://www.w3.org/2000/09/xmldsig#",
      "xenc": "http://www.w3.org/2001/04/xmlenc#",
      "xenc11": "http://www.w3.org/2009/xmlenc11#"}
SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
EMAIL_ATTRIBUTES = ("email", "mail", "emailaddress", "user.email",
                    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
                    "urn:oid:0.9.2342.19200300.100.1.3")
RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
_PEM_BEGIN = "-----BEGIN CERTIFICATE-----"
_PEM_END = "-----END CERTIFICATE-----"


class SAMLError(Exception):
    """What the provider sent does not hold."""


@dataclass(frozen=True)
class IdP:
    sso_url: str
    entity_id: str
    cert_pem: str            # one certificate, or several while the provider rotates

    @property
    def certs(self) -> list[str]:
        return [f"{_PEM_BEGIN}{part.split(_PEM_END)[0]}{_PEM_END}\n"
                for part in self.cert_pem.split(_PEM_BEGIN)[1:]]


def _parser() -> Any:  # noqa: ANN401 - an lxml parser
    from lxml import etree

    # no DTDs, no entities, no network: XML from outside is read as data only
    return etree.XMLParser(resolve_entities=False, no_network=True, dtd_validation=False,
                           load_dtd=False, huge_tree=False, remove_comments=True)


def normalize_cert(raw: str) -> str:
    """A PEM certificate from PEM or from the bare base64 metadata carries;
    refused unless it parses."""
    from cryptography import x509

    body = "".join(raw.replace(_PEM_BEGIN, "").replace(_PEM_END, "").split())
    pem = _PEM_BEGIN + "\n" + "\n".join(
        body[i:i + 64] for i in range(0, len(body), 64)) + "\n" + _PEM_END + "\n"
    try:
        x509.load_pem_x509_certificate(pem.encode())
    except ValueError as exc:
        raise SAMLError("the provider's certificate does not read as an X.509 certificate") from exc
    return pem


def normalize_certs(raw: str) -> str:
    """One or more certificates (PEM blocks one after another, or one bare
    base64 body), each checked, as one PEM bundle."""
    if raw.count(_PEM_BEGIN) > 1:
        return "".join(normalize_cert(_PEM_BEGIN + part)
                       for part in raw.split(_PEM_BEGIN)[1:])
    return normalize_cert(raw)


def from_metadata(xml: str) -> IdP:
    """The provider's entity id, its HTTP-Redirect single sign-on URL and its
    signing certificates (a second one while it rotates), from the metadata
    it publishes."""
    from lxml import etree

    try:
        root = etree.fromstring(xml.encode(), parser=_parser())
    except etree.XMLSyntaxError as exc:
        raise SAMLError("the metadata is not XML") from exc
    entity = root if root.tag.endswith("EntityDescriptor") else \
        root.find(".//md:EntityDescriptor", NS)
    desc = entity.find("md:IDPSSODescriptor", NS) if entity is not None else None
    if desc is None:
        raise SAMLError("the metadata describes no identity provider (IDPSSODescriptor)")
    redirect = [s.get("Location") for s in desc.findall("md:SingleSignOnService", NS)
                if s.get("Binding", "").endswith("HTTP-Redirect")]
    if not redirect:
        raise SAMLError("the provider offers no HTTP-Redirect single sign-on URL")
    certs = [k.find(".//ds:X509Certificate", NS) for k in desc.findall("md:KeyDescriptor", NS)
             if k.get("use") in (None, "signing")]
    bodies = list(dict.fromkeys("".join(c.text.split()) for c in certs if c is not None
                                and c.text))
    if not bodies:
        raise SAMLError("the metadata carries no signing certificate")
    return IdP(redirect[0], entity.get("entityID", ""),
               "".join(normalize_cert(b) for b in bodies[:4]))


# ── this service as a service provider: its key, its metadata ────────────────

def new_sp_key(entity_id: str) -> tuple[str, str]:
    """(private key PEM, self-signed certificate PEM) for one workspace: it
    signs our requests where the provider asks, and providers encrypt
    assertions to it. Ten years: providers pin it from our metadata."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    host = entity_id.split("://", 1)[-1].split("/", 1)[0][:64] or "axor"
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Axor SAML")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .sign(key, hashes.SHA256()))
    return (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode(),
            cert.public_bytes(serialization.Encoding.PEM).decode())


def _cert_body(pem: str) -> str:
    return "".join(pem.replace(_PEM_BEGIN, "").replace(_PEM_END, "").split())


def sp_metadata(entity_id: str, acs_url: str, cert_pem: str | None = None,
                sign_requests: bool = False) -> str:
    keys = ""
    if cert_pem:
        info = (f'<ds:KeyInfo xmlns:ds="{NS["ds"]}"><ds:X509Data><ds:X509Certificate>'
                f'{_cert_body(cert_pem)}</ds:X509Certificate></ds:X509Data></ds:KeyInfo>')
        keys = (f'<md:KeyDescriptor use="signing">{info}</md:KeyDescriptor>'
                f'<md:KeyDescriptor use="encryption">{info}'
                f'<md:EncryptionMethod Algorithm="{NS["xenc11"]}aes256-gcm"/>'
                f'<md:EncryptionMethod Algorithm="{NS["xenc"]}aes256-cbc"/>'
                f'<md:EncryptionMethod Algorithm="{NS["xenc"]}rsa-oaep-mgf1p"/>'
                '</md:KeyDescriptor>')
    return (
        '<?xml version="1.0"?>\n'
        f'<md:EntityDescriptor xmlns:md="{NS["md"]}" entityID="{escape(entity_id)}">'
        f'<md:SPSSODescriptor AuthnRequestsSigned="{"true" if sign_requests else "false"}" '
        'WantAssertionsSigned="true" '
        'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
        f'{keys}'
        '<md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>'
        '<md:AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'Location="{escape(acs_url)}" index="0" isDefault="true"/>'
        '</md:SPSSODescriptor></md:EntityDescriptor>')


def authn_request(idp: IdP, *, entity_id: str, acs_url: str, relay_state: str,
                  sign_key_pem: str | None = None) -> tuple[str, str]:
    """(redirect URL, request ID) — the ID is what the response must answer.
    With a key, the query is signed as the HTTP-Redirect binding says: over
    ``SAMLRequest=…&RelayState=…&SigAlg=…`` exactly as sent."""
    request_id = "_" + secrets.token_hex(16)
    now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    xml = (f'<samlp:AuthnRequest xmlns:samlp="{NS["samlp"]}" xmlns:saml="{NS["saml"]}" '
           f'ID="{request_id}" Version="2.0" IssueInstant="{now}" '
           f'Destination="{escape(idp.sso_url)}" AssertionConsumerServiceURL="{escape(acs_url)}" '
           'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST">'
           f'<saml:Issuer>{escape(entity_id)}</saml:Issuer>'
           '<samlp:NameIDPolicy Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress" '
           'AllowCreate="true"/></samlp:AuthnRequest>')
    deflated = zlib.compress(xml.encode())[2:-4]          # raw DEFLATE
    query = urlencode({"SAMLRequest": base64.b64encode(deflated).decode(),
                       "RelayState": relay_state})
    if sign_key_pem:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        query += "&" + urlencode({"SigAlg": RSA_SHA256})
        key = serialization.load_pem_private_key(sign_key_pem.encode(), None)
        sig = key.sign(query.encode(), padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr, call-arg]
        query += "&" + urlencode({"Signature": base64.b64encode(sig).decode()})
    sep = "&" if "?" in idp.sso_url else "?"
    return f"{idp.sso_url}{sep}{query}", request_id


# ── reading the provider's answer ───────────────────────────────────────────

def _time(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SAMLError(f"a time in the assertion does not read: {value}") from exc


def _parse(saml_response: str) -> Any:  # noqa: ANN401 - an lxml element
    from lxml import etree

    try:
        raw = base64.b64decode(saml_response, validate=False)
        return etree.fromstring(raw, parser=_parser())
    except (ValueError, etree.XMLSyntaxError) as exc:
        raise SAMLError("the provider's answer is not a SAML response") from exc


def audience_org(saml_response: str, prefix: str) -> str | None:
    """Which workspace an answer nobody asked for is addressed to — read
    before any signature is checked, only to choose whose certificate checks
    it; the audience is checked again once it is."""
    try:
        doc = _parse(saml_response)
    except SAMLError:
        return None
    for aud in doc.iter(f"{{{NS['saml']}}}Audience"):
        text = (aud.text or "").strip()
        if text.startswith(prefix) and "/" not in text[len(prefix):]:
            return text[len(prefix):] or None
    return None


_CIPHERS = {f"{NS['xenc']}aes128-cbc": ("cbc", 16), f"{NS['xenc']}aes192-cbc": ("cbc", 24),
            f"{NS['xenc']}aes256-cbc": ("cbc", 32), f"{NS['xenc11']}aes128-gcm": ("gcm", 16),
            f"{NS['xenc11']}aes192-gcm": ("gcm", 24), f"{NS['xenc11']}aes256-gcm": ("gcm", 32)}
_DIGESTS = {"http://www.w3.org/2000/09/xmldsig#sha1": "SHA1",
            "http://www.w3.org/2001/04/xmlenc#sha256": "SHA256",
            "http://www.w3.org/2001/04/xmlenc#sha512": "SHA512"}
_MGFS = {f"{NS['xenc11']}mgf1sha1": "SHA1", f"{NS['xenc11']}mgf1sha256": "SHA256",
         f"{NS['xenc11']}mgf1sha512": "SHA512"}


def _decrypt(encrypted: Any, key_pem: str | None) -> Any:  # noqa: ANN401 - lxml elements
    """The assertion inside an EncryptedAssertion: the content key unwrapped
    with our RSA key (OAEP — never PKCS#1 v1.5), the assertion with AES. Any
    failure reads the same, so the answer is no oracle."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from lxml import etree

    if not key_pem:
        raise SAMLError("the provider encrypted the assertion, but this workspace has no "
                        "key for it yet — give the provider our metadata again")
    data = encrypted.find("xenc:EncryptedData", NS)
    method = data.find("xenc:EncryptionMethod", NS) if data is not None else None
    cipher = _CIPHERS.get(method.get("Algorithm", "")) if method is not None else None
    if cipher is None:
        raise SAMLError("the assertion is encrypted with an algorithm this service does not "
                        "take — set the provider to AES-256-GCM or AES-256-CBC")
    ek = data.find("ds:KeyInfo/xenc:EncryptedKey", NS)
    if ek is None:
        ek = encrypted.find("xenc:EncryptedKey", NS)   # beside the data, by reference
    kmethod = ek.find("xenc:EncryptionMethod", NS) if ek is not None else None
    kalg = kmethod.get("Algorithm", "") if kmethod is not None else ""
    if kalg not in (f"{NS['xenc']}rsa-oaep-mgf1p", f"{NS['xenc11']}rsa-oaep"):
        raise SAMLError("the assertion's key is wrapped with RSA 1.5 or something unknown — "
                        "set the provider to RSA-OAEP")
    digest_el = kmethod.find("ds:DigestMethod", NS)
    digest = _DIGESTS.get(digest_el.get("Algorithm", "") if digest_el is not None else "",
                          "SHA1")
    mgf_el = kmethod.find("xenc11:MGF", NS)
    mgf = _MGFS.get(mgf_el.get("Algorithm", "") if mgf_el is not None else "", "SHA1")
    unreadable = SAMLError("the encrypted assertion does not decrypt with this workspace's key")
    try:
        key = serialization.load_pem_private_key(key_pem.encode(), None)
        wrapped = base64.b64decode("".join(ek.findtext("xenc:CipherData/xenc:CipherValue",
                                                        namespaces=NS).split()))
        cek = key.decrypt(wrapped, padding.OAEP(  # type: ignore[union-attr, call-arg]
            mgf=padding.MGF1(getattr(hashes, mgf)()), algorithm=getattr(hashes, digest)(),
            label=None))
        if len(cek) != cipher[1]:
            raise unreadable
        body = base64.b64decode("".join(data.findtext("xenc:CipherData/xenc:CipherValue",
                                                      namespaces=NS).split()))
        if cipher[0] == "gcm":
            plain = AESGCM(cek).decrypt(body[:12], body[12:], None)
        else:
            dec = Cipher(algorithms.AES(cek), modes.CBC(body[:16])).decryptor()
            padded = dec.update(body[16:]) + dec.finalize()
            pad = padded[-1] if padded else 0
            if not 1 <= pad <= 16:
                raise unreadable
            plain = padded[:-pad]
        assertion = etree.fromstring(plain, parser=_parser())
    except SAMLError:
        raise
    except Exception as exc:  # noqa: BLE001 - one answer for every way it fails
        raise unreadable from exc
    if assertion.tag != f"{{{NS['saml']}}}Assertion":
        raise unreadable
    return assertion


def _verified(element: Any, idp: IdP) -> Any:  # noqa: ANN401 - lxml elements
    """What the provider's signature covers, under any of its certificates."""
    from signxml import InvalidInput, InvalidSignature, XMLVerifier

    last: Exception | None = None
    for cert in idp.certs or [idp.cert_pem]:
        try:
            return XMLVerifier().verify(element, x509_cert=cert).signed_xml
        except (InvalidSignature, InvalidInput) as exc:
            last = exc
    raise SAMLError(f"the response's signature does not hold: {last}")


def read_response(saml_response: str, idp: IdP, *, entity_id: str, acs_url: str,
                  request_id: str | None, domains: tuple[str, ...],
                  sp_key_pem: str | None = None,
                  now: datetime.datetime | None = None) -> dict[str, str]:
    """``{"sub", "email", "assertion_id", "until"}`` from a signed, in-date
    assertion issued for this sign-in (``request_id``; None: provider-started,
    and then it must answer no request), to us, by this provider — or
    SAMLError."""
    now = now or datetime.datetime.now(datetime.UTC)
    doc = _parse(saml_response)
    status = doc.find("samlp:Status/samlp:StatusCode", NS)
    if status is not None and status.get("Value") != SUCCESS:
        code = status.get("Value", "").rsplit(":", 1)[-1]
        raise SAMLError(f"the provider did not sign you in ({code})")
    if doc.find("saml:EncryptedAssertion", NS) is None:
        verified = _verified(doc, idp)
        # only what the signature covers is read
        assertion = verified if verified.tag == f"{{{NS['saml']}}}Assertion" \
            else verified.find("saml:Assertion", NS)
    elif doc.find("ds:Signature", NS) is not None:
        # the response is signed over the ciphertext: what it covers, decrypted
        enc = _verified(doc, idp).find("saml:EncryptedAssertion", NS)
        if enc is None:
            raise SAMLError("the signed part carries no assertion")
        assertion = _decrypt(enc, sp_key_pem)
    else:
        # only the assertion inside can be signed: it must be
        signed = _verified(_decrypt(doc.find("saml:EncryptedAssertion", NS), sp_key_pem), idp)
        assertion = signed if signed.tag == f"{{{NS['saml']}}}Assertion" else None
    if assertion is None:
        raise SAMLError("the signed part carries no assertion")
    issuer = assertion.findtext("saml:Issuer", namespaces=NS) or ""
    if idp.entity_id and issuer.strip() != idp.entity_id:
        raise SAMLError(f"the assertion is from {issuer!r}, not this workspace's provider")
    conditions = assertion.find("saml:Conditions", NS)
    until = None
    if conditions is not None:
        before, until = _time(conditions.get("NotBefore")), _time(conditions.get("NotOnOrAfter"))
        if (before and now + SKEW < before) or (until and now - SKEW >= until):
            raise SAMLError("the assertion is out of its time window")
        audiences = [a.text.strip() for a in conditions.findall(
            "saml:AudienceRestriction/saml:Audience", NS) if a.text]
        if audiences and entity_id not in audiences:
            raise SAMLError("the assertion is for another service, not this one")
        if request_id is None and not audiences:
            raise SAMLError("a sign-in started at the provider must name this service "
                            "as its audience")
    confirmed = False
    for data in assertion.findall("saml:Subject/saml:SubjectConfirmation/"
                                  "saml:SubjectConfirmationData", NS):
        after = _time(data.get("NotOnOrAfter"))
        if data.get("Recipient") not in (None, acs_url) or (after and now - SKEW >= after):
            continue
        answers = data.get("InResponseTo")
        # asked: it answers exactly our request; provider-started: it answers none
        if (answers != request_id) if request_id else (answers is not None):
            continue
        confirmed = True
        until = min(t for t in (until, after) if t) if (until or after) else None
    if not confirmed:
        raise SAMLError("the assertion was not issued for this sign-in")
    name_id = (assertion.findtext("saml:Subject/saml:NameID", namespaces=NS) or "").strip()
    email = name_id.lower() if "@" in name_id else ""
    for attr in assertion.findall("saml:AttributeStatement/saml:Attribute", NS):
        if attr.get("Name", "").lower() in EMAIL_ATTRIBUTES or \
                attr.get("FriendlyName", "").lower() in EMAIL_ATTRIBUTES:
            value = attr.findtext("saml:AttributeValue", namespaces=NS) or ""
            if "@" in value:
                email = value.strip().lower()
                break
    if "@" not in email:
        raise SAMLError("the provider did not say who signed in (no email)")
    if email.rsplit("@", 1)[1] not in domains:
        raise SAMLError(f"{email} is not at a domain this workspace signs in "
                        f"({', '.join(domains)})")
    return {"sub": name_id or email, "email": email,
            "assertion_id": assertion.get("ID", ""),
            "until": (until or now + datetime.timedelta(minutes=10)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")}
