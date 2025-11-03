#!/usr/bin/python3
import argparse
import os
import socket
import threading
from collections import deque
import time

from udpftp.protocol import Packet, make_ctrl
from udpftp.reliability import Reliability
from udpftp.congestion import Reno, Vegas
from udpftp.utils import ensure_dir, md5_bytes, log


def parse_args():
    p = argparse.ArgumentParser(description='UDP-based FTP server (separate entry) with reliability and congestion control')
    p.add_argument('--host', default='0.0.0.0')
    p.add_argument('--port', type=int, default=9000)
    p.add_argument('--storage', default='./storage')
    return p.parse_args()


def make_cc(name: str):
    return Reno() if name == 'reno' else Vegas()


class Session:
    def __init__(self, sock, addr, role, remote_name, algo, cc_name, mss, window, storage, size=0):
        self.sock = sock
        self.addr = addr
        self.role = role
        self.remote_name = remote_name
        self.rel = Reliability(strategy=algo, mss=mss)
        self.cc = make_cc(cc_name)
        self.window = window
        self.storage = storage
        self.size = size
        self.queue = deque()
        self.cv = threading.Condition()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.bytes_recv_total = 0
        self.bytes_sent_total = 0
        self.algo = algo
        self.cc_name = cc_name

    def start(self):
        self.thread.start()

    def enqueue_raw(self, raw: bytes):
        with self.cv:
            self.queue.append(raw)
            self.cv.notify()

    def recv_packet_cb(self, timeout_ms: int):
        with self.cv:
            if not self.queue:
                self.cv.wait(timeout_ms / 1000.0)
            if not self.queue:
                raise TimeoutError('session recv timeout')
            raw = self.queue.popleft()
            pkt = Packet.unpack(raw)
            return pkt, self.addr

    def run(self):
        ensure_dir(self.storage)
        target_path = os.path.join(self.storage, self.remote_name)
        if self.role == 'upload':
            log('SERVER', f"Receiving upload {self.remote_name} size={self.size}")
            class QueueSocket:
                def __init__(self, real_sock, session):
                    self.real_sock = real_sock
                    self.session = session
                def recvfrom(self, bufsize):
                    with self.session.cv:
                        while not self.session.queue:
                            self.session.cv.wait()
                        raw = self.session.queue.popleft()
                        self.session.bytes_recv_total += len(raw)
                        return raw, self.session.addr
                def sendto(self, data, addr):
                    self.real_sock.sendto(data, addr)
            qs = QueueSocket(self.sock, self)
            t0 = time.monotonic()
            try:
                data = self.rel.recv(qs, self.addr, total_size=self.size, send_adv_window=self.window)
                t1 = time.monotonic()
                with open(target_path, 'wb') as f:
                    f.write(data)
                server_md5 = md5_bytes(data)
                self.sock.sendto(make_ctrl(server_md5).pack(), self.addr)
                log('SERVER', f"Upload stored {target_path}, MD5={server_md5}")
                success = True
            except Exception as e:
                t1 = time.monotonic()
                log('ERROR', f"Upload failed for {self.remote_name}: {e}")
                success = False
            finally:
                # Always record metrics, even on failure
                duration_s = max(t1 - t0, 1e-9)
                file_size = self.size
                throughput_bps = file_size / duration_s if success else 0
                utilization = file_size / max(self.bytes_recv_total, 1) if success else 0
                status = "SUCCESS" if success else "FAILED"
                log('METRIC', f"UPLOAD {status} name={self.remote_name} size={file_size} bytes_recv_total={self.bytes_recv_total} duration={duration_s:.3f}s throughput={throughput_bps:.2f}B/s utilization={utilization:.4f}")
        else:
            # Download
            full_path = target_path
            if not os.path.exists(full_path):
                self.sock.sendto(make_ctrl('ERR NOFILE').pack(), self.addr)
                return
            with open(full_path, 'rb') as f:
                data = f.read()
            md5 = md5_bytes(data)
            size = len(data)
            self.sock.sendto(make_ctrl(f"SIZE {size} MD5 {md5} OK").pack(), self.addr)
            # Wrap socket to count bytes sent by server
            class CountingSocket:
                def __init__(self, real_sock, session):
                    self.real_sock = real_sock
                    self.session = session
                def sendto(self, data, addr):
                    self.session.bytes_sent_total += len(data)
                    return self.real_sock.sendto(data, addr)
            cs = CountingSocket(self.sock, self)
            t0 = time.monotonic()
            try:
                stats = self.rel.send(cs, self.addr, data, self.cc, recv_packet=self.recv_packet_cb, recv_adv_window=self.window)
                t1 = time.monotonic()
                log('SERVER', f"Download {self.remote_name} done: packets={stats['packets']} duration={stats['duration_s']:.2f}s")
                success = True
                duration_from_stats = stats.get('duration_s', max(t1 - t0, 1e-9))
            except Exception as e:
                t1 = time.monotonic()
                log('ERROR', f"Download failed for {self.remote_name}: {e}")
                success = False
                duration_from_stats = max(t1 - t0, 1e-9)
            finally:
                # Always record metrics, even on failure
                duration_s = duration_from_stats
                file_size = size
                throughput_bps = file_size / duration_s if success else 0
                utilization = file_size / max(self.bytes_sent_total, 1) if success else 0
                status = "SUCCESS" if success else "FAILED"
                log('METRIC', f"DOWNLOAD {status} name={self.remote_name} size={file_size} bytes_sent_total={self.bytes_sent_total} duration={duration_s:.3f}s throughput={throughput_bps:.2f}B/s utilization={utilization:.4f}")

def main():
    args = parse_args()
    ensure_dir(args.storage)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    log('SERVER', f"Listening on {args.host}:{args.port}, storage={args.storage}")

    sessions = {}
    try:
        while True:
            raw, addr = sock.recvfrom(65535)
            pkt = Packet.unpack(raw)
            if pkt.flags & 0x10:  # CTRL
                cmd = pkt.payload.decode().strip().split()
                if len(cmd) < 6 or cmd[0] != 'CMD':
                    continue
                op = cmd[1]
                remote_name = cmd[2]
                size = int(cmd[3])
                algo = cmd[4]
                cc_name = cmd[5]
                mss = int(cmd[6]) if len(cmd) > 6 else 1200
                window = int(cmd[7]) if len(cmd) > 7 else 64
                if op == 'UPLOAD':
                    sock.sendto(make_ctrl('OK').pack(), addr)
                    s = Session(sock, addr, 'upload', remote_name, algo, cc_name, mss, window, args.storage, size=size)
                    sessions[addr] = s
                    s.start()
                elif op == 'DOWNLOAD':
                    s = Session(sock, addr, 'download', remote_name, algo, cc_name, mss, window, args.storage)
                    sessions[addr] = s
                    s.start()
                else:
                    sock.sendto(make_ctrl('ERR CMD').pack(), addr)
            else:
                s = sessions.get(addr)
                if s:
                    s.enqueue_raw(raw)
    except KeyboardInterrupt:
        log('SERVER', 'Shutting down')


if __name__ == '__main__':
    main()