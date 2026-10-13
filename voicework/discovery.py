"""User-started, bounded, read-only document discovery. Everything stays pending."""
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from xml.etree import ElementTree

from .store import MAX_SAMPLE_CHARS, validate_sample

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_ENTRIES = 30000
MAX_ITEMS = 500
SUPPORTED = {".txt", ".md", ".eml", ".docx", ".json", ".pdf"}
IGNORED_DIRS = {
    "node_modules", "vendor", "venv", "__pycache__", "site-packages", "dist", "build",
    "target", "cache", "caches", "logs", "library", "system", "windows", "program files",
    "programdata", "appdata", "proc", "sys", "dev", "volumes", "applications",
    "keychains", "credentials", "secrets", "cookies", "sessions", "backups", "trash",
}
SENSITIVE_NAME = re.compile(r"(?:credential|secret|password|keychain|cookie|token|id_rsa|id_ed25519|authorized_keys|known_hosts|^env$|^config(?:\.|$))", re.I)
SECRET_TEXT = re.compile(
    r"-----BEGIN (?:[A-Z ]*PRIVATE KEY|OPENSSH PRIVATE KEY)-----|"
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\bgh[pousr]_[A-Za-z0-9]{20,}\b|"
    r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b|"
    r"(?im:^\s*[\"']?(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|aws_secret_access_key)[\"']?\s*[:=]\s*[\"']?[^\s\"']{8,})"
)


def capabilities():
    return {"formats": ["txt", "md", "eml", "docx", "json"] + (["pdf"] if shutil.which("pdftotext") else []),
            "pdf_available": bool(shutil.which("pdftotext"))}


def _forbidden(path):
    return str(path) == "/private" or any(part.startswith(".") or part.lower() in IGNORED_DIRS for part in path.parts if part not in ("/", "")) or bool(SENSITIVE_NAME.search(path.name))


def _inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _read_regular(path, root):
    # O_NOFOLLOW protects the final component, and the resolved containment check
    # rejects symlink escapes. Directory entries themselves are never followed.
    resolved = path.resolve(strict=True)
    if not _inside(resolved, root) or resolved != path:
        raise ValueError("Symlink or path escape skipped")
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Not a regular file")
        if info.st_size > MAX_FILE_BYTES:
            raise ValueError("File exceeds 10 MiB limit")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("File exceeds 10 MiB limit")
        return raw
    finally:
        os.close(fd)


def _decode(raw):
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if b"\x00" in raw:
        raise ValueError("Binary content skipped")
    return raw.decode("utf-8-sig")


def _docx(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        info = archive.getinfo("word/document.xml")
        if info.file_size > 8 * 1024 * 1024 or info.compress_size and info.file_size / info.compress_size > 150:
            raise ValueError("Oversized compressed document skipped")
        xml = archive.read(info)
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
        raise ValueError("Document entities are not supported")
    root = ElementTree.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for paragraph in root.findall(".//w:p", ns):
        paragraphs.append("".join(node.text or "" for node in paragraph.findall(".//w:t", ns)))
    return "\n\n".join(paragraphs)


def _eml(raw):
    message = BytesParser(policy=policy.default).parsebytes(raw)
    body = message.get_body(preferencelist=("plain",))
    if body is None:
        raise ValueError("Email has no plain-text body; HTML and attachments were skipped")
    content = body.get_content()
    if not isinstance(content, str):
        raise ValueError("Email body is not text")
    return content


def _pdf(raw):
    command = shutil.which("pdftotext")
    if not command:
        raise ValueError("PDF skipped: optional pdftotext is not installed")
    with tempfile.TemporaryDirectory(prefix="voicework-pdf-") as directory:
        source = Path(directory) / "source.pdf"
        destination = Path(directory) / "text.txt"
        source.write_bytes(raw)
        result = subprocess.run([command, "-f", "1", "-l", "30", "-nopgbrk", str(source), str(destination)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=15, check=False)
        if result.returncode != 0 or not destination.exists():
            raise ValueError("PDF text extraction failed (encrypted, scanned, or unsupported file)")
        with destination.open("rb") as handle:
            output = handle.read(MAX_FILE_BYTES + 1)
        if len(output) > MAX_FILE_BYTES:
            raise ValueError("PDF extracted text exceeds limit")
        return _decode(output)


def _json_items(raw, path):
    data = json.loads(_decode(raw))
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        if not data["items"] or len(data["items"]) > MAX_ITEMS:
            raise ValueError("JSON items must contain 1 to 500 samples")
        # Only the explicit import schema is accepted. Status cannot bypass review.
        result = []
        for item in data["items"]:
            normalized = validate_sample(item)
            normalized.pop("fingerprint")
            normalized["source_path"] = str(path)
            result.append(normalized)
        return result
    conversations = data if isinstance(data, list) else [data]
    if not conversations or not all(isinstance(c, dict) and isinstance(c.get("mapping"), dict) for c in conversations):
        raise ValueError("JSON skipped: expected Voicework items or a ChatGPT conversation export")
    result = []
    for conversation in conversations:
        title = str(conversation.get("title") or path.stem)[:240]
        # Follow only the active branch when current_node is provided, so an
        # edited/deleted conversation branch is not silently imported as current.
        mapping = conversation["mapping"]
        nodes = list(mapping.values())
        if isinstance(conversation.get("current_node"), str) and conversation["current_node"] in mapping:
            nodes, seen = [], set()
            current = conversation["current_node"]
            while isinstance(current, str) and current in mapping and current not in seen:
                seen.add(current)
                node = mapping[current]
                if not isinstance(node, dict):
                    break
                nodes.append(node)
                current = node.get("parent")
            nodes.reverse()
        for node in nodes:
            if not isinstance(node, dict):
                continue
            message = node.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("author"), dict) or message["author"].get("role") != "user":
                continue
            content = message.get("content")
            if not isinstance(content, dict) or content.get("content_type") not in (None, "text"):
                continue
            parts = content.get("parts", [])
            if not isinstance(parts, list):
                continue
            text = "\n".join(p for p in parts if isinstance(p, str)).strip()
            if text:
                result.append({"title": title, "text": text[:MAX_SAMPLE_CHARS], "source_type": "chatgpt-export", "source_path": str(path)})
            if len(result) >= MAX_ITEMS:
                return result
    if not result:
        raise ValueError("No user-authored text messages found in export")
    return result


def extract(path, raw):
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _json_items(raw, path)
    if suffix == ".docx":
        text = _docx(raw)
    elif suffix == ".eml":
        text = _eml(raw)
    elif suffix == ".pdf":
        text = _pdf(raw)
    else:
        text = _decode(raw)
    text = text.strip()
    if not text:
        raise ValueError("No readable text found")
    return [{"title": path.stem[:300], "text": text[:MAX_SAMPLE_CHARS], "source_type": suffix[1:], "source_path": str(path)}]


def discover(roots, max_files=200, excluded_paths=()):
    if not isinstance(roots, list) or not 1 <= len(roots) <= 8 or not all(isinstance(r, str) and 0 < len(r) <= 4096 for r in roots):
        raise ValueError("roots must contain 1 to 8 absolute folder paths")
    if isinstance(max_files, bool) or not isinstance(max_files, int) or not 1 <= max_files <= 1000:
        raise ValueError("max_files must be an integer from 1 to 1000")
    exclusions = [Path(p).resolve() for p in excluded_paths]
    items, errors, seen_files = [], [], set()
    skipped = scanned = entries = total_bytes = truncated = 0
    stopped = False

    def report(path, message):
        if len(errors) < 100:
            errors.append("%s: %s" % (str(path)[:400], message))

    for root_text in roots:
        root_input = Path(root_text)
        if not root_input.is_absolute() or "\x00" in root_text:
            raise ValueError("Discovery roots must be absolute folder paths")
        if any(part == ".." for part in root_input.parts):
            raise ValueError("Parent traversal is not accepted in discovery roots")
        root = root_input.resolve()
        if root != root_input or root_input.is_symlink() or _forbidden(root) or any(_inside(root, p) for p in exclusions):
            report(root_input, "Hidden, sensitive, excluded, or symlink root skipped")
            skipped += 1
            continue
        if not root.is_dir():
            report(root, "Folder does not exist or cannot be read")
            skipped += 1
            continue
        stack = [root]
        while stack and not stopped:
            directory = stack.pop()
            try:
                with os.scandir(directory) as iterator:
                    for entry in iterator:
                        entries += 1
                        if entries > MAX_ENTRIES or scanned >= max_files or len(items) >= MAX_ITEMS or total_bytes >= MAX_TOTAL_BYTES:
                            report(root, "Scan limit reached; narrow the selected folders to continue")
                            stopped = True
                            break
                        path = Path(entry.path)
                        if entry.is_symlink() or _forbidden(path) or any(_inside(path, p) for p in exclusions):
                            skipped += 1
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(path)
                            continue
                        if not entry.is_file(follow_symlinks=False) or path.suffix.lower() not in SUPPORTED:
                            skipped += 1
                            continue
                        if str(path) in seen_files:
                            continue
                        seen_files.add(str(path))
                        scanned += 1
                        try:
                            raw = _read_regular(path, root)
                            total_bytes += len(raw)
                            if total_bytes > MAX_TOTAL_BYTES:
                                report(root, "50 MiB scan budget reached")
                                stopped = True
                                break
                            extracted = extract(path, raw)
                            if path.suffix.lower() == ".pdf":
                                report(path, "PDF extraction is limited to the first 30 pages; later pages and image-only text are not read")
                            if path.suffix.lower() == ".json" and len(extracted) >= MAX_ITEMS:
                                report(path, "JSON extraction reached the 500-message cap; additional messages may be omitted")
                            accepted = []
                            for item in extracted:
                                if SECRET_TEXT.search(item["text"]):
                                    report(path, "Credential-like content skipped")
                                    skipped += 1
                                    continue
                                # Final schema validation happens before any store write.
                                validate_sample(item)
                                accepted.append(item)
                                if len(items) + len(accepted) >= MAX_ITEMS:
                                    break
                            if any(len(item["text"]) == MAX_SAMPLE_CHARS for item in accepted):
                                truncated += 1
                                report(path, "Text reached the 30,000-character cap; the stored candidate may be truncated")
                            items.extend(accepted)
                        except ValueError as exc:
                            # Only expose our predictable validation messages; decoder
                            # errors can echo source bytes and should not reach the UI.
                            message = str(exc) if type(exc) is ValueError else "Invalid or unsupported document encoding/structure"
                            report(path, message)
                            skipped += 1
                        except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError, subprocess.TimeoutExpired, RecursionError):
                            report(path, "Unreadable, malformed, unsupported, or timed-out document")
                            skipped += 1
            except OSError:
                report(directory, "Folder cannot be read")
                skipped += 1
        if stopped:
            break
    return {"items": items, "skipped": skipped, "errors": errors, "scanned": scanned,
            "limited": stopped, "truncated": truncated, "capabilities": capabilities()}
