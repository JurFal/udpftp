#!/usr/bin/python3
import argparse
import os
import socket
import sys
import time

from udpftp.protocol import make_ctrl, Packet
from udpftp.reliability import Reliability
from udpftp.congestion import Reno, Vegas
from udpftp.utils import md5_file, md5_bytes, log


def parse_args():
    p = argparse.ArgumentParser(description="UDP-based FTP client with reliability and congestion control")
    p.add_argument('--server-host', required=True)
    p.add_argument('--server-port', type=int, required=True)
    p.add_argument('--op', choices=['upload','download'], required=True)
    p.add_argument('--file', help='local file path for upload or download output')
    p.add_argument('--remote-name', required=True, help='remote filename on server')
    p.add_argument('--algo', choices=['gbn','sr'], default='gbn')
    p.add_argument('--cc', choices=['reno','vegas'], default='reno')
    p.add_argument('--mss', type=int, default=1200)
    p.add_argument('--window', type=int, default=64, help='advertised receive window (packets)')
    return p.parse_args()


def make_cc(cc_name: str):
    if cc_name == 'reno':
        return Reno()
    elif cc_name == 'vegas':
        return Vegas()
    else:
        raise ValueError('unknown cc')

class CountingSocket:
    """Socket wrapper that counts bytes sent and received"""
    def __init__(self, real_sock):
        self.real_sock = real_sock
        self.bytes_sent = 0
        self.bytes_recv = 0
    
    def sendto(self, data, addr):
        self.bytes_sent += len(data)
        return self.real_sock.sendto(data, addr)
    
    def recvfrom(self, bufsize):
        data, addr = self.real_sock.recvfrom(bufsize)
        self.bytes_recv += len(data)
        return data, addr
    
    def setsockopt(self, *args):
        return self.real_sock.setsockopt(*args)

    # Ensure compatibility with select.select and timeout APIs
    def fileno(self):
        return self.real_sock.fileno()

    def settimeout(self, value):
        return self.real_sock.settimeout(value)

    def gettimeout(self):
        return self.real_sock.gettimeout()


def main():
    args = parse_args()
    addr = (args.server_host, args.server_port)
    real_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    real_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4*1024*1024)
    real_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4*1024*1024)
    
    # Wrap socket to count bytes
    sock = CountingSocket(real_sock)

    if args.op == 'upload':
        if not args.file or not os.path.exists(args.file):
            print('file required for upload and must exist', file=sys.stderr)
            sys.exit(1)
        size = os.path.getsize(args.file)
        ctrl = make_ctrl(f"CMD UPLOAD {args.remote_name} {size} {args.algo} {args.cc} {args.mss} {args.window}")
        sock.sendto(ctrl.pack(), addr)
        # wait for server OK
        raw, raddr = sock.recvfrom(65535)
        pkt = Packet.unpack(raw)
        if not pkt.flags & 0x10:  # CTRL
            print('unexpected response', file=sys.stderr)
            sys.exit(2)
        if pkt.payload.decode().strip() != 'OK':
            print('server rejected request', file=sys.stderr)
            sys.exit(3)
        # send file
        with open(args.file, 'rb') as f:
            data = f.read()
        cc = make_cc(args.cc)
        rel = Reliability(strategy=args.algo, mss=args.mss)
        
        # Reset counters before transmission
        bytes_sent_before = sock.bytes_sent
        t0 = time.monotonic()
        try:
            stats = rel.send(sock, addr, data, cc, recv_packet=None, recv_adv_window=args.window)
            t1 = time.monotonic()
            log('CLIENT', f"upload done: packets={stats['packets']} duration={stats['duration_s']:.2f}s")
            success = True
        except Exception as e:
            t1 = time.monotonic()
            log('ERROR', f"Upload failed: {e}")
            success = False
        
        # Calculate client-side upload statistics
        bytes_sent_for_transfer = sock.bytes_sent - bytes_sent_before
        duration_s = max(t1 - t0, 1e-9)
        file_size = size
        throughput_bps = file_size / duration_s if success else 0
        utilization = file_size / max(bytes_sent_for_transfer, 1) if success else 0
        status = "SUCCESS" if success else "FAILED"
        log('CLIENT_METRIC', f"UPLOAD {status} name={args.remote_name} size={file_size} bytes_sent={bytes_sent_for_transfer} duration={duration_s:.3f}s throughput={throughput_bps:.2f}B/s utilization={utilization:.4f}")
        
        if success:
            # wait for server md5
            raw, raddr = sock.recvfrom(65535)
            pkt = Packet.unpack(raw)
            if not pkt.flags & 0x10:
                print('missing md5 ctrl', file=sys.stderr)
                sys.exit(4)
            server_md5 = pkt.payload.decode().strip()
            local_md5 = md5_file(args.file)
            if server_md5 != local_md5:
                print('MD5 mismatch after upload, aborting!', file=sys.stderr)
                sys.exit(5)
            print('Upload verified: MD5 ok')
        else:
            sys.exit(6)
    else:
        # download
        ctrl = make_ctrl(f"CMD DOWNLOAD {args.remote_name} 0 {args.algo} {args.cc} {args.mss} {args.window}")
        sock.sendto(ctrl.pack(), addr)
        raw, raddr = sock.recvfrom(65535)
        pkt = Packet.unpack(raw)
        if not pkt.flags & 0x10:
            print('unexpected response', file=sys.stderr)
            sys.exit(2)
        parts = pkt.payload.decode().strip().split()
        # expect: SIZE <n> MD5 <hex> OK  (5 tokens)
        if len(parts) >= 5 and parts[0] == 'SIZE' and parts[2] == 'MD5' and parts[4] == 'OK':
            total_size = int(parts[1])
            server_md5 = parts[3]
        elif parts and parts[0] == 'ERR':
            print('server error: ' + ' '.join(parts), file=sys.stderr)
            sys.exit(3)
        else:
            print('bad server reply: ' + ' '.join(parts), file=sys.stderr)
            sys.exit(3)
        rel = Reliability(strategy=args.algo, mss=args.mss)
        
        # Reset counters before transmission
        bytes_recv_before = sock.bytes_recv
        t0 = time.monotonic()
        try:
            data = rel.recv(sock, addr, total_size, send_adv_window=args.window)
            t1 = time.monotonic()
            success = True
        except Exception as e:
            t1 = time.monotonic()
            log('ERROR', f"Download failed: {e}")
            success = False
            data = b''
        
        # Calculate client-side download statistics
        bytes_recv_for_transfer = sock.bytes_recv - bytes_recv_before
        duration_s = max(t1 - t0, 1e-9)
        file_size = total_size
        throughput_bps = file_size / duration_s if success else 0
        utilization = file_size / max(bytes_recv_for_transfer, 1) if success else 0
        status = "SUCCESS" if success else "FAILED"
        log('CLIENT_METRIC', f"DOWNLOAD {status} name={args.remote_name} size={file_size} bytes_recv={bytes_recv_for_transfer} duration={duration_s:.3f}s throughput={throughput_bps:.2f}B/s utilization={utilization:.4f}")
        
        if success:
            local_md5 = md5_bytes(data)
            if server_md5 != local_md5:
                print('MD5 mismatch after download, aborting!', file=sys.stderr)
                sys.exit(5)
            if not args.file:
                print('download requires --file as output path', file=sys.stderr)
                sys.exit(6)
            with open(args.file, 'wb') as f:
                f.write(data)
            print('Download verified: MD5 ok')
        else:
            sys.exit(7)


if __name__ == '__main__':
    main()