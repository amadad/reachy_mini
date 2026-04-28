import platform

import pytest


def _placo_available() -> bool:
    try:
        import placo  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(
    platform.system() == "Windows" or not _placo_available(),
    reason="Placo is not available in this environment",
)
def test_load_kinematics():  # noqa: D100, D103
    from reachy_mini.utils.constants import URDF_ROOT_PATH
    from reachy_mini.kinematics import PlacoKinematics

    # Test loading the kinematics
    kinematics = PlacoKinematics(URDF_ROOT_PATH)
    assert kinematics is not None, "Failed to load PlacoKinematics."
