from __future__ import annotations

import re
from urllib.parse import urljoin


URI_ATTR_PATTERN = re.compile(r'URI="([^"]+)"')


def _rewrite_uri_attributes(line: str, base_url: str, token_factory) -> str:
    def repl(match: re.Match[str]) -> str:
        original_uri = match.group(1)
        absolute_uri = urljoin(base_url, original_uri)
        proxied_uri = token_factory(absolute_uri)
        return f'URI="{proxied_uri}"'

    return URI_ATTR_PATTERN.sub(repl, line)


def rewrite_hls_manifest(manifest: str, base_url: str, token_factory) -> str:
    rewritten_lines: list[str] = []
    for raw_line in manifest.splitlines():
        line = raw_line.strip()
        if not line:
            rewritten_lines.append(raw_line)
            continue

        if line.startswith("#"):
            rewritten_lines.append(_rewrite_uri_attributes(raw_line, base_url, token_factory))
            continue

        absolute_uri = urljoin(base_url, line)
        proxied_uri = token_factory(absolute_uri)

        if raw_line != line:
            prefix = raw_line[: raw_line.index(line)]
            rewritten_lines.append(prefix + proxied_uri)
        else:
            rewritten_lines.append(proxied_uri)

    return "\n".join(rewritten_lines)
