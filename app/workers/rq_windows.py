from rq import SimpleWorker
from rq.timeouts import TimerDeathPenalty


class WindowsSimpleWorker(SimpleWorker):
    """RQ SimpleWorker variant that avoids Unix-only SIGALRM timeouts."""

    death_penalty_class = TimerDeathPenalty
