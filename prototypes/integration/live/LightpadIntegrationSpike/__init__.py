"""One-group Live control-surface integration spike."""

from .surface import LightpadIntegrationSpike


def create_instance(c_instance):
    return LightpadIntegrationSpike(c_instance)
