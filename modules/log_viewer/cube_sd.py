# -*- coding: utf-8 -*-
"""Copies flight logs from the Cube's SD card to the Solo over MAVFTP, for the flight log viewer.

The 4.7.2 build writes every flight log to the Cube's SD card (/APM/LOGS/<n>.BIN). The Solo already
keeps its own copy of the newest 50 flights in /log/dataflash; this lets the viewer also reach the
logs that are only on the SD card. A copy is saved on the Solo (/log/cubesd/<n>.BIN) so it crosses
the Cube's serial link only once, then it is viewed and downloaded like any other log.

Rules this module keeps (it is the only part of the viewer that talks to the Cube):
  * only on an explicit request from the viewer page, never in the background;
  * only while the Cube is disarmed, and the transfer stops the moment a heartbeat says armed;
  * one job at a time; the copy can be cancelled; nothing is retried forever (20 s without progress
    ends the job);
  * read-only towards the Cube: only MAVFTP TerminateSession, ResetSessions, ListDirectory,
    OpenFileRO, ReadFile and BurstReadFile are ever sent. Nothing is created, written, renamed or
    removed on the Cube's SD card;
  * its own UDP endpoint to the Solo's telemetry forwarder (system id 254, as the Parameters page uses), no stream requests, no
    parameter traffic, no shared state with the parameter page or the Cockpit;
  * on the Solo it only writes under /log/cubesd (.part file, renamed when complete), refuses to run
    when the disk is nearly full, and keeps the newest MAX_COPIES copies / MAX_BYTES in total.

Python 2.7 (Solo) and 3 (tests). pymavlink is only imported when a transfer starts.
"""
import os
import random
import re
import struct
import sys
import threading
import time
import traceback

CUBE_LOG_DIR = "/APM/LOGS"
COPY_DIR = "/log/cubesd"
MAX_COPIES = 20
MAX_BYTES = 1024 * 1024 * 1024
MIN_FREE_BYTES = 200 * 1024 * 1024          # keep this much free on the Solo's log disk
NO_PROGRESS_SECONDS = 20
LIST_CACHE_SECONDS = 600

# MAVFTP (https://mavlink.io/en/services/ftp.html)
OP_TERMINATE = 1
OP_RESET = 2
OP_LIST = 3
OP_OPEN_RO = 4
OP_READ = 5
OP_BURST = 15
OP_ACK = 128
OP_NAK = 129
NAK_EOF = 6
ALLOWED_OPS = (OP_TERMINATE, OP_RESET, OP_LIST, OP_OPEN_RO, OP_READ, OP_BURST)
HDR_FMT = "<HBBBBBBI"                        # seq, session, opcode, size, req_opcode, burst_complete, pad, offset
HDR_LEN = struct.calcsize(HDR_FMT)           # 12
PAYLOAD_LEN = 251
MAX_DATA = PAYLOAD_LEN - HDR_LEN             # 239

ENDPOINTS = ["udpout:10.1.1.10:14560", "udpin:0.0.0.0:14560", "udpin:127.0.0.1:14560",
             "udpout:127.0.0.1:14560", "udpin:0.0.0.0:14550", "udpout:127.0.0.1:14550",
             "udpout:10.1.1.10:14550"]
SOURCE_SYSTEM = 254                          # same id the Parameters page uses for its own MAVFTP reads
_LOG_NAME = re.compile(r"^0*(\d{1,8})\.BIN$", re.IGNORECASE)
_COPY_NAME = re.compile(r"^(\d{1,8})\.BIN$")


def _log(msg):
    """One line in /log/solowebutility.log (this process's stdout), so a failed copy can be traced later."""
    try:
        sys.stdout.write("[%s] [cubesd] %s\n" % (time.strftime("%H:%M:%S"), msg))
        sys.stdout.flush()
    except Exception:
        pass


class CubeSDError(Exception):
    """A problem with a readable message for the viewer page."""


class Aborted(CubeSDError):
    pass


# ---------- the real link to the Cube ----------

class MavTransport(object):
    """FILE_TRANSFER_PROTOCOL packets in and out through pymavlink, plus the Cube's armed flag."""

    def __init__(self, mav):
        self.mav = mav
        self._armed = None
        self._last_hb = 0
        self.seen = {}                           # what the Cube sent while we waited (for the error message)
        self.ftp_seen = 0
        self.last_ftp = None

    @classmethod
    def connect(cls, timeout=6.0):
        try:
            from pymavlink import mavutil
        except Exception:
            raise CubeSDError("pymavlink is not available on this Solo.")
        end = time.time() + timeout
        for ep in ENDPOINTS:
            if time.time() > end:
                break
            mav = None
            try:
                mav = mavutil.mavlink_connection(ep, source_system=SOURCE_SYSTEM)
                t0 = time.time()
                while time.time() - t0 < 1.2:
                    try:
                        mav.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS, mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
                    except Exception:
                        pass
                    hb = mav.recv_match(type="HEARTBEAT", blocking=True, timeout=0.4)
                    if hb is not None and hb.get_srcSystem() != SOURCE_SYSTEM and cls._is_cube(hb):
                        mav.target_system = hb.get_srcSystem()
                        mav.target_component = hb.get_srcComponent()
                        tr = cls(mav)
                        tr._note_heartbeat(hb)
                        return tr
                mav.close()
            except Exception:
                try:
                    if mav is not None:
                        mav.close()
                except Exception:
                    pass
        raise CubeSDError("Cannot reach the Cube over the Solo's telemetry link.")

    @staticmethod
    def _is_cube(hb):
        try:
            return hb.get_srcComponent() == 1 and hb.autopilot != 8 and hb.type != 26
        except Exception:
            return False

    def _note_heartbeat(self, hb):
        if self._is_cube(hb) and hb.get_srcSystem() == self.mav.target_system:
            self._armed = bool(hb.base_mode & 0x80)

    def _keepalive(self):
        now = time.time()
        if now - self._last_hb >= 2.0:           # lets the telemetry forwarder keep this endpoint registered
            self._last_hb = now
            try:
                self.mav.mav.heartbeat_send(6, 8, 0, 0, 0)
            except Exception:
                pass

    def send(self, raw):
        self._keepalive()
        payload = list(bytearray(raw)) + [0] * (PAYLOAD_LEN - len(raw))
        self.mav.mav.file_transfer_protocol_send(0, self.mav.target_system, self.mav.target_component, payload)

    def recv(self, timeout):
        self._keepalive()
        msg = self.mav.recv_match(blocking=True, timeout=timeout)
        if msg is None:
            return None
        kind = msg.get_type()
        self.seen[kind] = self.seen.get(kind, 0) + 1
        if kind == "HEARTBEAT":
            self._note_heartbeat(msg)
            return None
        if kind != "FILE_TRANSFER_PROTOCOL":
            return None
        self.ftp_seen += 1
        raw = bytes(bytearray(msg.payload))
        self.last_ftp = raw[:12]
        return raw

    def summary(self):
        """A short account of what the Cube sent, for the error message when it did not answer."""
        total = sum(self.seen.values())
        top = sorted(self.seen.items(), key=lambda kv: -kv[1])[:6]
        text = "Heard %d messages (%s)" % (total, ", ".join("%s %d" % kv for kv in top) or "none")
        text += "; file-transfer packets: %d" % self.ftp_seen
        if self.last_ftp and len(self.last_ftp) >= 12:
            seq, sess, op, size, req = struct.unpack("<HBBBB", self.last_ftp[:6])
            text += " (last: seq %d, op %d, reply to %d)" % (seq, op, req)
        try:
            text += "; link %s" % ("MAVLink2" if getattr(self.mav.mav, "WIRE_PROTOCOL_VERSION", "1.0") == "2.0" else "MAVLink1")
        except Exception:
            pass
        return text + "."

    def armed(self):
        return self._armed

    def close(self):
        try:
            self.mav.close()
        except Exception:
            pass


# ---------- MAVFTP client (read-only) ----------

class FtpClient(object):
    def __init__(self, transport, check=None, reply_timeout=5.0, retries=3, clock=time.time):
        self.t = transport
        self.check = check or (lambda: None)      # raises Aborted when the job must stop
        self.reply_timeout = reply_timeout
        self.retries = retries
        self.clock = clock
        self.seq = random.randint(1000, 30000)       # not the low numbers another FTP user on the Solo just used
        self.stats = {"bursts": 0, "kept": 0, "holes": 0, "reads": 0, "t0": clock(), "gaps": [], "in_burst": 0.0, "hole_time": 0.0}

    def _send(self, op, session, offset, size, data=b""):
        assert op in ALLOWED_OPS, "read-only client"
        self.seq = (self.seq + 1) & 0xFFFF
        self.t.send(struct.pack(HDR_FMT, self.seq, session, op, size, 0, 0, 0, offset) + data)
        return self.seq

    @staticmethod
    def _parse(raw):
        if raw is None or len(raw) < HDR_LEN:
            return None
        seq, session, op, size, req, burst, _pad, offset = struct.unpack(HDR_FMT, raw[:HDR_LEN])
        return {"seq": seq, "session": session, "op": op, "size": size, "req": req, "burst": burst,
                "offset": offset, "data": raw[HDR_LEN:HDR_LEN + size]}

    def request(self, op, session, offset, size, data=b""):
        """One request, one reply, retried on silence. The Cube numbers its reply request+1. A slow
        Cube (a long directory) can answer an earlier attempt while a later one is waiting, so a reply to
        any attempt of this request counts."""
        sent, stray = [], []
        for _ in range(self.retries):
            seq = self._send(op, session, offset, size, data)
            sent.append(seq)
            end = self.clock() + self.reply_timeout
            while self.clock() < end:
                self.check()
                r = self._parse(self.t.recv(0.25))
                if not r or r["req"] != op:
                    continue
                if ((r["seq"] - 1) & 0xFFFF) in sent:
                    return r
                if len(stray) < 4:
                    stray.append("seq %d offset %d" % (r["seq"], r["offset"]))
        extra = ""
        try:
            extra = " " + self.t.summary()
        except Exception:
            pass
        extra += " We sent seq %d..%d; replies that were not to us: %s." % (sent[0], sent[-1], "; ".join(stray) if stray else "none")
        raise CubeSDError("The Cube did not answer (MAVFTP %d).%s" % (op, extra))

    def reset(self):
        try:
            self._send(OP_RESET, 0, 0, 0)
            time.sleep(0.05)
        except CubeSDError:
            raise
        except Exception as ex:
            raise CubeSDError("Cannot send to the Cube: %s" % ex)

    def open_ro(self, path, tries=3):
        """OpenFileRO, also when the Cube is still busy with an earlier transfer. If the Cube stays silent
        (it may still be streaming a burst read left over from an earlier or cancelled copy, or another
        program is using its file transfer), send a Reset, wait until no file-transfer packets have arrived
        for a while (at most a few times the reply timeout), and ask again."""
        data = path.encode("ascii")
        last = None
        for attempt in range(1, tries + 1):
            try:
                r = self.request(OP_OPEN_RO, 0, 0, len(data), data)
                if attempt > 1:
                    _log("Opened %s on try %d." % (path, attempt))
                return r
            except Aborted:
                raise
            except CubeSDError as ex:
                last = ex
            _log("No answer to opening %s (try %d of %d); resetting the Cube's file transfer." % (path, attempt, tries))
            self.reset()
            now = self.clock()
            quiet_until = now + self.reply_timeout * 1.2
            give_up = now + self.reply_timeout * 4
            while self.clock() < min(quiet_until, give_up):
                self.check()
                if self._parse(self.t.recv(0.25)) is not None:
                    quiet_until = self.clock() + self.reply_timeout * 0.6   # still streaming: wait for it to stop
        raise CubeSDError("The Cube did not answer after %d tries. Something else may be using its file "
                          "transfer (an earlier copy, or another program). Wait a minute and try again. (%s)"
                          % (tries, last))

    def list_dir(self, path):
        """[(name, size or None, is_dir)] of a directory on the Cube's SD card."""
        out, offset = [], 0
        data = path.encode("ascii")
        while True:
            r = self.request(OP_LIST, 0, offset, len(data), data)
            if r["op"] == OP_NAK:
                code = bytearray(r["data"][:1])[0] if r["data"] else -1
                if code == NAK_EOF:
                    return out
                raise CubeSDError("The Cube cannot list %s (error %d)." % (path, code))
            got = 0
            for item in bytes(r["data"]).split(b"\x00"):
                if not item:
                    continue
                kind, rest = item[:1], item[1:].decode("latin-1")
                got += 1
                if kind == b"D":
                    out.append((rest, None, True))
                elif kind == b"F":
                    name, _, size = rest.partition("\t")
                    out.append((name, int(size) if size.isdigit() else None, False))
            if got == 0:
                return out
            offset += got
            if offset > 5000:
                return out

    def recent_logs(self, path, count=30):
        """[(name, size)] of the newest logs, newest first, without listing the directory.

        ArduPilot lists a directory by walking it from the start for every page, so a card with hundreds of
        logs answers slower and slower, and the Cube stops answering in time. Instead: read LASTLOG.TXT
        (the number of the newest log), then open each recent log by name; the reply to the open carries
        the file's size. Raises CubeSDError when LASTLOG.TXT can't be read (the caller falls back to
        list_dir)."""
        text = []
        self.download("%s/LASTLOG.TXT" % path, text.append, lambda got, total: None, burst=False)
        raw = b"".join(text).decode("latin-1").strip()
        digits = "".join(c for c in raw[:12] if c.isdigit())
        if not digits:
            raise CubeSDError("The Cube's LASTLOG.TXT is empty.")
        last = int(digits)
        out, misses = [], 0
        n = last
        while n >= 1 and len(out) < count and misses < 12:
            self.check()
            name = "%08d.BIN" % n
            r = self.open_ro("%s/%s" % (path, name))
            if r["op"] == OP_ACK and len(r["data"]) >= 4:
                out.append((name, struct.unpack("<I", r["data"][:4])[0]))
                misses = 0
                self._end(r["session"])
            else:
                misses += 1                         # a gap in the numbering (a deleted log)
            n -= 1
        return out

    def download(self, path, write, progress, burst=True):
        """Read a whole file: write(bytes) gets the data in order, progress(got, total) is called often."""
        r = self.open_ro(path)
        if r["op"] != OP_ACK:
            code = bytearray(r["data"][:1])[0] if r["data"] else -1
            raise CubeSDError("The Cube cannot open %s (error %d)." % (path, code))
        total = struct.unpack("<I", r["data"][:4])[0] if len(r["data"]) >= 4 else None
        session = r["session"]
        if total is None:
            self._end(session)
            raise CubeSDError("The Cube did not give the log size.")
        got = 0
        progress(0, total)
        last_progress = self.clock()
        try:
            while got < total:
                self.check()
                before = got
                if burst:
                    got = self._burst(session, got, total, write, progress)
                    if got == before:
                        burst = False                  # no burst support or it stalled: plain reads
                        continue
                else:
                    got = self._read(session, got, total, write, progress)
                if got > before:
                    last_progress = self.clock()
                elif self.clock() - last_progress > NO_PROGRESS_SECONDS:
                    raise CubeSDError("The copy stalled (no data for %d s)." % NO_PROGRESS_SECONDS)
        finally:
            self._end(session)
        return got

    def _end(self, session):
        try:
            self._send(OP_TERMINATE, session, 0, 0)
        except Exception:
            pass

    def _read(self, session, got, total, write, progress):
        """A few plain ReadFile requests (one packet each). Returns the new offset."""
        for _ in range(64):
            if got >= total:
                break
            r = self.request(OP_READ, session, got, MAX_DATA)
            self.stats["reads"] += 1
            if r["op"] == OP_NAK:
                code = bytearray(r["data"][:1])[0] if r["data"] else -1
                if code == NAK_EOF:
                    break
                raise CubeSDError("The Cube refused to send the log (error %d)." % code)
            if r["offset"] != got or not r["data"]:
                break
            write(r["data"])
            got += len(r["data"])
            progress(got, total)
        return got

    def _burst(self, session, got, total, write, progress):
        """One BurstReadFile: the Cube streams packets (no acknowledgements) until it flags the burst
        complete. Every packet is kept at its own offset, also when an earlier one was dropped, and the
        holes are then fetched with plain ReadFile requests (the way MAVProxy and Mission Planner do it),
        instead of throwing the rest of the burst away and asking for it again. Returns the new offset."""
        self._send(OP_BURST, session, got, MAX_DATA)
        self.stats["bursts"] += 1
        idle_end = self.clock() + self.reply_timeout * 2
        have = {}                                   # offset -> data, for packets that arrived ahead of `got`
        top = got                                   # the end of the furthest packet seen
        burst_start = self.clock()
        last_pkt = None
        gaps = self.stats["gaps"]
        while True:
            self.check()
            r = self._parse(self.t.recv(0.25))
            now = self.clock()
            if r is None or r["req"] != OP_BURST or r["session"] != session:
                if now > idle_end:
                    break
                continue
            idle_end = now + self.reply_timeout * 2
            if last_pkt is not None and len(gaps) < 20000:
                gaps.append(now - last_pkt)
            last_pkt = now
            if r["op"] == OP_NAK:
                return got                          # EOF, or this Cube has no burst reads: the caller falls back to plain reads
            if r["data"] and r["offset"] >= got:
                self.stats["kept"] += 1
                have[r["offset"]] = r["data"]
                top = max(top, r["offset"] + len(r["data"]))
                while got in have:
                    chunk = have.pop(got)
                    write(chunk)
                    got += len(chunk)
                    progress(got, total)
            if r["burst"] or got >= total:
                break
        self.stats["in_burst"] += self.clock() - burst_start
        hole_start = self.clock()
        # holes left by dropped packets: ask for just those
        while got < top and got < total:
            self.check()
            if got in have:
                chunk = have.pop(got)
                write(chunk)
                got += len(chunk)
                progress(got, total)
                continue
            later = [o for o in have if o > got]
            size = min(MAX_DATA, (min(later) - got) if later else MAX_DATA)
            r = self.request(OP_READ, session, got, size)
            self.stats["holes"] += 1
            if r["op"] == OP_NAK or r["offset"] != got or not r["data"]:
                break
            write(r["data"])
            got += len(r["data"])
            progress(got, total)
        self.stats["hole_time"] += self.clock() - hole_start
        return got

    def describe(self):
        """One line on how the transfer is going (shown while a log is copied)."""
        st = self.stats
        elapsed = max(self.clock() - st["t0"], 0.1)
        line = "%d bursts, %d packets kept, %d holes re-read, %d plain reads" % (st["bursts"], st["kept"], st["holes"], st["reads"])
        gaps = sorted(st["gaps"])
        if gaps:
            line += "; gap between packets: median %d ms, 90%% under %d ms, longest %d ms; %d s receiving bursts, %d s repairing holes, %d s in all" % (
                gaps[len(gaps) // 2] * 1000, gaps[int(len(gaps) * 0.9)] * 1000, gaps[-1] * 1000,
                st["in_burst"], st["hole_time"], elapsed)
        seen = getattr(self.t, "seen", None)
        if seen:
            line += "; the link carries %d messages/s (%d file packets/s)" % (
                sum(seen.values()) / elapsed, getattr(self.t, "ftp_seen", 0) / elapsed)
        return line


# ---------- the copy job ----------

def _clean_part_files(dest):
    try:
        for name in os.listdir(dest):
            if name.endswith(".part"):
                try:
                    os.remove(os.path.join(dest, name))
                except OSError:
                    pass
    except OSError:
        pass


def list_copies(dest=None):
    """Copies already on the Solo: [{n, size, mtime}], newest first. Never touches the Cube."""
    dest = dest or COPY_DIR
    out = []
    try:
        names = os.listdir(dest)
    except OSError:
        return out
    for name in names:
        m = _COPY_NAME.match(name)
        if not m:
            continue
        try:
            st = os.stat(os.path.join(dest, name))
        except OSError:
            continue
        out.append({"n": int(m.group(1)), "name": name, "size": st.st_size, "mtime": int(st.st_mtime)})
    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out


def enforce_limits(dest, max_copies=MAX_COPIES, max_bytes=MAX_BYTES, keep=None):
    """Delete the oldest copies (only files this module made) beyond the count / size limits."""
    copies = list_copies(dest)
    total = sum(c["size"] for c in copies)
    for c in reversed(copies):
        if len(copies) <= max_copies and total <= max_bytes:
            break
        if keep is not None and c["n"] == keep:
            continue
        try:
            os.remove(os.path.join(dest, c["name"]))
            copies.remove(c)
            total -= c["size"]
        except OSError:
            pass


def free_bytes(path):
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize
    except Exception:
        return None


class CubeSD(object):
    """One job at a time (list the Cube's logs, or copy one); status() is cheap and never touches the Cube."""

    def __init__(self, connect=None, dest=COPY_DIR, free=free_bytes, clock=time.time,
                 max_copies=MAX_COPIES, max_bytes=MAX_BYTES, min_free=MIN_FREE_BYTES):
        self.connect = connect or MavTransport.connect
        self.dest = dest
        self.free = free
        self.clock = clock
        self.max_copies, self.max_bytes, self.min_free = max_copies, max_bytes, min_free
        self.lock = threading.Lock()
        self.job_lock = threading.Lock()
        self.job = {"state": "idle"}
        self.listing = None
        self.listed_at = None
        self.cancel_flag = False
        self.thread = None
        self.queue = []                      # log numbers waiting to be copied after the current one
        self._rate = [0, 0.0]                # bytes and seconds of the items finished in this run (for the estimate)

    # -- what the page polls
    def status(self):
        with self.lock:
            job = dict(self.job)
            listing = None if self.listing is None else [dict(x) for x in self.listing]
            listed_at = self.listed_at
            queue = list(self.queue)
            rate = list(self._rate)
        if job.get("state") == "copying":
            job.update(self._estimate(job, queue, listing, rate))
        copies = dict((c["n"], c) for c in list_copies(self.dest))
        if listing is not None:
            for item in listing:
                c = copies.get(item["n"])
                item["copied"] = bool(c and c["size"] == item["size"])
        return {"job": job, "list": listing, "listedAt": listed_at, "copies": len(copies), "dir": CUBE_LOG_DIR,
                "queue": queue}

    def _estimate(self, job, queue, listing, rate):
        """Seconds left for the log being copied (eta) and for it plus the queued ones (queueEta); None until
        there is enough to go on. The speed is the average since this run began, so it does not jump about."""
        out = {"eta": None, "queueEta": None}
        started = job.get("itemStart")
        if started is None:
            return out
        secs = rate[1] + max(self.clock() - started, 0.0)
        got = rate[0] + (job.get("got") or 0)
        if secs < 5 or got < 65536:
            return out
        speed = got / secs
        left = max((job.get("total") or 0) - (job.get("got") or 0), 0)
        out["eta"] = int(left / speed)
        sizes = dict((x["n"], x["size"]) for x in (listing or []))
        out["queueEta"] = int((left + sum(sizes.get(q, 0) for q in queue)) / speed)
        return out

    def _set(self, **kw):
        with self.lock:
            self.job.update(kw)

    # -- starting jobs
    def _start(self, kind, target, armed, n=None):
        if armed():
            return False, "Logs can be fetched from the Cube after landing, when it is disarmed."
        if not self.job_lock.acquire(False):
            return False, "Another Cube SD card job is running."
        with self.lock:
            self.cancel_flag = False
            self.queue = []
            self._rate = [0, 0.0]
            self.job = {"state": "listing" if kind == "list" else "copying", "kind": kind, "n": n, "got": 0,
                        "total": 0, "speed": 0, "detail": "", "message": "Connecting to the Cube…", "error": None,
                        "startedAt": int(self.clock()), "copied": 0}
        self.thread = threading.Thread(target=self._run, args=(target, armed, n))
        self.thread.daemon = True
        self.thread.start()
        return True, ""

    def start_list(self, armed):
        return self._start("list", self._do_list, armed)

    def start_copy(self, n, armed):
        if armed():
            return False, "Logs can be fetched from the Cube after landing, when it is disarmed."
        with self.lock:
            if self.job.get("state") == "copying" and self.job.get("kind") == "copy":
                # a copy is running: line this one up behind it (it starts by itself, even with the page closed)
                if self.listing is None or not [x for x in self.listing if x["n"] == n]:
                    return False, "Check the Cube's SD card first, then pick a log."
                if n == self.job.get("n") or n in self.queue:
                    return True, "Already queued."
                self.queue.append(n)
                _log("Queued: log %s (%d waiting)." % (n, len(self.queue)))
                return True, "Queued."
        return self._start("copy", self._do_copy, armed, n)

    def cancel(self):
        with self.lock:
            self.cancel_flag = True               # the job thread drops the queue and says how many were left

    def wait(self, timeout=30):
        t = self.thread
        if t is not None:
            t.join(timeout)

    # -- the job thread
    def _run(self, target, armed, n):
        transport = None
        what = "list" if target == self._do_list else "copy of log %s" % n
        _log("Started: %s." % what)
        try:
            transport = self.connect()

            def check():
                if self.cancel_flag:
                    raise Aborted("Cancelled.")
                if armed() or transport.armed():
                    raise Aborted("The Cube was armed: the transfer was stopped.")

            client = FtpClient(transport, check=check)
            check()
            client.reset()
            while True:
                target(client, n)
                if target != self._do_copy:
                    break
                n = self._next_in_queue()
                if n is None:
                    break
                _log("Started: copy of log %s (from the queue)." % n)
                check()
        except Aborted as ex:
            _log("Stopped: %s" % ex)
            self._set(state="cancelled", message=str(ex) + self._drop_queue(), error=None, finishedAt=int(self.clock()))
        except CubeSDError as ex:
            _log("Failed: %s" % ex)
            self._set(state="error", error=str(ex) + self._drop_queue(), message=str(ex), finishedAt=int(self.clock()))
        except Exception as ex:
            _log("Unexpected problem: %s\n%s" % (ex, traceback.format_exc().rstrip()))
            self._set(state="error", error="Unexpected problem: %s" % ex + self._drop_queue(),
                      message="Unexpected problem: %s" % ex, finishedAt=int(self.clock()))
        finally:
            if transport is not None:
                transport.close()
            self.job_lock.release()

    def _drop_queue(self):
        with self.lock:
            left = len(self.queue)
            self.queue = []
        if not left:
            return ""
        return " The %d log%s waiting in the queue %s not copied." % (left, "" if left == 1 else "s", "was" if left == 1 else "were")

    def _next_in_queue(self):
        """After a copy: the next queued log number (the job is reset for it), or None, and the job is done."""
        with self.lock:
            if self.queue and not self.cancel_flag:
                n = self.queue.pop(0)
                copied = self.job.get("copied", 0)
                self.job = {"state": "copying", "kind": "copy", "n": n, "got": 0, "total": 0, "speed": 0,
                            "detail": "", "message": "Copying log %d from the Cube…" % n, "error": None,
                            "startedAt": self.job.get("startedAt"), "copied": copied}
                return n
            copied = self.job.get("copied", 0)
            msg = self.job.get("message", "")
            if copied > 1:
                msg = "%d logs copied to the Solo." % copied
            self.job.update(state="done", speed=0, message=msg, finishedAt=int(self.clock()))
            return None

    def _do_list(self, client, _n):
        self._set(message="Reading the Cube's log list…")
        items = []
        try:
            found = [(name, size, False) for name, size in client.recent_logs(CUBE_LOG_DIR)]
        except CubeSDError:
            self._set(message="Reading the Cube's log list (the long way)…")
            found = client.list_dir(CUBE_LOG_DIR)
        for name, size, is_dir in found:
            m = _LOG_NAME.match(name)
            if m and not is_dir and size is not None:
                items.append({"n": int(m.group(1)), "name": name, "size": size})
        items.sort(key=lambda d: d["n"], reverse=True)
        with self.lock:
            self.listing = items
            self.listed_at = int(self.clock())
        self._set(state="done", message="%d logs on the Cube's SD card." % len(items), finishedAt=int(self.clock()))
        _log("Done: %d logs on the Cube's SD card." % len(items))

    def _do_copy(self, client, n):
        with self.lock:
            known = None if self.listing is None else [x for x in self.listing if x["n"] == n]
        if not known:
            raise CubeSDError("Check the Cube's SD card first, then pick a log.")
        item = known[0]
        try:
            os.makedirs(self.dest)
        except OSError:
            pass
        free = self.free(self.dest)
        if free is not None and free < item["size"] + self.min_free:
            raise CubeSDError("Not enough space on the Solo for this log (%d MB free)." % (free // 1048576))
        _clean_part_files(self.dest)
        final = os.path.join(self.dest, "%d.BIN" % n)
        part = final + ".part"
        stamp = [self.clock(), 0]
        began = self.clock()
        self._set(total=item["size"], got=0, itemStart=began, message="Copying log %d from the Cube…" % n)

        def progress(got, total):
            now = self.clock()
            if now - stamp[0] >= 0.5 or got >= total:
                speed = (got - stamp[1]) / max(now - stamp[0], 0.001)
                stamp[0], stamp[1] = now, got
                self._set(got=got, total=total, speed=int(speed), detail=client.describe())

        with open(part, "wb") as f:
            try:
                total = client.download("%s/%s" % (CUBE_LOG_DIR, item["name"]), f.write, progress)
            except BaseException:
                f.close()
                try:
                    os.remove(part)
                except OSError:
                    pass
                raise
        if total != item["size"] and os.path.getsize(part) != item["size"]:
            try:
                os.remove(part)
            except OSError:
                pass
            raise CubeSDError("The copy is incomplete; it was discarded. Try again.")
        getattr(os, "replace", os.rename)(part, final)
        enforce_limits(self.dest, self.max_copies, self.max_bytes, keep=n)
        with self.lock:
            self._rate[0] += item["size"]
            self._rate[1] += max(self.clock() - began, 0.001)
            self.job["copied"] = self.job.get("copied", 0) + 1
        self._set(got=item["size"], total=item["size"], speed=0, message="Log %d copied to the Solo." % n)
        secs = max(self.clock() - began, 0.001)
        _log("Done: log %d copied, %d bytes in %d s (%.1f KB/s); %s." % (
            n, item["size"], secs, item["size"] / secs / 1024.0, client.describe()))


_MANAGER = []


def manager():
    if not _MANAGER:
        _MANAGER.append(CubeSD())
    return _MANAGER[0]
