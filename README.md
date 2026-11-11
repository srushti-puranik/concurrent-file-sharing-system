# Concurrent File Sharing System (TCP) with POX SDN Control - Team 19

A multi-threaded TCP file server where authenticated users upload and download files at the same time. A POX SDN controller (OpenFlow 1.0) manages an Open vSwitch switch: hosts that are not logged in get a very low rate, login raises a host's rate by role, 3 failed logins block a host, an operator can throttle or block hosts at runtime, and per-host bandwidth is measured every second from OVS flow statistics.

## How the SDN part works
- POX installs flow rules over OpenFlow 1.0: default deny, ARP, and per-host TCP port 5000 flows to the server.
- OpenFlow 1.0 has no meters, so rate limits are Open vSwitch queues (HTB). POX changes a queue's maximum rate at runtime with ovs-vsctl; the running transfer is not interrupted.
- The file server tells POX about LOGIN, LOGOUT and FAILS events over a Unix socket (/tmp/sdn_ctl.sock). The event carries the client IP, which POX maps to the network host.
- Every second POX requests flow statistics from the switch and writes per-host bandwidth to results/bandwidth_log.csv.

| Host state | Rate limit |
|---|---|
| Not logged in | 1 Mbps (login only) |
| User | 10 Mbps |
| Admin | 20 Mbps |
| Throttled | 2 Mbps by default, or any value |
| Blocked | all traffic dropped (30 s after 3 failed logins, or by the operator) |

## Folders
- src/ - server.py and client.py
- controller/ - controller.py (starts POX) and csfs_pox.py (the POX component)
- topology/ - topo.py (srv + h1..h10 on switch s1; --nosdn for the baseline)
- tests/ - runtime_policy.py, hard_scenario.py, queue_test.py (self-checking)
- perf/ - run_perf.py (experiments) and make_graphs.py
- results/ - measurements, graphs and test outputs
- docs/ - PROTOCOL.md, SRS, SAD, Test Plan

## Requirements and setup
Ubuntu with Python 3, Mininet 2.3.0, Open vSwitch 3.7.1 and python3-matplotlib. Mininet and the controller run with sudo.

POX is not stored in this repository. Clone it into the project folder once:

    cd concurrent-file-sharing-system
    git clone https://github.com/noxrepo/pox.git pox

POX 0.7.0 prints a warning on Python 3.14 (it officially supports older versions) but runs.

## Default accounts
alice / alice123 (user), bob / bob123 (user), admin / admin123 (admin)

## Run the SDN network (two terminals)
Terminal 1:

    sudo python3 topology/topo.py

Terminal 2 (after the mininet> prompt appears):

    sudo python3 controller/controller.py

At the mininet> prompt:

    h1 python3 src/client.py --host 10.0.0.100 -u alice -p alice123 upload /tmp/file.bin

Operator commands (from a third terminal): python3 controller/controller.py --send "status" (also: flows, block hN, unblock hN, throttle hN [kbps], unthrottle hN).

## Automated tests and experiments
    sudo python3 tests/queue_test.py         # OVS queues cap speed and can change at runtime
    sudo python3 tests/runtime_policy.py     # throttle / unthrottle / block / unblock during a transfer
    sudo python3 tests/hard_scenario.py      # saturation + intruder + client killed mid-transfer
    sudo python3 perf/run_perf.py            # 1, 3, 5, 10 transfers, with and without SDN
    python3 perf/make_graphs.py              # graphs in results/
    ./demo.sh                                # the three main runs in order

If a run fails with "File exists", run: sudo mn -c

## Results (POX + OVS queues)
- Queue test: a 5 Mbps queue gave 4.7 to 4.8 Mbps, changed to 20 Mbps gave about 18.4, changed to 1 Mbps gave about 0.96 (two runs).
- Runtime test (one 80 MB upload): normal 9.49 Mbps, throttled to 1 Mbps gave 0.94, unthrottled 9.43, blocked 0.00, unblocked 9.42. The file arrived intact (SHA-256).
- Hard scenario: 9 uploads over a 30 Mbps link, one intruder, one client killed. The 8 survivors each got 3.47 to 3.52 Mbps (aggregate 27.91 Mbps, Jain 1.000), no partial file was stored and the server kept running.
- Performance run: all 32 measured points passed their SHA-256 check. Without SDN one client uses about 94 Mbps and fairness varies (Jain 0.683 to 1.000). With SDN each client gets about 9.4 to 9.6 Mbps (limit 10) and Jain is 0.999 to 1.000. One SDN point (20 MB, 5 uploads) read 3.52 Mbps in the first run and 9.54 Mbps in a re-run; the cause was not identified (results/run2_point_20mb_n5.csv).
- At 10 clients both modes reach about 90 Mbps aggregate because 10 x 10 Mbps fills the 100 Mbps link.

## Limitations
No encryption (passwords travel in clear text). Users are identified by host IP. Rate limits use OVS queues changed through ovs-vsctl because OpenFlow 1.0 cannot set queue rates. Ping is dropped by the policy. Single run per performance point on a virtual machine; results vary between runs. An earlier version using ovs-ofctl and OpenFlow 1.3 meters is kept under the git tag v1-ofctl.

## Team
Team 19: SRUSHTI PURANIK [PES1UG25AM408]
         SHRITHL S SHETTY [PES1UG25AM383]
         VAMSHIK KUDLUR M R [PES1UG25AM436]
