#!/usr/bin/env python3
"""Team 19 - Concurrent TCP File Sharing Server (thread per client)."""
import socket, threading, json, struct, hashlib, os, time, argparse, secrets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "storage")
USERS = os.path.join(ROOT, "users.json")
META = os.path.join(ROOT, "metadata.json")
LOGF = os.path.join(ROOT, "logs", "server.log")
CHUNK = 64 * 1024
MAX_SIZE = 1 << 30
meta_lock = threading.Lock()
log_lock = threading.Lock()

CTL_SOCK = "/tmp/sdn_ctl.sock"
fail_count = {}
fail_lock = threading.Lock()

def notify_ctl(ev):
    """Send an event to the SDN controller and wait for its ack."""
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(2)
        c.connect(CTL_SOCK)
        c.sendall((json.dumps(ev) + "\n").encode())
        c.recv(64)
        c.close()
    except OSError:
        pass

def note_fail(ip):
    with fail_lock:
        fail_count[ip] = fail_count.get(ip, 0) + 1
        if fail_count[ip] >= 3:
            fail_count[ip] = 0
            return True
    return False

def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with log_lock:
        print(line, flush=True)
        with open(LOGF, "a") as f:
            f.write(line + "\n")

def recv_exact(s, n):
    buf = bytearray()
    while len(buf) < n:
        c = s.recv(min(CHUNK, n - len(buf)))
        if not c:
            raise ConnectionError("connection closed")
        buf += c
    return bytes(buf)

def send_msg(s, obj):
    data = json.dumps(obj).encode()
    s.sendall(struct.pack("!I", len(data)) + data)

def recv_msg(s):
    n = struct.unpack("!I", recv_exact(s, 4))[0]
    if n > (1 << 20):
        raise ValueError("header too large")
    return json.loads(recv_exact(s, n))

def hash_pw(salt, pw):
    return hashlib.sha256((salt + pw).encode()).hexdigest()

def init_storage():
    os.makedirs(STORE, exist_ok=True)
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    if not os.path.exists(USERS):
        users = {}
        for name, pw, role in [("alice", "alice123", "user"),
                               ("bob", "bob123", "user"),
                               ("admin", "admin123", "admin")]:
            salt = secrets.token_hex(8)
            users[name] = {"salt": salt, "hash": hash_pw(salt, pw), "role": role}
        with open(USERS, "w") as f:
            json.dump(users, f, indent=2)
    if not os.path.exists(META):
        with open(META, "w") as f:
            json.dump({}, f)

def load_meta():
    with open(META) as f:
        return json.load(f)

def save_meta(m):
    tmp = META + ".tmp"
    with open(tmp, "w") as f:
        json.dump(m, f, indent=2)
    os.replace(tmp, META)

def safe_name(n):
    n = os.path.basename(str(n))
    if n in ("", ".", "..") or n.startswith("."):
        return None
    return n

def can_access(meta_entry, user, role):
    return role == "admin" or meta_entry["owner"] == user

def do_list(conn, user, role):
    with meta_lock:
        m = load_meta()
    files = [{"name": k, **v} for k, v in m.items() if can_access(v, user, role)]
    send_msg(conn, {"status": "ok", "files": files})
    log(f"LIST user={user} count={len(files)}")

def do_upload(conn, req, user, role, addr):
    name = safe_name(req.get("name"))
    try:
        size = int(req.get("size", -1))
    except (TypeError, ValueError):
        size = -1
    want = req.get("sha256", "")
    if name is None or size < 0 or size > MAX_SIZE:
        send_msg(conn, {"status": "error", "msg": "invalid name or size"})
        return
    with meta_lock:
        m = load_meta()
        if name in m and not can_access(m[name], user, role):
            send_msg(conn, {"status": "error", "msg": "permission denied"})
            log(f"DENIED upload user={user} file={name}")
            return
    send_msg(conn, {"status": "ready"})
    tmp = os.path.join(STORE, f".tmp_{secrets.token_hex(6)}")
    h = hashlib.sha256()
    got = 0
    try:
        with open(tmp, "wb") as f:
            while got < size:
                c = conn.recv(min(CHUNK, size - got))
                if not c:
                    raise ConnectionError("interrupted upload")
                f.write(c)
                h.update(c)
                got += len(c)
        if h.hexdigest() != want:
            send_msg(conn, {"status": "error", "msg": "integrity check failed"})
            log(f"INTEGRITY FAIL upload user={user} file={name}")
            os.remove(tmp)
            return
        os.replace(tmp, os.path.join(STORE, name))
        with meta_lock:
            m = load_meta()
            m[name] = {"owner": user, "size": size, "sha256": want,
                       "uploaded": time.strftime("%Y-%m-%d %H:%M:%S")}
            save_meta(m)
        send_msg(conn, {"status": "ok", "sha256": want})
        log(f"UPLOAD OK user={user} file={name} bytes={size} from={addr[0]}")
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        log(f"UPLOAD INTERRUPTED user={user} file={name} received={got}/{size}")
        raise

def do_download(conn, req, user, role, addr):
    name = safe_name(req.get("name"))
    with meta_lock:
        m = load_meta()
    if name is None or name not in m:
        send_msg(conn, {"status": "error", "msg": "file not found"})
        return
    if not can_access(m[name], user, role):
        send_msg(conn, {"status": "error", "msg": "permission denied"})
        log(f"DENIED download user={user} file={name}")
        return
    path = os.path.join(STORE, name)
    size = os.path.getsize(path)
    send_msg(conn, {"status": "ok", "size": size, "sha256": m[name]["sha256"]})
    with open(path, "rb") as f:
        while True:
            c = f.read(CHUNK)
            if not c:
                break
            conn.sendall(c)
    log(f"DOWNLOAD OK user={user} file={name} bytes={size} to={addr[0]}")

def handle(conn, addr):
    user = role = None
    attempts = 0
    log(f"CONNECT {addr[0]}:{addr[1]}")
    try:
        conn.settimeout(120)
        while True:
            req = recv_msg(conn)
            cmd = req.get("cmd")
            if user is None:
                if cmd != "LOGIN":
                    send_msg(conn, {"status": "error", "msg": "login required"})
                    continue
                with open(USERS) as f:
                    users = json.load(f)
                u = users.get(req.get("user", ""))
                if u and secrets.compare_digest(
                        hash_pw(u["salt"], req.get("password", "")), u["hash"]):
                    user, role = req["user"], u["role"]
                    fail_count.pop(addr[0], None)
                    notify_ctl({"event": "LOGIN", "ip": addr[0], "user": user, "role": role})
                    send_msg(conn, {"status": "ok", "role": role})
                    log(f"LOGIN OK user={user} from={addr[0]}")
                else:
                    attempts += 1
                    send_msg(conn, {"status": "error", "msg": "invalid credentials"})
                    log(f"LOGIN FAIL from={addr[0]} attempt={attempts}")
                    if note_fail(addr[0]):
                        notify_ctl({"event": "FAILS", "ip": addr[0]})
                    if attempts >= 3:
                        break
                continue
            if cmd == "LIST":
                do_list(conn, user, role)
            elif cmd == "UPLOAD":
                do_upload(conn, req, user, role, addr)
            elif cmd == "DOWNLOAD":
                do_download(conn, req, user, role, addr)
            elif cmd == "QUIT":
                break
            else:
                send_msg(conn, {"status": "error", "msg": "unknown command"})
    except (ConnectionError, socket.timeout, ValueError, OSError) as e:
        log(f"SESSION ENDED {addr[0]} reason={e}")
    finally:
        if user:
            notify_ctl({"event": "LOGOUT", "ip": addr[0], "user": user})
        conn.close()
        log(f"DISCONNECT {addr[0]}:{addr[1]} user={user}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5000)
    a = ap.parse_args()
    init_storage()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((a.host, a.port))
    srv.listen(64)
    log(f"SERVER LISTENING on {a.host}:{a.port}")
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle, args=(conn, addr), daemon=True).start()

if __name__ == "__main__":
    main()
