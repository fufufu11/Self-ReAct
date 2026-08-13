"""日志/故障排查助手场景（R-07）。"""

from self_react.scenarios.log_troubleshooting.examples import (
    SCENARIO_EXAMPLES,
    ScenarioExample,
    build_example_llm,
    run_scenario_example,
)
from self_react.scenarios.log_troubleshooting.scenario import (
    SCENARIO_NAME,
    build_registry,
)

__all__ = [
    "SCENARIO_EXAMPLES",
    "SCENARIO_NAME",
    "ScenarioExample",
    "build_example_llm",
    "build_registry",
    "run_scenario_example",
]
