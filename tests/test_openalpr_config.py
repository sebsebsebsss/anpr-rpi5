import configparser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


def render_config(mask_source="", width="30", height="10"):
    source = Path(__file__).resolve().parents[1] / "files"
    env = Environment(loader=FileSystemLoader(source), undefined=StrictUndefined)
    text = env.get_template("openalpr.conf.j2").render(
        openalpr_share_dir="/usr/local/share/openalpr",
        alpr_detection_mask_source=mask_source,
        alpr_detection_mask_path="/etc/openalpr/detection-mask.png",
        alpr_mask_max_plate_width_percent=width,
        alpr_mask_max_plate_height_percent=height,
    )
    config = configparser.ConfigParser()
    config.read_string("[recognition]\n" + text)
    return config["recognition"]


def test_disabling_mask_restores_original_candidate_limits():
    # A private env file can retain compensation values after its mask is disabled.
    config = render_config(width="30.02", height="11.8")
    assert "detection_mask_image" not in config
    assert config.getfloat("max_plate_width_percent") == 30
    assert config.getfloat("max_plate_height_percent") == 10
    assert config["country"] == "gb"
    assert config.getint("topn") == 5
    assert config.getint("must_match_pattern") == 1


def test_mask_uses_deployed_path_and_explicit_candidate_compensation():
    config = render_config(mask_source="/private/mask.png", width="30.02", height="11.8")
    assert config["detection_mask_image"] == "/etc/openalpr/detection-mask.png"
    assert config.getfloat("max_plate_width_percent") == 30.02
    assert config.getfloat("max_plate_height_percent") == 11.8
    assert config["config_file"] == "/usr/local/share/openalpr/config/openalpr.defaults.conf"
    assert config["runtime_dir"] == "/usr/local/share/openalpr/runtime_data"
