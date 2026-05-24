"""FastAPI wrapper around the trained Iris classifier.

POST /predict -> single prediction
POST /predict/batch -> batched predictions (one inference call for N rows)
GET  /healthz -> liveness
"""
from pathlib import Path
from typing import List

import joblib
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

MODEL_PATH = Path(__file__).parent / "model.joblib"

app = FastAPI(title="iris-serving", version="0.1.0")

_bundle = joblib.load(MODEL_PATH)
_model = _bundle["model"]
_classes: List[str] = _bundle["classes"]


class IrisFeatures(BaseModel):
    sepal_length: float = Field(..., ge=0)
    sepal_width: float = Field(..., ge=0)
    petal_length: float = Field(..., ge=0)
    petal_width: float = Field(..., ge=0)

    def to_row(self) -> List[float]:
        return [self.sepal_length, self.sepal_width, self.petal_length, self.petal_width]


class Prediction(BaseModel):
    label: str
    class_id: int
    proba: List[float]


class BatchRequest(BaseModel):
    rows: List[IrisFeatures]


class BatchResponse(BaseModel):
    predictions: List[Prediction]


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "classes": _classes}


@app.post("/predict", response_model=Prediction)
def predict(features: IrisFeatures) -> Prediction:
    X = np.asarray([features.to_row()], dtype=np.float64)
    proba = _model.predict_proba(X)[0]
    class_id = int(np.argmax(proba))
    return Prediction(label=_classes[class_id], class_id=class_id, proba=proba.tolist())


@app.post("/predict/batch", response_model=BatchResponse)
def predict_batch(req: BatchRequest) -> BatchResponse:
    if not req.rows:
        raise HTTPException(status_code=400, detail="rows must not be empty")
    X = np.asarray([r.to_row() for r in req.rows], dtype=np.float64)
    proba = _model.predict_proba(X)
    class_ids = np.argmax(proba, axis=1)
    preds = [
        Prediction(label=_classes[int(c)], class_id=int(c), proba=p.tolist())
        for c, p in zip(class_ids, proba)
    ]
    return BatchResponse(predictions=preds)
