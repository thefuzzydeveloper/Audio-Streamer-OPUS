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
    private static final String KEY_LOW_CPU = "key_low_cpu";
    private static final String KEY_WIFI_LOCK = "key_wifi_lock";

    private static boolean isServiceRunning = false;
    private SharedPreferences prefs;

    private int bufferMs = 20;
    private boolean lowCpu = true;
    private boolean wifiHighPerf = true;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Load saved preferences
        prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        bufferMs = prefs.getInt(KEY_BUFFER_MS, 20);
        if (bufferMs < 5) bufferMs = 5;
        if (bufferMs > 50) bufferMs = 50;

        lowCpu = prefs.getBoolean(KEY_LOW_CPU, true);
        wifiHighPerf = prefs.getBoolean(KEY_WIFI_LOCK, true);

        // Auto-enforce rules on initial load
        if (bufferMs < 10) {
            lowCpu = false;
        }
        if (bufferMs < 20) {
            wifiHighPerf = true;
        }

        requestAppPermissions();

        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setGravity(Gravity.CENTER);
        layout.setPadding(48, 48, 48, 48);

        final TextView titleText = new TextView(this);
        titleText.setText("Opus Low-Latency Receiver");
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

        final CheckBox lowCpuCheck = new CheckBox(this);
        lowCpuCheck.setText("Low CPU Mode (Zero Standby / Sleep on Silence)");
        lowCpuCheck.setChecked(lowCpu);
        lowCpuCheck.setEnabled(bufferMs >= 10);
        lowCpuCheck.setPadding(0, 0, 0, 16);
        lowCpuCheck.setOnCheckedChangeListener((buttonView, isChecked) -> {
            if (bufferMs >= 10) {
                lowCpu = isChecked;
                savePreferences();
            }
        });
        layout.addView(lowCpuCheck);

        final CheckBox wifiHighPerfCheck = new CheckBox(this);
        wifiHighPerfCheck.setText("Force Wi-Fi Low Latency Lock");
        wifiHighPerfCheck.setChecked(wifiHighPerf);
        wifiHighPerfCheck.setPadding(0, 0, 0, 24);
        wifiHighPerfCheck.setOnCheckedChangeListener((buttonView, isChecked) -> {
            wifiHighPerf = isChecked;
            savePreferences();
        });
        layout.addView(wifiHighPerfCheck);

        final TextView bufferLabel = new TextView(this);
        updateBufferLabel(bufferLabel);
        layout.addView(bufferLabel);

        // SeekBar constrained strictly to 5ms - 50ms
        final SeekBar bufferBar = new SeekBar(this);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            bufferBar.setMin(5);
            bufferBar.setMax(50);
            bufferBar.setProgress(bufferMs);
        } else {
            bufferBar.setMax(45); // 0 to 45 (+5 offset)
            bufferBar.setProgress(bufferMs - 5);
        }

        bufferBar.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override
            public void onProgressChanged(SeekBar seekBar, int progress, boolean fromUser) {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    bufferMs = Math.max(10, Math.min(50, progress));
                } else {
                    bufferMs = Math.max(10, Math.min(50, progress + 10));
                }

                if (bufferMs < 10) {
                    lowCpu = false;
                    lowCpuCheck.setChecked(false);
                    lowCpuCheck.setEnabled(false);
                } else {
                    lowCpuCheck.setEnabled(true);
                }

                if (bufferMs < 20) {
                    wifiHighPerf = true;
                    wifiHighPerfCheck.setChecked(true);
                }

                updateBufferLabel(bufferLabel);
                savePreferences();

                // Push real-time buffer update to active native audio engine
                if (isServiceRunning) {
                    NativeAudio.setBufferMs(bufferMs);
                }
            }

            @Override public void onStartTrackingTouch(SeekBar seekBar) {}
            @Override public void onStopTrackingTouch(SeekBar seekBar) {}
        });
        bufferBar.setPadding(0, 16, 0, 32);
        layout.addView(bufferBar);

        final Button toggleButton = new Button(this);
        toggleButton.setText(isServiceRunning ? "Stop Receiver" : "Start Receiver");
        toggleButton.setOnClickListener(v -> {
            if (!isServiceRunning) {
                Intent startIntent = new Intent(this, AudioService.class);
                startIntent.setAction(AudioService.ACTION_START);
                startIntent.putExtra(AudioService.EXTRA_PORT, PORT);
                startIntent.putExtra(AudioService.EXTRA_LOW_CPU, lowCpu);
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
        String note = "";
        if (bufferMs < 10) {
            note = " (Ultra-Low Latency: Low CPU Off, Wi-Fi Lock On)";
        } else if (bufferMs < 20) {
            note = " (Auto Wi-Fi Lock Active)";
        }
        label.setText("Jitter Buffer: " + bufferMs + " ms" + note);
    }

    private void savePreferences() {
        if (prefs != null) {
            prefs.edit()
                 .putInt(KEY_BUFFER_MS, bufferMs)
                 .putBoolean(KEY_LOW_CPU, lowCpu)
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