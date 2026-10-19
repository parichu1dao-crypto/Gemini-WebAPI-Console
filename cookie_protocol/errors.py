"""Safe public failures: never include URLs, payloads, cookies or upstream text."""
import asyncio


class NotSubmitted(RuntimeError):
    submitted = False
    uncertain = False


class SubmissionUncertain(RuntimeError):
    submitted = True
    uncertain = True


class UpstreamErrorCard(SubmissionUncertain):
    """A submitted request returned an error card; never automatically resend."""


class GenerationCancelled(asyncio.CancelledError):
    def __init__(self, submitted):
        self.submitted = self.uncertain = submitted
        super().__init__('Generation cancelled; reconcile before retry' if submitted
                         else 'Generation cancelled before submission')
