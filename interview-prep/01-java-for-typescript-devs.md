# 01 — Java for TypeScript developers

Goal: read Java as fluently as you read TypeScript, and discuss the JVM without bluffing.
Not: memorise the standard library.

You already know more of this than you think. TypeScript took its type system, its
`interface`/`class`/`extends`, its generics and its `async` keyword from this lineage.

---

## The mental model shift

| TypeScript | Java | What actually differs |
|---|---|---|
| Types erased at runtime, `any` escapes | Types enforced by the JVM | You cannot lie to the compiler and fix it later. |
| Structural typing (shape matches) | Nominal typing (name matches) | Two identical-shaped classes are unrelated. Interfaces must be declared and implemented explicitly. |
| One thread + event loop | Many real threads | Concurrency is genuine, so shared mutable state can actually corrupt. This is the biggest shift. |
| `null`/`undefined` everywhere | `null`, plus `Optional<T>` | `NullPointerException` is the classic Java bug. |
| Compile to JS, ship source | Compile to bytecode, JIT to native | Long-running processes get *faster* as the JIT optimises hot paths. |
| `npm`, `package.json` | Maven/Gradle, `pom.xml` | Same idea, more XML. |

---

## Syntax you'll meet in the first hour

```java
// TS: const name: string = "x";
final String name = "x";
var name = "x";            // inferred, Java 10+. Same as TS `const` for locals.

// TS: function add(a: number, b: number): number { return a + b; }
public int add(int a, int b) { return a + b; }

// TS: interface Foo { bar(): void }
public interface Foo { void bar(); }

// TS: type Money = { minorUnits: number; currency: string }
public record Money(long minorUnits, Currency currency) {}   // immutable, equals/hashCode free

// TS: arr.map(x => x * 2).filter(x => x > 4)
list.stream().map(x -> x * 2).filter(x -> x > 4).toList();

// TS: `Total: ${amount}`
"Total: %s".formatted(amount);
```

### Records are the one to internalise

A `record` is Java's immutable data class. `Money`, `PartnerAck` and `PaymentInstruction` in
the sample are all records. The compiler generates the constructor, the accessors, `equals`,
`hashCode` and `toString`.

Accessors have **no `get` prefix** — `money.minorUnits()`, not `money.getMinorUnits()`. This
trips people up when reading.

### Sealed interfaces are discriminated unions

You used a discriminated union in the bunq widget's `Action` type. Java 17 has the same idea:

```java
public sealed interface Claim {
    record Acquired(String id) implements Claim {}
    record Replay(int status, String body) implements Claim {}
    record Conflict(String message) implements Claim {}
    record InFlight(String message) implements Claim {}
}
```

`sealed` means the compiler knows the complete list of implementations, so a `switch` over
them can be checked for exhaustiveness — exactly the guarantee your `const _: never = action`
trick bought you in TypeScript, but built into the language.

This is in `IdempotencyService`. Being able to say *"I modelled the idempotency outcomes as a
sealed interface so the caller has to handle every case"* is a good, specific thing to say.

### Checked exceptions — the one genuinely alien feature

Java has two kinds of exception. **Checked** ones must be declared (`throws IOException`) and
handled by the caller; **unchecked** ones (`RuntimeException` and subclasses) need not be.

Modern practice, and what the sample does, is to prefer unchecked exceptions for domain
errors. Checked exceptions do not compose with lambdas and streams, which is why almost every
modern framework avoids them.

---

## Concurrency — the part with no TypeScript equivalent

This is where the real learning is, and it is also what the interview cares about.

In Node, one thread runs your JavaScript, so `count++` is atomic by construction. In Java,
several threads run your code simultaneously and `count++` is three operations (read, add,
write). Two threads interleaving there lose updates.

What you need to hold:

- **`synchronized`** — a mutual-exclusion lock. Only one thread in the block at a time. Simple
  and slow-ish.
- **`java.util.concurrent.atomic`** — `AtomicInteger`, `AtomicReference`. Lock-free, backed by
  CPU compare-and-swap. Used in the sample's tests.
- **`ConcurrentHashMap`** — the thread-safe map you actually want. Never share a plain
  `HashMap` across threads; it can corrupt into an infinite loop under concurrent resize.
- **Immutability** — an object that cannot change is automatically thread-safe. This is why
  `record` and `final` matter, and why the functional habits you have from React transfer
  directly and are *more* valuable here.

### Virtual threads (Java 21) — worth knowing, it's current

Historically a Java thread mapped to an OS thread: ~1MB of stack, expensive to create, so you
pooled a few hundred and a blocking call was costly. That pressure is what drove the whole
reactive-programming movement (WebFlux, Project Reactor) — non-blocking code that is
notoriously hard to read and debug.

Java 21's **virtual threads** are scheduled by the JVM instead of the OS. Millions can exist,
and a blocked one costs almost nothing. So you can write straightforward blocking code and
get non-blocking scalability.

The sample enables them with `spring.threads.virtual.enabled: true` and uses
`Executors.newVirtualThreadPerTaskExecutor()` in `ResilienceConfig`.

**Interview-ready line:** *"Virtual threads make the simple blocking style viable again at
scale, which removes most of the reason to reach for reactive. The one caveat is that
`synchronized` blocks could pin a virtual thread to its carrier — so `ReentrantLock` is the
safer choice in hot paths."* (Pinning has been progressively reduced in later JDKs, but
knowing the concept is the point.)

---

## Spring Boot: the 20% that matters

Spring is a dependency-injection container. You declare what you need; it wires it up.

```java
@Service                       // "this is a bean, manage it"
public class TransferService {
    private final TransferRepository transfers;

    // Constructor injection. Spring sees one constructor and supplies the arguments.
    // Prefer this over @Autowired fields: dependencies are explicit, the object is
    // immutable, and it is trivially testable with `new` in a unit test.
    public TransferService(TransferRepository transfers) {
        this.transfers = transfers;
    }
}
```

**Stereotypes** — all the same thing with different labels: `@Component` (generic),
`@Service` (business logic), `@Repository` (data access, adds exception translation),
`@RestController` (HTTP endpoint), `@Configuration` (declares `@Bean` methods).

### `@Transactional`, and the two traps

`@Transactional` wraps a method in a database transaction: commit on normal return, roll back
on unchecked exception.

**Trap 1 — self-invocation.** Spring implements this with a *proxy*. Callers get a wrapper
that opens the transaction and delegates. A call from one method of a class to another method
of the same class goes straight to `this` and never touches the proxy, so the annotation is
**silently ignored**.

```java
public void a() { b(); }                    // b()'s @Transactional does nothing
@Transactional public void b() { ... }
```

This is exactly why the sample has a separate `IdempotencyClaimStore` bean — the comment on
that class spells it out. It applies equally to `@Async`, `@Cacheable` and `@Retryable`.
**This is a common interview question, and it is a great one to volunteer.**

**Trap 2 — rollback only on unchecked exceptions.** By default a *checked* exception commits
the transaction. If you want otherwise: `@Transactional(rollbackFor = Exception.class)`.

### Propagation

`REQUIRED` (default) joins an existing transaction. `REQUIRES_NEW` suspends it and starts an
independent one — which is what makes the idempotency claim commit immediately and become
visible to competing requests. That is the whole trick in `IdempotencyClaimStore.insertClaim`.

### `open-in-view` — free credibility

Spring Boot defaults `spring.jpa.open-in-view` to `true`, which holds a database connection
open for the **entire HTTP request**, including response serialisation. It exists so lazy
loading doesn't explode in your templates. Under load it exhausts the connection pool.

The sample sets it to `false`. Noticing this in someone's config is a strong senior signal.

### JPA gotcha the sample hit for real

`IdempotencyRecord` implements `Persistable<String>`, and the class comment explains why:
Spring Data's `save()` chooses `persist()` vs `merge()` by checking whether the `@Id` is null.
With an **assigned** id it always concludes "detached" and calls `merge()` — which turns a
duplicate-key INSERT into a silent **UPDATE**, destroying the uniqueness guarantee the whole
idempotency scheme depends on.

This wasn't theoretical. The first version of the code had this bug and the concurrency test
caught it: 15 of 16 threads acquired the same key. It is a good story to have, because it is
concrete evidence that you write tests that can actually fail.

---

## Reading the sample

Suggested order, roughly increasing in difficulty:

1. `domain/Money.java` — records, validation, why not `double`.
2. `domain/TransferStatus.java` — enums with behaviour. Enums here are real classes.
3. `api/TransferController.java` — Spring MVC, and the idempotency protocol as HTTP.
4. `idempotency/IdempotencyService.java` — sealed interfaces, the claim protocol.
5. `outbox/OutboxPublisher.java` — scheduling, at-least-once.
6. `partner/ResilientPartnerBankClient.java` — the hard one. Decorator composition.

Then run `mvn test`, open `ResiliencePatternsTest`, and change a config value to watch a test
fail. Understanding *why* it fails is worth more than reading it twice.

---

## Things not to bluff

If asked something you don't know, say so and reason from what you do. Interviewers are
calibrated to spot confident wrongness, and it costs far more than an honest gap. Areas where
a TypeScript-native background typically shows:

- **GC tuning.** Know that G1 is the default and ZGC is the low-pause option, and that most
  "GC problems" are really allocation-rate or memory-leak problems. Don't go deeper unprompted.
- **The memory model / `volatile`.** Know it exists and guarantees visibility across threads.
- **Build tooling minutiae.** Nobody cares.

Fine to say: *"I haven't tuned a JVM in production. My instinct would be to look at allocation
rate and heap-usage-after-GC first, but I'd want someone who has done it to check me."*
