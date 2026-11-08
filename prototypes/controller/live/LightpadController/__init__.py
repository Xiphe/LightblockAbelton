"""Temporary Remote Script. Import Live only when Live creates the surface."""


def create_instance(c_instance):
    from .configuration import AUDIO_PROOF_MODE, AUDIO_CONTROLLER_PROBE_MODE
    if AUDIO_PROOF_MODE and AUDIO_CONTROLLER_PROBE_MODE:
        raise ValueError("choose exactly one isolated proof mode")
    if AUDIO_CONTROLLER_PROBE_MODE:
        from .controller_probe_surface import ControllerProbeSurface
        return ControllerProbeSurface(c_instance)
    if AUDIO_PROOF_MODE:
        from .proof_surface import AudioProofSurface
        return AudioProofSurface(c_instance)
    from .surface import LightpadController
    return LightpadController(c_instance)
