package com.sololink.viewer;

import android.Manifest;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkInfo;
import android.net.NetworkRequest;
import android.net.wifi.ScanResult;
import android.net.wifi.WifiConfiguration;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.net.wifi.WifiNetworkSpecifier;
import android.net.wifi.WifiNetworkSuggestion;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PatternMatcher;
import android.provider.Settings;
import android.util.Log;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public class MainActivity extends Activity {

    private static final String TAG = "SoloLinkViewer";
    private static final String TARGET_URL = "http://10.1.1.10";
    private static final String SSID_PREFIX = "sololink_";
    private static final String WIFI_PASSKEY = "sololink";
    private static final int PERMISSION_REQ_CODE = 101;

    // UI elements
    private WebView webView;
    private View statusOverlay;
    private View topBarControls;
    private ProgressBar loadProgressBar;
    private ProgressBar statusSpinner;
    private TextView txtStatus;
    private TextView txtDetails;
    private Button btnRetry;
    private Button btnWifiSettings;
    private Button btnQuickRefresh;
    private Button btnQuickReconnect;

    // Managers & state
    private WifiManager wifiManager;
    private ConnectivityManager connectivityManager;
    private ConnectivityManager.NetworkCallback networkCallback;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private boolean isRegisteredReceiver = false;
    private boolean isConnecting = false;
    private boolean isPageLoaded = false;
    private long backPressedTime = 0;
    private String matchedSsid = null;
    private Runnable pageRetryRunnable;

    private final BroadcastReceiver wifiScanReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (WifiManager.SCAN_RESULTS_AVAILABLE_ACTION.equals(intent.getAction())) {
                boolean success = intent.getBooleanExtra(WifiManager.EXTRA_RESULTS_UPDATED, false);
                handleScanResults(success);
            }
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Keep display awake while viewing Solo telemetry/controls
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        setContentView(R.layout.activity_main);

        applyImmersiveFullScreen();
        initViews();
        setupWebView();

        wifiManager = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        connectivityManager = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);

        // Check if device is already connected to SoloLink Wi-Fi
        if (checkIfAlreadyConnectedToSoloLink()) {
            updateStatus("Connected to SoloLink!", "Loading web console at " + TARGET_URL + "...", true);
            bindToCurrentWifiAndLoadPage();
            return;
        }

        // Check permissions and start discovery
        if (checkPermissions()) {
            startSoloLinkDiscovery();
        } else {
            requestPermissions();
        }
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            applyImmersiveFullScreen();
        }
    }

    private void applyImmersiveFullScreen() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            getWindow().setDecorFitsSystemWindows(false);
            WindowInsetsController controller = getWindow().getInsetsController();
            if (controller != null) {
                controller.hide(WindowInsets.Type.statusBars() | WindowInsets.Type.navigationBars());
                controller.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            }
        } else {
            View decorView = getWindow().getDecorView();
            decorView.setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_FULLSCREEN
            );
        }
    }

    private void initViews() {
        webView = findViewById(R.id.webView);
        statusOverlay = findViewById(R.id.statusOverlay);
        topBarControls = findViewById(R.id.topBarControls);
        loadProgressBar = findViewById(R.id.loadProgressBar);
        statusSpinner = findViewById(R.id.statusSpinner);
        txtStatus = findViewById(R.id.txtStatus);
        txtDetails = findViewById(R.id.txtDetails);
        btnRetry = findViewById(R.id.btnRetry);
        btnWifiSettings = findViewById(R.id.btnWifiSettings);
        btnQuickRefresh = findViewById(R.id.btnQuickRefresh);
        btnQuickReconnect = findViewById(R.id.btnQuickReconnect);

        btnRetry.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                isConnecting = false;
                startSoloLinkDiscovery();
            }
        });

        btnWifiSettings.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                try {
                    startActivity(new Intent(Settings.ACTION_WIFI_SETTINGS));
                } catch (Exception e) {
                    Toast.makeText(MainActivity.this, "Unable to open Wi-Fi settings", Toast.LENGTH_SHORT).show();
                }
            }
        });

        btnQuickRefresh.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (webView != null) {
                    webView.reload();
                }
            }
        });

        btnQuickReconnect.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                isConnecting = false;
                isPageLoaded = false;
                statusOverlay.setVisibility(View.VISIBLE);
                topBarControls.setVisibility(View.GONE);
                startSoloLinkDiscovery();
            }
        });
    }

    private void setupWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setSupportZoom(true);
        settings.setBuiltInZoomControls(true);
        settings.setDisplayZoomControls(false);
        settings.setMediaPlaybackRequiresUserGesture(false);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                if (newProgress < 100) {
                    loadProgressBar.setVisibility(View.VISIBLE);
                    loadProgressBar.setProgress(newProgress);
                } else {
                    loadProgressBar.setVisibility(View.GONE);
                }
            }
        });

        webView.setWebViewClient(new WebViewClient() {
            // Keep all navigation within WebView, never load external browser!
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return false;
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return false;
            }

            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                Log.d(TAG, "onPageStarted: " + url);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                Log.d(TAG, "onPageFinished: " + url);
                isPageLoaded = true;
                // Once page has finished loading, reveal full screen WebView and discreet controls
                statusOverlay.setVisibility(View.GONE);
                webView.setVisibility(View.VISIBLE);
                topBarControls.setVisibility(View.VISIBLE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && request.isForMainFrame()) {
                    handlePageLoadError("Error loading " + TARGET_URL + ": " + error.getDescription());
                }
            }

            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                handlePageLoadError("Error loading " + failingUrl + ": " + description);
            }
        });
    }

    private void handlePageLoadError(String errMsg) {
        Log.w(TAG, "handlePageLoadError: " + errMsg);
        if (!isPageLoaded) {
            updateStatus("Waiting for Solo Web Server...",
                    "Connected to SoloLink, but 10.1.1.10 is not responding yet.\nRetrying in 3 seconds...", true);
            statusOverlay.setVisibility(View.VISIBLE);

            if (pageRetryRunnable != null) {
                mainHandler.removeCallbacks(pageRetryRunnable);
            }
            pageRetryRunnable = new Runnable() {
                @Override
                public void run() {
                    loadSoloWeb();
                }
            };
            mainHandler.postDelayed(pageRetryRunnable, 3000);
        }
    }

    private void loadSoloWeb() {
        Log.i(TAG, "Loading URL: " + TARGET_URL);
        updateStatus("Loading Solo Interface...", TARGET_URL, true);
        webView.loadUrl(TARGET_URL);
    }

    private boolean checkPermissions() {
        List<String> needed = new ArrayList<String>();
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.ACCESS_FINE_LOCATION);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (checkSelfPermission(Manifest.permission.NEARBY_WIFI_DEVICES) != PackageManager.PERMISSION_GRANTED) {
                needed.add(Manifest.permission.NEARBY_WIFI_DEVICES);
            }
        }
        return needed.isEmpty();
    }

    private void requestPermissions() {
        List<String> list = new ArrayList<String>();
        list.add(Manifest.permission.ACCESS_FINE_LOCATION);
        list.add(Manifest.permission.ACCESS_COARSE_LOCATION);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            list.add(Manifest.permission.NEARBY_WIFI_DEVICES);
        }
        requestPermissions(list.toArray(new String[0]), PERMISSION_REQ_CODE);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERMISSION_REQ_CODE) {
            boolean allGranted = true;
            for (int res : grantResults) {
                if (res != PackageManager.PERMISSION_GRANTED) {
                    allGranted = false;
                    break;
                }
            }
            if (allGranted || checkPermissions()) {
                startSoloLinkDiscovery();
            } else {
                updateStatus("Permission Required",
                        "Location / Wi-Fi permissions are needed to scan and connect to SoloLink.", false);
            }
        }
    }

    private boolean checkIfAlreadyConnectedToSoloLink() {
        try {
            if (wifiManager != null) {
                WifiInfo info = wifiManager.getConnectionInfo();
                if (info != null && info.getSSID() != null) {
                    String ssid = cleanSsid(info.getSSID());
                    if (isSoloLinkSsid(ssid)) {
                        matchedSsid = ssid;
                        Log.i(TAG, "Already connected to SoloLink: " + ssid);
                        return true;
                    }
                }
            }
        } catch (Exception e) {
            Log.e(TAG, "Error checking current connection: " + e.getMessage());
        }
        return false;
    }

    private void bindToCurrentWifiAndLoadPage() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && connectivityManager != null) {
            for (Network net : connectivityManager.getAllNetworks()) {
                NetworkCapabilities caps = connectivityManager.getNetworkCapabilities(net);
                if (caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) {
                    connectivityManager.bindProcessToNetwork(net);
                    Log.i(TAG, "Bound process to active Wi-Fi network");
                    break;
                }
            }
        }
        loadSoloWeb();
    }

    private void startSoloLinkDiscovery() {
        if (checkIfAlreadyConnectedToSoloLink()) {
            bindToCurrentWifiAndLoadPage();
            return;
        }

        updateStatus("Scanning for SoloLink...", "Looking for network beginning with \"sololink_\"...", true);

        // Ensure Wi-Fi is enabled
        if (wifiManager != null && !wifiManager.isWifiEnabled()) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
                wifiManager.setWifiEnabled(true);
            } else {
                Toast.makeText(this, "Please enable Wi-Fi", Toast.LENGTH_SHORT).show();
            }
        }

        // Register receiver for scan results
        if (!isRegisteredReceiver) {
            registerReceiver(wifiScanReceiver, new IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION));
            isRegisteredReceiver = true;
        }

        // Trigger immediate scan and also inspect current cache
        if (wifiManager != null) {
            boolean scanStarted = wifiManager.startScan();
            Log.d(TAG, "startScan returned: " + scanStarted);
            handleScanResults(scanStarted);
        }
    }

    private void handleScanResults(boolean updated) {
        if (isConnecting || isPageLoaded) {
            return;
        }

        try {
            List<ScanResult> results = wifiManager != null ? wifiManager.getScanResults() : null;
            if (results != null && !results.isEmpty()) {
                ScanResult bestMatch = null;
                for (ScanResult r : results) {
                    if (r.SSID != null && isSoloLinkSsid(r.SSID)) {
                        if (bestMatch == null || r.level > bestMatch.level) {
                            bestMatch = r;
                        }
                    }
                }

                if (bestMatch != null) {
                    matchedSsid = cleanSsid(bestMatch.SSID);
                    Log.i(TAG, "Found SoloLink network: " + matchedSsid + " (RSSI: " + bestMatch.level + " dBm)");
                    connectToSoloLink(matchedSsid);
                    return;
                }
            }
        } catch (SecurityException se) {
            Log.e(TAG, "SecurityException on getScanResults: " + se.getMessage());
        }

        // If scan didn't find it yet on Android 10+, we can initiate pattern specifier request as well
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && !isConnecting) {
            connectUsingNetworkSpecifier(null);
        } else {
            updateStatus("Searching for SoloLink...",
                    "No \"sololink_*\" network seen yet. Ensure Solo is powered on.", true);
        }
    }

    private boolean isSoloLinkSsid(String rawSsid) {
        if (rawSsid == null) return false;
        String s = cleanSsid(rawSsid).toLowerCase();
        return s.startsWith(SSID_PREFIX.toLowerCase());
    }

    private String cleanSsid(String ssid) {
        if (ssid != null && ssid.startsWith("\"") && ssid.endsWith("\"") && ssid.length() >= 2) {
            return ssid.substring(1, ssid.length() - 1);
        }
        return ssid;
    }

    private synchronized void connectToSoloLink(final String targetSsid) {
        if (isConnecting) return;
        isConnecting = true;

        updateStatus("Connecting to SoloLink...",
                "SSID: " + (targetSsid != null ? targetSsid : "sololink_*") + "\nPasskey: " + WIFI_PASSKEY, true);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            connectUsingNetworkSpecifier(targetSsid);
        } else {
            connectLegacyWifi(targetSsid);
        }
    }

    private void connectUsingNetworkSpecifier(final String targetSsid) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return;

        try {
            WifiNetworkSpecifier.Builder specBuilder = new WifiNetworkSpecifier.Builder();

            if (targetSsid != null && !targetSsid.isEmpty()) {
                specBuilder.setSsid(targetSsid);
            } else {
                specBuilder.setSsidPattern(new PatternMatcher(SSID_PREFIX, PatternMatcher.PATTERN_PREFIX));
            }
            specBuilder.setWpa2Passphrase(WIFI_PASSKEY);

            NetworkRequest request = new NetworkRequest.Builder()
                    .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
                    .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) // Solo drone has no internet!
                    .setNetworkSpecifier(specBuilder.build())
                    .build();

            if (networkCallback != null && connectivityManager != null) {
                try {
                    connectivityManager.unregisterNetworkCallback(networkCallback);
                } catch (Exception ignored) {}
            }

            networkCallback = new ConnectivityManager.NetworkCallback() {
                @Override
                public void onAvailable(Network network) {
                    super.onAvailable(network);
                    Log.i(TAG, "NetworkCallback: onAvailable. Binding process to network.");
                    connectivityManager.bindProcessToNetwork(network);

                    mainHandler.post(new Runnable() {
                        @Override
                        public void run() {
                            updateStatus("Connected to SoloLink!",
                                    "Network bound. Loading 10.1.1.10...", true);
                            loadSoloWeb();
                        }
                    });
                }

                @Override
                public void onUnavailable() {
                    super.onUnavailable();
                    Log.w(TAG, "NetworkCallback: onUnavailable");
                    mainHandler.post(new Runnable() {
                        @Override
                        public void run() {
                            isConnecting = false;
                            updateStatus("Connection Unavailable",
                                    "Could not connect to SoloLink. Make sure Solo Wi-Fi is reachable.", false);
                        }
                    });
                }

                @Override
                public void onLost(Network network) {
                    super.onLost(network);
                    Log.w(TAG, "NetworkCallback: onLost");
                    mainHandler.post(new Runnable() {
                        @Override
                        public void run() {
                            if (isPageLoaded) {
                                Toast.makeText(MainActivity.this, "SoloLink Wi-Fi connection lost", Toast.LENGTH_LONG).show();
                            }
                        }
                    });
                }
            };

            connectivityManager.requestNetwork(request, networkCallback);

            // Also add WifiNetworkSuggestion as helper on Android 10+
            if (targetSsid != null && wifiManager != null) {
                WifiNetworkSuggestion suggestion = new WifiNetworkSuggestion.Builder()
                        .setSsid(targetSsid)
                        .setWpa2Passphrase(WIFI_PASSKEY)
                        .setIsAppInteractionRequired(false)
                        .build();
                wifiManager.addNetworkSuggestions(Collections.singletonList(suggestion));
            }

        } catch (Exception e) {
            Log.e(TAG, "Exception in connectUsingNetworkSpecifier: " + e.getMessage(), e);
            updateStatus("Connection Error", e.getMessage(), false);
            isConnecting = false;
        }
    }

    @SuppressWarnings("deprecation")
    private void connectLegacyWifi(final String targetSsid) {
        if (wifiManager == null || targetSsid == null) return;

        try {
            WifiConfiguration conf = new WifiConfiguration();
            conf.SSID = "\"" + targetSsid + "\"";
            conf.preSharedKey = "\"" + WIFI_PASSKEY + "\"";
            conf.status = WifiConfiguration.Status.ENABLED;
            conf.allowedKeyManagement.set(WifiConfiguration.KeyMgmt.WPA_PSK);
            conf.allowedProtocols.set(WifiConfiguration.Protocol.RSN);
            conf.allowedProtocols.set(WifiConfiguration.Protocol.WPA);

            int netId = wifiManager.addNetwork(conf);
            if (netId == -1) {
                List<WifiConfiguration> list = wifiManager.getConfiguredNetworks();
                if (list != null) {
                    for (WifiConfiguration i : list) {
                        if (i.SSID != null && i.SSID.equals("\"" + targetSsid + "\"")) {
                            netId = i.networkId;
                            break;
                        }
                    }
                }
            }

            if (netId != -1) {
                wifiManager.disconnect();
                wifiManager.enableNetwork(netId, true);
                wifiManager.reconnect();

                // Wait a few seconds for connection and bind process
                mainHandler.postDelayed(new Runnable() {
                    @Override
                    public void run() {
                        bindToCurrentWifiAndLoadPage();
                    }
                }, 4000);
            } else {
                updateStatus("Failed to configure Wi-Fi", "Could not add network configuration.", false);
                isConnecting = false;
            }
        } catch (Exception e) {
            Log.e(TAG, "Error in connectLegacyWifi: " + e.getMessage(), e);
            updateStatus("Connection Error", e.getMessage(), false);
            isConnecting = false;
        }
    }

    private void updateStatus(final String title, final String details, final boolean inProgress) {
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                if (txtStatus != null) txtStatus.setText(title);
                if (txtDetails != null) txtDetails.setText(details);
                if (statusSpinner != null) {
                    statusSpinner.setVisibility(inProgress ? View.VISIBLE : View.GONE);
                }
            }
        });
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
            return;
        }

        if (System.currentTimeMillis() - backPressedTime < 2000) {
            super.onBackPressed();
        } else {
            backPressedTime = System.currentTimeMillis();
            Toast.makeText(this, "Press Back again to exit", Toast.LENGTH_SHORT).show();
        }
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (isRegisteredReceiver) {
            try {
                unregisterReceiver(wifiScanReceiver);
            } catch (Exception ignored) {}
            isRegisteredReceiver = false;
        }
        if (networkCallback != null && connectivityManager != null) {
            try {
                connectivityManager.unregisterNetworkCallback(networkCallback);
            } catch (Exception ignored) {}
            networkCallback = null;
        }
        if (pageRetryRunnable != null) {
            mainHandler.removeCallbacks(pageRetryRunnable);
        }
        if (webView != null) {
            webView.destroy();
        }
    }
}
