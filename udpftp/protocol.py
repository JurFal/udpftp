import struct
import time
from dataclasses import dataclass

# Packet flags
SYN = 0x01
ACK = 0x02
FIN = 0x04
DATA = 0x08
CTRL = 0x10

HEADER_FMT = "!IIBHQH"  # seq, ack, flags, window, ts_us, length
HEADER_LEN = struct.calcsize(HEADER_FMT)
DEFAULT_MSS = 1200  # payload size target (excluding header)
MAX_PACKET_SIZE = HEADER_LEN + DEFAULT_MSS


def now_us() -> int:
    return time.monotonic_ns() // 1000


@dataclass
class Packet:
    seq: int
    ack: int
    flags: int
    window: int
    ts_us: int
    payload: bytes

    def pack(self) -> bytes:
        length = len(self.payload) if self.payload else 0
        return struct.pack(HEADER_FMT, self.seq, self.ack, self.flags, self.window, self.ts_us, length) + (self.payload or b"")

    @staticmethod
    def unpack(data: bytes) -> "Packet":
        if len(data) < HEADER_LEN:
            raise ValueError("packet too short")
        seq, ack, flags, window, ts_us, length = struct.unpack(HEADER_FMT, data[:HEADER_LEN])
        payload = data[HEADER_LEN:HEADER_LEN+length]
        return Packet(seq=seq, ack=ack, flags=flags, window=window, ts_us=ts_us, payload=payload)

    def is_flag(self, f: int) -> bool:
        return (self.flags & f) != 0


def make_ctrl(payload_text: str, seq: int = 0, ack: int = 0, window: int = 0) -> Packet:
    return Packet(seq=seq, ack=ack, flags=CTRL, window=window, ts_us=now_us(), payload=payload_text.encode())


def make_ack(ack: int, window: int, dup_count: int = 0) -> Packet:
    # dup_count can be encoded in ack high bits if needed; keep simple here
    return Packet(seq=0, ack=ack, flags=ACK, window=window, ts_us=now_us(), payload=b"")


def make_data(seq: int, ack: int, window: int, payload: bytes) -> Packet:
    return Packet(seq=seq, ack=ack, flags=DATA, window=window, ts_us=now_us(), payload=payload)


def make_syn(window: int) -> Packet:
    return Packet(seq=0, ack=0, flags=SYN, window=window, ts_us=now_us(), payload=b"")


def make_fin() -> Packet:
    return Packet(seq=0, ack=0, flags=FIN, window=0, ts_us=now_us(), payload=b"")