[app]

title = RNS Portfwd
package.name = portfwd
package.domain = io.areche

source.dir = app
source.include_exts = py,kv,png,jpg,atlas,json,ttf

version = 0.1.0

requirements = python3,kivy,pyjnius,android,cryptography,cffi,pyserial,setuptools,rns==1.5.7,libbz2,liblzma

orientation = portrait
fullscreen = 0

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
