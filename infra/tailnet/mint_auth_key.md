# Minting a worker auth key (orchestrator spec)

This is the contract the orchestrator implements when a worker exchanges its
one-time **enrollment token** (design §8). Two HTTPS calls are made to the
Tailscale API:

1. Trade the OAuth client credentials for a short-lived access token.
2. Use that access token to create one auth key that is single-use,
   pre-authorized, non-ephemeral and tagged.

The orchestrator returns the key's `key` value to the worker once and never
stores or logs it.

## Configuration (orchestrator environment)

| Env var | Required | Example | Meaning |
| --- | --- | --- | --- |
| `TAILSCALE_OAUTH_CLIENT_ID` | yes | `k1a2b3c4d5CNTRL` | Client ID of the OAuth client created in README step 6 |
| `TAILSCALE_OAUTH_CLIENT_SECRET` | yes | `tskey-client-…` | Its secret. Case-sensitive. Only shown once, at creation |
| `TAILSCALE_TAILNET` | no | `-` (default) | Tailnet ID used in API paths. `-` means "the tailnet that owns this token" |
| `TAILSCALE_API_BASE` | no | `https://api.tailscale.com` (default) | Lets tests point the client at a fake server |

## Call 1: OAuth client-credentials token exchange

```
POST {TAILSCALE_API_BASE}/api/v2/oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&client_id=<TAILSCALE_OAUTH_CLIENT_ID>&client_secret=<TAILSCALE_OAUTH_CLIENT_SECRET>
```

* The endpoint follows the OAuth 2.0 client-credentials grant
  (RFC 6749 §4.4.2). Tailscale's own curl example sends only `client_id` and
  `client_secret`. Sending `grant_type=client_credentials` as well is correct
  per the RFC, and it is what Tailscale's Go example sends through
  `golang.org/x/oauth2/clientcredentials`.
  <https://tailscale.com/kb/1215/oauth-clients>
* Optional form fields `scope` and `tags` (space-delimited) can narrow the
  token. The orchestrator does not send them because the client has only
  one scope and one tag anyway.

Example:

```sh
curl -sS https://api.tailscale.com/api/v2/oauth/token \
  -d "grant_type=client_credentials" \
  -d "client_id=${TAILSCALE_OAUTH_CLIENT_ID}" \
  -d "client_secret=${TAILSCALE_OAUTH_CLIENT_SECRET}"
```

Expected `200 OK`:

```json
{
  "access_token": "tskey-api-0123456789ABCDEF-...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "auth_keys"
}
```

* `expires_in` is always 3600: access tokens last exactly 1 hour and the
  lifetime cannot be changed.
  <https://tailscale.com/kb/1215/oauth-clients>
* **Caching:** keep the token in memory and reuse it until
  `now + expires_in - 60 s`, then fetch a new one. Never write it to disk.
* `401` here means the client ID or secret is wrong, or the client was
  revoked. Surface it as a configuration error, not a retryable one.

## Call 2: create the auth key

```
POST {TAILSCALE_API_BASE}/api/v2/tailnet/{TAILSCALE_TAILNET}/keys
Authorization: Bearer <access_token>
Content-Type: application/json
```

Body for a **worker**:

```json
{
  "keyType": "auth",
  "description": "baja worker enr-0042",
  "capabilities": {
    "devices": {
      "create": {
        "reusable": false,
        "ephemeral": false,
        "preauthorized": true,
        "tags": ["tag:worker"]
      }
    }
  },
  "expirySeconds": 3600
}
```

For the **studio** role, use `"tags": ["tag:studio"]` and change the
description. Nothing else changes.

Field by field (source: Tailscale's OpenAPI schema,
`https://api.tailscale.com/api/v2?outputOpenapiSchema=true`, operation
`createKey` and schema `KeyCapabilities`):

| Field | Value | Why |
| --- | --- | --- |
| `keyType` | `"auth"` | A machine auth key. This is the default, but send it explicitly |
| `description` | `"baja worker enr-<id>"` | Ties the key to the enrollment record (attribution). **At most 50 characters: letters, digits, hyphens and spaces only.** No `:`, `_`, `/` or `@` |
| `capabilities.devices.create.reusable` | `false` | Single-use: the key registers one device and is then spent |
| `capabilities.devices.create.ephemeral` | `false` | The device survives going offline. Volunteers' PCs keep their identity, IP and hostname between sessions |
| `capabilities.devices.create.preauthorized` | `true` | Skips device approval, so the worker joins with no admin action |
| `capabilities.devices.create.tags` | `["tag:worker"]` or `["tag:studio"]` | Required for keys minted by an OAuth client. Allowed because `tag:enroller` (the client's tag) owns both in `acl.hujson` |
| `expirySeconds` | `3600` | How long the *unused key* stays valid. The worker uses it within seconds. This does **not** affect the device: tagged devices have node-key expiry disabled |

Notes:

* The API default for `expirySeconds` is 90 days. The admin console only
  offers 1–90 days, and the API documentation does not state a minimum. If
  the API rejects `3600`, use `86400`.
  <https://tailscale.com/kb/1085/auth-keys>
* Tag rule: "Auth keys created with this credential must have those exact
  tags, or tags owned by the credential's tags."
  <https://tailscale.com/kb/1623/trust-credentials>
* A key minted with an OAuth token is owned by the tailnet, not by a user,
  so the joined device does not disappear if a user is removed.

Example:

```sh
curl -sS "https://api.tailscale.com/api/v2/tailnet/-/keys" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
        "keyType": "auth",
        "description": "baja worker enr-0042",
        "capabilities": {"devices": {"create": {
          "reusable": false, "ephemeral": false, "preauthorized": true,
          "tags": ["tag:worker"]}}},
        "expirySeconds": 3600
      }'
```

Expected `200 OK` (shape from the OpenAPI `Key` schema):

```json
{
  "id": "kXXXX43CNTRL",
  "key": "tskey-auth-kXXXX43CNTRL-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "keyType": "auth",
  "created": "2026-10-25T14:02:11Z",
  "expires": "2026-10-25T15:02:11Z",
  "expirySeconds": 3600,
  "capabilities": {
    "devices": {
      "create": {
        "reusable": false,
        "ephemeral": false,
        "preauthorized": true,
        "tags": ["tag:worker"]
      }
    }
  },
  "description": "baja worker enr-0042",
  "userId": ""
}
```

* `key` is the secret the worker passes to `tailscale up --auth-key`. The
  full key can never be fetched again after this response.
* `id` is not secret. **Store `id` on the enrollment record**, so every join
  can be traced back to an enrollment token and the key can be revoked by
  ID.
* `userId` is empty because trust credentials create the key, not a user.

### Errors to handle

| Status | Likely cause | Orchestrator behaviour |
| --- | --- | --- |
| `400` | Malformed body, invalid description characters | Bug: log the response body (it contains no secret), fail the enrollment |
| `401` | Access token expired or revoked | Drop the cached token, redo call 1 once, retry once |
| `403` | Client lacks the `auth_keys` scope, or the requested tag is not owned by `tag:enroller` | Configuration error: fail the enrollment, alert the admin |
| `429` / `5xx` | Rate limit or Tailscale outage | Retry with backoff. The enrollment token stays unspent |

**Spend the enrollment token only after call 2 succeeds**, so a Tailscale
failure does not burn the volunteer's token.

## Revoking a key (optional, for cleanup and tests)

```
DELETE {TAILSCALE_API_BASE}/api/v2/tailnet/{TAILSCALE_TAILNET}/keys/{id}
Authorization: Bearer <access_token>
```

This needs the same `auth_keys` scope. Revoking a key does **not** remove
devices that already joined with it.
<https://tailscale.com/kb/1085/auth-keys>

## What the orchestrator returns to the worker

The design fixes the content of the enrollment-exchange response; its exact
schema belongs to the API contract in `shared/`. It contains at least:

* `tailscale_auth_key`: the `key` value above
* `tailscale_hostname`: the hostname the worker passes to `--hostname`
  (e.g. `baja-w-0042`, see `worker_join.md`)
* `worker_token`: the per-worker bearer token (design §13)

## Test double

The test suite never calls Tailscale (design §13). A fake that implements
these two endpoints and checks the following is enough:

* call 1 is form-encoded and carries the configured ID and secret;
* call 2 carries `Authorization: Bearer <token from call 1>`;
* call 2's body has `reusable=false`, `ephemeral=false`,
  `preauthorized=true` and exactly one tag from
  `{tag:worker, tag:studio}`;
* `description` matches `^[A-Za-z0-9 -]{1,50}$`.
