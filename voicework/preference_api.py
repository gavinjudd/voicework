"""Small HTTP adapter for PreferenceLearner; the host supplies origin/token checks."""
from .preference_learner import NOTICE, PreferenceLearner


class PreferenceRoutes:
    def __init__(self, learner):
        self.learner = learner

    @staticmethod
    def _fields(body, allowed):
        if not isinstance(body, dict) or set(body) - allowed:
            raise ValueError("Unknown preference request field")

    def route(self, handler, path, query, body):
        method = "GET" if handler.command == "HEAD" else handler.command
        if path == "/api/preferences/resolve" and method == "POST":
            self._fields(body, {"context"})
            handler._json(self.learner.resolve(body.get("context")))
            return True
        if path == "/api/preferences":
            if method == "GET":
                handler._json({"traits": self.learner.list_traits(), "notice": NOTICE})
                return True
            if method == "POST":
                handler._json({"trait": self.learner.create_trait(body)}, 201)
                return True
        parts = path.split("/")
        if len(parts) == 4 and parts[1:3] == ["api", "preferences"] and parts[3]:
            if method == "GET":
                handler._json({"trait": self.learner.get_trait(parts[3])})
                return True
            if method == "PATCH":
                handler._json({"trait": self.learner.update_trait(parts[3], body)})
                return True
        if len(parts) == 5 and parts[1:3] == ["api", "preferences"] and parts[3] and parts[4] == "evidence":
            if method == "GET":
                try:
                    limit, offset = int(query.get("limit", ["50"])[0]), int(query.get("offset", ["0"])[0])
                except (ValueError, TypeError, IndexError):
                    raise ValueError("Invalid evidence pagination")
                handler._json(self.learner.list_evidence(parts[3], limit=limit, offset=offset))
                return True
            if method == "POST":
                handler._json({"evidence": self.learner.add_evidence(parts[3], body)}, 201)
                return True
        if len(parts) == 5 and parts[1:3] == ["api", "preference-evidence"] and parts[3] and method == "POST":
            self._fields(body, {"reason"})
            handler._json({"evidence": self.learner.review_evidence(parts[3], parts[4], body.get("reason", ""))})
            return True
        return False
