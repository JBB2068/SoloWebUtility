package com.sololink.viewer;

/**
 * Picks u-blox UBX messages out of the base's byte stream (which also carries RTCM and NMEA):
 * NAV-SVIN (survey-in progress) and ACK/NAK for the settings the app sends. Also notes whether
 * NMEA text arrives, so the page can say "the base sends GPS text but no corrections".
 * Builds UBX messages too (survey-in and RTCM output, as Mission Planner sets up a base).
 */
final class UbxScanner {

    static final class Svin {
        long durationS;
        double accuracyM;
        long observations;
        boolean valid;
        boolean active;
    }

    private final byte[] buf = new byte[4096];
    private int len = 0;
    volatile boolean nmeaSeen = false;
    volatile boolean ubxSeen = false;
    volatile Svin svin = null;
    volatile int acks = 0;
    volatile int naks = 0;

    void add(byte[] data, int count) {
        for (int i = 0; i + 1 < count; i++) {
            if (data[i] == '$' && data[i + 1] == 'G') {
                nmeaSeen = true;
                break;
            }
        }
        int off = 0;
        while (off < count) {
            int n = Math.min(count - off, buf.length - len);
            System.arraycopy(data, off, buf, len, n);
            len += n;
            off += n;
            parse();
        }
    }

    private void parse() {
        int start = 0;
        while (len - start >= 8) {
            if ((buf[start] & 0xFF) != 0xB5 || (buf[start + 1] & 0xFF) != 0x62) {
                start++;
                continue;
            }
            int cls = buf[start + 2] & 0xFF;
            int id = buf[start + 3] & 0xFF;
            int plen = (buf[start + 4] & 0xFF) | ((buf[start + 5] & 0xFF) << 8);
            if (plen > 1024) {
                start++;
                continue;
            }
            int total = plen + 8;
            if (len - start < total) break;
            int[] ck = checksum(buf, start + 2, plen + 4);
            if ((buf[start + total - 2] & 0xFF) == ck[0] && (buf[start + total - 1] & 0xFF) == ck[1]) {
                ubxSeen = true;
                handle(cls, id, buf, start + 6, plen);
                start += total;
            } else {
                start++;
            }
        }
        if (start > 0) {
            System.arraycopy(buf, start, buf, 0, len - start);
            len -= start;
        }
        if (len == buf.length) len = 0;      // nothing usable in a full buffer: start over
    }

    private void handle(int cls, int id, byte[] b, int p, int plen) {
        if (cls == 0x01 && id == 0x3B && plen >= 40) {          // NAV-SVIN
            Svin s = new Svin();
            s.durationS = u32(b, p + 8);
            s.accuracyM = u32(b, p + 28) / 10000.0;
            s.observations = u32(b, p + 32);
            s.valid = b[p + 36] != 0;
            s.active = b[p + 37] != 0;
            svin = s;
        } else if (cls == 0x05 && id == 0x01) {
            acks++;
        } else if (cls == 0x05 && id == 0x00) {
            naks++;
        }
    }

    private static long u32(byte[] b, int p) {
        return (b[p] & 0xFFL) | ((b[p + 1] & 0xFFL) << 8) | ((b[p + 2] & 0xFFL) << 16) | ((b[p + 3] & 0xFFL) << 24);
    }

    static int[] checksum(byte[] b, int off, int count) {
        int a = 0;
        int c = 0;
        for (int i = off; i < off + count; i++) {
            a = (a + (b[i] & 0xFF)) & 0xFF;
            c = (c + a) & 0xFF;
        }
        return new int[] { a, c };
    }

    static byte[] message(int cls, int id, byte[] payload) {
        byte[] m = new byte[payload.length + 8];
        m[0] = (byte) 0xB5;
        m[1] = 0x62;
        m[2] = (byte) cls;
        m[3] = (byte) id;
        m[4] = (byte) (payload.length & 0xFF);
        m[5] = (byte) (payload.length >> 8);
        System.arraycopy(payload, 0, m, 6, payload.length);
        int[] ck = checksum(m, 2, payload.length + 4);
        m[m.length - 2] = (byte) ck[0];
        m[m.length - 1] = (byte) ck[1];
        return m;
    }

    private static void put32(byte[] b, int p, long v) {
        b[p] = (byte) v;
        b[p + 1] = (byte) (v >> 8);
        b[p + 2] = (byte) (v >> 16);
        b[p + 3] = (byte) (v >> 24);
    }

    /** CFG-TMODE3: mode 0 = off, 1 = survey-in (minimum time, accuracy limit). */
    static byte[] tmode3(int mode, long minDurationS, double accuracyLimitM) {
        byte[] p = new byte[40];
        p[2] = (byte) mode;
        put32(p, 24, minDurationS);
        put32(p, 28, Math.round(accuracyLimitM * 10000.0));
        return message(0x06, 0x71, p);
    }

    /** CFG-MSG, short form: rate of one message on the port this arrives on (USB). */
    static byte[] msgRate(int cls, int id, int rate) {
        return message(0x06, 0x01, new byte[] { (byte) cls, (byte) id, (byte) rate });
    }

    /** CFG-PRT for the USB port: UBX + NMEA + RTCM3 in and out. */
    static byte[] usbPort() {
        byte[] p = new byte[20];
        p[0] = 3;                       // USB
        p[12] = 0x27;                   // in: UBX, NMEA, RTCM2, RTCM3
        p[14] = 0x23;                   // out: UBX, NMEA, RTCM3
        return message(0x06, 0x00, p);
    }

    /**
     * What a base needs (not saved in the receiver, as Mission Planner does it): RTCM3 on USB,
     * the base position (1005) and observations for GPS, GLONASS, Galileo, BeiDou (MSM7) plus
     * GLONASS biases (1230); NAV-SVIN once a second for the progress; survey-in restarted.
     * An M8P refuses the messages it does not have (Galileo, BeiDou...): that is fine.
     */
    static byte[][] baseSetup(long minDurationS, double accuracyLimitM) {
        return new byte[][] {
            usbPort(),
            msgRate(0xF5, 0x05, 1),     // 1005 base position
            msgRate(0xF5, 0x4D, 1),     // 1077 GPS MSM7
            msgRate(0xF5, 0x57, 1),     // 1087 GLONASS MSM7
            msgRate(0xF5, 0x61, 1),     // 1097 Galileo MSM7
            msgRate(0xF5, 0x7F, 1),     // 1127 BeiDou MSM7
            msgRate(0xF5, 0xE6, 1),     // 1230 GLONASS code-phase biases
            msgRate(0x01, 0x3B, 1),     // NAV-SVIN
            tmode3(0, 0, 0),
            tmode3(1, minDurationS, accuracyLimitM),
        };
    }
}
