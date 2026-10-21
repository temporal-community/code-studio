"""The original OpenAI hello example's simulated weather tool."""

from temporal_agent_harness.harness import agent


@agent.tool_defn(inherently_safe=True)
async def get_weather(city: str) -> str:
    """Return the current weather for a city. `city` is a plain city name, e.g. "Paris"."""
    # Canned lookup — a hello-world, not a real weather service.
    return f"It's 72°F and sunny in {city}."
