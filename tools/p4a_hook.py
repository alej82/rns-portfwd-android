"""Finalize the generated manifest for the same-process foreground service."""

import os
import re

SERVICE_NAME = "io.areche.portfwd.PortfwdService"
RECEIVER_NAME = "io.areche.portfwd.StopReceiver"
SERVICE_RE = re.compile(
    r'<service\s+android:name="' + re.escape(SERVICE_NAME) + r'"([^>]*?)/>'
)


def after_apk_build(_toolchain):
    path = os.path.join(os.getcwd(), "src", "main", "AndroidManifest.xml")
    with open(path, encoding="utf-8") as manifest_file:
        manifest = manifest_file.read()

    match = SERVICE_RE.search(manifest)
    if match is None:
        raise RuntimeError(f"p4a manifest is missing the {SERVICE_NAME} service")

    attributes = match.group(1)
    changed = False
    if 'android:foregroundServiceType="dataSync"' not in attributes:
        attributes = attributes.rstrip() + ' android:foregroundServiceType="dataSync"'
        changed = True
    if 'android:exported=' not in attributes:
        attributes += ' android:exported="false"'
        changed = True
    replacement = f'<service android:name="{SERVICE_NAME}"{attributes} />'
    manifest = manifest[: match.start()] + replacement + manifest[match.end():]

    if RECEIVER_NAME not in manifest:
        receiver = f'<receiver android:name="{RECEIVER_NAME}" android:exported="false" />'
        manifest = manifest.replace("</application>", f"    {receiver}\n    </application>")
        changed = True

    if changed:
        with open(path, "w", encoding="utf-8") as manifest_file:
            manifest_file.write(manifest)
        print("PORTFWD_BUILD foreground service and stop receiver finalized", flush=True)
