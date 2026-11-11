#!/usr/bin/env python3
"""Starts the POX controller (csfs_pox), or sends a command to the running one.
  sudo python3 controller/controller.py            start POX
  python3 controller/controller.py --send "block h1"   operator command"""
import argparse, json, os, socket, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOCK = "/tmp/sdn_ctl.sock"

def send(cmd):
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(SOCK)
    c.sendall(json.dumps({"event": "CMD", "cmd": cmd}).encode() + b"\n")
    data = b""
    while True:
        ch = c.recv(4096)
        if not ch:
            break
        data += ch
    print(data.decode().strip())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, default=10)
    ap.add_argument("--heavy-mb", type=float, default=0, help="auto-throttle users after this many MB (0=off)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--send", help="send a command to the running controller")
    a = ap.parse_args()
    if a.send:
        send(a.send)
        return
    if os.geteuid() != 0:
        sys.exit("run with sudo")
    pox = os.path.join(ROOT, "pox", "pox.py")
    if not os.path.exists(pox):
        sys.exit("POX not found at " + pox)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(ROOT, "controller") + ":" + env.get("PYTHONPATH", "")
    cmd = [sys.executable, pox, "log.level", "--INFO", "csfs_pox",
           "--clients=%d" % a.clients, "--heavy_mb=%s" % a.heavy_mb]
    if a.quiet:
        cmd.append("--quiet")
    os.chdir(os.path.join(ROOT, "pox"))
    os.execve(sys.executable, cmd, env)

if __name__ == "__main__":
    main()
