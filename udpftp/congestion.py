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
        self.ema_rtt_ms = None   # smoothed RTT
        self.rtt_ema_weight = 0.9

    def on_ack(self, rtt_ms: float, bytes_acked: int) -> None:
        # Track minimum RTT as base RTT
        if rtt_ms is not None and rtt_ms > 0:
            if self.base_rtt_ms is None:
                self.base_rtt_ms = rtt_ms
            else:
                self.base_rtt_ms = min(self.base_rtt_ms, rtt_ms)
            # EMA smoothing for RTT
            if self.ema_rtt_ms is None:
                self.ema_rtt_ms = rtt_ms
            else:
                w = self.rtt_ema_weight
                self.ema_rtt_ms = w * self.ema_rtt_ms + (1.0 - w) * rtt_ms

        # Fallback: behave like Reno slow start until we have RTT
        if self.base_rtt_ms is None or self.ema_rtt_ms is None:
            if self.cwnd < self.ssthresh:
                self.cwnd += 1.0
            else:
                self.cwnd += 1.0 / max(self.cwnd, 1.0)
            return

        # Vegas diff in 'packets' (normalize by RTT): diff = cwnd * (1 - base_rtt / rtt)
        rtt_use = max(self.ema_rtt_ms, 1e-6)
        diff_packets = self.cwnd * (1.0 - (self.base_rtt_ms / rtt_use))

        # Adjust cwnd gently: ~1 packet per RTT (approximate via 1/cwnd per ACK)
        if diff_packets < self.alpha:
            # underutilized, increase
            self.cwnd += 1.0 / max(self.cwnd, 1.0)
        elif diff_packets > self.beta:
            # overutilized, decrease
            self.cwnd -= 1.0 / max(self.cwnd, 1.0)
        # else keep cwnd

        if self.cwnd < 1.0:
            self.cwnd = 1.0
        if self.cwnd > self.max_cwnd:
            self.cwnd = self.max_cwnd

    def on_loss(self) -> None:
        # Vegas prefers to avoid loss; reduce cwnd gently
        self.cwnd = max(self.cwnd - 1.0, 1.0)
        self.ssthresh = max(self.cwnd, 2.0)