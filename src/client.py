#!/usr/bin/env python3
"""Team 19 - TCP File Sharing Client."""
import socket, json, struct, hashlib, os, time, argparse, sys

CHUNK = 64 * 1024

def recv_exact(s, n):
    buf = bytearray()
    while len(buf) < n:
        c = s.recv(min(CHUNK, n - len(buf)))
        if not c:
            raise ConnectionError("connection closed")
        buf += c
    return bytes(buf)

def send_msg(s, obj):
    d = json.dumps(obj).encode()
    s.sendall(struct.pack("!I", len(d)) + d)

def recv_msg(s):
    n = struct.unpack("!I", recv_exact(s, 4))[0]
    return json.loads(recv_exact(s, n))

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            c = f.read(CHUNK)
            if not c:
                break
            h.update(c)
    return h.hexdigest()

class Client:
    def __init__(self, host, port, user, password):
        self.s = socket.create_connection((host, port), timeout=120)
        send_msg(self.s, {"cmd": "LOGIN", "user": user, "password": password})
        r = recv_msg(self.s)
        if r["status"] != "ok":
            raise PermissionError(r.get("msg", "login failed"))

    def list(self):
        send_msg(self.s, {"cmd": "LIST"})
        return recv_msg(self.s)["files"]

    def upload(self, path):
        size = os.path.getsize(path)
        digest = sha256_file(path)
        send_msg(self.s, {"cmd": "UPLOAD", "name": os.path.basename(path),
                          "size": size, "sha256": digest})
        r = recv_msg(self.s)
        if r["status"] != "ready":
            raise PermissionError(r.get("msg"))
        t0 = time.time()
        with open(path, "rb") as f:
            while True:
                c = f.read(CHUNK)
                if not c:
                    break
                self.s.sendall(c)
        r = recv_msg(self.s)
        dt = time.time() - t0
        if r["status"] != "ok":
            raise IOError(r.get("msg"))
        return size, dt

    def download(self, name, outdir="."):
        send_msg(self.s, {"cmd": "DOWNLOAD", "name": name})
        r = recv_msg(self.s)
        if r["status"] != "ok":
            raise PermissionError(r.get("msg"))
        size, want = r["size"], r["sha256"]
        os.makedirs(outdir, exist_ok=True)
        out = os.path.join(outdir, name)
        h = hashlib.sha256()
        got = 0
        t0 = time.time()
        with open(out, "wb") as f:
            while got < size:
                c = self.s.recv(min(CHUNK, size - got))
                if not c:
                    raise ConnectionError("interrupted download")
                f.write(c)
                h.update(c)
                got += len(c)
        dt = time.time() - t0
        ok = h.hexdigest() == want
        return size, dt, ok

    def close(self):
        try:
            send_msg(self.s, {"cmd": "QUIT"})
        except OSError:
            pass
        self.s.close()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("-u", "--user", required=True)
    ap.add_argument("-p", "--password", required=True)
    ap.add_argument("--out", default="downloads")
    ap.add_argument("action", choices=["list", "upload", "download"])
    ap.add_argument("arg", nargs="?")
    a = ap.parse_args()
    try:
        c = Client(a.host, a.port, a.user, a.password)
    except PermissionError as e:
        print(f"LOGIN FAILED: {e}")
        sys.exit(2)
    try:
        if a.action == "list":
            for f in c.list():
                print(f"{f['name']}  {f['size']} bytes  owner={f['owner']}  {f['uploaded']}")
        elif a.action == "upload":
            size, dt = c.upload(a.arg)
            print(f"UPLOAD OK {size} bytes in {dt:.3f}s = {size*8/dt/1e6:.2f} Mbps")
        else:
            size, dt, ok = c.download(a.arg, a.out)
            print(f"DOWNLOAD {size} bytes in {dt:.3f}s = {size*8/dt/1e6:.2f} Mbps")
            print("INTEGRITY OK" if ok else "INTEGRITY FAILED")
            sys.exit(0 if ok else 3)
    except (PermissionError, IOError, ConnectionError) as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    finally:
        c.close()

if __name__ == "__main__":
    main()
