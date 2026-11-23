# Single sign-on and SCIM — the admin's guide

On the **Business** plan and above, a workspace can sign its people in through
the company's identity provider (OpenID Connect or SAML 2.0) and let that
provider create and remove them (SCIM 2.0). Everything here is set in
**Settings → workspace**, below the members list, by the workspace's owner or
an admin.

- [Before you start](#before-you-start)
- [OpenID Connect](#openid-connect) — [Okta](#okta-oidc) · [Entra ID](#entra-id-oidc) · [Google Workspace](#google-workspace-oidc)
- [SAML 2.0](#saml-20) — [Okta](#okta-saml) · [Entra ID](#entra-id-saml) · [Google Workspace](#google-workspace-saml)
- [Turning passwords off](#turning-passwords-off)
- [How long a sign-in lasts](#how-long-a-sign-in-lasts)
- [SCIM provisioning](#scim-provisioning) — [Okta](#okta-scim) · [Entra ID](#entra-id-scim)
- [What happens when](#what-happens-when)
- [When something does not work](#when-something-does-not-work)

## Before you start

1. **The plan.** The workspace's licence must be Business or above. Paste it
   in Settings → Billing. A self-hosted install can set it as `AXOR_LICENSE`
   instead. Below Business, the SSO section says so and offers the plan.
2. **A public address for the login service.** Self-hosted installs set it
   in `.env`: `AXOR_IDENTITY_PUBLIC_URL`, which defaults to
   `https://$CP_HOST/identity`. Without it, the section says "needs this
   service's public address". On the hosted service it is already set.
3. **Your domain.** SSO covers the e-mail domains you list, such as
   `acme.com`. You can list only a domain proved by the **verified** e-mail
   address of the workspace's owner or one of its admins. Public mail domains
   (gmail.com, outlook.com and the like) are refused. If the field refuses
   your domain, verify your own address first (Settings → workspace →
   account).

Sign-in starts the same way whatever the protocol: a person types their work
address on the login form, the form sees that the domain uses single sign-on,
and it sends them to the provider.

## OpenID Connect

The section shows a **sign-in redirect URL**:
`<public url>/v1/sso/callback`. Register it with the provider. Then give
Axor three things back: the provider's **issuer**, the app's **client id** and
its **client secret**.

Axor asks for the scopes `openid email profile` and uses the authorization
code flow with PKCE and a nonce. The person's address comes from the token's
`email` claim, or from `preferred_username` if there is no `email`. An address
the provider marks `email_verified: false` is refused.

### Okta (OIDC)

1. Admin Console → **Applications → Create App Integration** → **OIDC -
   OpenID Connect** → **Web Application**.
2. **Sign-in redirect URIs**: paste Axor's redirect URL. Grant type:
   **Authorization Code**. Leave the sign-out redirect empty.
3. **Assignments**: the people or groups who should get in.
4. Copy the **Client ID** and **Client secret**.
5. Issuer: `https://<your-org>.okta.com`. If you use a custom authorization
   server, it is `https://<your-org>.okta.com/oauth2/default`.

### Entra ID (OIDC)

1. Entra admin center → **App registrations → New registration**.
   Supported account types: *this organizational directory only*. Redirect
   URI: platform **Web**, then paste Axor's redirect URL.
2. **Certificates & secrets → New client secret**. Copy its **Value** (not
   its ID).
3. **Token configuration → Add optional claim → ID → `email`**. Without it,
   Entra sends only `preferred_username` (the UPN). That works too when UPNs
   are e-mail addresses at your domain.
4. Issuer: `https://login.microsoftonline.com/<tenant id>/v2.0`. The tenant
   id is on the app's Overview page.

### Google Workspace (OIDC)

1. Google Cloud console, in a project owned by your Workspace organization →
   **APIs & Services → OAuth consent screen** → User type **Internal**.
2. **Credentials → Create credentials → OAuth client ID** → **Web
   application**. Authorized redirect URI: Axor's redirect URL.
3. Copy the client ID and secret. Issuer: `https://accounts.google.com`.

"Internal" keeps people outside your Workspace off the consent screen. Axor's
own domain check is a second guard.

## SAML 2.0

Choose **SAML 2.0** in the section. It shows what to give the provider:

| | |
|---|---|
| Entity ID (audience) | `<public url>/v1/saml/<workspace id>` |
| ACS (reply) URL | `<public url>/v1/saml/acs` |
| Our metadata | `<public url>/v1/saml/<workspace id>/metadata` |
| Name ID | the person's e-mail address |

Then paste **the provider's metadata XML** into Axor. Instead of the
metadata, you can fill in its SSO URL (HTTP-Redirect binding), its entity id
and its signing certificate (PEM).

What Axor expects:

- The **response or the assertion is signed**, with RSA-SHA256 or stronger.
  Axor reads only what the signature covers.
- The address is in the Name ID (format *emailAddress*) or in an attribute
  named `email`, `mail` or the standard e-mail claim URI.
- The assertion's audience is Axor's entity ID, and its recipient is the ACS
  URL. It answers the request Axor sent (`InResponseTo`) and is inside its
  time window (3 minutes' skew allowed).

Not supported yet: signed AuthnRequests, encrypted assertions, and sign-in
started from the provider's dashboard (IdP-initiated). Start from Axor's
login form.

### Okta (SAML)

1. **Applications → Create App Integration → SAML 2.0**.
2. **Single sign-on URL**: the ACS URL; tick *Use this for Recipient URL and
   Destination URL*. **Audience URI (SP Entity ID)**: the entity ID.
3. **Name ID format**: EmailAddress. **Application username**: Email.
4. Finish. Then **Sign On → SAML 2.0 → Metadata details → Metadata URL**:
   open it and paste the XML into Axor.

### Entra ID (SAML)

1. **Enterprise applications → New application → Create your own
   application** → *Integrate any other application you don't find in the
   gallery*.
2. **Single sign-on → SAML**. Basic SAML Configuration: **Identifier (Entity
   ID)** is the entity ID; **Reply URL** is the ACS URL.
3. **Attributes & Claims → Unique User Identifier (Name ID)**: source
   attribute `user.mail` (or `user.userprincipalname` if UPNs are your
   addresses), format *Email address*.
4. **SAML Certificates → Federation Metadata XML → Download**. Paste the
   file into Axor. Entra signs the assertion by default, which is what Axor
   expects.
5. **Users and groups**: assign the people who should get in.

### Google Workspace (SAML)

1. Admin console → **Apps → Web and mobile apps → Add app → Add custom SAML
   app**.
2. **Google Identity Provider details**: **Download metadata**. You will
   paste it into Axor.
3. **Service provider details**: ACS URL and Entity ID from Axor. Name ID
   format **EMAIL**, Name ID **Basic Information → Primary email**.
4. **User access**: turn the app **ON** for the organizational units that
   should get in. It can take a few minutes to apply.

## Turning passwords off

With **only through SSO** ticked, people at the listed domains can no longer
sign in with a password, or with GitHub or Google sign-in. The **owner keeps
their password** so a broken provider never locks the workspace out. Keep that
password somewhere safe.

The **default role** is the role a person gets the first time they sign in
through the provider without having been invited. It is `member` unless you
change it; `viewer` is the cautious choice. If someone was invited first, the
account they sign in with through SSO is the same account (matched by
address).

## How long a sign-in lasts

Under **Sign-ins**, choose how long a sign-in lasts: 8 hours to 2 weeks. After
that, everyone signs in again, through SSO if it is on. The time counts from
the sign-in, however often the session refreshed in between. Without a
setting, the deployment's own limit applies (14 days by default). This is on
the Business plan.

**Sign everyone else out** ends every sign-in to the workspace except yours.
Use it when a laptop goes missing or someone leaves in a hurry. A page that is
already open stops working within the access token's lifetime (15 minutes).
This is on every plan.

## SCIM provisioning

With SCIM, the provider creates people in the workspace when they are
assigned to the app and removes them when they are unassigned or deactivated.

1. In **Provisioning (SCIM)**, choose **make a token**. It is shown once.
   Making a new one stops the old one.
2. Give the provider:
   - the base URL `<public url>/scim/v2`;
   - the token, as an HTTP header `Authorization: Bearer <token>`.
3. Users are matched on `userName`, which must be the person's e-mail address.

When the provider pushes groups, they appear under the token. **Map a group
to a role** (viewer, member or admin) and its people get that role. Someone in
several mapped groups gets the highest; someone in none keeps the default
role. The owner's role never changes.

### Okta (SCIM)

1. On the SAML or OIDC app from above: **General → App Settings → Edit →
   Provisioning: SCIM** → Save.
2. **Provisioning → Integration → Edit**:
   - SCIM connector base URL: `<public url>/scim/v2`;
   - Unique identifier field for users: `userName`;
   - Supported provisioning actions: *Push New Users*, *Push Profile
     Updates*, *Push Groups*;
   - Authentication Mode: **HTTP Header**, Bearer: the token.
   **Test Connector Configuration**, then Save.
3. **Provisioning → To App → Edit**: enable *Create Users*, *Update User
   Attributes*, *Deactivate Users*.
4. **Push Groups**: add the groups you will map to roles in Axor.

### Entra ID (SCIM)

1. On the enterprise application: **Provisioning → Get started**,
   Provisioning Mode **Automatic**.
2. **Tenant URL**: `<public url>/scim/v2`. **Secret Token**: the token. **Test
   Connection**.
3. **Mappings → Provision Microsoft Entra ID Users**: map `userName` to
   `mail` (or to `userPrincipalName` if UPNs are your addresses).
4. **Start provisioning**. Entra runs a cycle about every 40 minutes. To try
   one person now, use **Provision on demand**.

Google Workspace does not provision to custom apps over SCIM. With Google,
people join through SSO the first time they sign in, and an admin removes
them in Settings → workspace.

## What happens when

| The provider… | In Axor |
|---|---|
| creates (assigns) a person | they become a member with the default role, or their group's role |
| deactivates, unassigns or deletes them | their membership goes; their sign-ins to this workspace end at once; the keys their machines report with are revoked |
| moves them between mapped groups | their role follows |
| renames them | the name shows in Axor |
| changes their address (`userName`) | their sign-in address changes, if the new one is at your domains, nobody else's, and they are in no other workspace |
| tries to deactivate the owner | refused (409): ownership moves in Axor, by hand |

Every change lands in the workspace's **audit trail** (Settings → audit),
credited to *your identity provider (SCIM)*: who was added, removed or
renamed, whose role a group changed. The monthly report counts them under
changes to people and sign-in. Changes an admin makes by hand (members,
invitations, the SSO settings, SCIM tokens, group mappings) are there too,
under the admin's name.

## When something does not work

The login form shows the provider's or Axor's reason in plain words. The
common ones:

| It says | Usually |
|---|---|
| *is not at a domain this workspace signs in* | the provider sent an address at another domain: check the Name ID, the `email` claim, or the domains listed |
| *the assertion is for another service* | the provider's Audience / Entity ID is not Axor's entity ID |
| *the response's signature does not hold* | the certificate pasted is not the one signing now (rotated?): paste the current metadata again |
| *the assertion was not issued for this sign-in* | the sign-in was started from the provider's dashboard (not supported yet), or the Recipient / Reply URL is not Axor's ACS URL |
| *the provider did not say who signed in* | no e-mail address in the Name ID or attributes |
| *needs this service's public address* | self-hosted: `AXOR_IDENTITY_PUBLIC_URL` is not set |
| SCIM test fails with 401 | the token was replaced, or the header is not `Bearer` |
