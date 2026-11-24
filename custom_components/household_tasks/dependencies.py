"""Identify direct feedback loops among Household Tasks entities."""
from homeassistant.core import valid_entity_id

from .const import DOMAIN


AGGREGATE_KEYS = ("open_names", "latest_open")


def active_entity_ids(config):
    """Only include dependencies of the selected opening and closing rules."""
    dependencies = []
    if config.get("trigger_type") in ("calendar", "date_entity", "numeric", "state"):
        dependencies.append(config.get("trigger_entity"))
    if config.get("close_type") in ("entity_state", "entity_numeric",
                                    "manual_or_entity_state", "manual_or_entity_numeric"):
        dependencies.append(config.get("close_entity"))
    return {entity_id for entity_id in dependencies
            if isinstance(entity_id, str) and valid_entity_id(entity_id)}


def cyclic_entries(configs, registry):
    """Return exactly the tasks participating in a direct dependency cycle."""
    if not configs:
        return set()
    owners = {}
    for entry_id in configs:
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry_id}_status")
        if entity_id is not None:
            owners[entity_id] = entry_id
    aggregates = set()
    for key in AGGREGATE_KEYS:
        unique_id = f"household_tasks_{key}"
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        if entity_id is None:
            # Before initial registration, reserve the default unless another
            # registered entity already owns it.
            default = f"sensor.{unique_id}"
            if registry.async_get(default) is None:
                entity_id = default
        if entity_id is not None:
            aggregates.add(entity_id)

    graph = {entry_id: set() for entry_id in configs}
    for entry_id, config in configs.items():
        for dependency in active_entity_ids(config):
            if dependency in aggregates:
                graph[entry_id].update(configs)
            elif dependency in owners:
                graph[entry_id].add(owners[dependency])

    # Iterative reachability avoids recursion depth failures with long chains.
    cycles = set()
    for entry_id, edges in graph.items():
        pending = list(edges)
        visited = set()
        while pending:
            other = pending.pop()
            if other == entry_id:
                cycles.add(entry_id)
                break
            if other not in visited:
                visited.add(other)
                pending.extend(graph[other] - visited)
    return cycles
