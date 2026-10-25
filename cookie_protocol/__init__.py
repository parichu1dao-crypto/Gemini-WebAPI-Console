"""Browser-validated processSession Cookie HTTP adapter (no browser body fallback)."""
from .errors import GenerationCancelled, NotSubmitted, SubmissionUncertain, UpstreamErrorCard
from .transport import GenerationResult, generate

__all__ = ['generate', 'GenerationResult', 'NotSubmitted', 'SubmissionUncertain',
           'UpstreamErrorCard', 'GenerationCancelled']
