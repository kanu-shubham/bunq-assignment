"""Locust load test for the iris-serving API.

Run against a running server:
    locust -f locustfile.py --host http://localhost:8000

Headless example (200 users, ramp 50/s, 30s):
    locust -f locustfile.py --host http://localhost:8000 \
        --headless -u 200 -r 50 -t 30s
"""
import random

from locust import HttpUser, between, task


def _row() -> dict:
    return {
        "sepal_length": random.uniform(4.0, 8.0),
        "sepal_width": random.uniform(2.0, 4.5),
        "petal_length": random.uniform(1.0, 7.0),
        "petal_width": random.uniform(0.1, 2.5),
    }


class PredictUser(HttpUser):
    wait_time = between(0, 0.05)

    @task(4)
    def predict_single(self) -> None:
        self.client.post("/predict", json=_row())

    @task(1)
    def predict_batch(self) -> None:
        rows = [_row() for _ in range(32)]
        self.client.post("/predict/batch", json={"rows": rows})
