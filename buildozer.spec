[app]

title = RNS Portfwd
package.name = portfwd
package.domain = io.areche

source.dir = app
source.include_exts = py,kv,png,jpg,atlas,json,ttf

version = 1.0.0

requirements = python3,kivy,pyjnius,android,cryptography,cffi,pyserial,setuptools,rns==1.6.0,libbz2,liblzma

orientation = portrait
fullscreen = 0

# Launcher icon and loading screen, generated from the logo by
# tools/make_icons.py (the artwork sources live in assets/icon/source).
icon.filename = assets/icon/icon.png
icon.adaptive_foreground.filename = assets/icon/icon-foreground.png
icon.adaptive_background.filename = assets/icon/icon-background.png
presplash.filename = assets/icon/presplash.jpg
android.presplash_color = #FFFFFF

android.permissions = INTERNET,FOREGROUND_SERVICE,WAKE_LOCK,POST_NOTIFICATIONS
android.add_src = android-java
android.api = 33
android.minapi = 24
android.ndk = 25b
android.ndk_api = 24
android.archs = arm64-v8a
android.accept_sdk_license = True
android.allow_backup = False

# Pinned toolchain: python-for-android v2024.01.21 (Python 3.11.5, NDK 25b).
p4a.source_dir = .p4a
p4a.branch = v2024.01.21
p4a.extra_args = --native-service io.areche.portfwd.PortfwdService
p4a.hook = tools/p4a_hook.py

[buildozer]

log_level = 2
warn_on_root = 0
