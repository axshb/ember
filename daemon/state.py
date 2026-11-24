from typing import Literal

# how long a condition has to stay clear before it counts as resolved, so a value
# bouncing around its threshold doesn't send fire/resolve pairs every poll
RESOLVE_AFTER = 60

Transition = Literal["fired", "resolved", "remind"]


class AlertState:
    """Tracks what's firing. Knows nothing about metrics, anything with a stable key can use it.

    Persisted through `store` so a restart doesn't re-fire everything that's still breached.
    """

    def __init__(self, store):
        self.store = store
        self.firing: dict[str, dict] = store.load_alert_state()

    def update(self, key: str, breached: bool, now: float, remind: float = 0) -> Transition | None:
        """`remind` is in seconds, 0 never reminds."""
        entry = self.firing.get(key)
        if entry is None:
            if not breached:
                return None
            self._save(key, {"since": now, "notified": now, "clear_since": None})
            return "fired"

        if breached:
            transition = None
            if remind > 0 and now - entry["notified"] >= remind:
                entry["notified"] = now
                transition = "remind"
            if transition or entry["clear_since"] is not None:
                entry["clear_since"] = None
                self._save(key, entry)
            return transition

        if entry["clear_since"] is None:
            entry["clear_since"] = now
            self._save(key, entry)
            return None
        if now - entry["clear_since"] >= RESOLVE_AFTER:
            self.firing.pop(key)
            self.store.delete_alert_state(key)
            return "resolved"
        return None

    def since(self, key: str) -> float | None:
        entry = self.firing.get(key)
        return entry["since"] if entry else None

    def forget(self, key: str) -> None:
        # undo a fire whose report never went out, so the next poll fires it again
        self.firing.pop(key, None)
        self.store.delete_alert_state(key)

    def prune(self, prefix: str, keep: set[str]) -> None:
        # drop state for rules that no longer exist, silently (deleting a rule isn't a resolve)
        for key in [k for k in self.firing if k.startswith(prefix) and k not in keep]:
            self.firing.pop(key)
            self.store.delete_alert_state(key)

    def _save(self, key: str, entry: dict) -> None:
        self.firing[key] = entry
        self.store.save_alert_state(key, entry)
