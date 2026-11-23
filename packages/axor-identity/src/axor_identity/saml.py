"""Single sign-on over SAML 2.0, for providers a company runs SAML on (ADFS,
older Okta and Entra ID apps, Ping, OneLogin, Shibboleth).

Service-provider initiated, the profile every provider takes:

- the login form's "continue with SSO" sends the browser to the provider's
  single sign-on URL with an AuthnRequest (HTTP-Redirect binding: deflated,
  base64, in the query), its ID remembered with the sign-in's state;
- the provider posts a SAMLResponse back to our assertion consumer service
  (HTTP-POST binding);
- we check the XML signature against the provider's certificate — and read
  only what the signature covers — then the assertion's issuer, audience,
  recipient, time window and InResponseTo, and take the address from the
  NameID or an email attribute. The account rules are OpenID Connect's
  (app.py ``_sso_account``): the workspace's domains, its default role.

The workspace's admin gives the provider's metadata (or its SSO URL, entity id
and certificate) and gives the provider ours: the entity id and the ACS URL.
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
      "ds": "http://www.w3.org/2000/09/xmldsig#"}
SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
EMAIL_ATTRIBUTES = ("email", "mail", "emailaddress", "user.email",
                    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
                    "urn:oid:0.9.2342.19200300.100.1.3")


class SAMLError(Exception):
    """What the provider sent does not hold."""


@dataclass(frozen=True)
class IdP:
    sso_url: str
    entity_id: str
    cert_pem: str


def _parser() -> Any:  # noqa: ANN401 - an lxml parser
    from lxml import etree

    # no DTDs, no entities, no network: XML from outside is read as data only
    return etree.XMLParser(resolve_entities=False, no_network=True, dtd_validation=False,
                           load_dtd=False, huge_tree=False, remove_comments=True)


def normalize_cert(raw: str) -> str:
    """A PEM certificate from PEM or from the bare base64 metadata carries;
    refused unless it parses."""
    from cryptography import x509

    body = "".join(raw.replace("-----BEGIN CERTIFICATE-----", "")
                   .replace("-----END CERTIFICATE-----", "").split())
    pem = "-----BEGIN CERTIFICATE-----\n" + "\n".join(
        body[i:i + 64] for i in range(0, len(body), 64)) + "\n-----END CERTIFICATE-----\n"
    try:
        x509.load_pem_x509_certificate(pem.encode())
    except ValueError as exc:
        raise SAMLError("the provider's certificate does not read as an X.509 certificate") from exc
    return pem


def from_metadata(xml: str) -> IdP:
    """The provider's entity id, its HTTP-Redirect single sign-on URL and its
    signing certificate, from the metadata it publishes."""
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
    certs = [c.text for c in certs if c is not None and c.text]
    if not certs:
        raise SAMLError("the metadata carries no signing certificate")
    return IdP(redirect[0], entity.get("entityID", ""), normalize_cert(certs[0]))


def sp_metadata(entity_id: str, acs_url: str) -> str:
    return (
        '<?xml version="1.0"?>\n'
        f'<md:EntityDescriptor xmlns:md="{NS["md"]}" entityID="{escape(entity_id)}">'
        '<md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" '
        'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
        '<md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>'
        '<md:AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'Location="{escape(acs_url)}" index="0" isDefault="true"/>'
        '</md:SPSSODescriptor></md:EntityDescriptor>')


def authn_request(idp: IdP, *, entity_id: str, acs_url: str, relay_state: str) -> tuple[str, str]:
    """(redirect URL, request ID) — the ID is what the response must answer."""
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
    sep = "&" if "?" in idp.sso_url else "?"
    return f"{idp.sso_url}{sep}{query}", request_id


def _time(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SAMLError(f"a time in the assertion does not read: {value}") from exc


def read_response(saml_response: str, idp: IdP, *, entity_id: str, acs_url: str,
                  request_id: str, domains: tuple[str, ...],
                  now: datetime.datetime | None = None) -> dict[str, str]:
    """``{"sub": NameID, "email": address}`` from a signed, in-date assertion
    issued for this sign-in, to us, by this provider — or SAMLError."""
    from lxml import etree
    from signxml import InvalidInput, InvalidSignature, XMLVerifier

    now = now or datetime.datetime.now(datetime.UTC)
    try:
        raw = base64.b64decode(saml_response, validate=False)
        doc = etree.fromstring(raw, parser=_parser())
    except (ValueError, etree.XMLSyntaxError) as exc:
        raise SAMLError("the provider's answer is not a SAML response") from exc
    status = doc.find("samlp:Status/samlp:StatusCode", NS)
    if status is not None and status.get("Value") != SUCCESS:
        code = status.get("Value", "").rsplit(":", 1)[-1]
        raise SAMLError(f"the provider did not sign you in ({code})")
    try:
        verified = XMLVerifier().verify(doc, x509_cert=idp.cert_pem).signed_xml
    except (InvalidSignature, InvalidInput) as exc:
        raise SAMLError(f"the response's signature does not hold: {exc}") from exc
    # only what the signature covers is read
    assertion = verified if verified.tag == f"{{{NS['saml']}}}Assertion" \
        else verified.find("saml:Assertion", NS)
    if assertion is None:
        raise SAMLError("the signed part carries no assertion")
    issuer = assertion.findtext("saml:Issuer", namespaces=NS) or ""
    if idp.entity_id and issuer.strip() != idp.entity_id:
        raise SAMLError(f"the assertion is from {issuer!r}, not this workspace's provider")
    conditions = assertion.find("saml:Conditions", NS)
    if conditions is not None:
        before, after = _time(conditions.get("NotBefore")), _time(conditions.get("NotOnOrAfter"))
        if (before and now + SKEW < before) or (after and now - SKEW >= after):
            raise SAMLError("the assertion is out of its time window")
        audiences = [a.text.strip() for a in conditions.findall(
            "saml:AudienceRestriction/saml:Audience", NS) if a.text]
        if audiences and entity_id not in audiences:
            raise SAMLError("the assertion is for another service, not this one")
    confirmed = False
    for data in assertion.findall("saml:Subject/saml:SubjectConfirmation/"
                                  "saml:SubjectConfirmationData", NS):
        after = _time(data.get("NotOnOrAfter"))
        if data.get("Recipient") not in (None, acs_url) or (after and now - SKEW >= after):
            continue
        if data.get("InResponseTo") not in (None, request_id):
            continue
        confirmed = confirmed or data.get("InResponseTo") == request_id
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
    return {"sub": name_id or email, "email": email}
