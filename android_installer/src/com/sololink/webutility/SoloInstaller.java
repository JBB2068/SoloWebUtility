package com.sololink.webutility;

import com.jcraft.jsch.ChannelExec;
import com.jcraft.jsch.ChannelSftp;
import com.jcraft.jsch.JSch;
import com.jcraft.jsch.Session;
import com.jcraft.jsch.SftpATTRS;
import com.jcraft.jsch.SftpProgressMonitor;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.Charset;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * Installs Solo Web Utility on a 3DR Solo over SoloLink Wi-Fi.
 *
 * Phone-side port of deploy_to_drone.py (the Windows installer): same files, same
 * target folders, same /etc/inittab respawn entry, same firmware staging in
 * /log/firmware. Pure Java (no Android classes) so it can be tested off-device.
 */
public class SoloInstaller {

    /** Progress and log output. */
    public interface Listener {
        void log(String line);
        void progress(String stage, int percent);
    }

    /** Files packed in the app (assets/manifest.txt). */
    public interface Assets {
        InputStream open(String rel) throws IOException;
        List<Entry> entries() throws IOException;
    }

    public static class Entry {
        public final String kind;   // "app" or "fw"
        public final long size;
        public final String md5;
        public final String rel;    // e.g. "static/app.js", "firmware/3dr-solo.tar.gz"

        public Entry(String kind, long size, String md5, String rel) {
            this.kind = kind;
            this.size = size;
            this.md5 = md5;
            this.rel = rel;
        }

        /** Parses one manifest line: kind TAB size TAB md5 TAB relpath */
        public static Entry parse(String line) {
            String[] p = line.split("\t");
            if (p.length != 4) {
                return null;
            }
            return new Entry(p[0].trim(), Long.parseLong(p[1].trim()), p[2].trim().toLowerCase(Locale.US),
                    p[3].trim().replace('\\', '/'));
        }
    }

    public static final String USER = "root";
    public static final String PASSWORD = "TjSDBkAu";
    public static final String LOG_FW_DIR = "/log/firmware";
    public static final String INITTAB_ENTRY =
            "SWU:345:respawn:/usr/bin/python /usr/bin/SoloWebUtility2/solo_web_app.py >>/log/solowebutility.log 2>&1\n";

    private static final Charset UTF8 = Charset.forName("UTF-8");

    private final Assets assets;
    private final Listener out;
    private final String copterHost;
    private final int copterPort;
    private final String controllerHost;
    private final int controllerPort;
    private volatile boolean cancelled = false;

    private Session session;

    public SoloInstaller(Assets assets, Listener out) {
        this(assets, out, "10.1.1.10", 22, "10.1.1.1", 22);
    }

    public SoloInstaller(Assets assets, Listener out, String copterHost, int copterPort,
                         String controllerHost, int controllerPort) {
        this.assets = assets;
        this.out = out;
        this.copterHost = copterHost;
        this.copterPort = copterPort;
        this.controllerHost = controllerHost;
        this.controllerPort = controllerPort;
    }

    public void cancel() {
        cancelled = true;
        Session s = session;
        if (s != null) {
            s.disconnect();
        }
    }

    private void checkCancelled() throws IOException {
        if (cancelled) {
            throw new IOException("Cancelled");
        }
    }

    // ------------------------------------------------------------------ main flow

    /** Runs the whole installation. Returns true on success; details go to the listener. */
    public boolean run() {
        try {
            return install();
        } catch (Exception e) {
            out.log("[!] Installation stopped: " + e.getMessage());
            return false;
        } finally {
            if (session != null) {
                session.disconnect();
                session = null;
            }
        }
    }

    private boolean install() throws Exception {
        List<Entry> all = assets.entries();
        List<Entry> appFiles = new ArrayList<Entry>();
        List<Entry> fwFiles = new ArrayList<Entry>();
        for (Entry e : all) {
            if ("app".equals(e.kind)) {
                appFiles.add(e);
            } else if ("fw".equals(e.kind)) {
                fwFiles.add(e);
            }
        }
        out.log("Solo Web Utility installer: " + appFiles.size() + " app files, " + fwFiles.size() + " firmware files.");

        // 0. The golden image in this app must be intact
        out.progress("Checking the packaged firmware", 1);
        if (!verifyPackagedGoldenImage(fwFiles)) {
            return false;
        }

        // 1. Connect
        out.progress("Connecting to the Solo", 3);
        if (!reachable(copterHost, copterPort, 4000)) {
            out.log("[!] Cannot reach the Solo at " + copterHost + ". Join the SoloLink Wi-Fi and turn the Solo on.");
            return false;
        }
        session = connect(copterHost, copterPort, 3);
        out.log("[OK] Connected to the Solo (" + copterHost + ").");

        Result df = exec("df -h / /log 2>/dev/null || df -h", 0);
        out.log("Solo storage:");
        for (String l : df.out.trim().split("\n")) {
            out.log("    " + l);
        }

        // 2. Stop the running server, clean stale update folders
        out.progress("Preparing the Solo", 5);
        exec("mount -o remount,rw / 2>/dev/null || true", 0);
        exec("rm -rf /tmp/updates /log/updates 2>/dev/null || true", 0);
        exec("fuser -k 80/tcp 2>/dev/null || true", 0);
        exec("for p in $(ps | grep -E 'solo_web_app|SoloWebUtility2/app.py' | grep -v grep | awk '{print $1}'); do kill -9 $p 2>/dev/null; done", 0);
        String target = ensureWritableTarget();
        out.log("Installation folder: " + target);

        ChannelSftp sftp = (ChannelSftp) session.openChannel("sftp");
        sftp.connect(15000);
        // one request at a time: the Solo's sshd overflows on pipelined writes over Wi-Fi
        sftp.setBulkRequests(1);
        try {
            // 3. App files
            out.progress("Installing Solo Web Utility files", 8);
            Set<String> dirs = new LinkedHashSet<String>();
            for (Entry e : appFiles) {
                String remote = target + "/" + e.rel;
                dirs.add(remote.substring(0, remote.lastIndexOf('/')));
            }
            exec("mkdir -p " + join(dirs) + " 2>/dev/null || true", 0);
            int i = 0;
            for (Entry e : appFiles) {
                checkCancelled();
                i++;
                String remote = target + "/" + e.rel;
                out.log(String.format(Locale.US, "  [%d/%d] %s (%s)", i, appFiles.size(), e.rel, size(e.size)));
                exec("rm -f " + q(remote) + " 2>/dev/null || true", 0);
                InputStream in = assets.open(e.rel);
                try {
                    sftp.put(in, remote, null, ChannelSftp.OVERWRITE);
                } finally {
                    in.close();
                }
                out.progress("Installing Solo Web Utility files", 8 + (12 * i) / Math.max(1, appFiles.size()));
            }
            Result chk = exec("cd " + q(target) + " && md5sum " + joinRel(appFiles) + " 2>/dev/null", 0);
            int bad = countMd5Mismatches(appFiles, chk.out, "");
            if (bad > 0) {
                out.log("[!] " + bad + " app file(s) did not arrive intact.");
                return false;
            }
            out.log("[OK] All " + appFiles.size() + " app files installed and verified.");

            exec("chmod -R 755 " + q(target) + " 2>/dev/null || true", 0);
            exec("chmod +x " + target + "/*.py " + target + "/bin/*.py 2>/dev/null || true", 0);
            authorizeCopterKeyOnController();

            // 4. Firmware
            out.progress("Copying firmware to the Solo", 20);
            exec("rm -f " + LOG_FW_DIR + "/arducopter_solo.apj " + LOG_FW_DIR + "/arducopter_green.apj"
                    + " /firmware/arducopter_solo.apj /firmware/arducopter_green.apj 2>/dev/null || true", 0);
            Set<String> fwDirs = new LinkedHashSet<String>();
            fwDirs.add(LOG_FW_DIR);
            for (Entry e : fwFiles) {
                String remote = fwRemote(e);
                fwDirs.add(remote.substring(0, remote.lastIndexOf('/')));
            }
            exec("mkdir -p " + join(fwDirs) + " 2>/dev/null || true", 0);

            long totalBytes = 0;
            for (Entry e : fwFiles) {
                totalBytes += e.size;
            }
            long doneBytes = 0;
            int j = 0;
            for (Entry e : fwFiles) {
                checkCancelled();
                j++;
                String remote = fwRemote(e);
                out.log(String.format(Locale.US, "  [%d/%d] %s (%s)", j, fwFiles.size(), e.rel, size(e.size)));
                if (remoteMatches(sftp, remote, e)) {
                    out.log("      already on the Solo, unchanged");
                } else {
                    exec("rm -f " + q(remote) + " 2>/dev/null || true", 0);
                    InputStream in = assets.open(e.rel);
                    try {
                        sftp.put(in, remote, new Monitor(e.rel, doneBytes, totalBytes), ChannelSftp.OVERWRITE);
                    } finally {
                        in.close();
                    }
                }
                doneBytes += e.size;
                out.progress("Copying firmware to the Solo", 20 + (int) (70 * doneBytes / Math.max(1, totalBytes)));
            }
            StringBuilder fwList = new StringBuilder();
            for (Entry e : fwFiles) {
                fwList.append(' ').append(q(fwRemote(e)));
            }
            Result fwChk = exec("md5sum" + fwList + " 2>/dev/null", 0);
            int fwBad = 0;
            for (Entry e : fwFiles) {
                String got = md5For(fwChk.out, fwRemote(e));
                if (!e.md5.equals(got)) {
                    out.log("[!] " + e.rel + " on the Solo is damaged (MD5 " + got + ").");
                    fwBad++;
                }
            }
            if (fwBad > 0) {
                return false;
            }
            out.log("[OK] All " + fwFiles.size() + " firmware files on the Solo verified.");

            exec("mkdir -p /usr/bin/SoloWebUtility2 /log/SoloWebUtility2 /home/root/SoloWebUtility2 2>/dev/null || true", 0);
            exec("ln -sfn /log/firmware /usr/bin/SoloWebUtility2/firmware 2>/dev/null || true", 0);
            exec("ln -sfn /log/firmware /log/SoloWebUtility2/firmware 2>/dev/null || true", 0);
            exec("ln -sfn /log/firmware /home/root/SoloWebUtility2/firmware 2>/dev/null || true", 0);

            // 5. Start on boot (inittab respawn) and start now
            out.progress("Starting Solo Web Utility", 92);
            exec("rm -f /etc/init.d/solowebutility /etc/rc5.d/S99solowebutility 2>/dev/null || true", 0);
            Result rcLocal = exec("cat /etc/rc.local 2>/dev/null || true", 0);
            if (rcLocal.out.contains("SoloWebUtility") || rcLocal.out.contains("solo_web_app")) {
                StringBuilder nb = new StringBuilder();
                for (String l : rcLocal.out.split("\n", -1)) {
                    if (!l.contains("SoloWebUtility") && !l.contains("solo_web_app")) {
                        nb.append(l).append('\n');
                    }
                }
                writeRemote(sftp, "/etc/rc.local", trimEnd(nb.toString()) + "\n");
                exec("chmod 755 /etc/rc.local", 0);
                out.log("[OK] Removed old start-up lines from /etc/rc.local.");
            }
            Result tab = exec("cat /etc/inittab 2>/dev/null || true", 0);
            if (!tab.out.contains("SWU:345:respawn") && !tab.out.contains("SoloWebUtility")) {
                writeRemote(sftp, "/etc/inittab", trimEnd(tab.out)
                        + "\n\n# SoloWebUtility2 continuous supervision\n" + INITTAB_ENTRY);
                exec("chmod 644 /etc/inittab 2>/dev/null || true", 0);
                out.log("[OK] Solo Web Utility now starts automatically (/etc/inittab).");
            } else {
                out.log("[OK] /etc/inittab already starts Solo Web Utility.");
            }
        } finally {
            sftp.disconnect();
        }

        exec("telinit q 2>/dev/null || kill -HUP 1 2>/dev/null || true", 0);
        exec("fuser -k 80/tcp 2>/dev/null || true", 0);
        exec("for p in $(ps | grep -E 'solo_web_app|SoloWebUtility2' | grep -v grep | awk '{print $1}'); do kill -9 $p 2>/dev/null; done", 0);
        exec("sleep 1", 0);
        exec("mkdir -p /log /tmp 2>/dev/null; touch /log/solowebutility.log 2>/dev/null || true", 0);
        exec("ln -sfn /log/solowebutility.log /tmp/solo_web.log 2>/dev/null; ln -sfn /log/solowebutility.log /log/solo_web.log 2>/dev/null;"
                + " ln -sfn /log/solowebutility.log /tmp/solo_web_server.log 2>/dev/null; ln -sfn /log/solowebutility.log /log/solo_web_server.log 2>/dev/null || true", 0);
        exec("telinit q 2>/dev/null || true", 0);
        sleep(1500);
        Result ps = exec("ps | grep -E 'solo_web_app|SoloWebUtility2' | grep -v grep || true", 0);
        if (ps.out.trim().isEmpty()) {
            exec("nohup /usr/bin/python " + target + "/solo_web_app.py 80 </dev/null >>/log/solowebutility.log 2>&1 &", 5000);
            sleep(2000);
            ps = exec("ps | grep -E 'solo_web_app|SoloWebUtility2' | grep -v grep || true", 0);
        }
        if (ps.out.trim().isEmpty()) {
            out.log("[!] The web server did not start. Last log lines:");
            Result tail = exec("tail -n 15 /log/solowebutility.log 2>/dev/null || true", 0);
            for (String l : tail.out.trim().split("\n")) {
                out.log("    " + l);
            }
            return false;
        }
        out.log("[OK] Web server running: " + ps.out.trim().split("\n")[0].trim());
        out.progress("Installed", 100);
        return true;
    }

    // ------------------------------------------------------------------ steps

    private boolean verifyPackagedGoldenImage(List<Entry> fwFiles) throws Exception {
        Entry tar = null;
        Entry md5File = null;
        for (Entry e : fwFiles) {
            if (e.rel.equals("firmware/3dr-solo.tar.gz")) {
                tar = e;
            } else if (e.rel.equals("firmware/3dr-solo.tar.gz.md5")) {
                md5File = e;
            }
        }
        if (tar == null || md5File == null) {
            out.log("[!] This app is missing the golden image (3dr-solo.tar.gz).");
            return false;
        }
        String want = new String(readAll(assets.open(md5File.rel)), UTF8).trim().split("\\s+")[0].toLowerCase(Locale.US);
        String got = md5(assets.open(tar.rel));
        if (!got.equals(want) || !got.equals(tar.md5)) {
            out.log("[!] The golden image in this app is damaged (MD5 " + got + ", expected " + want + "). Reinstall the app.");
            return false;
        }
        out.log("[OK] Packaged golden image verified (MD5 " + got + ").");
        return true;
    }

    private String ensureWritableTarget() throws Exception {
        String target;
        if (exec("touch /log/.rw_test 2>/dev/null && rm -f /log/.rw_test 2>/dev/null", 0).rc == 0) {
            target = "/log/SoloWebUtility2";
        } else {
            exec("mount -o remount,rw /dev/mmcblk0p4 /log 2>/dev/null || mount -o remount,rw /log 2>/dev/null || true", 0);
            if (exec("touch /log/.rw_test 2>/dev/null && rm -f /log/.rw_test 2>/dev/null", 0).rc == 0) {
                target = "/log/SoloWebUtility2";
            } else {
                target = "/home/root/SoloWebUtility2";
            }
        }
        if (target.equals("/log/SoloWebUtility2")) {
            exec("mkdir -p /log/SoloWebUtility2/bin /log/SoloWebUtility2/static /log/SoloWebUtility2/templates /log/SoloWebUtility2/modules /log/firmware 2>/dev/null || true", 0);
            exec("chmod -R 755 /log/SoloWebUtility2 2>/dev/null || true", 0);
            exec("ln -sfn /log/firmware /log/SoloWebUtility2/firmware 2>/dev/null || true", 0);
            exec("rm -rf /home/root/SoloWebUtility2 2>/dev/null; ln -sfn /log/SoloWebUtility2 /home/root/SoloWebUtility2 2>/dev/null || true", 0);
            exec("rm -rf /usr/bin/SoloWebUtility2 2>/dev/null; ln -sfn /log/SoloWebUtility2 /usr/bin/SoloWebUtility2 2>/dev/null || true", 0);
        } else {
            exec("rm -rf /home/root/SoloWebUtility2 2>/dev/null", 0);
            exec("mkdir -p /home/root/SoloWebUtility2/bin /home/root/SoloWebUtility2/static /home/root/SoloWebUtility2/templates /home/root/SoloWebUtility2/modules /home/root/SoloWebUtility2/firmware 2>/dev/null || true", 0);
            exec("chmod -R 755 /home/root/SoloWebUtility2 2>/dev/null || true", 0);
            exec("ln -sfn /log/firmware /home/root/SoloWebUtility2/firmware 2>/dev/null || true", 0);
        }
        return target;
    }

    /** Lets the copter's web server manage the Controller without a password prompt. */
    private void authorizeCopterKeyOnController() {
        try {
            exec("test -f /home/root/.ssh/id_rsa || ssh-keygen -t rsa -N \"\" -f /home/root/.ssh/id_rsa", 0);
            Result key = exec("cat /home/root/.ssh/id_rsa.pub 2>/dev/null", 0);
            String k = key.out.trim();
            if (key.rc != 0 || !k.contains("ssh-rsa")) {
                return;
            }
            if (!reachable(controllerHost, controllerPort, 2000)) {
                out.log("Controller not reachable yet; the Solo will use its password fallback.");
                return;
            }
            Session ctrl = connect(controllerHost, controllerPort, 1);
            try {
                execOn(ctrl, "mkdir -p /home/root/.ssh /root/.ssh && chmod 700 /home/root/.ssh /root/.ssh", 0);
                execOn(ctrl, "grep -q " + q(k) + " /home/root/.ssh/authorized_keys 2>/dev/null || echo " + q(k)
                        + " >> /home/root/.ssh/authorized_keys", 0);
                execOn(ctrl, "grep -q " + q(k) + " /root/.ssh/authorized_keys 2>/dev/null || echo " + q(k)
                        + " >> /root/.ssh/authorized_keys", 0);
                execOn(ctrl, "chmod 600 /home/root/.ssh/authorized_keys /root/.ssh/authorized_keys 2>/dev/null || true", 0);
                out.log("[OK] Solo's SSH key authorized on the Controller.");
            } finally {
                ctrl.disconnect();
            }
        } catch (Exception e) {
            out.log("Could not set up Controller access (" + e.getMessage() + "); the Solo will use its password fallback.");
        }
    }

    private boolean remoteMatches(ChannelSftp sftp, String remote, Entry e) {
        try {
            SftpATTRS a = sftp.stat(remote);
            if (a.getSize() != e.size) {
                return false;
            }
            Result r = exec("md5sum " + q(remote) + " 2>/dev/null", 0);
            return e.md5.equals(md5For(r.out, remote));
        } catch (Exception ex) {
            return false;
        }
    }

    private void writeRemote(ChannelSftp sftp, String path, String content) throws Exception {
        sftp.put(new ByteArrayInputStream(content.getBytes(UTF8)), path, null, ChannelSftp.OVERWRITE);
    }

    private static String fwRemote(Entry e) {
        // firmware/x -> /log/firmware/x ; firmware/previous/x -> /log/firmware/previous/x
        return LOG_FW_DIR + e.rel.substring("firmware".length());
    }

    // ------------------------------------------------------------------ SSH helpers

    private Session connect(String host, int port, int attempts) throws Exception {
        Exception last = null;
        for (int a = 1; a <= attempts; a++) {
            try {
                JSch jsch = new JSch();
                Session s = jsch.getSession(USER, host, port);
                s.setPassword(PASSWORD);
                s.setConfig("StrictHostKeyChecking", "no");
                s.setConfig("PreferredAuthentications", "password,keyboard-interactive");
                // Open Solo runs an older OpenSSH: allow its algorithms as well
                s.setConfig("kex", JSch.getConfig("kex")
                        + ",diffie-hellman-group14-sha1,diffie-hellman-group-exchange-sha1,diffie-hellman-group1-sha1");
                s.setConfig("server_host_key", JSch.getConfig("server_host_key") + ",ssh-rsa,ssh-dss");
                s.setConfig("cipher.s2c", JSch.getConfig("cipher.s2c") + ",aes128-cbc,3des-cbc");
                s.setConfig("cipher.c2s", JSch.getConfig("cipher.c2s") + ",aes128-cbc,3des-cbc");
                s.setServerAliveInterval(15000);
                s.connect(15000);
                return s;
            } catch (Exception e) {
                last = e;
                if (a < attempts) {
                    sleep(1500);
                }
            }
        }
        throw new IOException("SSH connection to " + host + " failed: " + (last != null ? last.getMessage() : "?"));
    }

    static class Result {
        final int rc;
        final String out;

        Result(int rc, String out) {
            this.rc = rc;
            this.out = out;
        }
    }

    private Result exec(String cmd, long timeoutMs) throws Exception {
        return execOn(session, cmd, timeoutMs);
    }

    /** Runs a command; timeoutMs > 0 returns early (rc 0) for commands that leave a process behind. */
    private Result execOn(Session s, String cmd, long timeoutMs) throws Exception {
        checkCancelled();
        String full = cmd.trim().startsWith("cd ") ? cmd : "cd / && " + cmd;
        ChannelExec ch = (ChannelExec) s.openChannel("exec");
        ch.setCommand(full);
        ch.setInputStream(null);
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        ch.setOutputStream(buf);
        ch.setErrStream(new ByteArrayOutputStream());
        ch.connect(15000);
        long deadline = timeoutMs > 0 ? System.currentTimeMillis() + timeoutMs : Long.MAX_VALUE;
        while (!ch.isClosed()) {
            if (System.currentTimeMillis() > deadline) {
                ch.disconnect();
                return new Result(0, buf.toString("UTF-8"));
            }
            sleep(20);
        }
        int rc = ch.getExitStatus();
        ch.disconnect();
        return new Result(rc, buf.toString("UTF-8"));
    }

    private class Monitor implements SftpProgressMonitor {
        private final String name;
        private final long base;
        private final long total;
        private long sent;
        private int lastPct = -1;

        Monitor(String name, long base, long total) {
            this.name = name;
            this.base = base;
            this.total = total;
        }

        @Override
        public void init(int op, String src, String dest, long max) {
        }

        @Override
        public boolean count(long n) {
            sent += n;
            int pct = (int) (70 * (base + sent) / Math.max(1, total));
            if (pct != lastPct) {
                lastPct = pct;
                out.progress("Copying firmware to the Solo (" + name.substring(name.lastIndexOf('/') + 1) + ")", 20 + pct);
            }
            return !cancelled;
        }

        @Override
        public void end() {
        }
    }

    // ------------------------------------------------------------------ small utils

    static boolean reachable(String host, int port, int timeoutMs) {
        Socket s = new Socket();
        try {
            s.connect(new InetSocketAddress(host, port), timeoutMs);
            return true;
        } catch (IOException e) {
            return false;
        } finally {
            try {
                s.close();
            } catch (IOException ignored) {
            }
        }
    }

    /** Single-quotes a string for the Solo's shell. */
    static String q(String s) {
        return "'" + s.replace("'", "'\\''") + "'";
    }

    private static String join(Set<String> items) {
        StringBuilder b = new StringBuilder();
        for (String s : items) {
            b.append(' ').append(q(s));
        }
        return b.toString().trim();
    }

    private static String joinRel(List<Entry> files) {
        StringBuilder b = new StringBuilder();
        for (Entry e : files) {
            b.append(' ').append(q(e.rel));
        }
        return b.toString().trim();
    }

    /** md5sum output line for path -> md5 ("" if absent). */
    static String md5For(String md5sumOutput, String path) {
        for (String l : md5sumOutput.split("\n")) {
            String t = l.trim();
            int sp = t.indexOf(' ');
            if (sp > 0 && t.substring(sp).trim().replaceFirst("^\\*", "").equals(path)) {
                return t.substring(0, sp).toLowerCase(Locale.US);
            }
        }
        return "";
    }

    private int countMd5Mismatches(List<Entry> files, String md5sumOutput, String prefix) {
        int bad = 0;
        for (Entry e : files) {
            String got = md5For(md5sumOutput, prefix + e.rel);
            if (!e.md5.equals(got)) {
                out.log("[!] " + e.rel + ": MD5 on the Solo " + (got.isEmpty() ? "missing" : got) + ", expected " + e.md5);
                bad++;
            }
        }
        return bad;
    }

    static String md5(InputStream in) throws Exception {
        try {
            MessageDigest md = MessageDigest.getInstance("MD5");
            byte[] b = new byte[65536];
            int n;
            while ((n = in.read(b)) > 0) {
                md.update(b, 0, n);
            }
            StringBuilder sb = new StringBuilder();
            for (byte x : md.digest()) {
                sb.append(String.format(Locale.US, "%02x", x));
            }
            return sb.toString();
        } finally {
            in.close();
        }
    }

    static byte[] readAll(InputStream in) throws IOException {
        try {
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) {
                b.write(buf, 0, n);
            }
            return b.toByteArray();
        } finally {
            in.close();
        }
    }

    private static String trimEnd(String s) {
        int e = s.length();
        while (e > 0 && Character.isWhitespace(s.charAt(e - 1))) {
            e--;
        }
        return s.substring(0, e);
    }

    static String size(long n) {
        if (n >= 1024L * 1024L) {
            return String.format(Locale.US, "%.1f MB", n / (1024.0 * 1024.0));
        }
        if (n >= 1024L) {
            return String.format(Locale.US, "%.1f KB", n / 1024.0);
        }
        return n + " B";
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException ignored) {
        }
    }
}
