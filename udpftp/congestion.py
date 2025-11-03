from dataclasses import dataclass


@dataclass
class CongestionControl:
    cwnd: float = 1.0  # measured in packets
    ssthresh: float = 32.0
    max_cwnd: float = 2000.0

    def on_ack(self, rtt_ms: float, bytes_acked: int) -> None:
        raise NotImplementedError

    def on_loss(self) -> None:
        raise NotImplementedError

    def on_timeout(self) -> None:
        self.ssthresh = max(self.cwnd / 2.0, 2.0)
        self.cwnd = 1.0

    def window(self) -> int:
        return max(1, min(int(self.cwnd), int(self.max_cwnd)))


class Reno(CongestionControl):
    def __init__(self, cwnd: float = 1.0, ssthresh: float = 32.0):
        super().__init__(cwnd=cwnd, ssthresh=ssthresh)

    def on_ack(self, rtt_ms: float, bytes_acked: int) -> None:
        if self.cwnd < self.ssthresh:
            # slow start: increase cwnd by 1 packet per ACK
            self.cwnd += 1.0
        else:
            # congestion avoidance: additive increase ~ 1 packet per RTT
            self.cwnd += 1.0 / max(self.cwnd, 1.0)
        self.cwnd = min(self.cwnd, self.max_cwnd)

    def on_loss(self) -> None:
        # Fast retransmit/fast recovery approximation
        self.ssthresh = max(self.cwnd / 2.0, 2.0)
        self.cwnd = self.ssthresh


class Vegas(CongestionControl):
    def __init__(self, cwnd: float = 1.0, ssthresh: float = 32.0, alpha: float = 1.0, beta: float = 3.0):
        super().__init__(cwnd=cwnd, ssthresh=ssthresh)
        self.alpha = alpha
        self.beta = beta
        self.base_rtt_ms = None  # type: float | None

    def on_ack(self, rtt_ms: float, bytes_acked: int) -> None:
        # Track minimum RTT as base RTT
        if rtt_ms is not None:
            if self.base_rtt_ms is None:
                self.base_rtt_ms = rtt_ms
            else:
                self.base_rtt_ms = min(self.base_rtt_ms, rtt_ms)
        if self.base_rtt_ms is None or rtt_ms is None or rtt_ms <= 0:
            # Fallback: behave like Reno slow start until we have RTT
            if self.cwnd < self.ssthresh:
                self.cwnd += 1.0
            else:
                self.cwnd += 1.0 / max(self.cwnd, 1.0)
            return
        # Estimate expected vs actual throughput
        # expected = cwnd / base_rtt; actual = cwnd / rtt
        # diff = expected - actual = cwnd * (1/base_rtt - 1/rtt)
        expected = self.cwnd / self.base_rtt_ms
        actual = self.cwnd / rtt_ms
        diff = expected - actual
        # Adjust cwnd based on diff thresholds
        if diff < self.alpha:
            self.cwnd += 1.0  # underutilized, increase
        elif diff > self.beta:
            self.cwnd -= 1.0  # overutilized, decrease
        # else keep cwnd
        if self.cwnd < 1.0:
            self.cwnd = 1.0
        if self.cwnd > self.max_cwnd:
            self.cwnd = self.max_cwnd

    def on_loss(self) -> None:
        # Vegas prefers to avoid loss; reduce cwnd gently
        self.cwnd = max(self.cwnd - 1.0, 1.0)
        self.ssthresh = max(self.cwnd, 2.0)