# 10 — The whole codebase, explained for a Java beginner

Every file in `partner-send/`, in an order that builds up. You know programming — you wrote a
state machine, a focus trap and a DI seam in the bunq widget. So this explains *Java*, not
programming, and maps back to JavaScript/TypeScript wherever there's a match.

**How to use it:** open the file being discussed next to this. Concepts are explained the
first time they appear and then used freely, so read top to bottom the first time.

---

# Part 0 — How to read any Java file

Every Java file has the same four-part shape.

```java
package com.example.prep.partnersend.domain;      // 1. where this file lives

import java.util.Currency;                        // 2. what it borrows

/** Doc comment. */                               // 3. documentation
public record Money(long minorUnits, ...) {       // 4. exactly one public type
    ...
}
```

**1. `package`** — the folder path, dot-separated. `com.example.prep.partnersend.domain` means
the file sits in `src/main/java/com/example/prep/partnersend/domain/`. The folder structure
*must* match the package name; the compiler enforces it. Closest JS equivalent: the module path.

**2. `import`** — like `import { Currency } from 'java.util'`. Java has no default export and
no renaming; you import a type by its full name and then use the short name. Anything in
`java.lang` (String, Integer, Exception…) is imported automatically.

**3. `/** ... */`** — a *Javadoc* comment. `//` and `/* */` are ordinary comments; `/** */`
attaches documentation to the thing below it, which IDEs show on hover. The `{@code x}` and
`<p>` inside are formatting markup.

**4. One public type per file**, and its name must match the filename. `Money.java` must
contain `public class Money` (or `record`/`interface`/`enum`).

### The words that appear in every declaration

```java
public static final String TOPIC = "transfer-events";
   │      │      │      │      │
   │      │      │      │      └── the name
   │      │      │      └───────── the type — always before the name in Java
   │      │      └──────────────── can't be reassigned (like `const`)
   │      └─────────────────────── belongs to the class, not to an instance
   └────────────────────────────── anyone can see it
```

| Word | Meaning | TypeScript equivalent |
|---|---|---|
| `public` | visible everywhere | `export` |
| `private` | visible only inside this class | `private` |
| `protected` | visible to this class and subclasses | `protected` |
| *(nothing)* | visible inside this **package** | no equivalent |
| `static` | belongs to the class itself, not an instance | `static` |
| `final` | cannot be reassigned after it's set | `const` / `readonly` |
| `void` | this method returns nothing | `: void` |

### Types come first, and there are two kinds

```java
long minorUnits = 1234;        // TS: let minorUnits: number = 1234
String code = "GBP";           // TS: let code: string = "GBP"
var total = amount.plus(fee);  // TS: const total = ... (type inferred)
```

- **Primitives** — `long`, `int`, `double`, `boolean`, `char`. Lowercase. Hold a raw value,
  can never be `null`.
- **Objects** — `String`, `Money`, `List`. Uppercase. Hold a reference, can be `null`.

`long` is a 64-bit integer. We use it for money because it's exact and huge (±9.2 quintillion).
`int` is 32-bit and would overflow at about £21 million in pence.

### Generics — the angle brackets

```java
List<String> names;              // a list of Strings
Optional<Transfer> maybeTransfer; // a Transfer that might be absent
Map<String, Object> props;        // keys are Strings, values are Objects
```

Identical to TypeScript's `Array<string>`. Unlike TypeScript, Java's generics are **checked at
runtime boundaries too** — you can't sneak the wrong type in with an `any`.

### Annotations — the `@` things

```java
@Service
@Transactional
public class TransferService { }
```

Annotations are **metadata**. On their own they do nothing; a framework reads them at startup
and behaves differently. `@Service` tells Spring "create one of these and manage it".
`@Transactional` tells Spring "wrap this method in a database transaction".

The closest thing you've used is a decorator (`@Component` in Angular, or TS experimental
decorators). There's a full glossary of every annotation in this project in Part 9.

### Lambdas and method references

```java
list.stream().map(x -> x * 2).toList();   // same as JS: list.map(x => x * 2)

event -> log.info("...")                  // a one-argument lambda
() -> doSomething()                       // a no-argument lambda

ResilientPartnerBankClient::isRetryable   // "method reference"
// identical to: t -> ResilientPartnerBankClient.isRetryable(t)
```

`::` is just shorthand for a lambda that calls one method.

---

# Part 1 — What the program actually does

Before any file, the plain-English story.

A partner bank sends us an HTTP request: *"please send £250 to this account, and here's a
unique key so you don't do it twice if I ask again."*

We:

1. **Check the key.** If we've seen it before, return the same answer as last time and do
   nothing else.
2. **Save the transfer** to the database, and *in the same transaction* save an "event" row
   saying it happened.
3. **Answer immediately** with `202 Accepted` — "recorded, not yet settled".
4. **Later, in the background**, a poller picks up the event row and publishes it to Kafka.
5. **A consumer** reads it from Kafka and updates a running total per partner.
6. Separately, when we do call a partner bank, the call is wrapped in protections so a slow or
   broken partner can't take our whole service down.

```
   HTTP POST                            ┌─────────────────┐
       │                                │ idempotency_    │
       ▼                                │ record          │
 TransferController ───── checks ──────▶└─────────────────┘
       │
       ▼
 TransferService ──── one transaction ──▶ transfer  +  outbox_event
       │
       │ (returns 202 immediately)
       ▼
 OutboxPublisher (every 500ms) ──▶ Kafka ──▶ TransferEventProcessor ──▶ partner_volume
```

Every file below is one box in that picture, or a helper for one.

---

# Part 2 — The domain layer

`src/main/java/com/example/prep/partnersend/domain/`

The "domain" is the business concepts — money, transfers, their lifecycle — with no knowledge
of HTTP, databases or Kafka.

## `Money.java`

```java
public record Money(long minorUnits, Currency currency) implements Comparable<Money> {
```

**`record`** is the important word. It's Java's immutable data class, added in Java 16. This
one line generates:

- a constructor: `new Money(1234, gbp)`
- accessor methods: `money.minorUnits()`, `money.currency()` — **note: no `get` prefix**
- `equals()` and `hashCode()` — so two Moneys with the same values are `.equals()`
- `toString()`

In TypeScript you'd write `type Money = { minorUnits: number; currency: Currency }` — but a
record also gives you real value equality, which JS objects never have (`{a:1} !== {a:1}`).

**`implements Comparable<Money>`** — a promise: "this class provides a `compareTo` method, so
Java can sort it." An interface in Java is a contract, exactly like a TypeScript `interface`,
except you must *declare* that you implement it. TypeScript is structural (matching shape is
enough); Java is nominal (you must say so).

```java
    public Money {
        Objects.requireNonNull(currency, "currency");
    }
```

A **compact constructor** — a record-only feature. It runs before the fields are assigned, so
it's where validation goes. `Objects.requireNonNull` throws if the value is null.

```java
    public static Money of(String currencyCode, long minorUnits) {
        return new Money(minorUnits, Currency.getInstance(currencyCode));
    }
```

A **static factory method**. `static` means you call it on the class, not an instance:
`Money.of("GBP", 1234)`. It's nicer to read than `new Money(1234, Currency.getInstance("GBP"))`.
This is a very common Java idiom — you'll see `of`, `from`, `parse`, `valueOf` everywhere.

```java
    public static Money parse(String currencyCode, String decimalAmount) {
        Currency currency = Currency.getInstance(currencyCode);
        int scale = currency.getDefaultFractionDigits();
        BigDecimal value = new BigDecimal(decimalAmount);
        BigDecimal scaled = value.setScale(scale, RoundingMode.UNNECESSARY);
        return new Money(scaled.movePointRight(scale).longValueExact(), currency);
    }
```

Line by line:

- `getDefaultFractionDigits()` — how many decimal places this currency has. GBP → 2, JPY → 0,
  KWD → 3. This is why hard-coding `× 100` is a bug.
- `new BigDecimal("12.34")` — an *exact* decimal number. Built from a **String**, never from a
  `double`, because a double is already wrong before you start.
- `setScale(2, RoundingMode.UNNECESSARY)` — "make this exactly 2 decimal places, and **throw**
  if that would require rounding." So `"12.345"` in GBP raises an error instead of silently
  becoming `12.34` or `12.35`.
- `movePointRight(2)` — `12.34` → `1234`.
- `longValueExact()` — convert to `long`, throwing if it wouldn't fit.

```java
    public Money plus(Money other) {
        requireSameCurrency(other);
        return new Money(Math.addExact(minorUnits, other.minorUnits), currency);
    }
```

Returns a **new** Money rather than mutating — same instinct as returning new state from a
React reducer. `Math.addExact` throws on overflow instead of silently wrapping round to a
negative number, which is the behaviour you want when the number is somebody's money.

Note `other.minorUnits` with no parentheses — inside the class you can read the field directly;
outside you must call the accessor `other.minorUnits()`.

## `TransferStatus.java`

```java
public enum TransferStatus {
    RECEIVED, VALIDATED, FUNDED, SUBMITTED, SETTLED, FAILED, RETURNED;
```

An **enum** is a fixed set of named constants. Closest TS equivalent is a string union type
`type Status = 'RECEIVED' | 'VALIDATED' | ...`, but a Java enum is a real class: it can have
fields, methods and behaviour.

```java
    private static final Map<TransferStatus, Set<TransferStatus>> ALLOWED;

    static {
        Map<TransferStatus, Set<TransferStatus>> m = new EnumMap<>(TransferStatus.class);
        m.put(RECEIVED, EnumSet.of(VALIDATED, FAILED));
        ...
        ALLOWED = Collections.unmodifiableMap(m);
    }
```

**`static { ... }`** is a *static initialiser block* — code that runs once, when the class is
first loaded. It's how you build a complex `static final` value that a single expression can't
express.

`Map` is Java's dictionary (JS `Map`). `Set` is a collection with no duplicates.
`EnumMap`/`EnumSet` are optimised versions for enum keys. `unmodifiableMap` makes it read-only,
so nobody can add a transition at runtime.

So `ALLOWED` is: *for each status, the set of statuses it may move to.*

```java
    public boolean canTransitionTo(TransferStatus next) {
        return ALLOWED.get(this).contains(next);
    }
```

`this` is the current enum constant. This is the same idea as the `switch` in your
`feedbackMachine.ts` reducer — a closed set of legal moves — just expressed as a lookup table.

## `Transfer.java`

This is the first **entity** — a class that maps to a database table.

```java
@Entity
@Table(name = "transfer")
public class Transfer {

    @Id
    @Column(name = "id", nullable = false, updatable = false, length = 36)
    private String id;
```

- `@Entity` — "this class is a database table". Read by JPA/Hibernate, the library that turns
  objects into SQL.
- `@Table(name = "transfer")` — the table name.
- `@Id` — this field is the primary key.
- `@Column(...)` — column settings. `updatable = false` means once written, never changed by
  an UPDATE.

**Why a `class` and not a `record`?** Because JPA needs to create the object empty and fill in
the fields afterwards, and records are immutable by design. Entities are the one place you
accept mutability.

```java
    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 32)
    private TransferStatus status;
```

`@Enumerated(EnumType.STRING)` stores the enum as the text `"RECEIVED"`. The alternative,
`ORDINAL`, stores its position number — and then reordering the enum silently corrupts every
existing row. Always use `STRING`.

```java
    @Version
    @Column(name = "version", nullable = false)
    private long version;
```

**Optimistic locking.** Hibernate adds `WHERE version = ?` to every UPDATE and increments the
number. If two processes load the same row and both save, the second one's UPDATE matches zero
rows and throws. Nobody holds a lock; the loser just retries with fresh data.

```java
    protected Transfer() {
    }
```

An empty constructor that does nothing, marked `protected` so application code won't call it.
JPA requires it — it creates the object with no arguments, then sets the fields by reflection.

```java
    public static Transfer receive(String partnerId, String partnerReference,
                                   Money amount, Instant now) {
        if (!amount.isPositive()) {
            throw new IllegalArgumentException("Transfer amount must be positive, got " + amount);
        }
        return new Transfer(UUID.randomUUID().toString(), partnerId, partnerReference, amount, now);
    }
```

`Instant` is a moment in time in UTC (Java's `Date` replacement — always use `java.time`).
`UUID.randomUUID()` generates a random unique id. `throw new X(...)` is the same as JS `throw`.

```java
    public void transitionTo(TransferStatus next, Instant now) {
        if (!status.canTransitionTo(next)) {
            throw new IllegalTransitionException(id, status, next);
        }
        this.status = next;
        this.updatedAt = now;
    }
```

**The only way status ever changes.** Every illegal move in the whole system funnels through
this one `if`. That's what makes a late duplicate webhook a loud, testable exception instead of
silent corruption.

## `IllegalTransitionException.java`

```java
public class IllegalTransitionException extends RuntimeException {
```

`extends` = inheritance. `RuntimeException` makes this an **unchecked** exception: callers may
ignore it, and it propagates automatically. (A *checked* exception — extending `Exception` —
must be declared with `throws` and handled by every caller. Modern Java avoids them because
they don't work with lambdas.)

```java
    private final transient TransferStatus from;
```

`transient` means "don't serialise this field". Exceptions are technically serialisable; this
just silences a compiler warning.

## `TransferRepository.java`

```java
public interface TransferRepository extends JpaRepository<Transfer, String> {
    Optional<Transfer> findByPartnerIdAndPartnerReference(String partnerId, String partnerReference);
}
```

This is the most magical thing in Spring, so it's worth pausing on.

**You write an interface with no implementation, and Spring writes the implementation at
startup.** `JpaRepository<Transfer, String>` means "a repository of `Transfer` entities whose
id is a `String`", and it gives you `save`, `findById`, `findAll`, `count`, `deleteAll` free.

The method above isn't inherited — Spring **parses the method name** and generates the query:
`findBy` + `PartnerId` + `And` + `PartnerReference` becomes
`SELECT * FROM transfer WHERE partner_id = ? AND partner_reference = ?`.

**`Optional<Transfer>`** is Java's "might not be there" wrapper — the language's answer to
`null`. You can't accidentally use it as a Transfer; you must unwrap it:

```java
transfers.findById(id).orElseThrow(() -> new TransferNotFoundException(id));
transfers.findById(id).ifPresent(t -> ...);       // do this if present
transfers.findById(partnerId).orElseGet(() -> new PartnerVolume(partnerId));  // or make one
```

---

# Part 3 — The web layer

`api/`

## `CreateTransferRequest.java`

```java
public record CreateTransferRequest(
        @NotBlank(message = "partnerReference is required")
        String partnerReference,

        @Pattern(regexp = "\\d{1,18}", message = "amountMinorUnits must be a positive integer string")
        String amountMinorUnits,
        ...
) {}
```

The shape of the incoming JSON. Jackson (the JSON library) maps JSON fields to record
components by name automatically.

`@NotBlank` and `@Pattern` are **Bean Validation** annotations. They do nothing by themselves —
they activate when a controller parameter is marked `@Valid`. `\\d{1,18}` is the regex `\d{1,18}`
(1–18 digits); the backslash is doubled because `\` is an escape character in Java strings.

Note `amountMinorUnits` is a **String**, not a number — because JSON numbers become doubles in
most parsers and would corrupt the value.

## `TransferController.java`

```java
@RestController
@RequestMapping("/v1/partners/{partnerId}/transfers")
public class TransferController {
```

- `@RestController` — "this class handles HTTP requests, and return values become JSON".
- `@RequestMapping` — the base URL for every method. `{partnerId}` is a path variable.

```java
    private final TransferService transferService;
    private final IdempotencyService idempotency;
    private final ObjectMapper objectMapper;

    public TransferController(TransferService transferService,
                              IdempotencyService idempotency,
                              ObjectMapper objectMapper) {
        this.transferService = transferService;
        ...
    }
```

**Constructor injection** — the single most important Spring pattern. We never call
`new TransferService(...)`. Spring sees the constructor, finds a managed object of each type,
and passes them in. This is dependency injection, the same idea as passing `submitFeedback` as
a prop into your feedback widget instead of importing it — it makes the class testable.

`private final` means these are set once in the constructor and never change.

```java
    @PostMapping(consumes = MediaType.APPLICATION_JSON_VALUE,
                 produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> createTransfer(
            @PathVariable String partnerId,
            @RequestHeader("Idempotency-Key") String idempotencyKey,
            @Valid @RequestBody CreateTransferRequest request)
            throws JsonProcessingException {
```

Each annotation binds one part of the HTTP request to one parameter:

| Annotation | Binds |
|---|---|
| `@PathVariable` | `{partnerId}` from the URL |
| `@RequestHeader("Idempotency-Key")` | that HTTP header |
| `@RequestBody` | the JSON body, parsed into the record |
| `@Valid` | run the `@NotBlank`/`@Pattern` rules; reject with 400 if they fail |

`ResponseEntity<String>` lets us control status code and headers, not just the body.
`throws JsonProcessingException` declares a *checked* exception this method may throw.

```java
        String canonical = objectMapper.writeValueAsString(request);
        var claim = idempotency.claim(partnerId, idempotencyKey, canonical);
```

We re-serialise the parsed object back to JSON. That "canonicalises" it: whitespace and key
order in the raw request can't make two identical requests look different when hashed.

```java
        if (claim instanceof IdempotencyService.Claim.Replay replay) {
            return ResponseEntity.status(replay.status())
                    .header("Idempotent-Replay", "true")
                    .body(replay.body());
        }
```

**`instanceof` with a pattern variable** (Java 16+). It checks the type *and* declares `replay`
already cast, in one step. TypeScript does the same thing with narrowing:
`if (claim.kind === 'replay') { claim.body }`.

```java
        String claimId = ((IdempotencyService.Claim.Acquired) claim).id();
        try {
            Money amount = new Money(Long.parseLong(request.amountMinorUnits()),
                                     Currency.getInstance(request.currency()));
            Transfer transfer = transferService.acceptTransfer(partnerId,
                                     request.partnerReference(), amount);

            String body = objectMapper.writeValueAsString(TransferResponse.from(transfer));
            idempotency.complete(claimId, HttpStatus.ACCEPTED.value(), body);

            return ResponseEntity.accepted().body(body);
        } catch (RuntimeException e) {
            idempotency.release(claimId);
            throw e;
        }
```

`(Type) value` is a **cast** — "trust me, it's this type". We've eliminated the other three
cases above, so only `Acquired` remains.

The ordering matters and is the point of the whole class: we `complete` the claim **after** the
transfer has committed. Completing first would make a rolled-back transfer permanently
replayable as a success. And on failure we `release` the key so the client's retry isn't locked
out — safe because nothing committed.

`catch (X e) { ... }` is the same as JS `catch`, except Java catches by exception type.

## `ApiExceptionHandler.java`

```java
@RestControllerAdvice
public class ApiExceptionHandler {

    @ExceptionHandler(TransferService.TransferNotFoundException.class)
    public ResponseEntity<TransferController.ApiError> notFound(...) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(...);
    }
```

`@RestControllerAdvice` is a global `try/catch` for every controller. Each `@ExceptionHandler`
says "when this exception escapes any controller, run this instead and return that response".
It keeps error mapping in one place instead of scattered through the controller.

`SomeClass.class` is a *class literal* — the runtime object representing that type, like
`typeof` in a value position.

---

# Part 4 — Idempotency

`idempotency/` — the heart of the project.

## `IdempotencyRecord.java`

```java
@Entity
@Table(name = "idempotency_record")
public class IdempotencyRecord implements Persistable<String> {

    public enum State { IN_PROGRESS, COMPLETED }
```

An enum declared *inside* a class — a **nested type**. Referred to from outside as
`IdempotencyRecord.State`.

```java
    @Id
    private String id;      // "partnerId:idempotencyKey"
```

The primary key is the partner and their key joined together. That's deliberate: the database's
uniqueness constraint is what makes duplicate detection race-proof.

```java
    @Transient
    private boolean persisted = false;

    @PostPersist
    @PostLoad
    void markPersisted() {
        this.persisted = true;
    }

    @Override
    public boolean isNew() {
        return !persisted;
    }
```

This is the fix for the bug described in the README. Explained slowly:

- `@Transient` — "this field is **not** a database column". (Different from the `transient`
  keyword, confusingly.)
- `@PostPersist` / `@PostLoad` — JPA lifecycle callbacks. They run automatically after the row
  is inserted, or after it's loaded from the database. Either way, the object is now "real", so
  `persisted` becomes true.
- `@Override` — tells the compiler "this is implementing/replacing an inherited method". If it
  isn't, compilation fails. Always use it; it catches typos.
- `isNew()` comes from `Persistable`, and Spring Data calls it to decide between INSERT
  (`persist`) and "find-then-update" (`merge`).

Without this, Spring saw a non-null id, assumed the object was already in the database, and
issued an UPDATE — so a duplicate key silently overwrote the existing claim instead of failing.

## `IdempotencyRepository.java`

```java
    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Query("delete from IdempotencyRecord r where r.state = :state and r.createdAt < :cutoff")
    int deleteClaimsInStateOlderThan(@Param("state") IdempotencyRecord.State state,
                                     @Param("cutoff") Instant cutoff);
```

When a method name can't express the query, write it. This is **JPQL**, not SQL — note it says
`IdempotencyRecord` (the class) and `r.createdAt` (the field), not table and column names.

- `:state` and `:cutoff` are named parameters, bound by `@Param`.
- `@Modifying` is required for anything that isn't a SELECT.
- `default` methods in an interface can have a body — used here for a convenience wrapper.

## `IdempotencyClaimStore.java`

```java
@Component
public class IdempotencyClaimStore {

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public String insertClaim(String partnerId, String idempotencyKey, String requestHash) {
        IdempotencyRecord record = IdempotencyRecord.claim(...);
        repository.saveAndFlush(record);
        return record.getId();
    }
```

Two things to understand here.

**`REQUIRES_NEW`** — start a brand-new transaction, suspending any existing one, and commit it
immediately when the method returns. Normal `@Transactional` (`REQUIRED`) joins the caller's
transaction. We need a separate one so the claim is **visible to other requests** before we
start the slow work.

**`saveAndFlush` vs `save`** — Hibernate normally batches writes until the transaction commits.
`flush` forces the INSERT to hit the database *now*, so the duplicate-key error is thrown here,
where we're catching it, rather than later somewhere we aren't.

**Why is this a separate class at all?** Because Spring implements `@Transactional` with a
**proxy** — a wrapper object that opens a transaction, then calls the real method. When one
method of a class calls another method of the same class, the call goes straight to `this` and
never passes through the wrapper, so the annotation does nothing at all, silently. Putting the
transactional methods in a separate injected bean forces the call to cross the proxy. This is a
classic Java interview question.

## `IdempotencyService.java`

```java
    public sealed interface Claim {
        record Acquired(String id) implements Claim {}
        record Replay(int status, String body) implements Claim {}
        record Conflict(String message) implements Claim {}
        record InFlight(String message) implements Claim {}
    }
```

**`sealed`** means "only these types may implement this interface". The compiler knows the
complete list, so it can check you've handled every case.

This is Java's discriminated union — the same tool as the `Action` type in your feedback
machine, where `const _: never = action` forced exhaustiveness. Here the language does it.

```java
    public Claim claim(String partnerId, String idempotencyKey, String requestBody) {
        String hash = sha256(requestBody);
        try {
            return new Claim.Acquired(store.insertClaim(partnerId, idempotencyKey, hash));
        } catch (DataIntegrityViolationException duplicate) {
            return inspectExisting(partnerId, idempotencyKey, hash);
        }
    }
```

The whole protocol in seven lines. Try to insert; if the database says "that key already
exists" (`DataIntegrityViolationException` is Spring's wrapper around a constraint violation),
go and look at what's there.

The alternative — "check if it exists, then insert" — is a race: two simultaneous retries both
see nothing and both insert. Only the database can decide the winner.

```java
    static String sha256(String input) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(input.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is required of every JVM", e);
        }
    }
```

Hashing the request body. `getBytes(UTF_8)` turns the String into bytes (always specify the
charset). The `catch` handles a checked exception that can't actually happen — every JVM is
required to support SHA-256 — by converting it to an unchecked one.

---

# Part 5 — Service and outbox

## `TransferService.java`

```java
@Service
public class TransferService {

    @Transactional
    public Transfer acceptTransfer(String partnerId, String partnerReference, Money amount) {
        Transfer transfer = Transfer.receive(partnerId, partnerReference, amount, clock.instant());
        transfers.save(transfer);

        outbox.save(OutboxEvent.of(transfer.getId(), "transfer.received", """
                {"transferId":"%s","partnerId":"%s",...}""".formatted(...), clock.instant()));

        return transfer;
    }
```

**The single most important method in the project**, because of what it does *and* what it
doesn't.

`@Transactional` wraps the whole method in one database transaction. Both `save` calls commit
together or neither does. That's the outbox pattern: the business row and the event row are
atomic because they're in the same database.

`"""..."""` is a **text block** (Java 15+) — a multi-line string, like a JS template literal but
without interpolation. `.formatted(a, b)` fills in the `%s` placeholders, like `printf`.

`clock.instant()` instead of `Instant.now()` — the clock is injected, so tests can control time.

**What it doesn't do:** call the partner bank. If we did, we'd hold a database transaction open
across a network call, and a slow partner would exhaust the connection pool and take down every
unrelated endpoint.

## `OutboxEvent.java`

```java
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "sequence_no")
    private Long sequenceNo;
```

`@GeneratedValue(IDENTITY)` — the database assigns the id (an auto-increment column). Contrast
with `Transfer` and `IdempotencyRecord`, which assign their own — which is exactly why they
needed the `Persistable` treatment and this doesn't.

`Long` (capital L) rather than `long`, because it must be `null` before insertion. That's the
difference between a primitive and its **boxed** object wrapper.

```java
    @Column(name = "published_at")
    private Instant publishedAt;

    public boolean isPublished() {
        return publishedAt != null;
    }
```

`null` here means "not yet published". Simple and effective.

## `EventPublisher.java`

```java
@FunctionalInterface
public interface EventPublisher {
    void publish(OutboxEvent event);
}
```

An interface with exactly one method. `@FunctionalInterface` makes the compiler enforce that,
and it means you can supply an implementation as a lambda:

```java
EventPublisher p = event -> log.info("PUBLISH {}", event.getEventId());
```

This is the dependency-injection seam again. Production supplies Kafka; tests supply a
recorder; the demo supplies a logger. Nothing else changes.

## `OutboxPublisher.java`

```java
    @Scheduled(fixedDelayString = "${outbox.poll-interval-ms:500}")
    public void pollAndPublish() {
        int published = drainOnce();
    }
```

`@Scheduled` runs the method on a timer (enabled by `@EnableScheduling` on the main class).
`fixedDelay` waits N ms *after the previous run finishes*.

`"${outbox.poll-interval-ms:500}"` reads a config property, defaulting to 500 if it's absent.

```java
    @Transactional
    public int drainOnce() {
        var batch = repository.findUnpublished(PageRequest.ofSize(batchSize));
        int published = 0;
        for (OutboxEvent event : batch) {
            event.recordAttempt();
            try {
                publisher.publish(event);
                event.markPublished(clock.instant());
                published++;
            } catch (RuntimeException e) {
                log.warn("Failed to publish {}: {}", event.getEventId(), e.toString());
            }
        }
        repository.saveAll(batch);
        return published;
    }
```

`for (Type x : collection)` is the enhanced for loop — JS `for (const x of collection)`.

Each event is published in its own `try/catch` so one bad event doesn't block the queue behind
it. A failure leaves `publishedAt` null, so the next poll retries it — which is **at-least-once
delivery**, and why consumers must be idempotent.

## `KafkaEventPublisher.java`

```java
@Component
@Profile("kafka")
public class KafkaEventPublisher implements EventPublisher {
```

`@Profile("kafka")` — only create this bean when the `kafka` profile is active. That's how the
default run needs no broker. The logging publisher is marked `@Profile("!kafka")` (the `!` means
"not"), so exactly one of them exists at a time.

```java
        ProducerRecord<String, String> record =
                new ProducerRecord<>(TOPIC, event.getAggregateId(), event.getPayload());
        record.headers().add(EVENT_ID_HEADER, event.getEventId().getBytes());
        ...
        template.send(record).get();
```

The three constructor arguments are topic, **key** and value. The key is the transfer id, and
Kafka assigns a partition by hashing it — which is what keeps all events for one transfer in
order.

`.send(...)` returns a future (a promise). `.get()` **blocks until it completes**. That's
deliberate: returning early would let us mark the row published before the broker had confirmed
it, and a broker failure would lose the event silently.

`new ProducerRecord<>(...)` — the empty `<>` is the "diamond operator": infer the type
parameters from the left-hand side.

---

# Part 6 — The resilience layer

`partner/`

## The small pieces

```java
@FunctionalInterface
public interface PartnerBankClient {
    PartnerAck submit(PaymentInstruction instruction);
}

public record PartnerAck(String schemeReference, boolean accepted) { }

public record PaymentInstruction(String transferId, String beneficiaryIban,
                                 Money amount, String idempotencyKey) {
    public static PaymentInstruction forTransfer(String transferId, String iban, Money amount) {
        return new PaymentInstruction(transferId, iban, amount, "txf-" + transferId);
    }
}
```

The key is **derived from the transfer id**, so it's identical on every retry attempt. A key
regenerated per attempt would make each retry look like a new payment to the partner.

```java
public class PartnerBankException extends RuntimeException {
    private final boolean retryable;

    public static PartnerBankException unavailable(String message) {
        return new PartnerBankException(message, true);
    }
    public static PartnerBankException rejected(String message) {
        return new PartnerBankException(message, false);
    }
}
```

One boolean carries the most important decision in the system: *may this be retried?* A 503 yes;
"beneficiary account closed" no.

## `ResilientPartnerBankClient.java`

The hardest file. It wraps a plain client in four layers of protection.

```java
public class ResilientPartnerBankClient implements PartnerBankClient {

    private final PartnerBankClient delegate;
```

**The decorator pattern**: this class implements the same interface as the thing it wraps, and
holds a reference to it (`delegate`). Callers can't tell the difference. React higher-order
components are the same idea.

```java
    @Override
    public PartnerAck submit(PaymentInstruction instruction) {
        Supplier<Future<PartnerAck>> futureSupplier =
                () -> CompletableFuture.supplyAsync(() -> delegate.submit(instruction), callExecutor);

        Callable<PartnerAck> timed = timeLimiter.decorateFutureSupplier(futureSupplier);
        Callable<PartnerAck> breakered = CircuitBreaker.decorateCallable(circuitBreaker, timed);
        Callable<PartnerAck> retried = Retry.decorateCallable(retry, breakered);
        Callable<PartnerAck> bulkheaded = Bulkhead.decorateCallable(bulkhead, retried);

        return bulkheaded.call();
    }
```

Read those four lines bottom-up to see the nesting:
`Bulkhead( Retry( CircuitBreaker( TimeLimiter( actual call ))))`.

The Java vocabulary:

| Type | Meaning | JS analogue |
|---|---|---|
| `Supplier<T>` | takes nothing, returns a T | `() => T` |
| `Callable<T>` | same, but allowed to throw checked exceptions | `() => T` |
| `Future<T>` | a value that will exist later | `Promise<T>` |
| `CompletableFuture<T>` | a Future you can chain and complete | `Promise<T>` |

`CompletableFuture.supplyAsync(work, executor)` runs `work` on another thread and hands back a
future. **Why bother?** Because you cannot interrupt a thread blocked in a socket read from the
outside. The only way to impose a deadline is to run the call elsewhere and stop waiting for it.
Note the consequence: an abandoned call is still running at the partner's end — which is why the
idempotency key matters.

Nothing runs until `.call()` on the last line. Up to then we're only building the wrapper stack.

```java
    public static boolean isRetryable(Throwable t) {
        if (t instanceof CallNotPermittedException) {
            return false;
        }
        PartnerBankException pbe = findPartnerBankException(t);
        if (pbe != null) {
            return pbe.isRetryable();
        }
        return hasCauseOfType(t, TimeoutException.class);
    }

    public static PartnerBankException findPartnerBankException(Throwable t) {
        Throwable current = t;
        while (current != null) {
            if (current instanceof PartnerBankException pbe) {
                return pbe;
            }
            if (current.getCause() == current) break;
            current = current.getCause();
        }
        return null;
    }
```

`Throwable` is the root of all exceptions. Exceptions **wrap** each other — a
`CompletableFuture` failure arrives as `ExecutionException` *caused by* the real one. So we walk
the `getCause()` chain to find the original, otherwise the retryable/not classification would be
lost and every failure would look the same.

`if (current.getCause() == current) break;` guards against a cause chain that points at itself,
which would loop forever.

## `ResilienceConfig.java`

```java
@Configuration
public class ResilienceConfig {

    @Bean
    public CircuitBreaker partnerCircuitBreaker() {
        CircuitBreakerConfig config = CircuitBreakerConfig.custom()
                .failureRateThreshold(50f)
                .slidingWindowSize(20)
                .minimumNumberOfCalls(10)
                .waitDurationInOpenState(Duration.ofSeconds(10))
                .build();
        return CircuitBreaker.of("partner-bank", config);
    }
```

`@Configuration` = "this class defines beans". `@Bean` = "call this method at startup, and put
the returned object in the container so it can be injected elsewhere". Use `@Bean` when the
object comes from a library you can't annotate; use `@Service`/`@Component` for your own classes.

`.custom().x().y().build()` is the **builder pattern** — each call returns the builder so they
chain, and `build()` produces the final object. Very common in Java for objects with many
optional settings.

`50f` is a `float` literal (the `f` suffix). `Duration.ofSeconds(10)` is a readable time span.

```java
    @Bean(destroyMethod = "shutdown")
    public ExecutorService partnerCallExecutor() {
        return Executors.newVirtualThreadPerTaskExecutor();
    }
```

An `ExecutorService` is a thread pool. This one makes a **virtual thread** per task — Java 21's
lightweight threads, managed by the JVM rather than the OS, so millions can exist cheaply.
`destroyMethod = "shutdown"` tells Spring to call `shutdown()` when the app stops.

```java
    @Bean
    @Primary
    public PartnerBankClient resilientPartnerBankClient(
            @Qualifier("rawPartnerBankClient") PartnerBankClient rawPartnerBankClient, ...) {
```

There are two `PartnerBankClient` beans, so Spring needs help:

- `@Qualifier("name")` — inject *this specific* bean by name.
- `@Primary` — "when someone asks for a PartnerBankClient without qualifying, give them this
  one." So the protected client is what you get by default, and the raw one must be asked for
  explicitly.

---

# Part 7 — The Kafka consumer

`consumer/`

## `TransferEventListener.java`

```java
@Component
@Profile("kafka")
public class TransferEventListener {

    @KafkaListener(topics = KafkaEventPublisher.TOPIC, groupId = "${kafka.consumer-group:partner-send}")
    public void onEvent(ConsumerRecord<String, String> record) {
        String eventId = header(record, KafkaEventPublisher.EVENT_ID_HEADER);
        ...
        boolean applied = processor.process(eventId, record.value());
    }
```

`@KafkaListener` runs this method for every message on the topic. Spring manages the polling
loop, the thread, and committing the offset after the method returns normally.

This class only knows about Kafka. The real work is in the processor, which knows nothing about
brokers — so the interesting logic can be tested without one.

Note it doesn't catch exceptions. Letting them propagate is what allows Spring's error handler to
retry or dead-letter. Swallowing them would silently drop events while committing the offset.

## `TransferEventProcessor.java`

```java
    @Transactional
    public boolean process(String eventId, String payload) {
        if (processedEvents.existsById(eventId)) {
            return false;
        }
        ...
        processedEvents.save(new ProcessedEvent(eventId, clock.instant()));

        PartnerVolume volume = volumes.findById(partnerId)
                .orElseGet(() -> new PartnerVolume(partnerId));
        volume.add(minorUnits);
        volumes.save(volume);
        return true;
    }
```

**The `@Transactional` is the point.** The "already processed" marker and the actual effect must
commit together. Record first and crash → the update is lost forever, because the redelivery is
skipped. Apply first and crash → it's applied twice. One transaction, and both orderings are safe.

`orElseGet(() -> new PartnerVolume(partnerId))` — unwrap the Optional, or build a new one if
absent. The lambda means the new object is only constructed when actually needed.

There's a long comment below the method explaining why the duplicate check is `existsById`
rather than catching a constraint violation: once a violation happens inside a transaction,
Spring marks it rollback-only, and catching the exception doesn't undo that — the commit then
fails with `UnexpectedRollbackException`. That bug was real, and the test caught it.

## `PartnerVolume.java`

A read model — a running total per partner, built by consuming events. It exists to make
duplicate processing *visible*: apply the same event twice and the total is simply wrong.

---

# Part 8 — Wiring and configuration

## `PartnerSendApplication.java`

```java
@SpringBootApplication
@EnableScheduling
public class PartnerSendApplication {
    public static void main(String[] args) {
        SpringApplication.run(PartnerSendApplication.class, args);
    }
}
```

`public static void main(String[] args)` is where every Java program starts.

`@SpringBootApplication` bundles three things:
- `@Configuration` — this class may define beans
- `@EnableAutoConfiguration` — Boot infers beans from what's on the classpath (H2 is present, so
  a database connection pool appears)
- `@ComponentScan` — scan this package and everything below it for `@Component`, `@Service`,
  `@RestController`, `@Repository`

That last one is why nothing has to be registered manually — and why every class lives under
`com.example.prep.partnersend`.

## `application.yaml`

```yaml
spring:
  datasource:
    url: jdbc:h2:mem:partnersend;DB_CLOSE_DELAY=-1
  jpa:
    hibernate:
      ddl-auto: create-drop
    open-in-view: false
  threads:
    virtual:
      enabled: true
outbox:
  poll-interval-ms: 500
```

- `jdbc:h2:mem:...` — an in-memory database, gone when the app stops. Real deployments use
  Postgres.
- `ddl-auto: create-drop` — Hibernate creates the tables from the entity classes at startup.
  Convenient for a demo; production uses `validate` with Flyway owning the schema.
- `open-in-view: false` — Boot's default is `true`, which holds a database connection for the
  whole HTTP request including JSON serialisation, exhausting the pool under load.
- `outbox.poll-interval-ms` — our own property, read by `@Value` and `@Scheduled`.

## `pom.xml`

Maven's `package.json`. `<dependencies>` are your npm dependencies; `<parent>` inherits sensible
versions for everything Spring-related, which is why most dependencies have no `<version>`.

`<scope>test</scope>` = a devDependency: available in tests, not shipped.

Commands: `mvn test`, `mvn spring-boot:run`, `mvn package`.

---

# Part 9 — The tests

```java
class MoneyTest {

    @Test
    @DisplayName("the reason money is never a double")
    void floatingPointCannotRepresentMoney() {
        double wrong = 0.1 + 0.2;
        assertThat(wrong).isNotEqualTo(0.3);
    }
}
```

- `@Test` — JUnit runs this method.
- `@DisplayName` — a readable name in the report.
- `assertThat(x).isEqualTo(y)` — AssertJ, which chains readably and gives good failure messages.
- Test classes and methods need no `public`.

```java
@SpringBootTest
@AutoConfigureMockMvc
class IdempotencyApiTest {
    @Autowired MockMvc mockMvc;

    @Test
    void firstRequestCreates() throws Exception {
        mockMvc.perform(post(URL)
                        .header("Idempotency-Key", "key-1")
                        .content(BODY))
                .andExpect(status().isAccepted());
    }
}
```

- `@SpringBootTest` — start the whole application for this test.
- `@Autowired` — field injection. Fine in tests; prefer constructor injection in real code.
- `MockMvc` — fires HTTP requests at the controllers without opening a real port.

```java
    @Test
    void onlyOneWinnerUnderConcurrency() throws Exception {
        CyclicBarrier startLine = new CyclicBarrier(THREADS);

        try (ExecutorService pool = Executors.newFixedThreadPool(THREADS)) {
            List<Callable<Claim>> tasks = IntStream.range(0, THREADS)
                    .<Callable<Claim>>mapToObj(i -> () -> {
                        startLine.await(10, TimeUnit.SECONDS);
                        return idempotency.claim("acme", "storm-key", "{...}");
                    })
                    .toList();

            List<Future<Claim>> futures = pool.invokeAll(tasks);
            ...
        }
    }
```

The most complex test, and worth decoding:

- `CyclicBarrier(16)` — a gate. Each thread calls `await()` and blocks; when the 16th arrives,
  all are released simultaneously. Without it the threads drift apart and never really collide.
- `try (X x = ...) { }` — **try-with-resources**. Automatically closes `x` at the end, like
  Python's `with`.
- `IntStream.range(0, 16).mapToObj(...)` — build 16 tasks. Same as
  `Array.from({length:16}, (_, i) => ...)`.
- `invokeAll(tasks)` — run them all, return a list of `Future`s.
- `future.get()` — wait for a result. Same as `await`.

This is the test that caught the `Persistable` bug: 15 of 16 threads acquired the same key.

```java
@SpringBootTest(properties = { "spring.kafka.bootstrap-servers=${spring.embedded.kafka.brokers}" })
@ActiveProfiles("kafka")
@EmbeddedKafka(partitions = 3, topics = KafkaEventPublisher.TOPIC)
class KafkaIntegrationTest {
```

`@EmbeddedKafka` starts a real Kafka broker **inside the test JVM** — no Docker.
`@ActiveProfiles("kafka")` turns on the beans marked `@Profile("kafka")`.

```java
        await().atMost(Duration.ofSeconds(30)).untilAsserted(() -> {
            assertThat(volumes.findById("acme")).isPresent();
        });
```

Awaitility. The pipeline is asynchronous, so "the answer is there" is only true *eventually*.
This retries the assertion until it passes or times out — much better than `Thread.sleep`.

---

# Part 10 — Annotation glossary

Every annotation in the project, in one place.

### Spring core
| | |
|---|---|
| `@Component` | generic managed object |
| `@Service` | same, labelled as business logic |
| `@Repository` | same, for data access |
| `@Configuration` | class that declares `@Bean` methods |
| `@Bean` | method whose return value becomes a managed object |
| `@Primary` | the default choice when several candidates match |
| `@Qualifier("n")` | inject the bean named `n` |
| `@Profile("x")` / `@Profile("!x")` | only when profile x is / isn't active |
| `@Value("${p:default}")` | inject a config property |
| `@Autowired` | inject here (unnecessary on constructors) |
| `@Scheduled` | run on a timer |
| `@EnableScheduling` | turn `@Scheduled` on |
| `@SpringBootApplication` | configuration + auto-config + component scan |

### Web
| | |
|---|---|
| `@RestController` | HTTP handler; returns become JSON |
| `@RequestMapping` | base path |
| `@PostMapping` / `@GetMapping` | method + path |
| `@PathVariable` | bind a `{placeholder}` |
| `@RequestHeader` | bind a header |
| `@RequestBody` | bind and parse the body |
| `@Valid` | run validation annotations |
| `@RestControllerAdvice` | global exception handling |
| `@ExceptionHandler` | handle one exception type |

### Persistence
| | |
|---|---|
| `@Entity` / `@Table` | this class is a table |
| `@Id` | primary key |
| `@Column` | column settings |
| `@GeneratedValue` | database assigns the id |
| `@Enumerated(STRING)` | store the enum as text |
| `@Version` | optimistic locking |
| `@Transient` | not a column |
| `@PostPersist` / `@PostLoad` | lifecycle callbacks |
| `@Transactional` | wrap in a transaction |
| `@Query` / `@Modifying` / `@Param` | hand-written JPQL |

### Validation
`@NotBlank`, `@Pattern`

### Testing
`@Test`, `@DisplayName`, `@BeforeEach`, `@SpringBootTest`, `@AutoConfigureMockMvc`,
`@TestConfiguration`, `@ActiveProfiles`, `@EmbeddedKafka`

### Kafka
`@KafkaListener`

### Language
`@Override` (compiler-checked), `@FunctionalInterface` (exactly one method)

---

# Where to go next

1. Run `mvn test`. Watch 47 tests pass.
2. Open `MoneyTest` and change an assertion so it fails. Read the error message — AssertJ's
   failures are unusually good, and reading them is how you learn the library.
3. Do exercise 2 from [§08](./08-learning-path.md): delete `implements Persistable<String>` from
   `IdempotencyRecord` and watch `ConcurrentIdempotencyTest` fail. That's the bug this codebase
   actually had, and feeling it is worth more than reading about it.
4. Then read [§01](./01-java-for-typescript-devs.md), which covers the same ground from the
   language side rather than the file side.
