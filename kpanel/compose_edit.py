"""Surgical edits to the `mc` service's environment block in docker-compose.yml.

Why line edits instead of yaml load -> modify -> dump: most of this compose
file is comments recording *why* each value is what it is, and a YAML dump
throws every one of them away. So only the lines that must change are
rewritten. Then old and new are both parsed, and the edit is rejected unless
the ONLY difference is the intended keys. Surgical in what it writes, checked
as if it were a full rewrite.
"""

import re

import yaml

SERVICE = "mc"
ENTRY_INDENT = " " * 6
MARKER = ENTRY_INDENT + "# --- set from kpanel ---------------------------------------------------"

_ENTRY = re.compile(r"^ {6}([A-Za-z_][A-Za-z0-9_]*):(.*)$")


class ComposeEditError(Exception):
    pass


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _env_block(lines):
    """(first, end): environment entries of the mc service are lines[first:end]."""
    svc = next((i for i, l in enumerate(lines) if l.rstrip() == f"  {SERVICE}:"), None)
    if svc is None:
        raise ComposeEditError(f"service '{SERVICE}' not found")
    env = None
    for i in range(svc + 1, len(lines)):
        l = lines[i]
        if l.strip() and not l.lstrip().startswith("#") and _indent(l) <= 2:
            break  # reached the next service
        if l.rstrip() == "    environment:":
            env = i
            break
    if env is None:
        raise ComposeEditError(f"'{SERVICE}' has no environment: block")
    last = env
    for i in range(env + 1, len(lines)):
        l = lines[i]
        if not l.strip():
            continue
        if _indent(l) < len(ENTRY_INDENT):
            break  # e.g. "    volumes:" or a 4-space comment
        if not l.lstrip().startswith("#"):
            last = i  # an entry (or a continuation line of one)
    return env + 1, last + 1


def _find(lines, key):
    first, end = _env_block(lines)
    for i in range(first, end):
        m = _ENTRY.match(lines[i])
        if m and m.group(1) == key:
            return i, m.group(2).strip()
    return None, None


def raw_value(text: str, key: str):
    """The value exactly as written in the file (quotes included), or None."""
    _, raw = _find(text.splitlines(), key)
    return raw


def is_externally_managed(raw) -> bool:
    """True for values like "${MC_OPS}": their truth lives wherever the variable is set, not here."""
    return raw is not None and "${" in raw


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


CUSTOM = "CUSTOM_SERVER_PROPERTIES"
_BLOCK_INDENT = " " * 8


def _custom_block(lines):
    """(header_idx, end_idx, {key: value}) of the CUSTOM_SERVER_PROPERTIES block, or (None, None, {})."""
    idx, raw = _find(lines, CUSTOM)
    if idx is None:
        return None, None, {}
    if raw not in ("|", "|-"):
        raise ComposeEditError(f"{CUSTOM} must be a '|' block to be edited by the panel")
    props, end = {}, idx + 1
    for i in range(idx + 1, len(lines)):
        l = lines[i]
        if l.strip() and _indent(l) < len(_BLOCK_INDENT):
            break
        if l.strip():
            k, sep, v = l.strip().partition("=")
            if not sep:
                raise ComposeEditError(f"{CUSTOM}: line without '=': {l.strip()!r}")
            props[k.strip()] = v
            end = i + 1
    return idx, end, props


def custom_properties(text: str) -> dict:
    """The key=value pairs currently in CUSTOM_SERVER_PROPERTIES."""
    return _custom_block(text.splitlines())[2]


def _render_custom(props: dict) -> list:
    return [f"{ENTRY_INDENT}{CUSTOM}: |"] + [f"{_BLOCK_INDENT}{k}={v}" for k, v in sorted(props.items())]


def apply_changes(text: str, changes: dict, custom: dict | None = None) -> str:
    """Return `text` with env KEYs and CUSTOM_SERVER_PROPERTIES entries set.

    `changes`: {ENV_KEY: value}, one line each in mc.environment.
    `custom`:  {property-key: value}, lines in the CUSTOM_SERVER_PROPERTIES block,
               for properties that itzg has no dedicated env var for.
    Raises ComposeEditError rather than ever writing something unverified.
    """
    custom = custom or {}
    if not changes and not custom:
        return text
    for k, v in list(changes.items()) + list(custom.items()):
        if not isinstance(v, str):
            raise ComposeEditError(f"{k}: value must be a string")
        if "$" in v or "\n" in v:
            raise ComposeEditError(f"{k}: '$' and line breaks are not allowed")
    for k in custom:
        if not re.fullmatch(r"[a-z0-9][a-z0-9.\-]*", k):
            raise ComposeEditError(f"{k!r} is not a valid property key")
    if CUSTOM in changes:
        raise ComposeEditError(f"set {CUSTOM} entries through `custom`, not as a raw value")

    before = yaml.safe_load(text)
    lines = text.splitlines()
    expected = dict(changes)

    if custom:
        idx, end, current = _custom_block(lines)
        merged = {**current, **custom}
        block = _render_custom(merged)
        if idx is not None:
            lines[idx:end] = block
        else:
            _, env_end = _env_block(lines)
            if MARKER in lines[:env_end]:
                lines[env_end:env_end] = block
            else:
                lines[env_end:env_end] = ["", MARKER] + block
        expected[CUSTOM] = "".join(f"{k}={v}\n" for k, v in sorted(merged.items()))

    for key, value in changes.items():
        idx, raw = _find(lines, key)
        new_line = f"{ENTRY_INDENT}{key}: {_quote(value)}"
        if idx is not None:
            if is_externally_managed(raw):
                raise ComposeEditError(f"{key} is set from a variable ({raw}); change the variable instead")
            if raw[:1] in ("|", ">") or raw == "":
                raise ComposeEditError(f"{key} uses a multi-line value; edit it by hand")
            lines[idx] = new_line
        else:
            _, end = _env_block(lines)
            if MARKER in lines[:end]:
                lines.insert(end, new_line)
            else:
                lines[end:end] = ["", MARKER, new_line]

    out = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    _verify(before, out, expected)
    return out


def _verify(before, new_text, changes):
    """Fail unless the parsed result differs from `before` in exactly `changes`."""
    try:
        after = yaml.safe_load(new_text)
    except yaml.YAMLError as e:
        raise ComposeEditError(f"edit produced invalid YAML: {e}") from None

    b_env = dict(before["services"][SERVICE].get("environment") or {})
    a_env = dict(after["services"][SERVICE].get("environment") or {})
    for k, v in changes.items():
        if a_env.get(k) != v:
            raise ComposeEditError(f"{k} did not end up as {v!r} (got {a_env.get(k)!r})")
        b_env[k] = v
    if a_env != b_env:
        extra = set(a_env.items()) ^ set(b_env.items())
        raise ComposeEditError(f"unexpected environment changes: {sorted(extra)}")

    # Everything outside mc.environment must be byte-for-byte identical once parsed.
    def strip(doc):
        d = yaml.safe_load(yaml.safe_dump(doc))
        d["services"][SERVICE].pop("environment", None)
        return d

    if strip(before) != strip(after):
        raise ComposeEditError("edit changed something outside mc.environment")
