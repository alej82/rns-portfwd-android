
from tools.p4a_hook import SERVICE_NAME, after_apk_build


def test_hook_types_service_and_is_idempotent(tmp_path, monkeypatch):
    manifest_dir = tmp_path / "src" / "main"
    manifest_dir.mkdir(parents=True)
    manifest = manifest_dir / "AndroidManifest.xml"
    manifest.write_text(
        f'<manifest><application><service android:name="{SERVICE_NAME}" /></application></manifest>'
    )
    monkeypatch.chdir(tmp_path)
    after_apk_build(None)
    text = manifest.read_text()
    assert 'android:foregroundServiceType="dataSync"' in text
    assert 'android:exported="false"' in text
    assert 'io.areche.portfwd.StopReceiver' in text
    after_apk_build(None)
    assert text.count("foregroundServiceType") == 1
