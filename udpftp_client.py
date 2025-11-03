#!/usr/bin/python3
import argparse
import os
import socket
import sys

from udpftp.protocol import make_ctrl, Packet
from udpftp.reliability import Reliability
from udpftp.congestion import Reno, Vegas
from udpftp.utils import md5_file, md5_bytes, log


def parse_args():
    p = argparse.ArgumentParser(description="UDP-based FTP client (separate entry) with reliability and congestion control")
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


def main():
    args = parse_args()
    addr = (args.server_host, args.server_port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4*1024*1024)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4*1024*1024)

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
        stats = rel.send(sock, addr, data, cc, recv_packet=None, recv_adv_window=args.window)
        log('CLIENT', f"upload done: packets={stats['packets']} duration={stats['duration_s']:.2f}s")
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
        # download
        ctrl = make_ctrl(f"CMD DOWNLOAD {args.remote_name} 0 {args.algo} {args.cc} {args.mss} {args.window}")
        sock.sendto(ctrl.pack(), addr)
        raw, raddr = sock.recvfrom(65535)
        pkt = Packet.unpack(raw)
        if not pkt.flags & 0x10:
            print('unexpected response', file=sys.stderr)
            sys.exit(2)
        parts = pkt.payload.decode().strip().split()
        # expect: SIZE <n> MD5 <hex> OK
        if len(parts) < 6 or parts[0] != 'SIZE' or parts[2] != 'MD5' or parts[4] != 'OK':
            print('bad server reply', file=sys.stderr)
            sys.exit(3)
        total_size = int(parts[1])
        server_md5 = parts[3]
        rel = Reliability(strategy=args.algo, mss=args.mss)
        data = rel.recv(sock, addr, total_size, send_adv_window=args.window)
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


if __name__ == '__main__':
    main()