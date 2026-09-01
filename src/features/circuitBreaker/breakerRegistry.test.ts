import { CircuitBreakerRegistry } from './breakerRegistry';
import { CIRCUIT } from './circuitBreaker';

describe('CircuitBreakerRegistry', () => {
  it('returns the same breaker for the same key', () => {
    const registry = CircuitBreakerRegistry.withOptions({ failureThreshold: 1 });

    expect(registry.get('/balances')).toBe(registry.get('/balances'));
    expect(registry.get('/balances')).not.toBe(registry.get('/quotes'));
  });

  it('keeps one sick downstream from tripping a healthy one', async () => {
    const registry = CircuitBreakerRegistry.withOptions({ failureThreshold: 1, callTimeoutMs: null });

    await expect(registry.get('/quotes').execute(() => Promise.reject(new Error('down')))).rejects.toThrow();

    expect(registry.get('/quotes').getState()).toBe(CIRCUIT.OPEN);
    expect(registry.get('/balances').getState()).toBe(CIRCUIT.CLOSED);
    await expect(registry.get('/balances').execute(() => Promise.resolve('ok'))).resolves.toBe('ok');
  });

  it('exposes every breaker for metrics', async () => {
    const registry = CircuitBreakerRegistry.withOptions({ failureThreshold: 1, callTimeoutMs: null });

    await expect(registry.get('/quotes').execute(() => Promise.reject(new Error('down')))).rejects.toThrow();
    registry.get('/balances');

    const snapshot = registry.snapshot();
    expect(Object.keys(snapshot).sort()).toEqual(['/balances', '/quotes']);
    expect(snapshot['/quotes'].state).toBe(CIRCUIT.OPEN);

    registry.resetAll();
    expect(registry.snapshot()['/quotes'].state).toBe(CIRCUIT.CLOSED);
  });
});
