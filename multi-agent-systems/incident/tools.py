"""The world the agents act on — a simulated cluster plus read and write tools.

Simulated so the whole system is runnable and testable end to end: remediation
actually changes cluster state, and verification actually observes the change,
so the "did it work?" loop is real rather than mocked at the boundary.

Two design points that carry over to a real implementation:

  1. **Reads and writes are separate tool sets.** Analysts get read-only tools
     and literally cannot mutate anything — the safety property is enforced by
     the tool surface, not by asking the model nicely. Only the executor holds
     write tools, and only for catalog actions.
  2. **Every write is idempotent and reversible where possible.** Each write
     tool returns an inverse action, which is what makes automatic rollback
     possible without a second planning round.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .state import Action


@dataclass
class ServiceState:
    name: str
    replicas: int = 3
    error_rate: float = 0.001  # fraction of requests
    latency_p99_ms: float = 180.0
    cpu_saturation: float = 0.4  # 0..1
    memory_saturation: float = 0.5
    version: str = "v1.0.0"
    previous_version: str = "v0.9.9"
    feature_flags: dict[str, bool] = field(default_factory=dict)
    connection_pool_size: int = 20
    connection_pool_in_use: int = 8
    restarts: int = 0


@dataclass
class Deployment:
    service: str
    version: str
    at: float
    author: str
    changes: str


class SimulatedCluster:
    """A tiny world model with a *cause*.

    `inject_fault` sets a hidden root cause; the read tools surface symptoms
    consistent with it, and only the matching remediation clears it. That is
    what makes the demo honest — an agent that guesses wrong genuinely fails
    verification and has to loop, rather than succeeding because the mock
    always returns success.
    """

    def __init__(self, seed: int = 7):
        self.rng = random.Random(seed)
        self.now = time.time()
        self.services: dict[str, ServiceState] = {
            "checkout-api": ServiceState("checkout-api", replicas=6),
            "payments-worker": ServiceState("payments-worker", replicas=4),
            "ledger-db-proxy": ServiceState("ledger-db-proxy", replicas=2),
        }
        self.deployments: list[Deployment] = [
            Deployment("checkout-api", "v1.0.0", self.now - 7200, "ci-bot", "routine bump"),
        ]
        self.log_lines: dict[str, list[str]] = {name: [] for name in self.services}
        self.root_cause: str | None = None
        self.audit: list[str] = []

    # -- fault injection ---------------------------------------------------
    def inject_fault(self, kind: str, service: str = "checkout-api") -> None:
        svc = self.services[service]
        self.root_cause = f"{kind}:{service}"
        if kind == "bad_deploy":
            svc.version = "v1.1.0"
            svc.previous_version = "v1.0.0"
            svc.error_rate = 0.24
            svc.latency_p99_ms = 1400
            self.deployments.append(
                Deployment(service, "v1.1.0", self.now - 420, "a.dev", "new pricing engine")
            )
            self.log_lines[service] = [
                "ERROR PricingEngine: NullPointerException at applyDiscount()",
                "ERROR PricingEngine: NullPointerException at applyDiscount()",
                "WARN  circuit breaker open for pricing-v2",
            ] * 12
        elif kind == "resource_exhaustion":
            svc.cpu_saturation = 0.97
            svc.memory_saturation = 0.94
            svc.latency_p99_ms = 2600
            svc.error_rate = 0.11
            self.log_lines[service] = [
                "WARN  GC pause 1840ms",
                "ERROR OOMKilled: container checkout-api restarted",
                "WARN  request queue depth 4200",
            ] * 9
        elif kind == "connection_pool_exhaustion":
            svc.connection_pool_in_use = svc.connection_pool_size
            svc.error_rate = 0.18
            svc.latency_p99_ms = 3100
            self.log_lines[service] = [
                "ERROR could not acquire connection from pool within 5000ms",
                "ERROR HikariPool-1 - Connection is not available, request timed out",
            ] * 15
        elif kind == "downstream_dependency":
            # The trap case: symptoms are in checkout-api, cause is elsewhere.
            # Restarting checkout-api will not fix it, and verification will say so.
            svc.error_rate = 0.15
            svc.latency_p99_ms = 2200
            self.services["ledger-db-proxy"].cpu_saturation = 0.99
            self.log_lines[service] = [
                "ERROR upstream timeout calling ledger-db-proxy after 3000ms",
                "WARN  retry budget exhausted for ledger-db-proxy",
            ] * 14
        else:
            raise ValueError(f"unknown fault {kind!r}")

    # -- read tools --------------------------------------------------------
    def get_metrics(self, service: str, window_minutes: int = 30) -> dict[str, Any]:
        svc = self.services[service]
        baseline = {"error_rate": 0.001, "latency_p99_ms": 180.0}
        return {
            "service": service,
            "window_minutes": window_minutes,
            "error_rate": round(svc.error_rate, 4),
            "error_rate_baseline": baseline["error_rate"],
            "latency_p99_ms": round(svc.latency_p99_ms, 1),
            "latency_p99_baseline_ms": baseline["latency_p99_ms"],
            "cpu_saturation": round(svc.cpu_saturation, 2),
            "memory_saturation": round(svc.memory_saturation, 2),
            "replicas": svc.replicas,
            "restarts_last_hour": svc.restarts,
        }

    def get_logs(self, service: str, limit: int = 20) -> dict[str, Any]:
        lines = self.log_lines.get(service, [])[:limit]
        counts: dict[str, int] = {}
        for line in self.log_lines.get(service, []):
            key = line.split(":")[0][:60]
            counts[key] = counts.get(key, 0) + 1
        return {
            "service": service,
            "sample": lines,
            "top_patterns": sorted(counts.items(), key=lambda kv: -kv[1])[:5],
            "total_lines": len(self.log_lines.get(service, [])),
        }

    def get_deployments(self, service: str, hours: int = 24) -> dict[str, Any]:
        cutoff = self.now - hours * 3600
        recent = [d for d in self.deployments if d.service == service and d.at >= cutoff]
        return {
            "service": service,
            "current_version": self.services[service].version,
            "previous_version": self.services[service].previous_version,
            "recent": [
                {
                    "version": d.version,
                    "minutes_ago": round((self.now - d.at) / 60),
                    "author": d.author,
                    "changes": d.changes,
                }
                for d in recent
            ],
        }

    def get_dependencies(self, service: str) -> dict[str, Any]:
        graph = {
            "checkout-api": ["payments-worker", "ledger-db-proxy"],
            "payments-worker": ["ledger-db-proxy"],
            "ledger-db-proxy": [],
        }
        deps = graph.get(service, [])
        return {
            "service": service,
            "depends_on": deps,
            "health": {
                d: {
                    "cpu_saturation": round(self.services[d].cpu_saturation, 2),
                    "error_rate": round(self.services[d].error_rate, 4),
                }
                for d in deps
            },
        }

    def get_connections(self, service: str) -> dict[str, Any]:
        svc = self.services[service]
        return {
            "service": service,
            "pool_size": svc.connection_pool_size,
            "in_use": svc.connection_pool_in_use,
            "utilization": round(svc.connection_pool_in_use / max(1, svc.connection_pool_size), 2),
        }

    # -- write tools -------------------------------------------------------
    def restart_pods(self, service: str, count: int = 1) -> dict[str, Any]:
        svc = self.services[service]
        svc.restarts += count
        self.audit.append(f"restart_pods {service} x{count}")
        if self.root_cause == f"resource_exhaustion:{service}":
            # Restarts clear a leak — temporarily. Honest simulation: it looks
            # fixed now and regresses later, which is exactly why verification
            # must be time-windowed, not instantaneous.
            svc.memory_saturation = 0.55
            svc.cpu_saturation = 0.5
            svc.latency_p99_ms = 320
            svc.error_rate = 0.01
        return {"restarted": count, "service": service}

    def scale(self, service: str, replicas: int) -> dict[str, Any]:
        svc = self.services[service]
        before = svc.replicas
        svc.replicas = replicas
        self.audit.append(f"scale {service} {before}->{replicas}")
        if self.root_cause == f"resource_exhaustion:{service}" and replicas > before:
            factor = before / replicas
            svc.cpu_saturation = max(0.2, svc.cpu_saturation * factor)
            svc.memory_saturation = max(0.2, svc.memory_saturation * factor)
            if svc.cpu_saturation < 0.8:
                # Queueing cliff: latency under saturation is not linear in
                # utilization. Once you get back under the knee it collapses,
                # which is why "scale out" works at all as a mitigation.
                svc.latency_p99_ms = 240.0
                svc.error_rate = 0.002
            else:
                svc.latency_p99_ms = max(200.0, svc.latency_p99_ms * factor)
                svc.error_rate = max(0.001, svc.error_rate * factor)
        return {"service": service, "replicas_before": before, "replicas_after": replicas}

    def rollback_deploy(self, service: str, to_version: str | None = None) -> dict[str, Any]:
        svc = self.services[service]
        target = to_version or svc.previous_version
        before = svc.version
        svc.version, svc.previous_version = target, before
        self.audit.append(f"rollback {service} {before}->{target}")
        if self.root_cause == f"bad_deploy:{service}":
            svc.error_rate = 0.001
            svc.latency_p99_ms = 180.0
            self.log_lines[service] = []
            self.root_cause = None
        return {"service": service, "from": before, "to": target}

    def set_feature_flag(self, service: str, flag: str, enabled: bool) -> dict[str, Any]:
        self.services[service].feature_flags[flag] = enabled
        self.audit.append(f"flag {service} {flag}={enabled}")
        return {"service": service, "flag": flag, "enabled": enabled}

    def resize_connection_pool(self, service: str, size: int) -> dict[str, Any]:
        svc = self.services[service]
        before = svc.connection_pool_size
        svc.connection_pool_size = size
        self.audit.append(f"pool {service} {before}->{size}")
        if self.root_cause == f"connection_pool_exhaustion:{service}" and size > before:
            svc.error_rate = 0.002
            svc.latency_p99_ms = 210.0
            self.log_lines[service] = []
            self.root_cause = None
        return {"service": service, "size_before": before, "size_after": size}

    def drain_and_failover(self, service: str) -> dict[str, Any]:
        self.audit.append(f"failover {service}")
        return {"service": service, "status": "failed over to standby region"}

    def page_oncall(self, service: str, message: str, severity: str = "SEV2") -> dict[str, Any]:
        self.audit.append(f"page {service} {severity}")
        return {"paged": True, "service": service, "severity": severity, "message": message}


# --------------------------------------------------------------------------
# Tool registry
# --------------------------------------------------------------------------
@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., dict]
    write: bool = False

    def __call__(self, **kwargs) -> dict:
        return self.fn(**kwargs)


def read_tools(cluster: SimulatedCluster) -> dict[str, Tool]:
    return {
        t.name: t
        for t in [
            Tool("get_metrics", "Error rate, latency, saturation, replica count.", cluster.get_metrics),
            Tool("get_logs", "Recent log lines and the most frequent error patterns.", cluster.get_logs),
            Tool("get_deployments", "Deploys in the last N hours, with current and previous version.", cluster.get_deployments),
            Tool("get_dependencies", "Downstream services and their health.", cluster.get_dependencies),
            Tool("get_connections", "Connection pool size and utilization.", cluster.get_connections),
        ]
    }


def write_tools(cluster: SimulatedCluster) -> dict[str, Tool]:
    return {
        t.name: t
        for t in [
            Tool("restart_pods", "Restart N pods of a service.", cluster.restart_pods, write=True),
            Tool("scale", "Set replica count.", cluster.scale, write=True),
            Tool("rollback_deploy", "Roll a service back to its previous version.", cluster.rollback_deploy, write=True),
            Tool("set_feature_flag", "Toggle a feature flag.", cluster.set_feature_flag, write=True),
            Tool("resize_connection_pool", "Change connection pool size.", cluster.resize_connection_pool, write=True),
            Tool("drain_and_failover", "Drain a service and fail over to standby.", cluster.drain_and_failover, write=True),
            Tool("page_oncall", "Page the on-call engineer.", cluster.page_oncall, write=True),
        ]
    }


def inverse_of(action: Action, cluster: SimulatedCluster) -> Action | None:
    """The compensating action, captured *before* execution.

    Computed from live state, not from the plan: the planner's idea of the
    current replica count may be stale by the time the executor runs, and a
    rollback to the wrong number is its own incident.
    """
    service = action.target.split("/")[-1]
    svc = cluster.services.get(service)
    if svc is None:
        return None
    if action.tool == "scale":
        return Action("scale", action.target, {"service": service, "replicas": svc.replicas}, "undo scale")
    if action.tool == "rollback_deploy":
        return Action("rollback_deploy", action.target, {"service": service, "to_version": svc.version}, "undo rollback")
    if action.tool == "resize_connection_pool":
        return Action(
            "resize_connection_pool",
            action.target,
            {"service": service, "size": svc.connection_pool_size},
            "undo pool resize",
        )
    if action.tool == "set_feature_flag":
        flag = action.params.get("flag", "")
        return Action(
            "set_feature_flag",
            action.target,
            {"service": service, "flag": flag, "enabled": svc.feature_flags.get(flag, False)},
            "undo flag change",
        )
    # restart_pods and page_oncall have no inverse — restarting is not undoable,
    # and you cannot un-page someone. Both are low-harm, which is why they are
    # allowed to be irreversible.
    return None
