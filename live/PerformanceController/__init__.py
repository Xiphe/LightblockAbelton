"""Matched Audio performance Remote Script; no production installer side effects."""
def create_instance(c_instance):
    from .surface import PerformanceController
    return PerformanceController(c_instance)
