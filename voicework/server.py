"""Loopback-only HTTP app with same-origin and per-launch token protection."""
import hmac
import json
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .discovery import capabilities as scan_capabilities, discover
from .learning import build_profile, contextual_preferences, export_markdown, retrieve, validate_context
from .store import Store, text_field
from .context import PriorStore, infer_context
from .evaluation import EvaluationStore
from .discovery_jobs import DiscoveryJobs
from .language import LanguageLessonStore, validate_focus
from .corrections import CorrectionStore, CorrectionConflict
from .quality import QualityStore

MAX_BODY_BYTES = 8 * 1024 * 1024


class HTTPError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _reject_constants(value):
    raise ValueError("Non-finite JSON numbers are not accepted")


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON keys are not accepted")
        value[key] = item
    return value


def validate_controls(value):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {"polish", "detail", "warmth", "assertiveness", "structure"}:
        raise ValueError("Unknown draft control")
    controls = {}
    for key in ("polish", "detail", "warmth", "assertiveness"):
        item = value.get(key, 50)
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not 0 <= item <= 100:
            raise ValueError("%s must be a number from 0 to 100" % key)
        controls[key] = item
    structure = value.get("structure", "auto")
    if not isinstance(structure, str) or structure not in {"auto", "prose", "bullets"}:
        raise ValueError("structure must be auto, prose, or bullets")
    controls["structure"] = structure
    return controls


class VoiceworkServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, store, provider, web_root):
        self.store = store
        self.provider = provider
        self.priors = PriorStore(store.path)
        self.language_lessons = LanguageLessonStore(store.path)
        self.corrections = CorrectionStore(store.path)
        self.quality_checks = QualityStore(store.path)
        self.evaluations = EvaluationStore(store.path)
        self.discovery_jobs = DiscoveryJobs(store)
        self.web_root = Path(web_root).resolve()
        self.token = secrets.token_urlsafe(32)
        self._slots = threading.BoundedSemaphore(16)
        self.generation_slots = threading.BoundedSemaphore(2)
        self.discovery_slot = threading.BoundedSemaphore(1)
        super().__init__(address, Handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        return connection, address

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                body = b'{"error":"Server busy; try again shortly"}'
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "Voicework"
    sys_version = ""

    def log_message(self, format, *args):
        # Do not place private paths, context, imports, or request bodies in logs.
        pass

    def send_error(self, code, message=None, explain=None):
        self._json({"error": message or "Request failed"}, code)

    def _respond(self, body, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status=200):
        self._respond(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def _security(self, mutation=False):
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1:
            raise HTTPError(403, "A local Host header is required")
        host = hosts[0].lower()
        port = self.server.server_address[1]
        allowed = {"127.0.0.1:%d" % port, "localhost:%d" % port}
        if port == 80:
            allowed.update({"127.0.0.1", "localhost"})
        if host not in allowed:
            raise HTTPError(403, "Only the local Voicework origin is allowed")
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1 or origins and origins[0] != "http://" + host:
            raise HTTPError(403, "Cross-origin requests are not allowed")
        if self.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
            raise HTTPError(403, "Cross-origin browser requests are not allowed")
        if mutation:
            tokens = self.headers.get_all("X-Voicework-Token", [])
            if len(tokens) != 1 or not hmac.compare_digest(tokens[0], self.server.token):
                raise HTTPError(403, "Missing or invalid Voicework token; reload the app")
            content_types = self.headers.get_all("Content-Type", [])
            if len(content_types) != 1 or content_types[0].split(";", 1)[0].strip().lower() != "application/json":
                raise HTTPError(415, "Mutations require application/json")

    def _body(self):
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isdigit():
            raise HTTPError(400, "A single Content-Length is required; chunked bodies are not supported")
        if len(lengths[0]) > 9 or int(lengths[0]) > MAX_BODY_BYTES:
            raise HTTPError(413, "Request exceeds 8 MiB limit")
        length = int(lengths[0])
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise HTTPError(400, "Incomplete request body")
        try:
            value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constants, object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError, RecursionError):
            raise HTTPError(400, "Malformed JSON; use a UTF-8 object with unique keys and finite numbers")
        if not isinstance(value, dict):
            raise HTTPError(400, "Request body must be a JSON object")
        return value

    def _run(self, mutation=False):
        try:
            self._security(mutation)
            url = urlsplit(self.path)
            if url.scheme or url.netloc:
                raise HTTPError(400, "Absolute request URLs are not allowed")
            body = self._body() if mutation else None
            self._route(url.path, parse_qs(url.query), body)
        except HTTPError as exc:
            self._json({"error": str(exc)}, exc.status)
        except KeyError:
            self._json({"error": "Record not found"}, 404)
        except CorrectionConflict as exc:
            self._json({"error": str(exc)}, 409)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except socket.timeout:
            try:
                self._json({"error": "Request timed out"}, 408)
            except OSError:
                pass
        except Exception:
            # Validation errors are explicit. Unexpected internal errors do not
            # leak local paths, provider internals, or database contents.
            self._json({"error": "Internal error; your existing records were preserved"}, 500)

    def do_GET(self):
        self._run()

    def do_HEAD(self):
        self._run()

    def do_POST(self):
        self._run(True)

    def do_PATCH(self):
        self._run(True)

    def do_DELETE(self):
        self._run(True)

    def do_OPTIONS(self):
        self._json({"error": "Cross-origin access is not supported"}, 405)

    @staticmethod
    def _fields(body, allowed):
        if set(body) - set(allowed):
            raise ValueError("Unknown request field")

    def _prior_preferences(self, context):
        priors = self.server.priors.list(confirmed_only=True)
        channel = context.get("channel", "work").strip().casefold()
        return [{**item, "is_prior": True} for item in priors if item["context"].get("channel", "work").strip().casefold() == channel]

    def _language_preferences(self, context):
        return [{**item, "is_language_lesson": True} for item in self.server.language_lessons.matching(context)]

    def _route(self, path, query, body):
        store, provider = self.server.store, self.server.provider
        method = "GET" if self.command == "HEAD" else self.command
        if method == "GET" and path == "/api/health":
            return self._json({"ok": True})
        if method == "GET" and path == "/api/state":
            samples, rounds, feedback = store.samples(), store.rounds(), store.feedback()
            lessons = self.server.language_lessons.list()
            return self._json({"samples": samples, "rounds": rounds, "feedback": feedback,
                "profile": build_profile(samples, feedback, lessons), "language_lessons": lessons,
                "corrections": self.server.corrections.list(), "quality_checks": self.server.quality_checks.list_cases(),
                "quality_summary": self.server.quality_checks.summary(),
                "priors": self.server.priors.list(), "evaluations": self.server.evaluations.list_cases(),
                "evaluation_summary": self.server.evaluations.summary(), "discovery_jobs": self.server.discovery_jobs.list(),
                "settings": {"provider": getattr(provider, "mode", "offline"), "model": getattr(provider, "model", None)},
                "stats": {"pending": sum(s["status"] == "pending" for s in samples),
                          "confirmed": sum(s["status"] == "confirmed" for s in samples), "rounds": len(rounds)},
                "capabilities": {"codex_available": bool(provider.available()), "local_scan": True,
                                 "browser_import": True, **scan_capabilities()}, "token": self.server.token})
        if method == "GET" and path == "/api/export":
            markdown = export_markdown(store.samples(), store.feedback(), query.get("examples") == ["1"], self.server.language_lessons.list())
            priors = self.server.priors.list(confirmed_only=True)
            if priors:
                markdown += "\n## Reviewed prior context (not authored samples)\n"
                for prior in priors:
                    # Quoting prevents rich-text HTML and separates source content.
                    packet = {key: prior[key] for key in ("label", "text", "context", "source")}
                    markdown += "\n" + "\n".join("> " + line.replace("<", "&lt;").replace(">", "&gt;") for line in json.dumps(packet, ensure_ascii=False, indent=2).splitlines()) + "\n"
            return self._json({"markdown": markdown, "filename": "VOICE_PROFILE.md"})
        if method == "GET" and path == "/api/corrections":
            return self._json({"corrections": self.server.corrections.list()})
        if method == "POST" and path == "/api/corrections":
            return self._json(self.server.corrections.add(body), 201)
        if method == "POST" and path.startswith("/api/corrections/"):
            parts = path.split("/")
            if len(parts) != 5 or not parts[3] or parts[4] not in {"undo", "checks"}:
                raise HTTPError(404, "Correction action not found")
            if parts[4] == "undo":
                self._fields(body, set())
                return self._json(self.server.corrections.undo(parts[3]))
            self._fields(body, {"context", "brief"})
            correction, target_lesson, context, brief = self.server.corrections.prepare_check(parts[3], body.get("context"), body.get("brief", ""))
            evidence = retrieve(store.samples(), context)
            preferences = contextual_preferences(store.feedback(), context) + self._prior_preferences(context) + self._language_preferences(context)
            if not self.server.generation_slots.acquire(blocking=False):
                raise HTTPError(429, "Generation is busy; try again shortly")
            try:
                case = self.server.quality_checks.create_case(correction, context, brief, provider, evidence, preferences, target_lesson)
                return self._json({"case": case}, 201)
            finally:
                self.server.generation_slots.release()
        if method == "POST" and path.startswith("/api/quality-checks/"):
            parts = path.split("/")
            if len(parts) != 5 or not parts[3] or parts[4] != "rate":
                raise HTTPError(404, "Quality check action not found")
            self._fields(body, {"choice", "meaning", "effort", "notes"})
            case = self.server.quality_checks.rate_case(parts[3], body)
            return self._json({"case": case, "summary": self.server.quality_checks.summary()})
        if method == "GET" and path == "/api/language-lessons":
            return self._json({"language_lessons": self.server.language_lessons.list()})
        if method == "POST" and path == "/api/language-lessons":
            return self._json({"lesson": self.server.language_lessons.add(body)}, 201)
        if path.startswith("/api/language-lessons/") and method in {"PATCH", "DELETE"}:
            ident = path[len("/api/language-lessons/"):]
            if not ident or "/" in ident or len(ident) > 100:
                raise HTTPError(404, "Language lesson not found")
            if method == "PATCH":
                return self._json({"lesson": self.server.language_lessons.update(ident, body)})
            self._fields(body, set())
            self.server.language_lessons.delete(ident)
            return self._json({"ok": True})
        if method == "GET" and path == "/api/priors":
            return self._json({"priors": self.server.priors.list()})
        if method == "POST" and path == "/api/priors":
            return self._json({"prior": self.server.priors.add(body)}, 201)
        if path.startswith("/api/priors/") and method in {"PATCH", "DELETE"}:
            ident = path.rsplit("/", 1)[-1]
            if method == "PATCH":
                self._fields(body, {"confirmed"})
                if not isinstance(body.get("confirmed"), bool):
                    raise ValueError("confirmed must be true or false")
                return self._json({"prior": self.server.priors.confirm(ident, body["confirmed"])})
            self._fields(body, set())
            self.server.priors.delete(ident)
            return self._json({"ok": True})
        if method == "POST" and path.startswith("/api/samples/") and path.endswith("/infer"):
            self._fields(body, set())
            ident = path.split("/")[-2]
            sample = next((x for x in store.samples() if x["id"] == ident), None)
            if sample is None:
                raise KeyError("Sample not found")
            if sample.get("held_out"):
                raise ValueError("Held-out samples cannot be sent for inference")
            if not self.server.generation_slots.acquire(blocking=False):
                raise HTTPError(429, "Generation is busy; try again shortly")
            try:
                return self._json({"inference": infer_context(sample, provider)})
            finally:
                self.server.generation_slots.release()
        if method == "POST" and path == "/api/discovery-jobs":
            self._fields(body, {"roots"})
            return self._json({"job": self.server.discovery_jobs.create(body.get("roots"))}, 201)
        if method == "POST" and path.startswith("/api/discovery-jobs/"):
            parts = path.split("/")
            if len(parts) != 5 or parts[-1] not in {"step", "cancel"}:
                raise HTTPError(404, "Discovery action not found")
            if not self.server.discovery_slot.acquire(blocking=False):
                raise HTTPError(429, "A local scan is already running")
            try:
                if parts[-1] == "cancel":
                    self._fields(body, set())
                    return self._json({"job": self.server.discovery_jobs.cancel(parts[-2])})
                self._fields(body, {"max_files"})
                return self._json({"job": self.server.discovery_jobs.step(parts[-2], body.get("max_files", 100))})
            finally:
                self.server.discovery_slot.release()
        if method == "POST" and path == "/api/evaluations":
            self._fields(body, {"context", "brief"})
            context = validate_context(body.get("context"))
            brief = text_field(body.get("brief", ""), "brief", 15000, True)
            evidence = retrieve(store.samples(), context)
            preferences = contextual_preferences(store.feedback(), context) + self._prior_preferences(context) + self._language_preferences(context)
            if not self.server.generation_slots.acquire(blocking=False):
                raise HTTPError(429, "Generation is busy; try again shortly")
            try:
                return self._json({"case": self.server.evaluations.create_case(context, brief, provider, evidence, preferences)}, 201)
            finally:
                self.server.generation_slots.release()
        if method == "POST" and path.startswith("/api/evaluations/") and path.endswith("/rate"):
            self._fields(body, {"choice", "factual_issue", "notes"})
            ident = path.split("/")[-2]
            case = self.server.evaluations.rate_case(ident, body.get("choice"), body.get("factual_issue", False), body.get("notes", ""))
            return self._json({"case": case, "summary": self.server.evaluations.summary()})
        if method == "POST" and path == "/api/samples":
            return self._json({"sample": store.add_sample(body)}, 201)
        if path.startswith("/api/samples/") and method in {"PATCH", "DELETE"}:
            sample_id = path[len("/api/samples/"):]
            if not sample_id or "/" in sample_id or len(sample_id) > 100:
                raise HTTPError(404, "Sample not found")
            if method == "PATCH":
                return self._json({"sample": store.update_sample(sample_id, body)})
            self._fields(body, set())
            store.delete_sample(sample_id)
            return self._json({"ok": True})
        if method == "POST" and path == "/api/import":
            self._fields(body, {"items"})
            result = store.add_samples(body.get("items"))
            return self._json({"added": result["added"], "duplicates": result["duplicates"]})
        if method == "POST" and path == "/api/discover":
            self._fields(body, {"roots", "max_files"})
            if not self.server.discovery_slot.acquire(blocking=False):
                raise HTTPError(429, "A local scan is already running")
            try:
                result = discover(body.get("roots"), body.get("max_files", 200), excluded_paths=[store.path.parent])
                items = result.pop("items")
                imported = store.add_samples(items) if items else {"added": 0, "duplicates": 0}
                result.update({"added": imported["added"], "duplicates": imported["duplicates"]})
                return self._json(result)
            finally:
                self.server.discovery_slot.release()
        if method == "POST" and path in {"/api/rounds", "/api/draft"}:
            self._fields(body, {"context", "focus"} if path == "/api/rounds" else {"context", "brief", "controls"})
            context = validate_context(body.get("context"))
            focus = validate_focus(body.get("focus", "mixed")) if path == "/api/rounds" else None
            brief = text_field(body.get("brief", ""), "brief", 15000, True) if path == "/api/draft" else None
            controls = validate_controls(body.get("controls")) if brief is not None else None
            evidence = retrieve(store.samples(), context)
            preferences = contextual_preferences(store.feedback(), context) + self._prior_preferences(context) + self._language_preferences(context)
            if not self.server.generation_slots.acquire(blocking=False):
                raise HTTPError(429, "Two generations are already running; try again shortly")
            try:
                if path == "/api/rounds":
                    generated = provider.generate_round(context, evidence, preferences, focus=focus)
                    if not isinstance(generated, dict):
                        raise ValueError("Provider returned an invalid round")
                    item = store.add_round(context, generated, [s["id"] for s in evidence], focus=focus)
                    return self._json({"round": item}, 201)
                generated = provider.generate_draft(context, brief, controls, evidence, preferences)
                if not isinstance(generated, dict):
                    raise ValueError("Provider returned an invalid draft")
                text_field(generated.get("text"), "draft", 30000, True)
                # Evidence provenance is based on what the server supplied, never
                # on IDs invented by a provider response.
                generated["evidence_ids"] = [s["id"] for s in evidence]
                return self._json(generated)
            finally:
                self.server.generation_slots.release()
        if method == "POST" and path == "/api/feedback":
            feedback = store.add_feedback(body)
            return self._json({"feedback": feedback, "profile": build_profile(store.samples(), store.feedback(), self.server.language_lessons.list())}, 201)
        if method == "GET" and path in {"/", "/index.html", "/app.js", "/styles.css", "/correction-diff.mjs"}:
            name = "index.html" if path == "/" else path[1:]
            asset = self.server.web_root / name
            if not asset.is_file() or asset.is_symlink():
                raise HTTPError(404, "App asset not found")
            types = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
            return self._respond(asset.read_bytes(), types[asset.suffix])
        raise HTTPError(404, "Route not found")


def make_server(host="127.0.0.1", port=8765, data_dir=None, provider=None, web_root=None):
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Voicework only binds to 127.0.0.1 or localhost")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("Invalid port")
    if provider is None:
        from .provider import Provider
        provider = Provider(mode="offline")
    root = Path(__file__).resolve().parent.parent
    data = Path(data_dir) if data_dir is not None else root / "data"
    return VoiceworkServer((host, port), Store(data / "voicework.sqlite3"), provider, web_root or root / "web")
