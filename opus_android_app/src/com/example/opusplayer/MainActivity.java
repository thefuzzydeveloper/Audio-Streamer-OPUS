package com.example.opusplayer;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.view.Gravity;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.LinearLayout;
import android.widget.SeekBar;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends Activity {
    private static final int PORT = 12345;
    private static final String PREFS_NAME = "OpusPlayerSettings";
    private static final String KEY_BUFFER_MS = "key_buffer_ms";
    private static final String KEY_WIFI_LOCK = "key_wifi_lock";

    private static boolean isServiceRunning = false;
    private SharedPreferences prefs;

    private int bufferMs = 20; // 20ms matches exactly 1 Opus frame from streamer.py
    private boolean wifiHighPerf = true;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        bufferMs = prefs.getInt(KEY_BUFFER_MS, 20);
        wifiHighPerf = prefs.getBoolean(KEY_WIFI_LOCK, true);

        requestAppPermissions();

        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setGravity(Gravity.CENTER);
        layout.setPadding(48, 48, 48, 48);

        final TextView titleText = new TextView(this);
        titleText.setText("Opus Clean Stream Receiver");
        titleText.setTextSize(22);
        titleText.setGravity(Gravity.CENTER);
        titleText.setPadding(0, 0, 0, 24);
        layout.addView(titleText);

        final TextView statusText = new TextView(this);
        statusText.setText(isServiceRunning ? "Status: ACTIVE" : "Status: STOPPED");
        statusText.setTextSize(16);
        statusText.setGravity(Gravity.CENTER);
        statusText.setPadding(0, 0, 0, 24);
        layout.addView(statusText);

        final CheckBox wifiHighPerfCheck = new CheckBox(this);
        wifiHighPerfCheck.setText("Force Wi-Fi Low Latency Lock");
        wifiHighPerfCheck.setChecked(wifiHighPerf);
        wifiHighPerfCheck.setPadding(0, 0, 0, 20);
        wifiHighPerfCheck.setOnCheckedChangeListener((buttonView, isChecked) -> {
            wifiHighPerf = isChecked;
            savePreferences();
        });
        layout.addView(wifiHighPerfCheck);

        final TextView bufferLabel = new TextView(this);
        updateBufferLabel(bufferLabel);
        layout.addView(bufferLabel);

        final SeekBar bufferBar = new SeekBar(this);
        bufferBar.setMax(500); // 10ms to 500ms
        bufferBar.setProgress(bufferMs);
        bufferBar.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override
            public void onProgressChanged(SeekBar seekBar, int progress, boolean fromUser) {
                bufferMs = Math.max(10, progress);
                updateBufferLabel(bufferLabel);
                savePreferences();

                if (isServiceRunning) {
                    NativeAudio.setBufferMs(bufferMs);
                }
            }

            @Override public void onStartTrackingTouch(SeekBar seekBar) {}
            @Override public void onStopTrackingTouch(SeekBar seekBar) {}
        });
        bufferBar.setPadding(0, 16, 0, 24);
        layout.addView(bufferBar);

        // Quick Preset Buttons
        LinearLayout presetLayout = new LinearLayout(this);
        presetLayout.setOrientation(LinearLayout.HORIZONTAL);
        presetLayout.setGravity(Gravity.CENTER);
        presetLayout.setPadding(0, 0, 0, 24);

        final Button btnUltraLow = new Button(this);
        btnUltraLow.setText("Fast Wi-Fi (20ms)");
        btnUltraLow.setOnClickListener(v -> {
            bufferMs = 20;
            bufferBar.setProgress(20);
        });
        presetLayout.addView(btnUltraLow);

        final Button btnSafe = new Button(this);
        btnSafe.setText("Weak Wi-Fi (150ms)");
        btnSafe.setOnClickListener(v -> {
            bufferMs = 150;
            bufferBar.setProgress(150);
        });
        presetLayout.addView(btnSafe);

        layout.addView(presetLayout);

        final Button toggleButton = new Button(this);
        toggleButton.setText(isServiceRunning ? "Stop Receiver" : "Start Receiver");
        toggleButton.setOnClickListener(v -> {
            if (!isServiceRunning) {
                Intent startIntent = new Intent(this, AudioService.class);
                startIntent.setAction(AudioService.ACTION_START);
                startIntent.putExtra(AudioService.EXTRA_PORT, PORT);
                startIntent.putExtra(AudioService.EXTRA_BUFFER_MS, bufferMs);
                startIntent.putExtra(AudioService.EXTRA_WIFI_HIGH_PERF, wifiHighPerf);

                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    startForegroundService(startIntent);
                } else {
                    startService(startIntent);
                }

                isServiceRunning = true;
                statusText.setText("Status: ACTIVE (Listening on " + PORT + ")");
                toggleButton.setText("Stop Receiver");
            } else {
                Intent stopIntent = new Intent(this, AudioService.class);
                stopIntent.setAction(AudioService.ACTION_STOP);
                startService(stopIntent);

                isServiceRunning = false;
                statusText.setText("Status: STOPPED");
                toggleButton.setText("Start Receiver");
            }
        });
        layout.addView(toggleButton);

        final Button battBtn = new Button(this);
        battBtn.setText("Bypass Battery Optimization");
        battBtn.setOnClickListener(v -> checkAndRequestBatteryOptimizations());
        layout.addView(battBtn);

        setContentView(layout);
    }

    private void updateBufferLabel(TextView label) {
        String description = (bufferMs <= 30) ? " (Optimal Low-Latency)" : " (Safe Buffer)";
        label.setText("Buffer: " + bufferMs + " ms" + description);
    }

    private void savePreferences() {
        if (prefs != null) {
            prefs.edit()
                 .putInt(KEY_BUFFER_MS, bufferMs)
                 .putBoolean(KEY_WIFI_LOCK, wifiHighPerf)
                 .apply();
        }
    }

    private void requestAppPermissions() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 101);
            }
        }
    }

    private void checkAndRequestBatteryOptimizations() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
            if (pm != null && !pm.isIgnoringBatteryOptimizations(getPackageName())) {
                try {
                    Intent intent = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS);
                    intent.setData(Uri.parse("package:" + getPackageName()));
                    startActivity(intent);
                } catch (Exception e) {
                    Intent fallback = new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS);
                    startActivity(fallback);
                }
            } else {
                Toast.makeText(this, "Battery optimization already disabled.", Toast.LENGTH_SHORT).show();
            }
        }
    }
}