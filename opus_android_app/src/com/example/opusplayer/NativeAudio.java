package com.example.opusplayer;

public class NativeAudio {
    static {
        System.loadLibrary("audio_player");
    }

    public static native boolean startAudio(int port, boolean lowCpu, int bufferMs);
    public static native void stopAudio();
    public static native void setBufferMs(int bufferMs);
}