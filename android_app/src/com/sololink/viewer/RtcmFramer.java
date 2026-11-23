package com.sololink.viewer;

import java.util.ArrayList;
import java.util.List;

/**
 * Cuts a byte stream into whole RTCM3 frames: 0xD3, 6 reserved bits (0), 10-bit length, message,
 * CRC-24Q. Anything else the receiver sends (NMEA, UBX) and frames with a bad checksum are skipped.
 */
final class RtcmFramer {

    static final int MAX_FRAME = 1023 + 6;
    private final byte[] buf = new byte[4 * MAX_FRAME];
    private int len = 0;

    List<byte[]> add(byte[] data, int count) {
        List<byte[]> frames = new ArrayList<byte[]>();
        int off = 0;
        while (off < count) {
            int n = Math.min(count - off, buf.length - len);
            System.arraycopy(data, off, buf, len, n);
            len += n;
            off += n;
            parse(frames);
        }
        return frames;
    }

    private void parse(List<byte[]> frames) {
        int start = 0;
        while (len - start >= 3) {
            if ((buf[start] & 0xFF) != 0xD3 || (buf[start + 1] & 0xFC) != 0) {
                start++;
                continue;
            }
            int length = ((buf[start + 1] & 0x03) << 8) | (buf[start + 2] & 0xFF);
            int total = length + 6;
            if (len - start < total) break;
            int crc = ((buf[start + total - 3] & 0xFF) << 16) | ((buf[start + total - 2] & 0xFF) << 8)
                    | (buf[start + total - 1] & 0xFF);
            if (length > 0 && crc24q(buf, start, total - 3) == crc) {
                byte[] frame = new byte[total];
                System.arraycopy(buf, start, frame, 0, total);
                frames.add(frame);
                start += total;
            } else {
                start++;
            }
        }
        if (start > 0) {
            System.arraycopy(buf, start, buf, 0, len - start);
            len -= start;
        }
    }

    static int crc24q(byte[] data, int off, int count) {
        int crc = 0;
        for (int i = off; i < off + count; i++) {
            crc ^= (data[i] & 0xFF) << 16;
            for (int b = 0; b < 8; b++) {
                crc <<= 1;
                if ((crc & 0x1000000) != 0) crc ^= 0x1864CFB;
            }
        }
        return crc & 0xFFFFFF;
    }

    /** Message type of a whole frame (first 12 bits of the message). */
    static int messageType(byte[] frame) {
        if (frame.length < 6) return -1;
        return ((frame[3] & 0xFF) << 4) | ((frame[4] & 0xFF) >> 4);
    }
}
