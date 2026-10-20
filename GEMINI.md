# Project Rules: SoloWebUtility2

## Target Environment
- Dedicated Host: Runs EXCLUSIVELY on the 3DR Solo Drone (i.MX6 Linux) at 10.1.1.10. There is NO PC or Windows runtime mode. Never include Windows fallbacks, remote SSH wrappers, or paramiko code in drone app files. All drone commands, processes, and calibrations run locally via subprocess or local MAVLink on the drone.
- Hardware: 3DR Solo Drone (i.MX6 Linux) at 10.1.1.10.
- Python Runtime: Stock Python 2.7 ONLY.
- Syntax Rule: NEVER use Python 3 features (no f-strings, no pathlib, no urllib.parse, no type hints). Use .format() or % formatting.
- Dependencies: Do NOT use Flask. Use only the Python 2.7 standard library (BaseHTTPServer, SimpleHTTPServer, SocketServer, json, urllib, urllib2) and pre-installed pymavlink.

## Network & Deployment Rules
- This PC cannot connect to the internet and the Solo Wi-Fi (10.1.1.10) at the same time.
- NEVER attempt to ping, SSH, or run test scripts against 10.1.1.10 from your environment.
- NEVER add Python version probes or interpreter checks to deploy_to_drone.py. Assume /usr/bin/python (Python 2.7) is present.
- After editing files, stop and ask the user to switch Wi-Fi and run deploy_to_drone.bat.

## Reference Implementation
- The working baseline lives in C:\Users\User\Documents\SoloWebUtility (deploy_to_drone.py, solo_web_app.py, and param_mgr.py). Mirror that exact server and deployment architecture.
