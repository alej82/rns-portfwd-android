package io.areche.portfwd;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/** Receives the notification action, signals Python and brings the app back. */
public class StopReceiver extends BroadcastReceiver {
    private static final String TAG = "PORTFWD_BACKGROUND";

    @Override
    public void onReceive(Context context, Intent intent) {
        StopSignal.request();
        Intent launch = new Intent(context, org.kivy.android.PythonActivity.class);
        launch.setFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK
                        | Intent.FLAG_ACTIVITY_CLEAR_TOP
                        | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        try {
            context.startActivity(launch);
        } catch (Exception error) {
            Log.e(TAG, "could not open the activity for the stop action", error);
        }
    }
}
