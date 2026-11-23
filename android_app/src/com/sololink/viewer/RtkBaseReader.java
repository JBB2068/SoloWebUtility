package com.sololink.viewer;

import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.hardware.usb.UsbConstants;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbEndpoint;
import android.hardware.usb.UsbInterface;
import android.hardware.usb.UsbManager;
import android.os.Build;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * RTK base on the phone's USB port: reads RTCM3 from a GNSS receiver that shows up as a USB CDC-ACM
 * serial device (a u-blox M8P/F9P on its own USB port) and hands whole, checked RTCM frames to the
 * listener about once a second. The base is set up beforehand in u-center (survey-in or a fixed
 * position, RTCM output on USB); this class only reads. The page posts the frames to the Solo,
 * which sends them to the Cube as GPS_RTCM_DATA.
 *
 * States for the page: none (no receiver), permission (Android is asking), denied, connected
 * (reading; "waiting" once nothing arrives for a while), error.
 */
public class RtkBaseReader {

    public interface Listener {
        /** Called on the reader thread. */
        void onRtcmFrames(List<byte[]> frames);

        /** Called on any thread. */
        void onRtkState(String state, String device, String message);

        /** Once a second while reading: what the base sends (JSON: bytes, nmea, ubx, svin, acks, naks). */
        void onRtkInfo(String json);
    }

    private static final String TAG = "SoloLinkRtk";
    static final String ACTION_USB_PERMISSION = "com.sololink.viewer.USB_PERMISSION";
    static final int UBLOX_VENDOR_ID = 0x1546;
    private static final int USB_CLASS_CDC_DATA = 0x0A;
    private static final int READ_TIMEOUT_MS = 250;
    private static final long HAND_OVER_MS = 1000;      // frames go to the page once a second
    private static final long QUIET_MS = 5000;          // no RTCM for this long: "waiting"
    private static final int MAX_PENDING_FRAMES = 400;  // ~ a minute of a typical base: drop the oldest

    private final Context context;
    private final UsbManager usbManager;
    private final Listener listener;

    private UsbDevice device;
    private UsbDeviceConnection connection;
    private UsbInterface dataInterface;
    private UsbInterface controlInterface;
    private UsbEndpoint inEndpoint;
    private UsbEndpoint outEndpoint;
    private final Object writeLock = new Object();
    private volatile UbxScanner ubx = new UbxScanner();
    private volatile long bytesIn = 0;
    private volatile boolean setupSent = false;
    private Thread readThread;
    private volatile boolean reading = false;
    private boolean receiverRegistered = false;
    private boolean permissionAsked = false;
    private String state = "none";

    private final BroadcastReceiver usbReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context ctx, Intent intent) {
            String action = intent.getAction();
            UsbDevice dev = intent.getParcelableExtra(UsbManager.EXTRA_DEVICE);
            if (ACTION_USB_PERMISSION.equals(action)) {
                permissionAsked = false;
                if (intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false) && dev != null) {
                    open(dev);
                } else {
                    setState("denied", dev, null);
                }
            } else if (UsbManager.ACTION_USB_DEVICE_ATTACHED.equals(action)) {
                scan();
            } else if (UsbManager.ACTION_USB_DEVICE_DETACHED.equals(action)) {
                if (dev != null && device != null && dev.getDeviceName().equals(device.getDeviceName())) {
                    close();
                    setState("none", null, "The base receiver was unplugged.");
                }
            }
        }
    };

    public RtkBaseReader(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.usbManager = (UsbManager) context.getSystemService(Context.USB_SERVICE);
        this.listener = listener;
    }

    public synchronized String getState() {
        return state;
    }

    public synchronized String getDeviceLabel() {
        return device != null ? label(device) : "";
    }

    /** Listen for the base being plugged in or out, and look for one now. */
    public void start() {
        if (usbManager == null) {
            setState("error", null, "This phone has no USB host support.");
            return;
        }
        if (!receiverRegistered) {
            IntentFilter filter = new IntentFilter();
            filter.addAction(ACTION_USB_PERMISSION);
            filter.addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED);
            filter.addAction(UsbManager.ACTION_USB_DEVICE_DETACHED);
            if (Build.VERSION.SDK_INT >= 33) {
                // the permission answer comes through our own PendingIntent; attach/detach are system broadcasts
                context.registerReceiver(usbReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
            } else {
                context.registerReceiver(usbReceiver, filter);
            }
            receiverRegistered = true;
        }
        scan();
    }

    public void stop() {
        close();
        if (receiverRegistered) {
            try {
                context.unregisterReceiver(usbReceiver);
            } catch (Exception ignored) {}
            receiverRegistered = false;
        }
    }

    /** Find a base receiver; ask Android for permission to use it, or open it. */
    public synchronized void scan() {
        if (usbManager == null || reading) return;
        UsbDevice found = null;
        for (UsbDevice dev : usbManager.getDeviceList().values()) {
            if (findBulkIn(dev) == null) continue;
            if (dev.getVendorId() == UBLOX_VENDOR_ID) {
                found = dev;
                break;
            }
            if (found == null) found = dev;
        }
        if (found == null) {
            setState("none", null, null);
            return;
        }
        if (usbManager.hasPermission(found)) {
            open(found);
            return;
        }
        if (permissionAsked) return;
        permissionAsked = true;
        Intent intent = new Intent(ACTION_USB_PERMISSION);
        intent.setPackage(context.getPackageName());     // explicit: required for a mutable PendingIntent
        int flags = Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0;
        PendingIntent pi = PendingIntent.getBroadcast(context, 0, intent, flags);
        setState("permission", found, null);
        usbManager.requestPermission(found, pi);
    }

    /** The CDC data interface's bulk IN endpoint, or null (not a serial device). */
    private static UsbEndpoint findBulkIn(UsbDevice dev) {
        return findBulk(dev, UsbConstants.USB_DIR_IN);
    }

    private static UsbEndpoint findBulk(UsbDevice dev, int direction) {
        for (int i = 0; i < dev.getInterfaceCount(); i++) {
            UsbInterface iface = dev.getInterface(i);
            if (iface.getInterfaceClass() != USB_CLASS_CDC_DATA) continue;
            for (int e = 0; e < iface.getEndpointCount(); e++) {
                UsbEndpoint ep = iface.getEndpoint(e);
                if (ep.getType() == UsbConstants.USB_ENDPOINT_XFER_BULK && ep.getDirection() == direction) {
                    return ep;
                }
            }
        }
        return null;
    }

    /**
     * Set the base up the way Mission Planner does when it connects: RTCM3 output on USB and a
     * new survey-in (not saved in the receiver). Returns null when sent, or why not.
     */
    public String setupBase(long minDurationS, double accuracyLimitM) {
        UsbDeviceConnection conn;
        UsbEndpoint out;
        synchronized (this) {
            conn = connection;
            out = outEndpoint;
        }
        if (conn == null || !reading) return "No base receiver is connected.";
        if (out == null) return "The base receiver cannot be written to.";
        synchronized (writeLock) {
            for (byte[] msg : UbxScanner.baseSetup(minDurationS, accuracyLimitM)) {
                int n = conn.bulkTransfer(out, msg, msg.length, 1000);
                if (n != msg.length) return "Sending the settings to the base failed.";
                SystemClock.sleep(30);
            }
        }
        setupSent = true;
        Log.i(TAG, "Base setup sent: survey-in " + minDurationS + " s, " + accuracyLimitM + " m");
        return null;
    }

    private synchronized void open(UsbDevice dev) {
        if (reading) return;
        UsbEndpoint ep = findBulkIn(dev);
        if (ep == null) {
            setState("error", dev, "Not a USB serial (CDC) receiver.");
            return;
        }
        UsbDeviceConnection conn = usbManager.openDevice(dev);
        if (conn == null) {
            setState("error", dev, "Android could not open the base receiver.");
            return;
        }
        UsbInterface data = null;
        UsbInterface control = null;
        for (int i = 0; i < dev.getInterfaceCount(); i++) {
            UsbInterface iface = dev.getInterface(i);
            if (iface.getInterfaceClass() == UsbConstants.USB_CLASS_COMM && control == null) control = iface;
            for (int e = 0; e < iface.getEndpointCount(); e++) {
                if (iface.getEndpoint(e) == ep) data = iface;
            }
        }
        if (data == null || !conn.claimInterface(data, true)) {
            conn.close();
            setState("error", dev, "Another app is using the base receiver.");
            return;
        }
        if (control != null && conn.claimInterface(control, true)) {
            // DTR + RTS on, 115200 8N1. A u-blox USB port ignores the speed but some receivers
            // only send while DTR is set.
            int idx = control.getId();
            conn.controlTransfer(0x21, 0x22, 0x03, idx, null, 0, 500);
            byte[] coding = { 0x00, (byte) 0xC2, 0x01, 0x00, 0x00, 0x00, 0x08 };
            conn.controlTransfer(0x21, 0x20, 0, idx, coding, coding.length, 500);
        } else {
            control = null;
        }
        device = dev;
        connection = conn;
        dataInterface = data;
        controlInterface = control;
        inEndpoint = ep;
        outEndpoint = findBulk(dev, UsbConstants.USB_DIR_OUT);
        ubx = new UbxScanner();
        bytesIn = 0;
        setupSent = false;
        reading = true;
        setState("connected", dev, null);
        readThread = new Thread(new Runnable() {
            @Override
            public void run() {
                readLoop();
            }
        }, "rtk-base-reader");
        readThread.start();
    }

    private void readLoop() {
        RtcmFramer framer = new RtcmFramer();
        List<byte[]> pending = new ArrayList<byte[]>();
        byte[] buf = new byte[16384];
        long lastHandOver = SystemClock.elapsedRealtime();
        long lastFrame = lastHandOver;
        boolean quiet = false;
        UsbDeviceConnection conn;
        UsbEndpoint ep;
        synchronized (this) {
            conn = connection;
            ep = inEndpoint;
        }
        while (reading && conn != null) {
            long before = SystemClock.elapsedRealtime();
            int n = conn.bulkTransfer(ep, buf, buf.length, READ_TIMEOUT_MS);
            long now = SystemClock.elapsedRealtime();
            if (n < 0 && now - before < 50) {
                // an error, not a timeout (e.g. just unplugged; the detach broadcast follows): don't spin
                SystemClock.sleep(100);
                now = SystemClock.elapsedRealtime();
            }
            if (n > 0) {
                bytesIn += n;
                ubx.add(buf, n);
                List<byte[]> frames = framer.add(buf, n);
                if (!frames.isEmpty()) {
                    pending.addAll(frames);
                    lastFrame = now;
                    if (quiet) {
                        quiet = false;
                        setState("connected", device, null);
                    }
                }
                while (pending.size() > MAX_PENDING_FRAMES) pending.remove(0);
            }
            if (!quiet && now - lastFrame > QUIET_MS) {
                quiet = true;
                setState("waiting", device, null);
            }
            if (now - lastHandOver >= HAND_OVER_MS) {
                lastHandOver = now;
                try {
                    listener.onRtkInfo(info());
                } catch (Exception e) {
                    Log.w(TAG, "RTK info failed: " + e.getMessage());
                }
                if (!pending.isEmpty()) {
                    List<byte[]> out = pending;
                    pending = new ArrayList<byte[]>();
                    try {
                        listener.onRtcmFrames(out);
                    } catch (Exception e) {
                        Log.w(TAG, "RTCM hand-over failed: " + e.getMessage());
                    }
                }
            }
        }
    }

    private void close() {
        Thread t;
        synchronized (this) {
            reading = false;
            t = readThread;
            readThread = null;
        }
        if (t != null && t != Thread.currentThread()) {
            try {
                t.join(1000);
            } catch (InterruptedException ignored) {}
        }
        synchronized (this) {
            if (connection != null) {
                try {
                    if (dataInterface != null) connection.releaseInterface(dataInterface);
                    if (controlInterface != null) connection.releaseInterface(controlInterface);
                } catch (Exception ignored) {}
                connection.close();
            }
            connection = null;
            dataInterface = null;
            controlInterface = null;
            inEndpoint = null;
            outEndpoint = null;
            device = null;
        }
    }

    /** What the base sends, for the page. */
    String info() {
        JSONObject o = new JSONObject();
        try {
            UbxScanner u = ubx;
            o.put("bytes", bytesIn);
            o.put("nmea", u.nmeaSeen);
            o.put("ubx", u.ubxSeen);
            o.put("writable", outEndpoint != null);
            o.put("setup_sent", setupSent);
            o.put("acks", u.acks);
            o.put("naks", u.naks);
            UbxScanner.Svin s = u.svin;
            if (s != null) {
                JSONObject j = new JSONObject();
                j.put("active", s.active);
                j.put("valid", s.valid);
                j.put("dur", s.durationS);
                j.put("acc", Math.round(s.accuracyM * 100.0) / 100.0);
                j.put("obs", s.observations);
                o.put("svin", j);
            }
        } catch (JSONException ignored) {}
        return o.toString();
    }

    private void setState(String newState, UsbDevice dev, String message) {
        String label;
        synchronized (this) {
            state = newState;
            label = dev != null ? label(dev) : "";
        }
        Log.i(TAG, "RTK base: " + newState + " " + label + (message != null ? " (" + message + ")" : ""));
        listener.onRtkState(newState, label, message);
    }

    private static String label(UsbDevice dev) {
        String name = null;
        if (Build.VERSION.SDK_INT >= 21) {
            try {
                name = dev.getProductName();
            } catch (SecurityException ignored) {}
        }
        if (name == null || name.trim().isEmpty()) {
            name = String.format("USB %04x:%04x", dev.getVendorId(), dev.getProductId());
        }
        return name.trim();
    }
}
