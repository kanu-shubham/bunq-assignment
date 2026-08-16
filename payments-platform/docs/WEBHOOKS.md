# Webhooks — verifying signatures

Paykit sends an HTTP `POST` to your endpoint for every event you subscribe to. **Verify the
signature before you trust the payload.**

## Why this matters

Your webhook URL is a public HTTP endpoint. Anyone who learns it can send you:

```json
{ "type": "payment_intent.succeeded", "data": { "amount": 10000000 } }
```

If your integration trusts the body, you have just shipped goods for a payment that never
happened. The signature is what makes the request provably ours.

## The request

```http
POST /your-endpoint HTTP/1.1
Content-Type: application/json
Paykit-Signature: t=1772366400,v1=5257a869e7ecebeda32affa62cdca3fa51cad7e77a0e56ff536d0ce8e108d8bd
Paykit-Event-Id: evt_9xK2mNp4QrSt7vWy
Paykit-Event-Type: payment_intent.succeeded
Paykit-Delivery-Attempt: 1
User-Agent: Paykit-Webhooks/1.0

{"event_id":"evt_...","type":"payment_intent.succeeded","data":{...}}
```

## The algorithm

1. Parse `t` (unix seconds) and `v1` (hex) from `Paykit-Signature`.
2. Build the signed value: `"{t}.{raw request body}"` — the **exact bytes** you received.
3. Compute `HMAC-SHA256(signed_value, your_endpoint_secret)`, hex-encoded.
4. Compare with `v1` using a **constant-time** comparison.
5. Reject if `|now - t|` is more than 5 minutes.

> **Use the raw body.** Parse-then-re-serialise changes key order and whitespace, and the
> signature will not match. Read the bytes before any JSON middleware touches them.

### Why the timestamp is inside the signature

Without step 5, an attacker who captures one valid request can replay it forever — the body has
not changed, so the signature stays valid. And because `t` is part of the *signed value*, they
cannot refresh it without your secret. Signing the body alone is the common mistake here.

## Java

```java
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;

public boolean verify(String rawBody, String header, String secret) throws Exception {
    Long timestamp = null;
    String signature = null;
    for (String part : header.split(",")) {
        String[] kv = part.split("=", 2);
        if (kv.length != 2) continue;
        if (kv[0].trim().equals("t"))  timestamp = Long.parseLong(kv[1].trim());
        if (kv[0].trim().equals("v1")) signature = kv[1].trim();
    }
    if (timestamp == null || signature == null) return false;

    // Replay window. Also rejects far-future timestamps.
    if (Duration.between(Instant.ofEpochSecond(timestamp), Instant.now())
                .abs().compareTo(Duration.ofMinutes(5)) > 0) {
        return false;
    }

    Mac mac = Mac.getInstance("HmacSHA256");
    mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
    String expected = HexFormat.of().formatHex(
            mac.doFinal((timestamp + "." + rawBody).getBytes(StandardCharsets.UTF_8)));

    // Constant time: String.equals leaks how many leading bytes matched.
    return MessageDigest.isEqual(expected.getBytes(StandardCharsets.UTF_8),
                                 signature.getBytes(StandardCharsets.UTF_8));
}
```

The platform's own implementation is `webhook-service/.../service/WebhookSignature.java`, and
`WebhookSignatureTest` covers tampering, wrong secrets, replay and forged timestamps.

## Node.js

```javascript
const crypto = require('crypto');

// express.raw({ type: 'application/json' }) — do NOT use express.json() here.
function verify(rawBody, header, secret) {
  const parts = Object.fromEntries(header.split(',').map(p => p.split('=')));
  const timestamp = parseInt(parts.t, 10);
  if (Math.abs(Date.now() / 1000 - timestamp) > 300) return false;

  const expected = crypto.createHmac('sha256', secret)
      .update(`${timestamp}.${rawBody}`)
      .digest('hex');

  return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(parts.v1));
}
```

## Python

```python
import hashlib, hmac, time

def verify(raw_body: bytes, header: str, secret: str) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    timestamp = int(parts["t"])
    if abs(time.time() - timestamp) > 300:
        return False

    expected = hmac.new(
        secret.encode(),
        f"{timestamp}.".encode() + raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, parts["v1"])
```

---

## Responding

| Your response | What we do |
|---|---|
| any `2xx` | delivered, done |
| `410 Gone` | endpoint disabled immediately — the polite way to retire a URL |
| anything else, or a timeout | retried with backoff |

**Respond fast.** We give up after 10 seconds. Acknowledge with a `200` first and do the work
asynchronously; a slow endpoint gets retried and you will process the event twice.

## Retries

`10s → 20s → 40s → 80s → 160s → 320s → 640s → 1280s`, capped at one hour, with **±50% jitter**
and 8 attempts total.

The jitter is not cosmetic. If your server goes down while 10,000 deliveries are pending, an
un-jittered schedule sends all 10,000 retries at the same instant — repeatedly — which is a
self-inflicted DDoS on a server that is already struggling.

After 50 consecutive failures an endpoint is auto-disabled. Re-enable it with
`POST /v1/webhook_endpoints/{id}/enable`.

## Be idempotent

You **will** receive the same event twice. Kafka delivers at least once, the outbox can re-send
after a crash, and a delivery that times out after your server committed will be retried.

Store `Paykit-Event-Id` with a unique constraint and ignore duplicates:

```sql
INSERT INTO processed_webhooks (event_id) VALUES (?) ON CONFLICT DO NOTHING;
```

That is precisely what `ledger-service` and `webhook-service` do internally — see
`ProcessedEvent` in either.

## Ordering

Events for **one payment** arrive in order (they share a Kafka partition key). Events for
different payments have no ordering guarantee between them, and correctly so — they are
independent. Do not build logic that depends on cross-payment ordering.

## Testing locally

The compose stack includes an echo server:

```bash
curl -X POST http://localhost/v1/webhook_endpoints \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"url":"http://webhook-receiver:8080/hook","enabled_events":["*"]}'

docker compose logs -f webhook-receiver
```
