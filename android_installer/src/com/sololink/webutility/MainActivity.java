package com.sololink.webutility;

import android.app.Activity;
import android.content.Intent;
import android.content.res.AssetManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.ConnectivityManager;
import android.net.LinkAddress;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.net.Uri;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.util.ArrayList;
import java.util.List;

/**
 * Solo Web Utility v1.2.0 for Android: installs Solo Web Utility on a 3DR Solo from
 * the phone over SoloLink Wi-Fi (same result as the Windows installer). Installer only:
 * the utility is then used in a browser at http://10.1.1.10 and this app can be deleted.
 */
public class MainActivity extends Activity {

    private static final String SOLO_URL = "http://10.1.1.10";
    private static final int BG = Color.parseColor("#0f172a");
    private static final int CARD = Color.parseColor("#1e293b");
    private static final int TEXT = Color.parseColor("#e2e8f0");
    private static final int MUTED = Color.parseColor("#94a3b8");
    private static final int ACCENT = Color.parseColor("#f59e0b");

    private final Handler ui = new Handler(Looper.getMainLooper());
    private TextView status;
    private TextView logView;
    private ScrollView logScroll;
    private ProgressBar bar;
    private Button btnInstall;
    private Button btnOpen;
    private Button btnWifi;
    private volatile SoloInstaller running;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();
        refreshNetworkState();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (running == null) {
            refreshNetworkState();
        }
    }

    // ------------------------------------------------------------------ UI

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics());
    }

    private Button button(String text, int color) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setTextColor(color == ACCENT ? Color.BLACK : TEXT);
        b.setBackgroundColor(color);
        b.setTextSize(16);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(52));
        lp.topMargin = dp(10);
        b.setLayoutParams(lp);
        return b;
    }

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(BG);
        root.setPadding(dp(16), dp(20), dp(16), dp(16));

        TextView title = new TextView(this);
        title.setText("Solo Web Utility v1.2.0");
        title.setTextColor(ACCENT);
        title.setTextSize(22);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        root.addView(title);

        TextView sub = new TextView(this);
        sub.setText("Installs Solo Web Utility and its firmware on your 3DR Solo over SoloLink Wi-Fi.\n"
                + "1. Turn on the Solo and the Controller.\n"
                + "2. Join the SoloLink_… Wi-Fi on this phone (keep it connected if Android says there is no internet).\n"
                + "3. Tap Install. Keep this screen open until it finishes (about 15–25 minutes).\n"
                + "Afterwards use Solo Web Utility in a browser at http://10.1.1.10 and delete this app.");
        sub.setTextColor(MUTED);
        sub.setTextSize(14);
        sub.setPadding(0, dp(6), 0, dp(6));
        root.addView(sub);

        status = new TextView(this);
        status.setTextColor(TEXT);
        status.setTextSize(15);
        status.setTypeface(Typeface.DEFAULT_BOLD);
        status.setPadding(0, dp(8), 0, dp(4));
        root.addView(status);

        bar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        bar.setMax(100);
        root.addView(bar, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(10)));

        btnWifi = button("Open Wi-Fi settings", CARD);
        btnWifi.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                startActivity(new Intent(Settings.ACTION_WIFI_SETTINGS));
            }
        });
        root.addView(btnWifi);

        btnInstall = button("Install on Solo", ACCENT);
        btnInstall.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                startInstall();
            }
        });
        root.addView(btnInstall);

        btnOpen = button("Open http://10.1.1.10 in the browser", CARD);
        btnOpen.setVisibility(View.GONE);   // shown after a successful install
        btnOpen.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(SOLO_URL)));
            }
        });
        root.addView(btnOpen);

        logView = new TextView(this);
        logView.setTextColor(MUTED);
        logView.setTextSize(12);
        logView.setTypeface(Typeface.MONOSPACE);
        logView.setPadding(dp(10), dp(10), dp(10), dp(10));
        logScroll = new ScrollView(this);
        logScroll.setBackgroundColor(CARD);
        logScroll.addView(logView);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f);
        lp.topMargin = dp(12);
        root.addView(logScroll, lp);

        setContentView(root);
    }

    private void setStatus(final String s, final int pct) {
        ui.post(new Runnable() {
            @Override
            public void run() {
                status.setText(s);
                if (pct >= 0) {
                    bar.setProgress(pct);
                }
            }
        });
    }

    private void appendLog(final String line) {
        ui.post(new Runnable() {
            @Override
            public void run() {
                logView.append(line + "\n");
                logScroll.post(new Runnable() {
                    @Override
                    public void run() {
                        logScroll.fullScroll(View.FOCUS_DOWN);
                    }
                });
            }
        });
    }

    // ------------------------------------------------------------------ network

    /** The Wi-Fi network that holds a 10.1.1.x address (SoloLink), or null. */
    private Network findSoloLink() {
        ConnectivityManager cm = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        if (cm == null) {
            return null;
        }
        for (Network n : cm.getAllNetworks()) {
            NetworkCapabilities caps = cm.getNetworkCapabilities(n);
            if (caps == null || !caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) {
                continue;
            }
            LinkProperties lp = cm.getLinkProperties(n);
            if (lp == null) {
                continue;
            }
            for (LinkAddress a : lp.getLinkAddresses()) {
                String ip = a.getAddress().getHostAddress();
                if (ip != null && ip.startsWith("10.1.1.")) {
                    return n;
                }
            }
        }
        return null;
    }

    /** Sends this app's traffic over SoloLink even though it has no internet. */
    private boolean bindToSoloLink() {
        Network n = findSoloLink();
        ConnectivityManager cm = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        if (n == null || cm == null) {
            return false;
        }
        cm.bindProcessToNetwork(n);
        return true;
    }

    private void refreshNetworkState() {
        boolean on = findSoloLink() != null;
        btnWifi.setVisibility(on ? View.GONE : View.VISIBLE);
        btnInstall.setEnabled(on);
        btnInstall.setAlpha(on ? 1f : 0.4f);
        setStatus(on ? "Connected to SoloLink. Ready to install." : "Not on SoloLink Wi-Fi. Join SoloLink_… first.", -1);
    }

    // ------------------------------------------------------------------ install

    private void startInstall() {
        if (running != null) {
            return;
        }
        if (!bindToSoloLink()) {
            refreshNetworkState();
            return;
        }
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        btnInstall.setEnabled(false);
        btnInstall.setAlpha(0.4f);
        btnOpen.setVisibility(View.GONE);
        logView.setText("");
        setStatus("Starting…", 0);

        final SoloInstaller inst = new SoloInstaller(new ApkAssets(getAssets()), new SoloInstaller.Listener() {
            @Override
            public void log(String line) {
                appendLog(line);
            }

            @Override
            public void progress(String stage, int percent) {
                setStatus(stage, percent);
            }
        });
        running = inst;
        new Thread(new Runnable() {
            @Override
            public void run() {
                final boolean ok = inst.run();
                ui.post(new Runnable() {
                    @Override
                    public void run() {
                        running = null;
                        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                        btnInstall.setEnabled(true);
                        btnInstall.setAlpha(1f);
                        if (ok) {
                            btnInstall.setText("Install again");
                            btnOpen.setVisibility(View.VISIBLE);
                            setStatus("Installed. Open http://10.1.1.10 in a browser (password: sololink). "
                                    + "This installer app can now be deleted.", 100);
                            appendLog("\nDone. Solo Web Utility is running on the Solo: open http://10.1.1.10 in a browser"
                                    + " on this phone or a PC joined to SoloLink. You can delete this installer app.");
                        } else {
                            setStatus("Installation did not finish. See the messages below, then tap Install again.", -1);
                        }
                    }
                });
            }
        }, "solo-installer").start();
    }

    @Override
    public void onBackPressed() {
        if (running != null) {
            setStatus("Installation is running. Keep this screen open until it finishes.", -1);
            return;
        }
        super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        SoloInstaller r = running;
        if (r != null) {
            r.cancel();
        }
        super.onDestroy();
    }

    // ------------------------------------------------------------------ assets

    /** Reads files from the APK: assets/manifest.txt + assets/swu/<relpath>. */
    static class ApkAssets implements SoloInstaller.Assets {
        private final AssetManager am;

        ApkAssets(AssetManager am) {
            this.am = am;
        }

        @Override
        public InputStream open(String rel) throws IOException {
            return am.open("swu/" + rel, AssetManager.ACCESS_STREAMING);
        }

        @Override
        public List<SoloInstaller.Entry> entries() throws IOException {
            List<SoloInstaller.Entry> list = new ArrayList<SoloInstaller.Entry>();
            BufferedReader r = new BufferedReader(new InputStreamReader(am.open("manifest.txt"), "UTF-8"));
            try {
                String line;
                while ((line = r.readLine()) != null) {
                    if (line.trim().isEmpty() || line.startsWith("#")) {
                        continue;
                    }
                    SoloInstaller.Entry e = SoloInstaller.Entry.parse(line);
                    if (e != null) {
                        list.add(e);
                    }
                }
            } finally {
                r.close();
            }
            return list;
        }
    }
}
