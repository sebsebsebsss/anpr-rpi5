import pytest
from gate_anpr_preview import build_command


def settings(**changes):
    return {
        "GATE_WEB_STREAM_RTSP_URL": "rtsp://camera.example/preview",
        "GATE_WEB_STREAM_WIDTH": "960",
        "GATE_WEB_STREAM_HEIGHT": "540",
        "GATE_WEB_STREAM_FPS": "10",
        **changes,
    }


def test_profiles_share_one_decoder_and_write_only_atomic_jpegs():
    command = build_command(settings(GATE_WEB_STREAM_PROFILES="1"), "/run/preview/stream.jpg")
    assert command.count("-i") == 1
    assert command.count("-atomic_writing") == 3
    assert command.count("-update") == 3
    assert [value for value in command if value.endswith(".jpg")] == [
        "/run/preview/stream.jpg",
        "/run/preview/stream-tablet.jpg",
        "/run/preview/stream-kiosk.jpg",
    ]
    graph = command[command.index("-filter_complex") + 1]
    assert "fps=10,scale=960:540,split=3" in graph
    assert "min(800,iw)" in graph and "min(640,iw)" in graph
    assert "h=-2" in graph  # Preserve the full frame's aspect ratio; never crop.


def test_profiles_default_off_and_custom_paths_are_not_shell_code():
    command = build_command(settings(), "/run/preview space/camera.jpg")
    assert command.count("-i") == command.count("-atomic_writing") == 1
    assert command[-1] == "/run/preview space/camera.jpg"
    assert "split=" not in command[command.index("-filter_complex") + 1]


@pytest.mark.parametrize(
    "key,value",
    [
        ("GATE_WEB_STREAM_FPS", "nan"),
        ("GATE_WEB_STREAM_FPS", "0"),
        ("GATE_WEB_STREAM_FPS", "inf"),
        ("GATE_WEB_STREAM_FPS", "60"),
        ("GATE_WEB_STREAM_WIDTH", "641"),
        ("GATE_WEB_STREAM_HEIGHT", "0"),
        ("GATE_WEB_STREAM_PROFILES", "true"),
        ("GATE_WEB_STREAM_RTSP_URL", ""),
    ],
)
def test_invalid_settings_fail_before_starting_encoder(key, value):
    with pytest.raises(ValueError):
        build_command(settings(**{key: value}), "/run/preview/stream.jpg")


@pytest.mark.parametrize("path", ["relative.jpg", "/run/preview/stream-tablet.jpg", "/run/preview/stream-kiosk.jpg"])
def test_output_cannot_collide_with_profile_files(path):
    with pytest.raises(ValueError):
        build_command(settings(), path)
