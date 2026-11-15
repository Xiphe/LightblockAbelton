"""PRIVATE disposable optimistic-volume adapter. No normal controller imports."""
from .surface import OptimisticFaderSpike


def create_instance(c_instance):
    return OptimisticFaderSpike(c_instance)
