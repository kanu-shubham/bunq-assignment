"""ragx — retrieval-augmented answering over company documentation.

See DESIGN.md for the architecture and the trade-offs; `factory.build()` is the
composition root.
"""

from .config import Config
from .factory import RagSystem, build
from .types import Answer, Chunk, Citation, Document, Principal, ScoredChunk, Visibility

__all__ = [
    "Answer",
    "Chunk",
    "Citation",
    "Config",
    "Document",
    "Principal",
    "RagSystem",
    "ScoredChunk",
    "Visibility",
    "build",
]
