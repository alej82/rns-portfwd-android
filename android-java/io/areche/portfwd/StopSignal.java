package io.areche.portfwd;

import java.util.concurrent.atomic.AtomicBoolean;

/** One-shot stop request shared between the notification receiver and Python. */
public final class StopSignal {
    private static final AtomicBoolean PENDING = new AtomicBoolean(false);

    private StopSignal() {
    }

    public static void request() {
        PENDING.set(true);
    }

    public static boolean consume() {
        return PENDING.getAndSet(false);
    }
}
