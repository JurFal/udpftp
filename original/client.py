import socket
import struct
import os
import stat
import re
import sys
import time
import random

CLIENT_PORT = 7777
FILE_SIZE = 1024

packet_struct = struct.Struct('I1024s')

if __name__ == "__main__":
    server_ip="172.22.2.160"
    #file_name = "chain.py"
    file_name = "bomb2.tar"
    s = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    data = (file_name).encode('utf-8')
    server_addr=(server_ip,CLIENT_PORT)
    s.sendto(data,server_addr)
    f = open(file_name,"rb")
    while True:
        data = f.read(FILE_SIZE)
        if str(data)!="b''":
            end_flag = 0
            s.sendto(packet_struct.pack(*(end_flag,data)),server_addr)
        else:
            data = 'end'.encode('utf-8')
            end_flag = 1
            s.sendto(packet_struct.pack(*(end_flag,data)),server_addr)
            break

    s.close()