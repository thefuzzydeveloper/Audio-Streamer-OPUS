package com.example.opusplayer;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

public class AudioService extends Service {
    public static final String ACTION_START = "com.example.opusplayer.START";
    public static final String ACTION_STOP = "com.example.opusplayer.STOP";
    public static final String EXTRA_PORT = "EXTRA_PORT";
    public static final String EXTRA_LOW_CPU = "EXTRA_LOW_CPU";
    public static final String EXTRA_BUFFER_MS = "EXTRA_BUFFER_MS";
    public static final String EXTRA_WIFI_HIGH_PERF = "EXTRA_WIFI_HIGH_PERF";

    private static final String CHANNEL_ID = "opus_audio_playback_channel";
    private static final int NOTIFICATION_ID = 1001;

    private PowerManager.WakeLock wakeLock;
    private WifiManager.WifiLock wifiLock;
    private boolean isRunning = false;

    @Override
    public void onCreate() {
        super.onCreate();
        createNotificationChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) return START_NOT_STICKY;

        String action = intent.getAction();
        if (ACTION_START.equals(action) && !isRunning) {
            int port = intent.getIntExtra(EXTRA_PORT, 12345);
            boolean lowCpu = intent.getBooleanExtra(EXTRA_LOW_CPU, true);
            int bufferMs = intent.getIntExtra(EXTRA_BUFFER_MS, 50);
            boolean wifiHighPerf = intent.getBooleanExtra(EXTRA_WIFI_HIGH_PERF, false);

            startAudioPlayback(port, lowCpu, bufferMs, wifiHighPerf);
        } else if (ACTION_STOP.equals(action)) {
            stopAudioPlayback();
        }

        return START_NOT_STICKY;
    }

    private void startAudioPlayback(int port, boolean lowCpu, int bufferMs, boolean wifiHighPerf) {
        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        if (pm != null && wakeLock == null) {
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "OpusPlayer::CpuLock");
            wakeLock.acquire();
        }

        // Enforce full low-latency Wi-Fi lock if buffer < 20ms or if explicitly requested
        boolean needWifiLock = wifiHighPerf || (bufferMs < 20);
        if (needWifiLock) {
            WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wm != null && wifiLock == null) {
                int mode = WifiManager.WIFI_MODE_FULL_HIGH_PERF;
                if (Build.VERSION.SDK_INT >= 29) {
                    mode = WifiManager.WIFI_MODE_FULL_LOW_LATENCY;
                }
                wifiLock = wm.createWifiLock(mode, "OpusPlayer::LowLatencyWifiLock");
                wifiLock.acquire();
            }
        }

        Intent notificationIntent = new Intent(this, MainActivity.class);
        PendingIntent pendingIntent = PendingIntent.getActivity(
                this, 0, notificationIntent,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
        );

        Notification.Builder builder = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);

        String info = "Buffer: " + bufferMs + "ms" + (needWifiLock ? " | Low-Latency Wi-Fi" : "");
        Notification notification = builder
                .setContentTitle("Opus Audio Receiver Active")
                .setContentText(info)
                .setSmallIcon(R.drawable.ic_launcher)
                .setContentIntent(pendingIntent)
                .setOngoing(true)
                .build();

        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, notification, 2);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }

        NativeAudio.startAudio(port, lowCpu, bufferMs);
        isRunning = true;
    }

    private void stopAudioPlayback() {
        if (!isRunning) return;

        NativeAudio.stopAudio();

        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
            wakeLock = null;
        }

        if (wifiLock != null && wifiLock.isHeld()) {
            wifiLock.release();
            wifiLock = null;
        }

        stopForeground(true);
        stopSelf();
        isRunning = false;
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID,
                    "Opus Audio Playback Service",
                    NotificationManager.IMPORTANCE_LOW
            );
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) {
                manager.createNotificationChannel(channel);
            }
        }
    }

    @Override
    public void onDestroy() {
        stopAudioPlayback();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}