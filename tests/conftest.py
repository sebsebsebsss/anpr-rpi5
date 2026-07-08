import os
import sys
import tempfile

# Ensure files/ and files/web/ are on the path so imports work without the Pi.
_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in [
    os.path.join(_repo, "files"),
    os.path.join(_repo, "files", "web"),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Redirect Pi-only paths to tmp so app.py can be imported in CI.
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
_tmp_cooldown = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
_tmp_cooldown.close()
os.environ.setdefault("GATE_ANPR_EVENTS_DB", _tmp_db.name)
os.environ.setdefault("GATE_WEB_COOLDOWN_PATH", _tmp_cooldown.name)
os.environ.setdefault("GATE_API_SHARED_SECRET", "test-secret-for-ci")
os.environ.setdefault("GATE_ALLOWED_ORIGINS", "http://localhost,http://gatepi5")

# Stub out RPi.GPIO so tests run on any platform.
import types

gpio_mod = types.ModuleType("RPi")
gpio_inner = types.ModuleType("RPi.GPIO")
gpio_inner.BOARD = "BOARD"
gpio_inner.OUT = "OUT"
gpio_inner.HIGH = 1
gpio_inner.LOW = 0
gpio_inner.setwarnings = lambda *a, **kw: None
gpio_inner.setmode = lambda *a, **kw: None
gpio_inner.setup = lambda *a, **kw: None
gpio_inner.output = lambda *a, **kw: None
gpio_inner.cleanup = lambda *a, **kw: None
gpio_mod.GPIO = gpio_inner
sys.modules.setdefault("RPi", gpio_mod)
sys.modules.setdefault("RPi.GPIO", gpio_inner)
sys.modules.setdefault("lgpio", types.ModuleType("lgpio"))
sys.modules.setdefault("greenstalk", types.ModuleType("greenstalk"))
