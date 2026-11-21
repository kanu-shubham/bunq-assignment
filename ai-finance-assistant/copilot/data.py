"""Loading the (sample) transaction ledger."""
import csv
from functools import lru_cache
from pathlib import Path

from . import config
from .models import Transaction


@lru_cache(maxsize=4)
def load_transactions(path: Path = config.TRANSACTIONS_CSV) -> tuple[Transaction, ...]:
    with open(path, newline="") as f:
        return tuple(Transaction(**row) for row in csv.DictReader(f))
