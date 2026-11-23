"""Single sign-on over SAML 2.0, against a fake identity provider that signs
its assertions with a real certificate: the sign-in goes through, and what
does not hold — a tampered address, another audience, another sign-in, an
expired assertion, an unsigned one slipped in beside a signed one — is
refused."""
from __future__ import annotations

import base64
import datetime
import pathlib
import zlib
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from axor_identity.app import create_app
from axor_identity.keys import generate_pem, load_signing_key
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLSigner, methods

PUBLIC = "https://cp.example.com/identity"
IDP_SSO = "https://idp.acme.dev/saml/sso"
IDP_ENTITY = "https://idp.acme.dev/saml"
PW = "correct horse battery"
SP_ENTITY_FMT = PUBLIC + "/v1/saml/{org}"
ACS = PUBLIC + "/v1/saml/acs"


def _cert() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "idp.acme.dev")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365))
            .sign(key, hashes.SHA256()))
    return (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()),
            cert.public_bytes(serialization.Encoding.PEM))


KEY, CERT = _cert()


def _metadata() -> str:
    body = b"".join(CERT.split(b"\n")[1:-2]).decode()
    return (f'<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" '
            f'entityID="{IDP_ENTITY}"><md:IDPSSODescriptor '
            f'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
            f'<md:KeyDescriptor use="signing"><ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">'
            f'<ds:X509Data><ds:X509Certificate>{body}</ds:X509Certificate></ds:X509Data>'
            f'</ds:KeyInfo></md:KeyDescriptor>'
            f'<md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" '
            f'Location="{IDP_SSO}"/></md:IDPSSODescriptor></md:EntityDescriptor>')


def _t(delta: int = 0) -> str:
    return (datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=delta)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _assertion(*, email: str, in_response_to: str, audience: str, recipient: str = ACS,
               issuer: str = IDP_ENTITY, until: int = 300) -> etree._Element:
    xml = (f'<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_a1" '
           f'Version="2.0" IssueInstant="{_t()}"><saml:Issuer>{issuer}</saml:Issuer>'
           f'<saml:Subject><saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:'
           f'emailAddress">{email}</saml:NameID><saml:SubjectConfirmation '
           f'Method="urn:oasis:names:tc:SAML:2.0:cm:bearer"><saml:SubjectConfirmationData '
           f'InResponseTo="{in_response_to}" Recipient="{recipient}" NotOnOrAfter="{_t(until)}"/>'
           f'</saml:SubjectConfirmation></saml:Subject><saml:Conditions NotBefore="{_t(-60)}" '
           f'NotOnOrAfter="{_t(until)}"><saml:AudienceRestriction><saml:Audience>{audience}'
           f'</saml:Audience></saml:AudienceRestriction></saml:Conditions>'
           f'<saml:AttributeStatement><saml:Attribute Name="email"><saml:AttributeValue>{email}'
           f'</saml:AttributeValue></saml:Attribute></saml:AttributeStatement></saml:Assertion>')
    return etree.fromstring(xml.encode())


def _signed(assertion: etree._Element) -> etree._Element:
    # exclusive canonicalization, as identity providers sign SAML
    signer = XMLSigner(method=methods.enveloped, signature_algorithm="rsa-sha256",
                       digest_algorithm="sha256",
                       c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#")
    return signer.sign(assertion, key=KEY, cert=CERT, reference_uri="#_a1")


def _response(assertion: etree._Element, in_response_to: str) -> str:
    root = etree.fromstring(
        f'<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        f'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_r1" Version="2.0" '
        f'IssueInstant="{_t()}" Destination="{ACS}" InResponseTo="{in_response_to}">'
        f'<saml:Issuer>{IDP_ENTITY}</saml:Issuer><samlp:Status><samlp:StatusCode '
        f'Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status></samlp:Response>'
        .encode())
    root.append(assertion)
    return base64.b64encode(etree.tostring(root)).decode()


@pytest.fixture
async def app(tmp_path: pathlib.Path):  # noqa: ANN201
    application = create_app(f"sqlite+aiosqlite:///{tmp_path}/id.db",
                             signing_key=load_signing_key(generate_pem(), kid="k"),
                             providers={}, public_url=PUBLIC, mailer=False)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def c(app):  # noqa: ANN001, ANN201
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://identity") as client:
        yield client


async def _setup(c: httpx.AsyncClient, app) -> str:  # noqa: ANN001
    olga = (await c.post("/v1/signup", json={"email": "olga@acme.dev", "password": PW,
                                             "org_name": "Acme"})).json()
    await app.state.store.mark_verified(olga["user"]["user_id"])
    org = olga["org"]["org_id"]
    await app.state.store.set_org_tier(org, "business", "2999-01-01")
    h = {"Authorization": f"Bearer {olga['access_token']}"}
    r = await c.put(f"/v1/orgs/{org}/sso", headers=h, json={
        "protocol": "saml", "saml_metadata": _metadata(), "domains": ["acme.dev"]})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["protocol"] == "saml" and got["saml_sso_url"] == IDP_SSO and got["has_cert"]
    assert got["saml_acs_url"] == ACS
    assert got["saml_entity_id_ours"] == SP_ENTITY_FMT.format(org=org)
    return org


async def _start(c: httpx.AsyncClient) -> tuple[str, str]:
    start = await c.get("/v1/sso/start",
                        params={"email": "anna@acme.dev", "return_to": "/#settings"})
    loc = urlsplit(start.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == IDP_SSO
    q = parse_qs(loc.query)
    request = zlib.decompress(base64.b64decode(q["SAMLRequest"][0]), -15)
    rid = etree.fromstring(request).get("ID")
    return rid, q["RelayState"][0]


async def _post(c: httpx.AsyncClient, response: str, relay: str) -> dict:
    back = await c.post("/v1/saml/acs", content=urlencode({"SAMLResponse": response,
                                                           "RelayState": relay}),
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert back.status_code == 303, back.text
    return parse_qs(urlsplit(back.headers["location"]).query)


async def test_a_saml_sign_in_goes_through(c, app) -> None:  # noqa: ANN001
    org = await _setup(c, app)
    rid, relay = await _start(c)
    a = _signed(_assertion(email="anna@acme.dev", in_response_to=rid,
                           audience=SP_ENTITY_FMT.format(org=org)))
    back = await _post(c, _response(a, rid), relay)
    assert back["axor_org"] == [org]
    s = (await c.post("/v1/oauth/exchange",
                      json={"code": back["axor_login"][0], "org_id": org})).json()
    assert s["user"]["email"] == "anna@acme.dev" and s["org"]["role"] == "member"
    # the relay state is single use: a replay is refused
    again = await c.post("/v1/saml/acs", content=urlencode({"SAMLResponse": _response(a, rid),
                                                            "RelayState": relay}),
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert again.status_code == 400
    sp = await c.get(f"/v1/saml/{org}/metadata")
    assert ACS in sp.text and SP_ENTITY_FMT.format(org=org) in sp.text


async def test_what_does_not_hold_is_refused(c, app) -> None:  # noqa: ANN001
    org = await _setup(c, app)
    aud = SP_ENTITY_FMT.format(org=org)

    async def refused(make) -> str:  # noqa: ANN001
        rid, relay = await _start(c)
        back = await _post(c, make(rid), relay)
        assert "axor_login" not in back
        return back["axor_login_error"][0]

    def tampered(rid: str) -> str:
        a = _signed(_assertion(email="anna@acme.dev", in_response_to=rid, audience=aud))
        a.find(".//{urn:oasis:names:tc:SAML:2.0:assertion}NameID").text = "olga@acme.dev"
        for v in a.iter("{urn:oasis:names:tc:SAML:2.0:assertion}AttributeValue"):
            v.text = "olga@acme.dev"
        return _response(a, rid)

    assert "signature does not hold" in await refused(tampered)
    assert "another service" in await refused(lambda rid: _response(_signed(_assertion(
        email="anna@acme.dev", in_response_to=rid, audience="https://other.example")), rid))
    assert "not issued for this sign-in" in await refused(lambda rid: _response(_signed(_assertion(
        email="anna@acme.dev", in_response_to="_someone-elses", audience=aud)), rid))
    assert "time window" in await refused(lambda rid: _response(_signed(_assertion(
        email="anna@acme.dev", in_response_to=rid, audience=aud, until=-600)), rid))
    assert "not at a domain" in await refused(lambda rid: _response(_signed(_assertion(
        email="ceo@victim.dev", in_response_to=rid, audience=aud)), rid))
    assert "not this workspace's provider" in await refused(lambda rid: _response(_signed(
        _assertion(email="anna@acme.dev", in_response_to=rid, audience=aud,
                   issuer="https://evil")), rid))

    def wrapped(rid: str) -> str:
        """An unsigned assertion for the owner put first, the signed one for
        Anna after it: only what the signature covers is read."""
        good = _signed(_assertion(email="anna@acme.dev", in_response_to=rid, audience=aud))
        evil = _assertion(email="olga@acme.dev", in_response_to=rid, audience=aud)
        evil.set("ID", "_evil")
        root = etree.fromstring(base64.b64decode(_response(good, rid)))
        root.insert(2, evil)
        return base64.b64encode(etree.tostring(root)).decode()

    rid, relay = await _start(c)
    back = await _post(c, wrapped(rid), relay)
    s = (await c.post("/v1/oauth/exchange",
                      json={"code": back["axor_login"][0], "org_id": org})).json()
    assert s["user"]["email"] == "anna@acme.dev"


async def test_a_certificate_that_is_not_one_is_refused(c, app) -> None:  # noqa: ANN001
    olga = (await c.post("/v1/signup", json={"email": "olga@acme.dev", "password": PW,
                                             "org_name": "Acme"})).json()
    await app.state.store.mark_verified(olga["user"]["user_id"])
    org = olga["org"]["org_id"]
    await app.state.store.set_org_tier(org, "business", "2999-01-01")
    h = {"Authorization": f"Bearer {olga['access_token']}"}
    r = await c.put(f"/v1/orgs/{org}/sso", headers=h, json={
        "protocol": "saml", "saml_sso_url": IDP_SSO, "saml_cert": "not a cert",
        "domains": ["acme.dev"]})
    assert r.status_code == 422
    r = await c.put(f"/v1/orgs/{org}/sso", headers=h, json={
        "protocol": "saml", "saml_metadata": "<nope", "domains": ["acme.dev"]})
    assert r.status_code == 422
