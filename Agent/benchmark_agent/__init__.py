"""Benchmark-calibration Catan agent."""

from Agent.benchmark_agent.decision import BenchmarkDecisionEngine

__all__ = ["BenchmarkCalibrationAgent", "BenchmarkDecisionEngine"]


def __getattr__(name: str):
    if name == "BenchmarkCalibrationAgent":
        from Agent.benchmark_agent.agent import BenchmarkCalibrationAgent

        return BenchmarkCalibrationAgent
    raise AttributeError(name)
