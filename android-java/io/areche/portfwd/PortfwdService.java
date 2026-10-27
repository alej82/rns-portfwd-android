package io.areche.portfwd;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

public class PortfwdService extends Service {
    private static final String TAG = "PORTFWD_BACKGROUND";
    private static final String CHANNEL_ID = "portfwd_session";
    private static final int NOTIFICATION_ID = 48;
    private static final int SERVICE_ID = 48;
    private static final String ACTION_START = "io.areche.portfwd.action.START";
    private static final String ACTION_STOP = "io.areche.portfwd.action.STOP";
    private static final String EXTRA_TITLE = "title";
    private static final String EXTRA_TEXT = "text";
    private static final String EXTRA_STOP_LABEL = "stop_label";

    private static PortfwdService instance;

    private PowerManager.WakeLock wakeLock;
    private String title = "RNS Portfwd";
    private String text = "Sesión activa";
    private String stopLabel = "Detener";
    private boolean foregroundStarted = false;

    public static boolean start(Context context, String title, String text, String stopLabel) {
        if (context == null) {
            return false;
        }
        Intent intent = new Intent(context, PortfwdService.class);
        intent.setAction(ACTION_START);
        intent.putExtra(EXTRA_TITLE, title);
        intent.putExtra(EXTRA_TEXT, text);
        intent.putExtra(EXTRA_STOP_LABEL, stopLabel);
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent);
            } else {
                context.startService(intent);
            }
            return true;
        } catch (Exception error) {
            Log.e(TAG, "could not start foreground session", error);
            return false;
        }
    }

    public static boolean update(Context context, String title, String text, String stopLabel) {
        PortfwdService service = instance;
        if (service == null) {
            return false;
        }
        service.title = title;
        service.text = text;
        service.stopLabel = stopLabel;
        try {
            NotificationManager manager =
                    (NotificationManager) service.getSystemService(Context.NOTIFICATION_SERVICE);
            manager.notify(NOTIFICATION_ID, service.buildNotification());
            return true;
        } catch (Exception error) {
            Log.e(TAG, "could not update notification", error);
            return false;
        }
    }

    public static boolean isRunning() {
        return instance != null;
    }

    public static void stop(Context context) {
        if (context != null) {
            context.stopService(new Intent(context, PortfwdService.class));
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        createChannel();
        acquireWakeLock();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null || ACTION_STOP.equals(intent.getAction())) {
            stopSelf();
            return START_NOT_STICKY;
        }

        String nextTitle = intent.getStringExtra(EXTRA_TITLE);
        String nextText = intent.getStringExtra(EXTRA_TEXT);
        if (nextTitle != null) {
            title = nextTitle;
        }
        if (nextText != null) {
            text = nextText;
        }
        String nextStopLabel = intent.getStringExtra(EXTRA_STOP_LABEL);
        if (nextStopLabel != null) {
            stopLabel = nextStopLabel;
        }

        Notification notification = buildNotification();
        if (!foregroundStarted) {
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    startForeground(
                            SERVICE_ID,
                            notification,
                            ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
                } else {
                    startForeground(SERVICE_ID, notification);
                }
                foregroundStarted = true;
                Log.i(TAG, "foreground session started type=dataSync");
            } catch (Exception error) {
                Log.e(TAG, "could not enter the foreground", error);
                stopSelf();
                return START_NOT_STICKY;
            }
        } else {
            NotificationManager manager =
                    (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            manager.notify(NOTIFICATION_ID, notification);
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onTaskRemoved(Intent rootIntent) {
        Log.i(TAG, "task removed, stopping forwarding and the process");
        stopSelf();
        super.onTaskRemoved(rootIntent);
        android.os.Process.killProcess(android.os.Process.myPid());
    }

    @Override
    public void onDestroy() {
        Log.i(TAG, "foreground session stopped");
        if (foregroundStarted) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                stopForeground(STOP_FOREGROUND_REMOVE);
            } else {
                stopForeground(true);
            }
            foregroundStarted = false;
        }
        releaseWakeLock();
        instance = null;
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager =
                (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "Sesión de RNS Portfwd",
                NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("Mantiene los reenvíos activos en segundo plano");
        channel.setShowBadge(false);
        manager.createNotificationChannel(channel);
    }

    private Notification buildNotification() {
        Intent launch = new Intent(this, org.kivy.android.PythonActivity.class);
        launch.setFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK
                        | Intent.FLAG_ACTIVITY_CLEAR_TOP
                        | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pending = PendingIntent.getActivity(
                this,
                0,
                launch,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        Intent stopIntent = new Intent(this, StopReceiver.class);
        stopIntent.setAction(ACTION_STOP);
        PendingIntent stopPending = PendingIntent.getBroadcast(
                this,
                1,
                stopIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            builder = new Notification.Builder(this, CHANNEL_ID);
        } else {
            builder = new Notification.Builder(this);
        }
        builder.setContentTitle(title)
                .setContentText(text)
                .setSmallIcon(android.R.drawable.stat_notify_sync)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setShowWhen(false)
                .setContentIntent(pending)
                .setCategory(Notification.CATEGORY_SERVICE)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .addAction(
                        android.R.drawable.ic_menu_close_clear_cancel,
                        stopLabel,
                        stopPending);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            builder.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE);
        }
        return builder.build();
    }

    private void acquireWakeLock() {
        try {
            PowerManager power = (PowerManager) getSystemService(Context.POWER_SERVICE);
            wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "RNSPortfwd::Session");
            wakeLock.setReferenceCounted(false);
            wakeLock.acquire();
        } catch (Exception error) {
            Log.w(TAG, "could not acquire the session wake lock", error);
            wakeLock = null;
        }
    }

    private void releaseWakeLock() {
        try {
            if (wakeLock != null && wakeLock.isHeld()) {
                wakeLock.release();
            }
        } catch (Exception error) {
            Log.w(TAG, "could not release the session wake lock", error);
        }
        wakeLock = null;
    }
}
