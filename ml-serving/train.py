"""Train a tiny logistic-regression classifier on Iris and dump it to disk.

The model is not the point — the serving is. We just need something small,
deterministic, and cheap to load at container start.
"""
from pathlib import Path

import joblib
from sklearn.datasets import load_iris
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

MODEL_PATH = Path(__file__).parent / "model.joblib"


def main() -> None:
    X, y = load_iris(return_X_y=True)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    pipe = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=1000)),
        ]
    )
    pipe.fit(X_train, y_train)

    train_acc = pipe.score(X_train, y_train)
    test_acc = pipe.score(X_test, y_test)
    print(f"train acc: {train_acc:.3f}  test acc: {test_acc:.3f}")

    joblib.dump({"model": pipe, "classes": load_iris().target_names.tolist()}, MODEL_PATH)
    print(f"wrote {MODEL_PATH}")


if __name__ == "__main__":
    main()
