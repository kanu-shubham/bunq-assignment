/**
 * One breaker per downstream, not one per process.
 *
 * A single shared breaker means a sick `/quotes` endpoint opens the circuit on
 * a perfectly healthy `/balances`. The blast radius of the safety mechanism
 * ends up larger than the blast radius of the fault it was protecting against.
 *
 * The right granularity is "things that fail together" — usually a service, or
 * an endpoint on a service. Note the key must be a *route template*, not a
 * concrete URL: keying on `/transfers/123` gives every transfer its own
 * breaker, each with a sample size of one, which never opens.
 */

import { CircuitBreaker, type CircuitBreakerOptions, type CircuitSnapshot } from './circuitBreaker';

export class CircuitBreakerRegistry {
  private readonly breakers = new Map<string, CircuitBreaker>();

  constructor(private readonly factory: (key: string) => CircuitBreaker) {}

  /** Convenience factory: every breaker gets the same options. */
  static withOptions(options: CircuitBreakerOptions = {}): CircuitBreakerRegistry {
    return new CircuitBreakerRegistry(() => new CircuitBreaker(options));
  }

  get(key: string): CircuitBreaker {
    let breaker = this.breakers.get(key);
    if (!breaker) {
      breaker = this.factory(key);
      this.breakers.set(key, breaker);
    }
    return breaker;
  }

  /** Feed this to metrics: how many downstreams are open right now. */
  snapshot(): Record<string, CircuitSnapshot> {
    const result: Record<string, CircuitSnapshot> = {};
    for (const [key, breaker] of this.breakers) {
      result[key] = breaker.snapshot();
    }
    return result;
  }

  resetAll(): void {
    for (const breaker of this.breakers.values()) breaker.reset();
  }
}
