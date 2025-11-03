import select
import time
from typing import Callable, Dict, Tuple

from .protocol import Packet, make_ack, make_data, DATA, ACK
from .congestion import CongestionControl
from .utils import log

RecvFunc = Callable[[int], Tuple[Packet, Tuple[str, int]]]


class Reliability:
    def __init__(self, strategy: str = "gbn", mss: int = 1200, timeout_ms: int = 200, dup_ack_threshold: int = 3):
        self.strategy = strategy.lower()
        assert self.strategy in ("gbn", "sr")
        self.mss = mss
        self.timeout_ms = timeout_ms
        self.dup_ack_threshold = dup_ack_threshold

    # Sender side: send data_bytes using provided sock/addr, receive acks via recv_packet()
    def send(self, sock, addr, data_bytes: bytes, cc: CongestionControl, recv_packet: RecvFunc, recv_adv_window: int = 64) -> Dict:
        total_packets = (len(data_bytes) + self.mss - 1) // self.mss
        base = 0
        next_seq = 0
        send_buffer: Dict[int, Packet] = {}
        send_times: Dict[int, float] = {}
        acked: Dict[int, bool] = {}
        last_acked_seq = -1
        dup_ack_count = 0
        start_time = time.monotonic()

        def allowed_window():
            return min(cc.window(), recv_adv_window)

        while base < total_packets:
            # transmit as window allows
            while next_seq < total_packets and next_seq < base + allowed_window():
                offset = next_seq * self.mss
                chunk = data_bytes[offset:offset + self.mss]
                pkt = make_data(seq=next_seq, ack=0, window=recv_adv_window, payload=chunk)
                sock.sendto(pkt.pack(), addr)
                send_buffer[next_seq] = pkt
                send_times[next_seq] = time.monotonic()
                next_seq += 1
            # wait for ack or timeout
            # use non-blocking select on the socket
            pkt_obj = None
            if recv_packet is not None:
                try:
                    pkt_obj, _ = recv_packet(self.timeout_ms)
                except Exception:
                    pkt_obj = None
            else:
                rlist, _, _ = select.select([sock], [], [], self.timeout_ms / 1000.0)
                if rlist:
                    try:
                        raw, raddr = sock.recvfrom(65535)
                        pkt_obj = Packet.unpack(raw)
                    except Exception:
                        pkt_obj = None
            if pkt_obj:
                try:
                    pkt = pkt_obj
                    if pkt.flags & ACK:
                        ackno = pkt.ack
                        rtt = None
                        if ackno - 1 in send_times:
                            rtt = (time.monotonic() - send_times.get(ackno - 1, time.monotonic())) * 1000.0
                        cc.on_ack(rtt_ms=rtt if rtt is not None else 0.0, bytes_acked=self.mss)
                        if self.strategy == "gbn":
                            if ackno > base:
                                # cumulative ack
                                for s in range(base, ackno):
                                    acked[s] = True
                                    send_buffer.pop(s, None)
                                    send_times.pop(s, None)
                                base = ackno
                                dup_ack_count = 0
                                last_acked_seq = ackno
                            elif ackno == base:
                                # duplicate ack
                                if last_acked_seq == ackno:
                                    dup_ack_count += 1
                                    if dup_ack_count >= self.dup_ack_threshold and base in send_buffer:
                                        # fast retransmit oldest unacked
                                        sock.sendto(send_buffer[base].pack(), addr)
                                else:
                                    last_acked_seq = ackno
                                    dup_ack_count = 1
                        else:  # SR
                            # ack refers to specific seq (seq+1)
                            s = ackno - 1
                            if s in send_buffer:
                                acked[s] = True
                                send_buffer.pop(s, None)
                                send_times.pop(s, None)
                            # slide base forward while consecutive acked
                            while base in acked and acked[base]:
                                base += 1
                except Exception:
                    pass
            else:
                # timeout
                if self.strategy == "gbn":
                    # retransmit oldest unacked
                    if base in send_buffer:
                        sock.sendto(send_buffer[base].pack(), addr)
                    cc.on_timeout()
                else:  # SR
                    # retransmit any outstanding unacked
                    for s, pkt in list(send_buffer.items()):
                        if s not in acked:
                            sock.sendto(pkt.pack(), addr)
                    cc.on_timeout()
        duration = time.monotonic() - start_time
        return {"packets": total_packets, "duration_s": duration}

    # Receiver side: receive packets and ack them, assemble in-order data
    def recv(self, sock, addr, total_size: int, send_adv_window: int = 64) -> bytes:
        total_packets = (total_size + self.mss - 1) // self.mss
        if self.strategy == "gbn":
            expected = 0
            out = bytearray()
            while expected < total_packets:
                raw, raddr = sock.recvfrom(65535)
                pkt = Packet.unpack(raw)
                if pkt.flags & DATA:
                    if pkt.seq == expected:
                        out.extend(pkt.payload)
                        expected += 1
                        ackpkt = make_ack(ack=expected, window=send_adv_window)
                        sock.sendto(ackpkt.pack(), addr)
                    else:
                        # re-ack last in-order
                        ackpkt = make_ack(ack=expected, window=send_adv_window)
                        sock.sendto(ackpkt.pack(), addr)
            return bytes(out[:total_size])
        else:
            expected = 0
            received: Dict[int, bytes] = {}
            out = bytearray()
            while expected < total_packets:
                raw, raddr = sock.recvfrom(65535)
                pkt = Packet.unpack(raw)
                if pkt.flags & DATA:
                    received[pkt.seq] = pkt.payload
                    # ack this packet specifically
                    ackpkt = make_ack(ack=pkt.seq + 1, window=send_adv_window)
                    sock.sendto(ackpkt.pack(), addr)
                    # flush in-order
                    while expected in received:
                        out.extend(received.pop(expected))
                        expected += 1
            return bytes(out[:total_size])