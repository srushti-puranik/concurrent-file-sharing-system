# Protocol Specification - Team 19

Transport: TCP, server port 5000.

## Message format
Each control message is a 4-byte big-endian length followed by a JSON object (max 1 MiB). File data follows as raw bytes in chunks of up to 64 KB.

## Commands
- LOGIN: user, password. Reply: status ok and role, or error "invalid credentials".
- LIST: no fields. Reply: status ok and files (name, size, owner, sha256, uploaded).
- UPLOAD: name, size, sha256. Reply: status ready; after the data, status ok, or an error.
- DOWNLOAD: name. Reply: status ok, size, sha256; then the raw data.
- QUIT: connection closed.

## Rules
- Any command before a successful LOGIN returns "login required".
- Three failed logins close the connection.
- A user may access only own files; admin may access all; otherwise "permission denied".
- The server checks the SHA-256 of every upload and answers "integrity check failed" on a mismatch.
- A dropped upload leaves no file and no metadata entry.
- Idle connections time out after 120 seconds.

## Where SDN policy applies
- TCP connect to port 5000: allowed, 1 Mbps until login.
- LOGIN success: server tells the controller; limit 10 Mbps (user) or 20 Mbps (admin).
- 3 failed logins: server tells the controller; host blocked 30 s.
- Upload and download data: limited by the host's current OVS queue rate; bytes counted per host.
- QUIT or disconnect: host back to 1 Mbps.

## Server to controller
Unix socket /tmp/sdn_ctl.sock, one JSON line per event: LOGIN (ip, user, role), LOGOUT (ip, user), FAILS (ip).
